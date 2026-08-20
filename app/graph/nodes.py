from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import delete, func as sa_func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    ApprovalBatch,
    ApprovalItem,
    Block,
    Chunk,
    Conflict,
    Document,
    Fact,
    FactSpan,
    Family,
    Finding,
    Gap,
    RegisterRow,
    RegisterRowVersion,
    Run,
)
from app.db.session import advisory_lock, session_scope
from app.extract.schemas import CLASSIFY_SCHEMA, FACT_SCHEMA, INVOICE_SCHEMA
from app.graph.idempotency import cached, input_sha, remember
from app.graph.state import RunState
from app.ingest.segment import chunk_blocks, find_span, segment
from app.llm.call import SOURCE_RULE, call_model, log_decision
from app.llm.provider import ModelRequest, build_provider
from app.render.register import ProjectedRow, project
from app.retrieval.embed import embed
from app.rules.examine import examine
from app.rules.loader import load_pack
from app.security.injection import wrap_source

# ---------------------------------------------------------------------------
# Every node opens its own session and commits before returning. That is what
# makes behaviour 2 work: the checkpointer records "node done" only after the
# domain writes are durable, so a kill can never land between them.
# ---------------------------------------------------------------------------


def _provider():
    return build_provider()


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------- intake
def intake(state: RunState) -> dict:
    with session_scope() as s:
        sha = input_sha("intake", {"run": state["run_id"], "mode": state["mode"]})
        hit = cached(s, state["run_id"], "intake", sha)
        if hit:
            return hit

        docs = (
            s.execute(
                select(Document)
                .where(Document.family_id == state["family_id"])
                .order_by(Document.ingested_at)
            )
            .scalars()
            .all()
        )
        doc_ids = [d.id for d in docs]
        log_decision(
            s,
            run_id=state["run_id"],
            node="intake",
            decision=f"scope:{state['mode']}",
            detail={"documents": len(doc_ids), "trigger": state.get("trigger_document_id")},
        )
        out = {"doc_ids": doc_ids, "escalations": [], "repair_attempts": 0}
        remember(s, state["run_id"], "intake", sha, out)
        return out


# ------------------------------------------------------------------- classify
def _classify_pass(state: RunState, node: str, model: str) -> dict:
    provider = _provider()
    low: list[str] = []
    with session_scope() as s:
        sha = input_sha(node, {"run": state["run_id"], "docs": sorted(state["doc_ids"])})
        hit = cached(s, state["run_id"], node, sha)
        if hit:
            return hit

        targets = (
            s.execute(select(Document).where(Document.id.in_(state["doc_ids"]))).scalars().all()
        )
        for d in targets:
            if node == "classify" and d.doc_type != "unclassified":
                continue
            if node == "reclassify" and d.id not in state.get("low_confidence_docs", []):
                continue

            req = ModelRequest(
                task="classify",
                model=model,
                system=(
                    f"{SOURCE_RULE}\n\nClassify one commercial document. Answer only with "
                    "the schema. If it is genuinely none of the listed types, answer "
                    "'unknown' rather than guessing the closest one."
                ),
                user=wrap_source(d.id, d.filename, d.raw_text[:6000]),
                schema=CLASSIFY_SCHEMA,
                meta={"key": d.filename},
            )
            resp = call_model(
                s, run_id=state["run_id"], node=node, provider=provider, req=req
            )
            data = resp.data
            conf = float(data.get("confidence", 0.0))
            d.doc_type = data.get("doc_type", "unknown")
            d.doc_type_confidence = conf
            d.doc_type_tier = model
            d.doc_date = _parse_date(data.get("document_date"))
            d.meta = {**(d.meta or {}), "amends_reference": data.get("amends_reference", "")}

            unresolved = conf < settings.classify_confidence_floor or d.doc_type == "unknown"
            log_decision(
                s,
                run_id=state["run_id"],
                node=node,
                decision=f"classified:{d.doc_type}"
                + (":below-floor" if unresolved else ":accepted"),
                confidence=conf,
                detail={"document": d.filename, "tier": model},
            )
            if unresolved:
                low.append(d.id)

        out = {"low_confidence_docs": low}
        remember(s, state["run_id"], node, sha, out)
        return out


def classify(state: RunState) -> dict:
    return _classify_pass(state, "classify", settings.model_fast)


def reclassify(state: RunState) -> dict:
    """The retry that behaviour 1 asks for: same work, more expensive model."""
    return _classify_pass(state, "reclassify", settings.model_deep)


