from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import ApprovalBatch, ApprovalItem
from app.db.session import session_scope


class BatchNotFound(LookupError):
    pass


class UnknownItem(ValueError):
    pass


def get_batch(batch_id: str) -> dict[str, Any]:
    with session_scope() as s:
        batch = s.get(ApprovalBatch, batch_id)
        if batch is None:
            raise BatchNotFound(batch_id)
        items = (
            s.execute(select(ApprovalItem).where(ApprovalItem.batch_id == batch_id))
            .scalars()
            .all()
        )
        return {
            "batch_id": batch.id,
            "run_id": batch.run_id,
            "family_id": batch.family_id,
            "status": batch.status,
            "items": [_item(i) for i in items],
        }


def pending_for_run(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        batch = s.execute(
            select(ApprovalBatch).where(ApprovalBatch.run_id == run_id)
        ).scalar_one_or_none()
        if batch is None:
            return {"batch_id": None, "items": []}
        batch_id = batch.id
    return get_batch(batch_id)


def decide(
    batch_id: str, decisions: list[dict[str, Any]], decided_by: str = "api"
) -> dict[str, Any]:
    """Accept and reject in the same call, item by item.

    Partial decisions persist. Rejecting one item has no effect on any other,
    which is the property behaviour 3 is actually testing.
    """
    with session_scope() as s:
        batch = s.get(ApprovalBatch, batch_id)
        if batch is None:
            raise BatchNotFound(batch_id)
        items = {
            i.id: i
            for i in s.execute(
                select(ApprovalItem).where(ApprovalItem.batch_id == batch_id)
            ).scalars()
        }
        by_ref = {i.ref_id: i for i in items.values()}

        applied = 0
        for d in decisions:
            key = d.get("item_id") or d.get("ref_id")
            item = items.get(key) or by_ref.get(key)
            if item is None:
                raise UnknownItem(f"{key!r} is not an item in batch {batch_id}")
            verdict = d.get("decision", "").lower()
            if verdict not in ("accept", "reject"):
                raise UnknownItem(
                    f"decision for {key!r} must be 'accept' or 'reject', got {verdict!r}"
                )
            item.decision = verdict
            item.reason = d.get("reason")
            item.decided_by = decided_by
            item.decided_at = utcnow()
            applied += 1

        remaining = [i for i in items.values() if i.decision == "pending"]
        return {
            "batch_id": batch_id,
            "decided": applied,
            "still_pending": len(remaining),
            "pending_item_ids": [i.id for i in remaining],
        }


def decide_all(batch_id: str, verdict: str, reason: str | None = None) -> dict[str, Any]:
    """Convenience for demos and the CLI. Deliberately not exposed as an API default:
    a blanket approval is a decision a person makes, not one the system assumes."""
    batch = get_batch(batch_id)
    return decide(
        batch_id,
        [{"item_id": i["item_id"], "decision": verdict, "reason": reason} for i in batch["items"]],
        decided_by="cli",
    )


def _item(i: ApprovalItem) -> dict[str, Any]:
    return {
        "item_id": i.id,
        "kind": i.kind,
        "ref_id": i.ref_id,
        "title": i.title,
        "payload": i.payload,
        "decision": i.decision,
        "reason": i.reason,
        "decided_by": i.decided_by,
        "decided_at": i.decided_at.isoformat() if i.decided_at else None,
    }
