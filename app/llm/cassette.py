from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.llm.provider import CassetteMiss, ModelRequest, ModelResponse


class CassetteProvider:
    """Replays a real recorded exchange, keyed on the exact request hash.

    Stricter than the fixture provider and it is the honest one to use when
    checking that a prompt change did not change model behaviour -- but it goes
    stale the moment a prompt is edited, which is why it is not the CI default.
    """

    name = "cassette"

    def __init__(self) -> None:
        self.dir = Path(settings.cassette_dir)

    def complete(self, req: ModelRequest) -> ModelResponse:
        p = self.dir / f"{req.sha()}.json"
        if not p.exists():
            raise CassetteMiss(
                f"no cassette for task={req.task} sha={req.sha()[:12]}. "
                f"Record one with `make record`, or use MODEL_BACKEND=fixture."
            )
        blob = json.loads(p.read_text(encoding="utf-8"))
        return ModelResponse(
            data=blob["data"],
            tokens_in=blob.get("tokens_in", 0),
            tokens_out=blob.get("tokens_out", 0),
            model=blob.get("model", req.model),
            replayed=True,
        )