def escalate_unknown(state: RunState) -> dict:
    """Still unresolved after two tiers. Block the document, keep the run going."""
    with session_scope() as s:
        esc = list(state.get("escalations", []))
        for doc_id in state.get("low_confidence_docs", []):
            d = s.get(Document, doc_id)
            if d is None:
                continue
            d.blocked_reason = (
                f"could not be classified above the {settings.classify_confidence_floor} "
                f"confidence floor after a retry at {settings.model_deep}"
            )
            esc.append(
                {"kind": "unclassified_document", "document_id": d.id, "filename": d.filename}
            )
            log_decision(
                s,
                run_id=state["run_id"],
                node="escalate_unknown",
                decision="escalated:unclassified",
                detail={"document": d.filename},
            )
        return {"escalations": esc}


def route_after_classify(state: RunState) -> str:
    return "reclassify" if state.get("low_confidence_docs") else "segment"


def route_after_reclassify(state: RunState) -> str:
    return "escalate_unknown" if state.get("low_confidence_docs") else "segment"


# -------------------------------------------------------------------- segment
def segment_node(state: RunState) -> dict:
    with session_scope() as s:
        for doc_id in state["doc_ids"]:
            d = s.get(Document, doc_id)
            if d is None:
                continue
            already = s.execute(
                select(Block.id).where(Block.document_id == d.id).limit(1)
            ).scalar_one_or_none()
            if already:
                continue  # documents are immutable, so blocks are computed once

            blocks = segment(d.raw_text)
            block_rows = [
                Block(
                    document_id=d.id,
                    page=b.page,
                    idx=b.idx,
                    char_start=b.char_start,
                    char_end=b.char_end,
                    text=b.text,
                )
                for b in blocks
            ]
            s.add_all(block_rows)
            s.flush()
            by_idx = {b.idx: block_rows[i].id for i, b in enumerate(blocks)}

            for ids, text in chunk_blocks(blocks):
                s.add(
                    Chunk(
                        document_id=d.id,
                        family_id=d.family_id,
                        block_ids=[by_idx[i] for i in ids],
                        text=text,
                        embedding=embed(text),
                    )
                )
            s.flush()
        # tsvector is maintained in SQL so the FTS index sees exactly what it ranks
        s.execute(
            Chunk.__table__.update()
            .where(Chunk.family_id == state["family_id"], Chunk.tsv.is_(None))
            .values(tsv=sa_func.to_tsvector("english", Chunk.text))
        )
        log_decision(
            s,
            run_id=state["run_id"],
            node="segment",
            decision="segmented",
            detail={"documents": len(state["doc_ids"])},
        )
    return {}


# -------------------------------------------------------------------- extract
def _extract_pass(state: RunState, node: str, model: str) -> dict:
    provider = _provider()
    empty: list[str] = []
    with session_scope() as s:
        # Two runs on one family must not interleave their extraction, or one can
        # delete facts the other is halfway through verifying.
        advisory_lock(s, state["family_id"])
        targets = (
            s.execute(
                select(Document).where(
                    Document.id.in_(state["doc_ids"]), Document.blocked_reason.is_(None)
                )
            )
            .scalars()
            .all()
        )
        for d in targets:
            if node == "repair_extract" and d.id not in state.get("empty_extract_docs", []):
                continue
            if d.doc_type in ("invoice", "credit_note"):
                got = _extract_invoice(s, state, d, provider, model, node)
            else:
                got = _extract_terms(s, state, d, provider, model, node)
            if got == 0:
                empty.append(d.id)
        return {
            "empty_extract_docs": empty,
            "repair_attempts": state.get("repair_attempts", 0) + (1 if node == "repair_extract" else 0),
        }


