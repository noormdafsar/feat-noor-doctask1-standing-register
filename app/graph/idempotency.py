from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NodeResult


def input_sha(node: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        (node + "|" + json.dumps(payload, sort_keys=True, default=str)).encode()
    ).hexdigest()


def cached(s: Session, run_id: str, node: str, sha: str) -> dict[str, Any] | None:
    row = s.execute(
        select(NodeResult).where(
            NodeResult.run_id == run_id, NodeResult.node == node, NodeResult.input_sha == sha
        )
    ).scalar_one_or_none()
    return row.output if row else None


def remember(s: Session, run_id: str, node: str, sha: str, output: dict[str, Any]) -> None:
    existing = s.execute(
        select(NodeResult).where(
            NodeResult.run_id == run_id, NodeResult.node == node, NodeResult.input_sha == sha
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.output = output
        return
    s.add(NodeResult(run_id=run_id, node=node, input_sha=sha, output=output))
