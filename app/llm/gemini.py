from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from app.config import settings
from app.llm.provider import ModelError, ModelRequest, ModelResponse

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider:
    """Direct REST against the Gemini API.

    Deliberately not an SDK: one dependency fewer, one source of version drift
    fewer, and the request shape stays visible in the diff.
    """

    name = "gemini"

    # Process-wide circuit breaker: model name -> timestamp it may be tried again.
    # Without this, a model that is down costs the full retry backoff on EVERY
    # call, and a 16-call run spends six minutes waiting on a model that answered
    # 503 the first time. Shared across instances because a provider is
    # constructed per node.
    _open_until: dict[str, float] = {}
    BREAKER_COOLDOWN = 120.0
    MAX_ATTEMPTS = 2

    def __init__(self, record: bool = False, timeout: float = 120.0) -> None:
        if not settings.gemini_api_key:
            raise ModelError(
                "MODEL_BACKEND is gemini/record but GEMINI_API_KEY is empty. "
                "Set it in .env, or run with MODEL_BACKEND=fixture (no key needed)."
            )
        self.record = record
        self.client = httpx.Client(timeout=timeout)

    def complete(self, req: ModelRequest) -> ModelResponse:
        body = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": req.user}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(req.schema),
            },
        }
        now = time.time()
        open_until = self._open_until.get(req.model, 0.0)
        if now < open_until:
            raise ModelError(
                f"{req.model} is in cooldown for another {open_until - now:.0f}s after "
                f"repeated failures; falling through to the next model in the chain"
            )

        last: str = ""
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                r = self.client.post(
                    ENDPOINT.format(model=req.model),
                    params={"key": settings.gemini_api_key},
                    json=body,
                )
                if r.status_code >= 400:
                    # Name the cause. "retryable" told a caller nothing, which cost
                    # real debugging time the first time this path was exercised.
                    last = f"HTTP {r.status_code} from {req.model}: {_api_message(r)}"
                    if r.status_code in (429, 500, 502, 503, 504):
                        if attempt + 1 < self.MAX_ATTEMPTS:
                            time.sleep(1.0 * (2**attempt))
                            continue
                        # Overloaded or rate limited twice running: stop paying this
                        # cost on every subsequent call in the run.
                        self._open_until[req.model] = time.time() + self.BREAKER_COOLDOWN
                        raise ModelError(last)
                    raise ModelError(f"{last} (not retryable)")
                payload = r.json()
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
                usage = payload.get("usageMetadata", {})
                resp = ModelResponse(
                    data=json.loads(text),
                    tokens_in=usage.get("promptTokenCount", 0),
                    tokens_out=usage.get("candidatesTokenCount", 0),
                    model=req.model,
                )
                if self.record:
                    _write_cassette(req, resp)
                return resp
            except ModelError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < self.MAX_ATTEMPTS:
                    time.sleep(1.0 * (2**attempt))
        raise ModelError(
            f"gemini call to {req.model} failed after {self.MAX_ATTEMPTS} attempts. "
            f"Last error: {last}. "
            f"If this is a 404, the model name is retired -- list what your key can "
            f"reach with: curl 'https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY'"
        )


def _api_message(r) -> str:
    try:
        return r.json().get("error", {}).get("message", r.text[:300])
    except Exception:  # noqa: BLE001
        return r.text[:300]


def _to_gemini_schema(schema: dict) -> dict:
    """JSON Schema -> the subset Gemini's responseSchema accepts."""
    out: dict = {}
    t = schema.get("type")
    if t:
        out["type"] = t.upper() if isinstance(t, str) else t
    if "description" in schema:
        out["description"] = schema["description"]
    if "enum" in schema:
        out["enum"] = [str(e) for e in schema["enum"]]
        out["type"] = "STRING"
    if t == "object":
        out["properties"] = {k: _to_gemini_schema(v) for k, v in schema.get("properties", {}).items()}
        if schema.get("required"):
            out["required"] = schema["required"]
    if t == "array":
        out["items"] = _to_gemini_schema(schema.get("items", {"type": "string"}))
    return out


def _write_cassette(req: ModelRequest, resp: ModelResponse) -> None:
    d = Path(settings.cassette_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{req.sha()}.json").write_text(
        json.dumps(
            {
                "task": req.task,
                "model": req.model,
                "meta": req.meta,
                "data": resp.data,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
