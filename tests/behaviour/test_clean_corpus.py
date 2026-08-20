from __future__ import annotations

from app.domain import approvals, deliverable, runs
from tests.conftest import seed_family


def test_a_clean_corpus_yields_an_honest_empty_report():
    """northwind-v1 satisfies every rule in the pack. The right answer is nothing,
    and it has to be a real answer rather than an absence of output."""
    fam = seed_family("northwind-v1")
    out = runs.start_run(family_id=fam, rule_pack="procurement-baseline")

    report = deliverable.findings(fam, out["run_id"])
    assert report["count"] == 0, [f["rule_id"] + ": " + f["detail"] for f in report["findings"]]
    assert "No findings" in report["message"]

    # And it still produced a real register with real provenance.
    batch = approvals.pending_for_run(out["run_id"])
    approvals.decide_all(batch["batch_id"], "accept")
    runs.resume_run(out["run_id"])
    rows = {r["term_key"]: r for r in deliverable.register(fam, "json")["rows"]}
    assert rows["unit_price"]["value"]["value"] == "30.00"
    assert rows["payment_terms_days"]["value"]["value"] == "30"
    assert rows["governing_law"]["value"]["value"] == "Scotland"


def test_the_same_corpus_under_a_stricter_playbook_does_find_something():
    """Configuration over code: a different rule pack, no code change, a different
    answer. Proves the clean result above is a property of the rules, not a system
    that cannot produce findings."""
    fam = seed_family("northwind-v1")
    out = runs.start_run(family_id=fam, rule_pack="payment-terms-playbook")
    report = deliverable.findings(fam, out["run_id"])
    ids = {f["rule_id"] for f in report["findings"]}
    # 120 days termination notice breaks the 90 day ceiling in this playbook.
    assert "PB-03" in ids, report["findings"]


def test_provenance_points_at_real_text():
    fam = seed_family("northwind-v1")
    runs.start_run(family_id=fam)
    prov = deliverable.provenance(fam, "payment_terms_days")
    assert prov["chain"]
    link = prov["chain"][-1]
    assert link["char_end"] > link["char_start"] >= 0
    assert "30 days" in link["quote"]
    assert link["filename"] == "01-northwind-msa.md"
    assert link["block_id"]
