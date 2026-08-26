from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models import Document, Family, Finding, Gap, RegisterRow, RegisterRowVersion
from app.db.session import session_scope
from app.render.register import TERM_LABELS, to_markdown


def register(family_id: str, fmt: str = "json") -> Any:
    """The published register: only what a human approved at the gate.

    Deliberately reads committed rows rather than re-projecting, so that what the
    API returns is exactly what was signed off, not the newest thing the system
    happens to believe.
    """
    with session_scope() as s:
        fam = s.get(Family, family_id)
        rows = (
            s.execute(
                select(RegisterRow)
                .where(RegisterRow.family_id == family_id)
                .order_by(RegisterRow.term_key)
            )
            .scalars()
            .all()
        )
        gaps = {
            g.term_key
            for g in s.execute(select(Gap).where(Gap.family_id == family_id)).scalars()
        }
        payload = {
            "family": {"id": family_id, "name": fam.name if fam else family_id},
            "rows": [
                {
                    "term_key": r.term_key,
                    "label": TERM_LABELS.get(r.term_key, r.term_key),
                    "value": r.current_value,
                    "status": r.status,
                    "version": r.version,
                    "content_hash": r.content_hash,
                    "derived_from_fact_ids": r.derived_from_fact_ids,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ],
            "unsupported_terms": sorted(gaps),
        }

    if fmt == "json":
        return payload
    if fmt == "markdown":
        return _markdown(payload)
    raise ValueError(f"unsupported format {fmt!r}; use json or markdown")


def _markdown(payload: dict) -> str:
    lines = [
        f"# Obligation and Commercial Register - {payload['family']['name']}",
        "",
        "| Term | Current value | Status | Version | Content hash |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in payload["rows"]:
        lines.append(
            f"| {r['label']} | {r['value'].get('display', '')} | {r['status']} | "
            f"{r['version']} | `{r['content_hash'][:12]}` |"
        )
    if payload["unsupported_terms"]:
        lines += [
            "",
            "## Not stated in sources",
            "",
            "These terms were looked for and are genuinely absent. They are reported "
            "rather than inferred.",
            "",
        ]
        for t in payload["unsupported_terms"]:
            lines.append(f"- {TERM_LABELS.get(t, t)}")
    return "\n".join(lines) + "\n"


def findings(family_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Findings from one run -- the latest one unless a run is named.

    Every run records its own findings, so a family that has been examined a
    dozen times holds a dozen copies of the same five. Returning all of them
    made the interface show sixty findings where there are five, and made "how
    many problems does this family have?" unanswerable. The current findings are
    the ones the current run produced.
    """
    with session_scope() as s:
        if run_id is None:
            run_id = s.execute(
                select(Finding.run_id)
                .where(Finding.family_id == family_id)
                .order_by(Finding.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        q = select(Finding).where(Finding.family_id == family_id)
        if run_id:
            q = q.where(Finding.run_id == run_id)
        rows = s.execute(q.order_by(Finding.created_at.desc())).scalars().all()
        return {
            "count": len(rows),
            # The rarest output in this industry, and it has to be a real branch.
            "message": (
                "No findings. The sources were checked against every rule in the pack "
                "and nothing failed."
                if not rows
                else f"{len(rows)} finding(s)."
            ),
            "findings": [
                {
                    "id": f.id,
                    "rule_id": f.rule_id,
                    "rule_pack": f.rule_pack,
                    "kind": f.kind,
                    "severity": f.severity,
                    "stage": f.stage,
                    "title": f.title,
                    "detail": f.detail,
                    "evidence": f.evidence,
                    "status": f.status,
                    "run_id": f.run_id,
                }
                for f in rows
            ],
        }


def changelog(family_id: str, since: str | None = None) -> dict[str, Any]:
    """What changed, when, and because of which source."""
    with session_scope() as s:
        q = select(RegisterRowVersion).where(RegisterRowVersion.family_id == family_id)
        if since:
            q = q.where(RegisterRowVersion.created_at >= since)
        versions = (
            s.execute(q.order_by(RegisterRowVersion.created_at.desc())).scalars().all()
        )
        doc_names = {
            d.id: d.filename
            for d in s.execute(
                select(Document).where(Document.family_id == family_id)
            ).scalars()
        }
        return {
            "family_id": family_id,
            "entries": [
                {
                    "term_key": v.term_key,
                    "label": TERM_LABELS.get(v.term_key, v.term_key),
                    "version": v.version,
                    "value": v.value,
                    "content_hash": v.content_hash,
                    "run_id": v.run_id,
                    "reason": v.reason,
                    "because_of": doc_names.get(v.because_of_document_id or "", None),
                    "at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in versions
            ],
        }


def provenance(family_id: str, term_key: str) -> dict[str, Any]:
    """Document, page, offsets, quote, and the amendment chain behind a value."""
    from app.render.register import project

    with session_scope() as s:
        rows = {r.term_key: r for r in project(s, family_id)}
        row = rows.get(term_key)
        if row is None:
            raise LookupError(term_key)
        committed = s.execute(
            select(RegisterRow).where(
                RegisterRow.family_id == family_id, RegisterRow.term_key == term_key
            )
        ).scalar_one_or_none()
        return {
            "term_key": term_key,
            "label": row.label,
            "status": row.status,
            "published_value": committed.current_value if committed else None,
            "projected_value": row.value,
            "chain": [c.as_dict() for c in row.chain],
            "conflicts": row.conflicts,
        }
