from __future__ import annotations

from app.domain import approvals, deliverable, families, runs
from tests.conftest import arrival, seed_family


def _hashes(fam: str) -> dict[str, str]:
    return {r["term_key"]: r["content_hash"] for r in deliverable.register(fam, "json")["rows"]}


def test_an_arrival_changes_what_it_touches_and_nothing_else():
    fam = seed_family("acme-v1")
    first = runs.start_run(family_id=fam)
    batch = approvals.pending_for_run(first["run_id"])
    approvals.decide_all(batch["batch_id"], "accept")
    runs.resume_run(first["run_id"])

    before = _hashes(fam)
    assert before

    # New paper arrives: amendment 3 changes the unit price and nothing else.
    path = arrival("08-amendment-3.md")
    result = families.ingest_bytes(fam, path.name, path.read_bytes())
    assert result["status"] == "accepted"

    second = runs.start_run(
        family_id=fam, mode="incremental", trigger_document_id=result["document_id"]
    )
    stats = second["stats"]

    # The claim is measured, not asserted.
    assert stats["rows_changed"] == 1, stats
    assert stats["changed_terms"] == ["unit_price"], stats
    assert stats["rows_byte_identical"] == stats["rows_total"] - 1

    batch = approvals.pending_for_run(second["run_id"])
    row_items = [i for i in batch["items"] if i["kind"] == "row_update"]
    assert [i["ref_id"] for i in row_items] == ["unit_price"], (
        "an update must only ask a human about what actually changed"
    )
    approvals.decide_all(batch["batch_id"], "accept")
    runs.resume_run(second["run_id"])

    after = _hashes(fam)
    changed = [k for k in before if before[k] != after.get(k)]
    assert changed == ["unit_price"]
    for k, h in before.items():
        if k != "unit_price":
            assert after[k] == h, f"{k} was rewritten without changing"

    assert after["unit_price"] != before["unit_price"]
    rows = {r["term_key"]: r for r in deliverable.register(fam, "json")["rows"]}
    assert rows["unit_price"]["value"]["value"] == "51.00"
    assert rows["unit_price"]["version"] == 2


def test_changelog_says_what_changed_and_why():
    fam = seed_family("acme-v1")
    first = runs.start_run(family_id=fam)
    b = approvals.pending_for_run(first["run_id"])
    approvals.decide_all(b["batch_id"], "accept")
    runs.resume_run(first["run_id"])

    path = arrival("08-amendment-3.md")
    result = families.ingest_bytes(fam, path.name, path.read_bytes())
    second = runs.start_run(
        family_id=fam, mode="incremental", trigger_document_id=result["document_id"]
    )
    b = approvals.pending_for_run(second["run_id"])
    approvals.decide_all(b["batch_id"], "accept")
    runs.resume_run(second["run_id"])

    log = deliverable.changelog(fam)
    latest = log["entries"][0]
    assert latest["term_key"] == "unit_price"
    assert latest["version"] == 2
    assert latest["because_of"] == "08-amendment-3.md"
    assert latest["run_id"] == second["run_id"]
    assert latest["at"]


def test_redropping_the_same_file_is_a_no_op():
    fam = seed_family("acme-v1")
    path = arrival("08-amendment-3.md")
    first = families.ingest_bytes(fam, path.name, path.read_bytes())
    second = families.ingest_bytes(fam, path.name, path.read_bytes())
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["document_id"] == first["document_id"]
