from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Document, Family, Finding
from app.extract.schemas import VERDICT_SCHEMA
from app.ingest.segment import find_span
from app.llm.call import SOURCE_RULE, call_model, log_decision
from app.llm.provider import ModelProvider, ModelRequest
from app.render.register import ProjectedRow
from app.retrieval.search import hybrid_search
from app.rules.deterministic import REGISTRY
from app.rules.loader import RulePack
from app.security.injection import scan, wrap_source

ADJUDICATION_FLOOR = 0.6


def examine(
    s: Session,
    *,
    run_id: str,
    family_id: str,
    rows: list[ProjectedRow],
    pack: RulePack,
    provider: ModelProvider,
) -> list[Finding]:
    """Three passes, cheapest first.

    A clean corpus passes stage one, produces nothing citable in stage two, and
    yields the honest empty report -- which is a real output of this function, not
    a special case bolted on to produce one.
    """
    by_key = {r.term_key: r for r in rows}
    invoices = _invoice_lines(s, family_id)
    findings: list[Finding] = []
    fam = s.get(Family, family_id)
    slug = fam.slug if fam else family_id

    # ---- pass 0: the documents themselves, before any of them reach a model ----
    findings += _scan_for_instructions(s, run_id=run_id, family_id=family_id, pack=pack)

    # ---- pass 1: deterministic. no model, no cost, no possible hallucination ----
    grounded_queue = []
    for rule in pack.rules:
        if rule.stage != "deterministic":
            grounded_queue.append(rule)
            continue
        fn = REGISTRY.get(rule.check)
        if fn is None:
            raise KeyError(f"rule {rule.id} references unknown check {rule.check!r}")
        res = fn(by_key, rule.params, invoices)
        log_decision(
            s,
            run_id=run_id,
            node="examine",
            decision=f"stage1:{rule.id}:{'pass' if res.passed else 'fail'}",
            detail={"rule": rule.id, "unsupported": res.unsupported},
        )
        if res.passed:
            continue
        findings.append(
            _finding(
                run_id=run_id,
                family_id=family_id,
                pack=pack,
                rule_id=rule.id,
                title=rule.title,
                severity="info" if res.unsupported else rule.severity,
                stage="deterministic",
                kind="unsupported" if res.unsupported else "rule",
                detail=res.detail,
                evidence=res.evidence,
                target=rule.params.get("term_key", ""),
            )
        )

    # ---- pass 2: retrieval-grounded, citation mandatory ----
    needs_adjudication: list[tuple[Any, dict]] = []
    for rule in grounded_queue:
        hits = hybrid_search(s, family_id=family_id, query=rule.retrieval_query, limit=4)
        if not hits:
            findings.append(
                _finding(
                    run_id=run_id,
                    family_id=family_id,
                    pack=pack,
                    rule_id=rule.id,
                    title=rule.title,
                    severity="info",
                    stage="grounded",
                    kind="unsupported",
                    detail="Nothing in the sources addresses this rule.",
                    evidence=[],
                )
            )
            continue

        verdict = _judge(
            s,
            run_id=run_id,
            provider=provider,
            model=settings.model_fast,
            rule=rule,
            hits=hits,
            family_id=family_id,
            slug=slug,
        )
        if verdict is None:
            continue
        if verdict["confidence"] < ADJUDICATION_FLOOR or verdict["verdict"] == "unsupported":
            needs_adjudication.append((rule, {"hits": hits, "first": verdict}))
            continue
        if verdict["verdict"] == "fail":
            findings.append(
                _finding(
                    run_id=run_id,
                    family_id=family_id,
                    pack=pack,
                    rule_id=rule.id,
                    title=rule.title,
                    severity=rule.severity,
                    stage="grounded",
                    kind="rule",
                    detail=verdict["detail"],
                    evidence=verdict["evidence"],
                )
            )

    # ---- pass 3: adjudication at the deep tier, then escalation rather than a coin flip ----
    for rule, ctx in needs_adjudication:
        second = _judge(
            s,
            run_id=run_id,
            provider=provider,
            model=settings.model_deep,
            rule=rule,
            hits=ctx["hits"],
            family_id=family_id,
            adversarial=True,
        )
        log_decision(
            s,
            run_id=run_id,
            node="examine",
            decision=f"stage3:{rule.id}",
            detail={"first": ctx["first"]["verdict"], "second": (second or {}).get("verdict")},
        )
        if second is None or second["confidence"] < ADJUDICATION_FLOOR:
            findings.append(
                _finding(
                    run_id=run_id,
                    family_id=family_id,
                    pack=pack,
                    rule_id=rule.id,
                    title=rule.title,
                    severity="info",
                    stage="adjudication",
                    kind="escalated",
                    detail=(
                        "Two passes disagreed or neither was confident. Escalated to a "
                        "human rather than picking a side."
                    ),
                    evidence=(second or ctx["first"]).get("evidence", []),
                )
            )
            continue
        if second["verdict"] == "fail":
            findings.append(
                _finding(
                    run_id=run_id,
                    family_id=family_id,
                    pack=pack,
                    rule_id=rule.id,
                    title=rule.title,
                    severity=rule.severity,
                    stage="adjudication",
                    kind="rule",
                    detail=second["detail"],
                    evidence=second["evidence"],
                )
            )

    for f in findings:
        s.add(f)
    s.flush()
    return findings


