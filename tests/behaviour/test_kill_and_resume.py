from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import func, select

from app.db.models import ModelCall, Run
from app.db.session import session_scope
from app.domain import approvals, deliverable, runs
from tests.conftest import seed_family

PRE_KILL_TASKS = ("classify", "extract_terms", "extract_invoice")


def _model_calls(run_id: str, tasks: tuple[str, ...]) -> int:
    with session_scope() as s:
        return s.execute(
            select(func.count())
            .select_from(ModelCall)
            .where(ModelCall.run_id == run_id, ModelCall.task.in_(tasks))
        ).scalar_one()


def _latest_run(family_id: str) -> str:
    with session_scope() as s:
        return s.execute(
            select(Run.id).where(Run.family_id == family_id).order_by(Run.started_at.desc())
        ).scalars().first()


def test_sigkill_midrun_resumes_without_reworking_or_repaying():
    fam = seed_family("acme-v1")

    env = {**os.environ, "CRASH_AFTER_NODE": "extract", "MODEL_BACKEND": "fixture"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "run", "acme-v1"],
        env=env,
        capture_output=True,
        timeout=300,
    )
    # -9 is SIGKILL. Not an exception the process could have caught and tidied up after.
    assert proc.returncode in (-9, 137), (
        f"expected the child to be killed, got {proc.returncode}: {proc.stderr[-800:]!r}"
    )

    run_id = _latest_run(fam)
    assert run_id
    spend_before = _model_calls(run_id, PRE_KILL_TASKS)
    assert spend_before > 0, "the kill must land after real work was committed"

    with session_scope() as s:
        assert s.get(Run, run_id).status == "running"

    # Same operation as approving past the gate: hand the graph its thread id.
    after = runs.resume_run(run_id)
    assert after["status"] == "awaiting_approval"

    spend_after = _model_calls(run_id, PRE_KILL_TASKS)
    assert spend_after == spend_before, (
        f"resume re-paid for work already done: {spend_before} -> {spend_after}"
    )

    batch = approvals.pending_for_run(run_id)
    approvals.decide_all(batch["batch_id"], "accept")
    final = runs.resume_run(run_id)
    assert final["status"] == "complete"

    # And the result is the same as an uninterrupted run would have produced.
    rows = {r["term_key"]: r for r in deliverable.register(fam, "json")["rows"]}
    assert rows["unit_price"]["value"]["value"] == "48.50"
    assert rows["payment_terms_days"]["value"]["value"] == "60"


def test_kill_before_any_model_work_still_resumes():
    fam = seed_family("acme-v1")
    env = {**os.environ, "CRASH_AFTER_NODE": "intake", "MODEL_BACKEND": "fixture"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "run", "acme-v1"],
        env=env,
        capture_output=True,
        timeout=300,
    )
    assert proc.returncode in (-9, 137)

    run_id = _latest_run(fam)
    after = runs.resume_run(run_id)
    assert after["status"] == "awaiting_approval"
    assert any(s["node"] == "extract" for s in after["steps"])