def _extract_terms(s: Session, state: RunState, d: Document, provider, model: str, node: str) -> int:
    settled = s.execute(
        select(sa_func.count())
        .select_from(Fact)
        .where(Fact.document_id == d.id, Fact.status == "verified")
    ).scalar_one()
    if settled and node == "extract":
        # This is what makes an update cost like an update. Documents are immutable,
        # so a document that already has verified facts is never re-read: an
        # incremental run pays for the one document that arrived, not the whole pile.
        log_decision(
            s,
            run_id=state["run_id"],
            node=node,
            decision="extract:already-settled",
            detail={"document": d.filename, "facts": settled},
        )
        return settled

    # Re-entrant by construction: facts for this document are cleared before new
    # ones are written, so a replay after a kill cannot duplicate them, and two
    # runs cannot stack two copies of the same fact into the chain.
    s.execute(delete(Fact).where(Fact.document_id == d.id))

    req = ModelRequest(
        task="extract_terms",
        model=model,
        system=(
            f"{SOURCE_RULE}\n\n"
            "Extract commercial terms. Every fact needs a quote copied EXACTLY from "
            "the source; the quote is checked character by character and a fact whose "
            "quote does not appear verbatim is discarded. If a term is not stated, do "
            "not include it. Never infer, never round, never normalise a value that is "
            "not written down."
        ),
        user=wrap_source(d.id, d.filename, d.raw_text[:20000]),
        schema=FACT_SCHEMA,
        meta={"key": d.filename},
    )
    resp = call_model(s, run_id=state["run_id"], node=node, provider=provider, req=req)
    facts = resp.data.get("facts", []) or []
    for f in facts:
        fact = Fact(
            document_id=d.id,
            family_id=d.family_id,
            key=f.get("key", ""),
            value={"value": f.get("value", ""), "unit": f.get("unit", "")},
            effective_from=_parse_date(f.get("effective_from")) or d.doc_date,
            confidence=float(f.get("confidence", 0.5)),
            status="unverified",
            run_id=state["run_id"],
        )
        s.add(fact)
        s.flush()
        s.add(
            FactSpan(
                fact_id=fact.id,
                document_id=d.id,
                block_id="",
                char_start=-1,
                char_end=-1,
                quote=f.get("quote", ""),
            )
        )
    s.flush()
    log_decision(
        s,
        run_id=state["run_id"],
        node=node,
        decision="extracted" if facts else "extracted:empty",
        detail={"document": d.filename, "facts": len(facts)},
    )
    return len(facts)


def _extract_invoice(s: Session, state: RunState, d: Document, provider, model: str, node: str) -> int:
    settled = (d.meta or {}).get("invoice_lines")
    if settled and node == "extract":
        log_decision(
            s,
            run_id=state["run_id"],
            node=node,
            decision="extract:already-settled",
            detail={"document": d.filename, "lines": len(settled)},
        )
        return len(settled)

    req = ModelRequest(
        task="extract_invoice",
        model=model,
        system=(
            f"{SOURCE_RULE}\n\nExtract invoice lines. Every line needs a quote copied "
            "exactly from the document."
        ),
        user=wrap_source(d.id, d.filename, d.raw_text[:20000]),
        schema=INVOICE_SCHEMA,
        meta={"key": d.filename},
    )
    resp = call_model(s, run_id=state["run_id"], node=node, provider=provider, req=req)
    lines = resp.data.get("lines", []) or []
    kept = []
    for ln in lines:
        span = find_span(d.raw_text, ln.get("quote", ""))
        if span is None:
            # Same rule as facts: an unverifiable line is dropped, not published.
            s.add(
                Gap(
                    family_id=d.family_id,
                    term_key="invoice_line",
                    reason="quote did not appear verbatim in the source",
                    document_id=d.id,
                    detail={"quote": ln.get("quote", "")[:200]},
                    run_id=state["run_id"],
                )
            )
            continue
        kept.append({**ln, "char_start": span[0], "char_end": span[1], "block_id": ""})
    d.meta = {**(d.meta or {}), "invoice_lines": kept}
    log_decision(
        s,
        run_id=state["run_id"],
        node=node,
        decision="extracted:invoice",
        detail={"document": d.filename, "lines": len(kept), "dropped": len(lines) - len(kept)},
    )
    return len(kept)


def extract(state: RunState) -> dict:
    return _extract_pass(state, "extract", settings.model_fast)


def repair_extract(state: RunState) -> dict:
    return _extract_pass(state, "repair_extract", settings.model_deep)


def route_after_extract(state: RunState) -> str:
    if state.get("empty_extract_docs") and state.get("repair_attempts", 0) < 2:
        return "repair_extract"
    return "verify_spans"


