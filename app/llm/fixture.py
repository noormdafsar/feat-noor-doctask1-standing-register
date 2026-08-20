from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.llm.provider import ModelError, ModelRequest, ModelResponse


class FixtureProvider:
    """Deterministic canned model output, keyed on (task, document filename).

    Why keyed on meta rather than on the prompt hash: this provider exists so the
    behaviour suite can exercise the real graph, the real database and the real
    transaction boundaries without a key. Keying on the prompt would make every
    prompt edit a test failure for reasons that have nothing to do with behaviour.

    The fixtures are deliberately imperfect on purpose -- one carries a quote that
    does not exist in the source, so span verification has something real to catch.
    Nothing here is asserted as correct model output; the tests assert what the
    *system* does with model output.
    """

    name = "fixture"

    def __init__(self) -> None:
        p = Path(settings.fixture_file)
        if not p.exists():
            raise ModelError(f"fixture file missing: {p}")
        self.data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))

    def complete(self, req: ModelRequest) -> ModelResponse:
        table = self.data.get(req.task, {})
        key = str(req.meta.get("key", ""))
        payload = table.get(key)
        if payload is None:
            payload = table.get("__default__")
        if payload is None:
            raise ModelError(
                f"no fixture for task={req.task!r} key={key!r}. "
                f"Add one to {settings.fixture_file}."
            )
        # Token counts are synthetic but stable, so the cost rollup has real
        # arithmetic to be right or wrong about.
        return ModelResponse(
            data=payload,
            tokens_in=len(req.user) // 4,
            tokens_out=len(json.dumps(payload)) // 4,
            model="fixture",
        )
