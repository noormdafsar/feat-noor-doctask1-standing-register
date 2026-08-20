from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, Fact, FactSpan
from app.extract.schemas import TERM_KEYS

# Later documents win at equal effective dates. An amendment beats the order form
# it amends, which beats the master agreement it hangs off.
DOC_PRECEDENCE = {
    "master_agreement": 0,
    "order_form": 1,
    "sow": 1,
    "amendment": 2,
    "credit_note": 3,
    "invoice": 3,
}

TERM_LABELS = {
    "parties": "Parties",
    "effective_date": "Effective date",
    "initial_term_months": "Initial term (months)",
    "renewal_type": "Renewal",
    "renewal_notice_days": "Renewal notice (days)",
    "unit_price": "Unit price",
    "billing_frequency": "Billing frequency",
    "payment_terms_days": "Payment terms (days)",
    "liability_cap": "Liability cap",
    "sla_uptime_pct": "SLA uptime (%)",
    "sla_credit_pct": "SLA credit (%)",
    "termination_for_convenience_days": "Termination for convenience (days)",
    "assignment_restricted": "Assignment restricted",
    "governing_law": "Governing law",
}


@dataclass
class ChainLink:
    fact_id: str
    document_id: str
    filename: str
    doc_type: str
    value: str
    unit: str
    effective_from: str | None
    quote: str
    block_id: str
    char_start: int
    char_end: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "doc_type": self.doc_type,
            "value": self.value,
            "unit": self.unit,
            "effective_from": self.effective_from,
            "quote": self.quote,
            "block_id": self.block_id,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }

    def hash_payload(self) -> dict[str, Any]:
        """Content only -- no surrogate keys.

        fact_id and document_id are freshly generated every run, so hashing them
        would make every row look changed on every run and quietly destroy the one
        guarantee this hash exists to provide. Filename plus offsets plus the quote
        identify the source just as precisely and are stable across runs.
        """
        return {
            "filename": self.filename,
            "doc_type": self.doc_type,
            "value": self.value,
            "unit": self.unit,
            "effective_from": self.effective_from,
            "quote": self.quote,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class ProjectedRow:
    term_key: str
    label: str
    value: dict[str, Any]
    status: str
    chain: list[ChainLink] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def derived_from(self) -> list[str]:
        return [c.fact_id for c in self.chain]

    def content_hash(self) -> str:
        """The byte-identity proof.

        Covers the value AND the provenance chain, so a row whose value happens to
        be unchanged but whose supporting source moved is still reported as changed.
        """
        value = {k: v for k, v in self.value.items() if k != "source_document_id"}
        canonical = json.dumps(
            {
                "term_key": self.term_key,
                "value": value,
                "status": self.status,
                "chain": [c.hash_payload() for c in self.chain],
                "conflicts": [
                    {k: v for k, v in c.items() if k != "fact_ids"} for c in self.conflicts
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "term_key": self.term_key,
            "label": self.label,
            "value": self.value,
            "status": self.status,
            "chain": [c.as_dict() for c in self.chain],
            "conflicts": self.conflicts,
            "content_hash": self.content_hash(),
        }


def _sort_key(link: ChainLink) -> tuple:
    eff = link.effective_from or "0001-01-01"
    return (eff, DOC_PRECEDENCE.get(link.doc_type, 5), link.document_id)


def project(s: Session, family_id: str, as_of: date | None = None) -> list[ProjectedRow]:
    """Pure function of the fact store: facts in, register rows out.

    No model is called here and none ever will be. This is the whole reason a
    register row can carry a stable content hash across runs.
    """
    as_of = as_of or date.max

    facts = (
        s.execute(
            select(Fact, Document)
            .join(Document, Document.id == Fact.document_id)
            .where(Fact.family_id == family_id, Fact.status == "verified")
        )
        .all()
    )
    span_by_fact: dict[str, FactSpan] = {}
    if facts:
        fact_ids = [f.Fact.id for f in facts]
        for sp in s.execute(select(FactSpan).where(FactSpan.fact_id.in_(fact_ids))).scalars():
            span_by_fact.setdefault(sp.fact_id, sp)

    by_key: dict[str, list[ChainLink]] = {}
    for row in facts:
        f: Fact = row.Fact
        d: Document = row.Document
        sp = span_by_fact.get(f.id)
        if sp is None:
            # Unreachable in normal flow: verify_spans deletes span-less facts.
            continue
        link = ChainLink(
            fact_id=f.id,
            document_id=d.id,
            filename=d.filename,
            doc_type=d.doc_type,
            value=str(f.value.get("value", "")),
            unit=str(f.value.get("unit", "")),
            effective_from=f.effective_from.isoformat() if f.effective_from else None,
            quote=sp.quote,
            block_id=sp.block_id,
            char_start=sp.char_start,
            char_end=sp.char_end,
        )
        by_key.setdefault(f.key, []).append(link)

    blocked_docs = s.execute(
        select(Document.id).where(
            Document.family_id == family_id, Document.blocked_reason.isnot(None)
        )
    ).scalars().all()

    rows: list[ProjectedRow] = []
    for key in TERM_KEYS:
        chain = sorted(by_key.get(key, []), key=_sort_key)
        applicable = [
            c
            for c in chain
            if c.effective_from is None or date.fromisoformat(c.effective_from) <= as_of
        ]
        if not applicable:
            rows.append(
                ProjectedRow(
                    term_key=key,
                    label=TERM_LABELS.get(key, key),
                    value={"value": None, "display": "not stated in sources"},
                    status="absent",
                )
            )
            continue

        current = applicable[-1]
        conflicts = _detect_conflicts(applicable)
        status = "contested" if conflicts else "settled"
        if blocked_docs and any(c.document_id in blocked_docs for c in applicable):
            status = "blocked"
        rows.append(
            ProjectedRow(
                term_key=key,
                label=TERM_LABELS.get(key, key),
                value={
                    "value": current.value,
                    "unit": current.unit,
                    "display": f"{current.value} {current.unit}".strip(),
                    "effective_from": current.effective_from,
                    "source_document_id": current.document_id,
                },
                status=status,
                chain=applicable,
                conflicts=conflicts,
            )
        )
    return rows


def _detect_conflicts(chain: list[ChainLink]) -> list[dict[str, Any]]:
    """Two sources claiming different values with equal authority.

    Surfaced, never resolved. Picking a winner here would be exactly the silent
    resolution the brief forbids.
    """
    out: list[dict[str, Any]] = []
    by_date: dict[str | None, list[ChainLink]] = {}
    for c in chain:
        by_date.setdefault(c.effective_from, []).append(c)
    for eff, group in by_date.items():
        values = {c.value for c in group}
        if len(values) > 1:
            out.append(
                {
                    "kind": "contradictory_same_date",
                    "effective_from": eff,
                    "values": sorted(values),
                    "fact_ids": sorted(c.fact_id for c in group),
                    "documents": sorted({c.filename for c in group}),
                }
            )
    return out


def to_markdown(family_name: str, rows: list[ProjectedRow]) -> str:
    lines = [
        f"# Obligation and Commercial Register - {family_name}",
        "",
        "Every value below is projected from verified source spans. A term with no "
        "supporting span reads `not stated in sources` rather than a plausible guess.",
        "",
        "| Term | Current value | Status | Effective | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        src = r.chain[-1].filename if r.chain else "-"
        eff = (r.value.get("effective_from") or "-") if r.chain else "-"
        lines.append(
            f"| {r.label} | {r.value.get('display')} | {r.status} | {eff} | {src} |"
        )
    lines.append("")
    contested = [r for r in rows if r.status == "contested"]
    if contested:
        lines += ["## Contested terms", ""]
        for r in contested:
            for c in r.conflicts:
                lines.append(
                    f"- **{r.label}**: {', '.join(c['values'])} "
                    f"(effective {c['effective_from']}) across {', '.join(c['documents'])}"
                )
        lines.append("")
    return "\n".join(lines)