# --------------------------------------------------------------- verify_spans
def verify_spans(state: RunState) -> dict:
    """The line that stops the system bluffing.

    A quote is re-matched against the document text it claims to come from. No
    fuzzy matching: if it is not there, the fact is deleted and a gap is recorded.
    """
    dropped = 0
    kept = 0
    with session_scope() as s:
        advisory_lock(s, state["family_id"])
        facts = (
            s.execute(select(Fact).where(Fact.run_id == state["run_id"])).scalars().all()
        )
        for f in facts:
            d = s.get(Document, f.document_id)
            span_row = s.execute(
                select(FactSpan).where(FactSpan.fact_id == f.id)
            ).scalar_one_or_none()
            if d is None or span_row is None:
                s.delete(f)
                dropped += 1
                continue
            located = find_span(d.raw_text, span_row.quote)
            if located is None:
                s.add(
                    Gap(
                        family_id=f.family_id,
                        term_key=f.key,
                        reason="quote did not appear verbatim in the cited document",
                        document_id=d.id,
                        detail={"quote": span_row.quote[:200], "claimed_value": f.value},
                        run_id=state["run_id"],
                    )
                )
                s.delete(f)
                dropped += 1
                continue

            block = s.execute(
                select(Block)
                .where(
                    Block.document_id == d.id,
                    Block.char_start <= located[0],
                    Block.char_end >= located[0],
                )
                .limit(1)
            ).scalar_one_or_none()
            span_row.char_start, span_row.char_end = located
            span_row.quote = d.raw_text[located[0] : located[1]]
            span_row.block_id = block.id if block else ""
            f.status = "verified"
            kept += 1

        log_decision(
            s,
            run_id=state["run_id"],
            node="verify_spans",
            decision="verified",
            detail={"kept": kept, "dropped_unverifiable": dropped},
        )
    return {"stats": {**state.get("stats", {}), "facts_kept": kept, "facts_dropped": dropped}}


# ------------------------------------------------------------------------ link
def link(state: RunState) -> dict:
    """Hang each amendment off the agreement it amends.

    Ambiguity escalates rather than picking the nearest match, because a wrongly
    parented amendment silently corrupts every term it touches.
    """
    esc = list(state.get("escalations", []))
    with session_scope() as s:
        docs = (
            s.execute(select(Document).where(Document.id.in_(state["doc_ids"]))).scalars().all()
        )
        parents = [d for d in docs if d.doc_type in ("master_agreement", "order_form", "sow")]
        for d in docs:
            if d.doc_type != "amendment":
                continue
            ref = (d.meta or {}).get("amends_reference", "").strip().lower()
            candidates = parents
            if ref:
                narrowed = [
                    p
                    for p in parents
                    if ref in p.filename.lower() or ref in p.raw_text[:400].lower()
                ]
                if narrowed:
                    candidates = narrowed
            if len(candidates) > 1:
                # An order form quotes the master agreement's reference too, so a
                # reference match alone is not enough to disambiguate. An amendment
                # amends the agreement, not the paperwork hanging off it.
                masters = [p for p in candidates if p.doc_type == "master_agreement"]
                if len(masters) == 1:
                    candidates = masters
            if len(candidates) == 1:
                d.parent_document_id = candidates[0].id
                log_decision(
                    s,
                    run_id=state["run_id"],
                    node="link",
                    decision="linked",
                    detail={"amendment": d.filename, "parent": candidates[0].filename},
                )
            else:
                esc.append(
                    {
                        "kind": "ambiguous_parent",
                        "document_id": d.id,
                        "filename": d.filename,
                        "candidates": [c.filename for c in candidates],
                    }
                )
                log_decision(
                    s,
                    run_id=state["run_id"],
                    node="link",
                    decision="escalated:ambiguous-parent",
                    detail={"amendment": d.filename, "candidates": len(candidates)},
                )
    return {"escalations": esc}