def _judge(
    s: Session,
    *,
    run_id: str,
    provider: ModelProvider,
    model: str,
    rule,
    hits,
    family_id: str,
    slug: str = "",
    adversarial: bool = False,
) -> dict | None:
    docs = {
        d.id: d
        for d in s.execute(
            select(Document).where(Document.id.in_([h.document_id for h in hits]))
        ).scalars()
    }
    body = "\n\n".join(
        wrap_source(h.document_id, docs[h.document_id].filename, h.text)
        for h in hits
        if h.document_id in docs
    )
    stance = (
        "Assume the previous reviewer was wrong. Look for the reading that "
        "contradicts them. If neither reading is supported, answer 'unsupported'."
        if adversarial
        else ""
    )
    req = ModelRequest(
        task="judge_rule",
        model=model,
        system=(
            f"{SOURCE_RULE}\n\n"
            "You judge one compliance rule against supplied passages. You must quote "
            "the exact supporting text. A verdict you cannot quote for is 'unsupported'. "
            "Never infer a value that is not written down."
        ),
        user=f"RULE: {rule.title}\nQUESTION: {rule.question}\n{stance}\n\n{body}",
        schema=VERDICT_SCHEMA,
        meta={"key": f"{slug}:{rule.id}", "family_id": family_id},
    )
    resp = call_model(s, run_id=run_id, node="examine", provider=provider, req=req)
    data = resp.data
    quote = (data.get("quote") or "").strip()
    evidence: list[dict[str, Any]] = []

    if quote:
        # A verdict whose citation does not verify is discarded before it is ever
        # a finding. This is the single line that stops a fluent model from
        # inventing a compliance failure.
        for h in hits:
            doc = docs.get(h.document_id)
            if doc is None:
                continue
            span = find_span(doc.raw_text, quote)
            if span:
                evidence.append(
                    {
                        "document_id": doc.id,
                        "filename": doc.filename,
                        "block_id": h.block_ids[0] if h.block_ids else "",
                        "char_start": span[0],
                        "char_end": span[1],
                        "quote": doc.raw_text[span[0] : span[1]],
                    }
                )
                break

    if data.get("verdict") == "fail" and not evidence:
        log_decision(
            s,
            run_id=run_id,
            node="examine",
            decision=f"stage2:{rule.id}:discarded-uncitable",
            detail={"quote": quote[:120]},
        )
        return {"verdict": "unsupported", "confidence": 0.0, "detail": data.get("detail", ""), "evidence": []}

    return {
        "verdict": data.get("verdict", "unsupported"),
        "confidence": float(data.get("confidence", 0.0)),
        "detail": data.get("detail", ""),
        "evidence": evidence,
    }


def _scan_for_instructions(s: Session, *, run_id: str, family_id: str, pack: RulePack) -> list[Finding]:
    """A source that tries to give orders is reported, never obeyed."""
    out: list[Finding] = []
    docs = s.execute(select(Document).where(Document.family_id == family_id)).scalars().all()
    for d in docs:
        for hit in scan(d.raw_text):
            out.append(
                _finding(
                    run_id=run_id,
                    family_id=family_id,
                    pack=pack,
                    rule_id="INJ-01",
                    title="Source document contains instructions aimed at the system",
                    severity="high",
                    stage="scan",
                    kind="suspicious_instruction",
                    detail=(
                        f"{d.filename} contains text matching the {hit.pattern!r} pattern. "
                        "It was treated as evidence about the document and had no effect on "
                        "any verdict."
                    ),
                    evidence=[
                        {
                            "document_id": d.id,
                            "filename": d.filename,
                            "block_id": "",
                            "char_start": hit.char_start,
                            "char_end": hit.char_end,
                            "quote": hit.quote,
                        }
                    ],
                    target=d.id,
                )
            )
            log_decision(
                s,
                run_id=run_id,
                node="examine",
                decision="scan:suspicious_instruction",
                detail={"document": d.filename, "pattern": hit.pattern},
            )
    return out


def _invoice_lines(s: Session, family_id: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    docs = s.execute(
        select(Document).where(Document.family_id == family_id, Document.doc_type == "invoice")
    ).scalars()
    for d in docs:
        for ln in d.meta.get("invoice_lines", []):
            lines.append({**ln, "document_id": d.id, "filename": d.filename})
    return lines


def _finding(**kw) -> Finding:
    pack: RulePack = kw.pop("pack")
    evidence = kw.pop("evidence", [])
    kind = kw.get("kind", "rule")
    # Structural guarantee: a rule finding without evidence cannot exist.
    if kind == "rule" and not evidence:
        raise ValueError(f"refusing to create evidence-free finding for {kw.get('rule_id')}")
    return Finding(rule_pack=f"{pack.name}@{pack.version}", evidence=evidence, **kw)
