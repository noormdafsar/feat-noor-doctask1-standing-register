from __future__ import annotations

from app.domain import approvals, deliverable, runs
from tests.conftest import seed_family


def test_rejecting_one_item_commits_the_others():
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)
    batch = approvals.pending_for_run(out["run_id"])

    row_items = [i for i in batch["items"] if i["kind"] == "row_update"]
    assert len(row_items) > 3

    rejected = next(i for i in row_items if i["ref_id"] == "payment_terms_days")
    decisions = [
        {
            "item_id": i["item_id"],
            "decision": "reject" if i["item_id"] == rejected["item_id"] else "accept",
            "reason": "legal wants to renegotiate this one" if i["item_id"] == rejected["item_id"] else None,
        }
        for i in batch["items"]
    ]
    result = approvals.decide(batch["batch_id"], decisions)
    assert result["still_pending"] == 0

    runs.resume_run(out["run_id"])

    published = deliverable.register(fam, "json")
    keys = {r["term_key"] for r in published["rows"]}

    # The one rejection is honoured, and it took nothing else with it.
    assert "payment_terms_days" not in keys
    assert "unit_price" in keys
    assert "liability_cap" in keys
    assert len(keys) >= len(row_items) - 1


def test_findings_can_be_rejected_individually():
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)
    batch = approvals.pending_for_run(out["run_id"])
    findings = [i for i in batch["items"] if i["kind"] == "finding"]
    assert findings, "acme-v1 is a deliberately non-compliant corpus"

    first = findings[0]
    approvals.decide(
        batch["batch_id"],
        [
            {
                "item_id": i["item_id"],
                "decision": "reject" if i["item_id"] == first["item_id"] else "accept",
                "reason": "false positive" if i["item_id"] == first["item_id"] else None,
            }
            for i in batch["items"]
        ],
    )
    runs.resume_run(out["run_id"])

    after = deliverable.findings(fam, out["run_id"])
    by_id = {f["id"]: f for f in after["findings"]}
    assert by_id[first["ref_id"]]["status"] == "rejected"
    others = [f for f in after["findings"] if f["id"] != first["ref_id"]]
    assert others and all(f["status"] == "accepted" for f in others)