# ------------------------------------------------------------------- reconcile
def reconcile(state: RunState) -> dict:
    """Project the register and work out what actually changed.

    The lock is held for exactly this critical section, which is what makes two
    concurrent runs on one family serialise instead of interleaving.
    """
    with session_scope() as s:
        advisory_lock(s, state["family_id"])

        rows = project(s, state["family_id"])
        existing = {
            r.term_key: r
            for r in s.execute(
                select(RegisterRow).where(RegisterRow.family_id == state["family_id"])
            ).scalars()
        }

        changed, unchanged = [], []
        for row in rows:
            prior = existing.get(row.term_key)
            if prior is not None and prior.content_hash == row.content_hash():
                unchanged.append(row.term_key)
            else:
                changed.append(row.term_key)

        s.execute(
            delete(Conflict).where(
                Conflict.family_id == state["family_id"], Conflict.status == "open"
            )
        )
        for row in rows:
            for c in row.conflicts:
                s.add(
                    Conflict(
                        family_id=state["family_id"],
                        term_key=row.term_key,
                        kind=c["kind"],
                        candidate_fact_ids=c["fact_ids"],
                        detail=c,
                        run_id=state["run_id"],
                    )
                )
            if row.status == "absent":
                s.add(
                    Gap(
                        family_id=state["family_id"],
                        term_key=row.term_key,
                        reason="no verified source states this term",
                        run_id=state["run_id"],
                    )
                )

        log_decision(
            s,
            run_id=state["run_id"],
            node="reconcile",
            decision="projected",
            detail={
                "rows": len(rows),
                "changed": len(changed),
                "byte_identical": len(unchanged),
            },
        )
        run = s.get(Run, state["run_id"])
        if run is not None:
            run.stats = {
                **(run.stats or {}),
                "rows_total": len(rows),
                "rows_changed": len(changed),
                "rows_byte_identical": len(unchanged),
                "changed_terms": changed,
            }
    return {
        "stats": {
            **state.get("stats", {}),
            "rows_total": len(rows),
            "rows_changed": len(changed),
            "rows_byte_identical": len(unchanged),
            "changed_terms": changed,
        }
    }


# --------------------------------------------------------------------- examine
def examine_node(state: RunState) -> dict:
    provider = _provider()
    with session_scope() as s:
        s.execute(delete(Finding).where(Finding.run_id == state["run_id"]))
        rows = project(s, state["family_id"])
        pack = load_pack(state["rule_pack"])
        found = examine(
            s,
            run_id=state["run_id"],
            family_id=state["family_id"],
            rows=rows,
            pack=pack,
            provider=provider,
        )
        log_decision(
            s,
            run_id=state["run_id"],
            node="examine",
            decision="examined",
            detail={"rules": len(pack.rules), "findings": len(found)},
        )
        count = len(found)
    return {"stats": {**state.get("stats", {}), "findings": count}}


# ------------------------------------------------------------------------ gate
def build_batch(state: RunState) -> dict:
    """Assemble the review batch. Idempotent: replay reuses the same batch."""
    with session_scope() as s:
        batch = s.execute(
            select(ApprovalBatch).where(ApprovalBatch.run_id == state["run_id"])
        ).scalar_one_or_none()
        if batch is None:
            batch = ApprovalBatch(run_id=state["run_id"], family_id=state["family_id"])
            s.add(batch)
            s.flush()
        s.execute(delete(ApprovalItem).where(ApprovalItem.batch_id == batch.id))

        rows = project(s, state["family_id"])
        existing = {
            r.term_key: r
            for r in s.execute(
                select(RegisterRow).where(RegisterRow.family_id == state["family_id"])
            ).scalars()
        }
        for row in rows:
            prior = existing.get(row.term_key)
            if prior is not None and prior.content_hash == row.content_hash():
                continue
            s.add(
                ApprovalItem(
                    batch_id=batch.id,
                    kind="row_update",
                    ref_id=row.term_key,
                    title=f"{row.label}: {row.value.get('display')}",
                    payload={
                        "term_key": row.term_key,
                        "before": prior.current_value if prior else None,
                        "after": row.value,
                        "status": row.status,
                        "chain": [c.as_dict() for c in row.chain],
                        "content_hash": row.content_hash(),
                    },
                )
            )

        for c in s.execute(
            select(Conflict).where(
                Conflict.family_id == state["family_id"], Conflict.status == "open"
            )
        ).scalars():
            s.add(
                ApprovalItem(
                    batch_id=batch.id,
                    kind="conflict",
                    ref_id=c.id,
                    title=f"Contradiction on {c.term_key}",
                    payload=c.detail,
                )
            )

        for f in s.execute(
            select(Finding).where(Finding.run_id == state["run_id"])
        ).scalars():
            s.add(
                ApprovalItem(
                    batch_id=batch.id,
                    kind="finding",
                    ref_id=f.id,
                    title=f"[{f.severity}] {f.title}",
                    payload={
                        "rule_id": f.rule_id,
                        "kind": f.kind,
                        "stage": f.stage,
                        "detail": f.detail,
                        "evidence": f.evidence,
                    },
                )
            )

        for e in state.get("escalations", []):
            s.add(
                ApprovalItem(
                    batch_id=batch.id,
                    kind="escalation",
                    ref_id=e.get("document_id", ""),
                    title=f"Escalation: {e.get('kind')} ({e.get('filename', '')})",
                    payload=e,
                )
            )
        s.flush()
        n = s.execute(
            select(ApprovalItem).where(ApprovalItem.batch_id == batch.id)
        ).scalars().all()
        log_decision(
            s,
            run_id=state["run_id"],
            node="gate",
            decision="awaiting-human",
            detail={"batch_id": batch.id, "items": len(n)},
        )
        run = s.get(Run, state["run_id"])
        if run is not None:
            run.status = "awaiting_approval"
        return {"batch_id": batch.id}


