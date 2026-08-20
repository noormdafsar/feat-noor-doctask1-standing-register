from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.retrieval.embed import embed

RRF_K = 60


_WORD = re.compile(r"[A-Za-z0-9]+")


def _or_tsquery(query: str) -> str:
    """Sanitised OR query. Words only, so nothing user-supplied reaches tsquery syntax."""
    words = [w.lower() for w in _WORD.findall(query) if len(w) > 2]
    return " | ".join(dict.fromkeys(words))


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    block_ids: list[str]
    text: str
    score: float


def hybrid_search(s: Session, *, family_id: str, query: str, limit: int = 6) -> list[Hit]:
    """Dense + lexical, fused by reciprocal rank.

    Every query is scoped by family_id. A rule check on one client's pile can
    never retrieve from another's, and that is enforced here rather than trusted
    to the caller -- see tests/behaviour/test_retrieval_scope.py.
    """
    if not family_id:
        raise ValueError("hybrid_search requires a family_id; unscoped retrieval is not allowed")

    vec = embed(query)
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

    dense = s.execute(
        sql(
            """
            SELECT id, document_id, block_ids, text
            FROM chunk
            WHERE family_id = :fam
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :k
            """
        ),
        {"fam": family_id, "vec": vec_literal, "k": limit * 3},
    ).all()

    # OR semantics, deliberately. websearch_to_tsquery ANDs its terms, which on a
    # query like "monthly availability uptime percentage" means one absent word
    # returns nothing at all -- the opposite of what a recall stage should do.
    ts_query = _or_tsquery(query)
    lexical = (
        s.execute(
            sql(
                """
                SELECT id, document_id, block_ids, text
                FROM chunk
                WHERE family_id = :fam
                  AND tsv @@ to_tsquery('english', :q)
                ORDER BY ts_rank_cd(tsv, to_tsquery('english', :q)) DESC
                LIMIT :k
                """
            ),
            {"fam": family_id, "q": ts_query, "k": limit * 3},
        ).all()
        if ts_query
        else []
    )

    scores: dict[str, float] = {}
    rows: dict[str, tuple] = {}
    for rank, r in enumerate(dense):
        scores[r.id] = scores.get(r.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        rows[r.id] = r
    for rank, r in enumerate(lexical):
        scores[r.id] = scores.get(r.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        rows[r.id] = r

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [
        Hit(
            chunk_id=cid,
            document_id=rows[cid].document_id,
            block_ids=list(rows[cid].block_ids),
            text=rows[cid].text,
            score=score,
        )
        for cid, score in ordered
    ]
