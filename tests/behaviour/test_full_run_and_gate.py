from __future__ import annotations

from app.domain import approvals, deliverable, runs
from tests.conftest import seed_family


def test_run_stops_at_the_gate_and_publishes_nothing_first():
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam, mode="full", rule_pack="procurement-baseline")

    assert out["status"] == "awaiting_approval"
    assert out["approval_batch_id"]

    # Behaviour 1: visible stages, and at least one decision that could have
    # changed the path.
    nodes_seen = {s["node"] for s in out["steps"]}
    assert {"intake", "classify", "segment", "extract", "verify_spans", "link",
            "reconcile", "examine", "gate"} <= nodes_seen
    assert any(s["decision"].startswith("classified:") for s in out["steps"])

    # Nothing is published before a human decides.
    published = deliverable.register(fam, "json")
    assert published["rows"] == []

    batch = approvals.pending_for_run(out["run_id"])
    assert batch["items"], "the gate must have something to review"
    assert all(i["decision"] == "pending" for i in batch["items"])


def test_resume_refuses_while_any_item_is_undecided():
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)
    batch = approvals.pending_for_run(out["run_id"])

    # Decide all but one.
    approvals.decide(
        batch["batch_id"],
        [{"item_id": i["item_id"], "decision": "accept"} for i in batch["items"][:-1]],
    )
    try:
        runs.resume_run(out["run_id"])
    except runs.RunNotAwaitingApproval as exc:
        assert "still undecided" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("resume must refuse while an item is undecided")


def test_register_publishes_after_approval():
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)
    batch = approvals.pending_for_run(out["run_id"])
    approvals.decide_all(batch["batch_id"], "accept")
    final = runs.resume_run(out["run_id"])

    assert final["status"] == "complete"
    published = deliverable.register(fam, "json")
    rows = {r["term_key"]: r for r in published["rows"]}

    # The amendment chain resolved: the current unit price is amendment 2's, not
    # the master agreement's or the first amendment's.
    assert rows["unit_price"]["value"]["value"] == "48.50"
    assert rows["payment_terms_days"]["value"]["value"] == "60"
    assert rows["renewal_notice_days"]["value"]["value"] == "30"
    assert rows["initial_term_months"]["value"]["value"] == "24"