def gate(state: RunState) -> dict:
    """The human gate, as a graph interrupt.

    Approval resumes this same run from this same checkpoint. There is no
    auto-approve path and no flag that skips it: whoever drives the system,
    person or program, makes this call explicitly.
    """
    from langgraph.types import interrupt

    decisions = interrupt(
        {
            "batch_id": state.get("batch_id"),
            "run_id": state["run_id"],
            "family_id": state["family_id"],
        }
    )
    return {"decisions": decisions if isinstance(decisions, list) else []}


# ---------------------------------------------------------------------- commit
def commit(state: RunState) -> dict:
    """Apply exactly what was approved, and nothing else."""
    with session_scope() as s:
        advisory_lock(s, state["family_id"])
        batch_id = state.get("batch_id")
        items = (
            s.execute(select(ApprovalItem).where(ApprovalItem.batch_id == batch_id))
            .scalars()
            .all()
        )
        rows = {r.term_key: r for r in project(s, state["family_id"])}
        existing = {
            r.term_key: r
            for r in s.execute(
                select(RegisterRow).where(RegisterRow.family_id == state["family_id"])
            ).scalars()
        }

        applied = rejected = 0
        for item in items:
            if item.decision == "reject":
                rejected += 1
                if item.kind == "finding":
                    f = s.get(Finding, item.ref_id)
                    if f is not None:
                        f.status = "rejected"
                elif item.kind == "conflict":
                    c = s.get(Conflict, item.ref_id)
                    if c is not None:
                        c.status = "rejected"
                        c.resolution = item.reason or "rejected at the gate"
                continue
            if item.decision != "accept":
                continue

            applied += 1
            if item.kind == "row_update":
                row: ProjectedRow | None = rows.get(item.ref_id)
                if row is None:
                    continue
                prior = existing.get(row.term_key)
                version = (prior.version + 1) if prior else 1
                if prior is None:
                    prior = RegisterRow(
                        family_id=state["family_id"],
                        term_key=row.term_key,
                        current_value=row.value,
                        status=row.status,
                        content_hash=row.content_hash(),
                        derived_from_fact_ids=row.derived_from,
                        version=version,
                    )
                    s.add(prior)
                else:
                    prior.current_value = row.value
                    prior.status = row.status
                    prior.content_hash = row.content_hash()
                    prior.derived_from_fact_ids = row.derived_from
                    prior.version = version
                s.add(
                    RegisterRowVersion(
                        family_id=state["family_id"],
                        term_key=row.term_key,
                        version=version,
                        content_hash=row.content_hash(),
                        value=row.value,
                        run_id=state["run_id"],
                        reason=item.reason or "approved at the gate",
                        because_of_document_id=state.get("trigger_document_id")
                        or (row.chain[-1].document_id if row.chain else None),
                    )
                )
            elif item.kind == "finding":
                f = s.get(Finding, item.ref_id)
                if f is not None:
                    f.status = "accepted"
            elif item.kind == "conflict":
                c = s.get(Conflict, item.ref_id)
                if c is not None:
                    c.status = "accepted"
                    c.resolution = item.reason or "acknowledged at the gate"

        batch = s.get(ApprovalBatch, batch_id) if batch_id else None
        if batch is not None:
            batch.status = "resolved"
            from app.db.base import utcnow

            batch.resolved_at = utcnow()

        run = s.get(Run, state["run_id"])
        if run is not None:
            from app.db.base import utcnow

            run.status = "complete"
            run.finished_at = utcnow()
            run.stats = {**(run.stats or {}), "applied": applied, "rejected": rejected}

        log_decision(
            s,
            run_id=state["run_id"],
            node="commit",
            decision="committed",
            detail={"applied": applied, "rejected": rejected},
        )
    return {"stats": {**state.get("stats", {}), "applied": applied, "rejected": rejected}}
