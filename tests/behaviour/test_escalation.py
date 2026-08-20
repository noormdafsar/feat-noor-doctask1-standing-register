from __future__ import annotations

from app.domain import approvals, deliverable, runs
from tests.conftest import held_back, seed_family

MYSTERY = "10-mystery-note.md"


def test_unclassifiable_document_is_retried_then_escalated_without_stopping_the_run():
    fam = seed_family("acme-v1", extra=[held_back("_edge", MYSTERY)])
    out = runs.start_run(family_id=fam)

    steps = out["steps"]
    # Behaviour 1: the route actually changed. Cheap tier first, then the deep tier.
    assert any(s["node"] == "classify" and "below-floor" in s["decision"] for s in steps)
    assert any(s["node"] == "reclassify" for s in steps), "the retry edge was never taken"
    assert any(s["node"] == "escalate_unknown" for s in steps), "escalation never happened"

    reclassify_step = next(s for s in steps if s["node"] == "reclassify" and s["model"])
    assert reclassify_step["model"] != "", "the retry must be a different, deeper tier"

    batch = approvals.pending_for_run(out["run_id"])
    escalations = [i for i in batch["items"] if i["kind"] == "escalation"]
    assert escalations, "an escalation must reach the human queue"
    assert any(MYSTERY in i["title"] for i in escalations)

    # The run did not stop: the rest of the register was still produced.
    row_items = [i for i in batch["items"] if i["kind"] == "row_update"]
    assert len(row_items) > 5, "escalation blocked the row, not the run"

    approvals.decide_all(batch["batch_id"], "accept")
    runs.resume_run(out["run_id"])
    rows = {r["term_key"]: r for r in deliverable.register(fam, "json")["rows"]}
    assert rows["unit_price"]["value"]["value"] == "48.50"


def test_a_rejected_format_is_named_not_swallowed():
    from app.domain import families

    fam = seed_family("acme-v1")
    result = families.ingest_bytes(fam, "board-deck.pptx", b"not really a deck")
    assert result["status"] == "rejected"
    assert ".pptx" in result["reason"]
    assert "declared format set" in result["reason"]
