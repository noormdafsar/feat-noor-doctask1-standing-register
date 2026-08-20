from __future__ import annotations

from typing import Any

from langgraph.types import Command
from sqlalchemy import func, select

from app.config import settings
from app.db.models import ApprovalBatch, ApprovalItem, Run, Step
from app.db.session import session_scope
from app.graph.build import compiled_graph
from app.graph.state import RunState


class RunNotFound(LookupError):
    pass


class RunNotAwaitingApproval(RuntimeError):
    pass


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 60}


def start_run(
    *,
    family_id: str,
    mode: str = "full",
    rule_pack: str = "procurement-baseline",
    trigger_document_id: str | None = None,
) -> dict[str, Any]:
    """Drive the graph until it either finishes or stops at the gate."""
    with session_scope() as s:
        run = Run(
            family_id=family_id,
            mode=mode,
            rule_pack=rule_pack,
            trigger_document_id=trigger_document_id,
        )
        s.add(run)
        s.flush()
        run_id = run.id

    state: RunState = {
        "run_id": run_id,
        "family_id": family_id,
        "mode": mode,
        "rule_pack": rule_pack,
        "trigger_document_id": trigger_document_id,
        "doc_ids": [],
        "low_confidence_docs": [],
        "empty_extract_docs": [],
        "repair_attempts": 0,
        "escalations": [],
        "stats": {},
    }
    with compiled_graph() as graph:
        graph.invoke(state, _config(run_id))
    return status(run_id)


def resume_run(run_id: str) -> dict[str, Any]:
    """Continue a run that is parked at the gate, or one that was killed mid-flight.

    Both cases are the same operation: hand the graph its thread id and let the
    checkpointer decide where to pick up.
    """
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)

    with compiled_graph() as graph:
        snapshot = graph.get_state(_config(run_id))
        if not snapshot.next:
            return status(run_id)
        interrupted = bool(getattr(snapshot, "tasks", None)) and any(
            getattr(t, "interrupts", None) for t in snapshot.tasks
        )
        if interrupted:
            decisions = _decisions_for(run_id)
            graph.invoke(Command(resume=decisions), _config(run_id))
        else:
            # Killed between nodes: no interrupt is pending, so re-entering the
            # thread with a null input replays from the last committed checkpoint.
            graph.invoke(None, _config(run_id))
    return status(run_id)


def _decisions_for(run_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        batch = s.execute(
            select(ApprovalBatch).where(ApprovalBatch.run_id == run_id)
        ).scalar_one_or_none()
        if batch is None:
            return []
        items = (
            s.execute(select(ApprovalItem).where(ApprovalItem.batch_id == batch.id))
            .scalars()
            .all()
        )
        pending = [i for i in items if i.decision == "pending"]
        if pending:
            raise RunNotAwaitingApproval(
                f"{len(pending)} item(s) in batch {batch.id} are still undecided. "
                f"Every item must be explicitly accepted or rejected before the run "
                f"can commit."
            )
        return [
            {"item_id": i.id, "kind": i.kind, "ref_id": i.ref_id, "decision": i.decision}
            for i in items
        ]


def status(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)
        batch = s.execute(
            select(ApprovalBatch).where(ApprovalBatch.run_id == run_id)
        ).scalar_one_or_none()
        steps = (
            s.execute(select(Step).where(Step.run_id == run_id).order_by(Step.seq))
            .scalars()
            .all()
        )
        return {
            "run_id": run.id,
            "family_id": run.family_id,
            "mode": run.mode,
            "rule_pack": run.rule_pack,
            "status": run.status,
            "stats": run.stats or {},
            "approval_batch_id": batch.id if batch else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "steps": [
                {
                    "seq": st.seq,
                    "node": st.node,
                    "decision": st.decision,
                    "confidence": st.confidence,
                    "detail": st.detail,
                    "model": st.model,
                    "tokens_in": st.tokens_in,
                    "tokens_out": st.tokens_out,
                    "usd": st.usd_estimate,
                    "ms": st.latency_ms,
                    "replayed": st.replayed,
                }
                for st in steps
            ],
        }


def cost(run_id: str) -> dict[str, Any]:
    """Behaviour 10: what it spent and where the time went, stage by stage."""
    with session_scope() as s:
        rows = s.execute(
            select(
                Step.node,
                func.count().label("calls"),
                func.sum(Step.tokens_in),
                func.sum(Step.tokens_out),
                func.sum(Step.usd_estimate),
                func.sum(Step.latency_ms),
            )
            .where(Step.run_id == run_id)
            .group_by(Step.node)
        ).all()
        by_stage = [
            {
                "node": r[0],
                "steps": r[1],
                "tokens_in": int(r[2] or 0),
                "tokens_out": int(r[3] or 0),
                "usd": round(float(r[4] or 0.0), 6),
                "ms": int(r[5] or 0),
            }
            for r in rows
        ]
        return {
            "run_id": run_id,
            "by_stage": sorted(by_stage, key=lambda x: -x["ms"]),
            "total": {
                "tokens_in": sum(x["tokens_in"] for x in by_stage),
                "tokens_out": sum(x["tokens_out"] for x in by_stage),
                "usd": round(sum(x["usd"] for x in by_stage), 6),
                "ms": sum(x["ms"] for x in by_stage),
            },
            "note": (
                "Costs are estimated from token counts against the price table in "
                f"app/config.py. Model backend for this run: {settings.model_backend}."
            ),
        }


def list_runs(family_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Run).order_by(Run.started_at.desc()).limit(50)
        if family_id:
            q = q.where(Run.family_id == family_id)
        return [
            {
                "run_id": r.id,
                "family_id": r.family_id,
                "mode": r.mode,
                "status": r.status,
                "stats": r.stats or {},
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for r in s.execute(q).scalars()
        ]
