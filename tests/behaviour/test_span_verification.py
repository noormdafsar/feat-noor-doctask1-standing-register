from __future__ import annotations

from sqlalchemy import select

from app.db.models import Fact, Gap
from app.db.session import session_scope
from app.domain import approvals, deliverable, runs
from tests.conftest import seed_family


def test_unverifiable_quote_is_dropped_not_published():
    """The fixture claims a governing law of Singapore and cites text that does not
    exist in the document. The system must refuse it and say so."""
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)

    with session_scope() as s:
        surviving = s.execute(
            select(Fact).where(Fact.family_id == fam, Fact.key == "governing_law")
        ).scalars().all()
        assert surviving == [], "a fact with an unverifiable quote must not survive"

        gaps = s.execute(
            select(Gap).where(Gap.family_id == fam, Gap.term_key == "governing_law")
        ).scalars().all()
        assert gaps, "dropping a fact must leave a recorded gap"
        assert "verbatim" in gaps[0].reason

    batch = approvals.pending_for_run(out["run_id"])
    approvals.decide_all(batch["batch_id"], "accept")
    runs.resume_run(out["run_id"])

    published = deliverable.register(fam, "json")
    rows = {r["term_key"]: r for r in published["rows"]}
    # Absent, and honestly labelled -- never a plausible-looking guess.
    assert "governing_law" not in rows or rows["governing_law"]["status"] == "absent"
    assert "governing_law" in published["unsupported_terms"]

    md = deliverable.register(fam, "markdown")
    assert "Not stated in sources" in md


def test_every_surviving_fact_has_a_real_span():
    fam = seed_family("acme-v1")
    runs.start_run(family_id=fam)
    with session_scope() as s:
        facts = s.execute(select(Fact).where(Fact.family_id == fam)).scalars().all()
        assert facts
        for f in facts:
            assert f.status == "verified"
            assert f.spans, f"{f.key} survived without a span"
            for sp in f.spans:
                assert sp.char_start >= 0 and sp.char_end > sp.char_start
                assert sp.quote
