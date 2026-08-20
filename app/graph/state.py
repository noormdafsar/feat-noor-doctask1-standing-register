from __future__ import annotations

from typing import Any, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    family_id: str
    mode: str  # full | incremental
    rule_pack: str
    trigger_document_id: str | None

    doc_ids: list[str]
    low_confidence_docs: list[str]
    empty_extract_docs: list[str]
    repair_attempts: int
    escalations: list[dict[str, Any]]

    batch_id: str | None
    decisions: list[dict[str, Any]]
    stats: dict[str, Any]
