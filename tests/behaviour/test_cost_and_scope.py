from __future__ import annotations

import pytest

from app.db.session import session_scope
from app.domain import runs
from app.retrieval.search import hybrid_search
from tests.conftest import seed_family


def test_cost_rollup_is_arithmetic_not_decoration():
    fam = seed_family("acme-v1")
    out = runs.start_run(family_id=fam)
    report = runs.cost(out["run_id"])

    assert report["by_stage"]
    assert report["total"]["tokens_in"] == sum(s["tokens_in"] for s in report["by_stage"])
    assert report["total"]["ms"] == sum(s["ms"] for s in report["by_stage"])

    from_steps = sum(s["tokens_in"] for s in out["steps"])
    assert report["total"]["tokens_in"] == from_steps

    nodes = {s["node"] for s in report["by_stage"]}
    assert {"classify", "extract", "examine"} <= nodes
    assert "fixture" in report["note"]


def test_retrieval_cannot_cross_families():
    acme = seed_family("acme-v1")
    northwind = seed_family("northwind-v1")
    runs.start_run(family_id=acme)
    runs.start_run(family_id=northwind)

    with session_scope() as s:
        hits = hybrid_search(s, family_id=northwind, query="Vertex Cloud Systems unit price", limit=8)
        assert hits, "the query should still return something from its own family"
        for h in hits:
            assert "Vertex Cloud Systems" not in h.text
            assert "Acme Industrial" not in h.text


def test_unscoped_retrieval_is_refused():
    with session_scope() as s:
        with pytest.raises(ValueError, match="family_id"):
            hybrid_search(s, family_id="", query="payment terms")
