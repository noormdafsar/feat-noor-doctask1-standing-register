from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelError(RuntimeError):
    pass


class CassetteMiss(ModelError):
    pass


class BudgetExceeded(ModelError):
    pass


@dataclass
class ModelRequest:
    """Everything a provider needs, and nothing about how it is served.

    `meta` is how the fixture provider finds its canned answer without depending
    on the exact wording of a prompt; it is never sent to a live model.
    """

    task: str
    system: str
    user: str
    schema: dict[str, Any]
    model: str
    meta: dict[str, Any] = field(default_factory=dict)

    def sha(self) -> str:
        payload = json.dumps(
            {"t": self.task, "s": self.system, "u": self.user, "m": self.model},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class ModelResponse:
    data: dict[str, Any]
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    replayed: bool = False


class ModelProvider(Protocol):
    name: str

    def complete(self, req: ModelRequest) -> ModelResponse: ...


def build_provider(backend: str | None = None) -> ModelProvider:
    from app.config import settings

    backend = backend or settings.model_backend
    if backend == "fixture":
        from app.llm.fixture import FixtureProvider

        return FixtureProvider()
    if backend == "cassette":
        from app.llm.cassette import CassetteProvider

        return CassetteProvider()
    if backend in ("gemini", "record"):
        from app.llm.gemini import GeminiProvider

        return GeminiProvider(record=(backend == "record"))
    raise ModelError(f"unknown MODEL_BACKEND: {backend!r}")
