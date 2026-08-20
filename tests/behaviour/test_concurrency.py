from __future__ import annotations

import threading
import time

from sqlalchemy import func, select

from app.db.models import RegisterRow
from app.db.session import session_scope
from app.domain import approvals, deliverable, runs
from tests.conftest import _truncate, seed_family


def _serial_hashes() -> dict[str, str]:
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)
    batch = approvals.pending_for_run(out["run_id"])
    approvals.decide_all(batch["batch_id"], "accept")
    runs.resume_run(out["run_id"])
    return {
        r["term_key"]: r["content_hash"] for r in deliverable.register(fam, "json")["rows"]
    }


def test_same_family_twice_equals_running_them_one_after_the_other():
    baseline = _serial_hashes()

    _truncate()
    fam = seed_family("acme-v1")

    results: list[dict] = []
    errors: list[BaseException] = []

    def go():
        try:
            results.append(runs.start_run(family_id=fam))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)

    assert not errors, f"concurrent runs raised: {errors}"
    assert len(results) == 2
    assert results[0]["run_id"] != results[1]["run_id"]

    # Two runs stayed two runs: two separate review batches, neither corrupted.
    for out in results:
        batch = approvals.pending_for_run(out["run_id"])
        assert batch["batch_id"]
        approvals.decide_all(batch["batch_id"], "accept")
        runs.resume_run(out["run_id"])

    with session_scope() as s:
        dupes = s.execute(
            select(RegisterRow.term_key, func.count())
            .where(RegisterRow.family_id == fam)
            .group_by(RegisterRow.term_key)
            .having(func.count() > 1)
        ).all()
    assert dupes == [], f"concurrent commits duplicated register rows: {dupes}"

    concurrent = {
        r["term_key"]: r["content_hash"] for r in deliverable.register(fam, "json")["rows"]
    }
    assert concurrent == baseline, "concurrent execution changed the register content"


def test_two_families_genuinely_overlap():
    """The complement: prove the lock is per family, not one global mutex."""
    fam_a = seed_family("acme-v1")
    fam_b = seed_family("northwind-v1")

    spans: dict[str, tuple[float, float]] = {}

    def go(name: str, fam: str):
        t0 = time.perf_counter()
        runs.start_run(family_id=fam)
        spans[name] = (t0, time.perf_counter())

    ta = threading.Thread(target=go, args=("a", fam_a))
    tb = threading.Thread(target=go, args=("b", fam_b))
    ta.start()
    tb.start()
    ta.join(timeout=300)
    tb.join(timeout=300)

    assert set(spans) == {"a", "b"}
    (a0, a1), (b0, b1) = spans["a"], spans["b"]
    overlap = min(a1, b1) - max(a0, b0)
    assert overlap > 0, "two different families serialised; the lock is too coarse"
