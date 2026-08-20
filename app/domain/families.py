from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import Document, Family
from app.db.session import session_scope
from app.ingest.loader import UnreadableDocument, UnsupportedFormat, load_bytes


class FamilyNotFound(LookupError):
    pass


def ensure_family(slug: str, name: str | None = None) -> str:
    with session_scope() as s:
        fam = s.execute(select(Family).where(Family.slug == slug)).scalar_one_or_none()
        if fam is None:
            fam = Family(slug=slug, name=name or slug)
            s.add(fam)
            s.flush()
        return fam.id


def resolve(slug_or_id: str) -> str:
    with session_scope() as s:
        fam = s.execute(
            select(Family).where((Family.slug == slug_or_id) | (Family.id == slug_or_id))
        ).scalar_one_or_none()
        if fam is None:
            raise FamilyNotFound(slug_or_id)
        return fam.id


def list_families() -> list[dict[str, Any]]:
    with session_scope() as s:
        out = []
        for fam in s.execute(select(Family).order_by(Family.slug)).scalars():
            n = len(
                s.execute(select(Document.id).where(Document.family_id == fam.id)).all()
            )
            out.append({"id": fam.id, "slug": fam.slug, "name": fam.name, "documents": n})
        return out


def ingest_bytes(family_id: str, filename: str, blob: bytes) -> dict[str, Any]:
    """One file in. Duplicate content is a no-op, not a second document."""
    try:
        loaded = load_bytes(filename, blob)
    except (UnsupportedFormat, UnreadableDocument) as exc:
        return {"filename": filename, "status": "rejected", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        # One bad file must never take down the rest of the batch, and an
        # unexpected failure still has to say what it was.
        return {
            "filename": filename,
            "status": "rejected",
            "reason": f"{filename}: could not be read ({type(exc).__name__}: {exc})",
        }

    try:
        return _store(family_id, filename, loaded)
    except Exception as exc:  # noqa: BLE001
        return {
            "filename": filename,
            "status": "rejected",
            "reason": f"{filename}: could not be stored ({type(exc).__name__}: {exc})",
        }


def _store(family_id: str, filename: str, loaded) -> dict[str, Any]:
    with session_scope() as s:
        existing = s.execute(
            select(Document).where(
                Document.family_id == family_id, Document.sha256 == loaded.sha256
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {
                "filename": filename,
                "status": "duplicate",
                "document_id": existing.id,
                "reason": f"identical content already ingested as {existing.filename}",
            }
        d = Document(
            family_id=family_id,
            sha256=loaded.sha256,
            filename=loaded.filename,
            mime=loaded.mime,
            raw_text=loaded.text,
        )
        s.add(d)
        s.flush()
        return {"filename": filename, "status": "accepted", "document_id": d.id}


def ingest_paths(family_id: str, paths: list[Path]) -> list[dict[str, Any]]:
    return [ingest_bytes(family_id, p.name, p.read_bytes()) for p in paths]


def list_documents(family_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "doc_type": d.doc_type,
                "confidence": d.doc_type_confidence,
                "tier": d.doc_type_tier,
                "doc_date": d.doc_date.isoformat() if d.doc_date else None,
                "blocked_reason": d.blocked_reason,
                "sha256": d.sha256[:12],
                "invoice_lines": len((d.meta or {}).get("invoice_lines", [])),
            }
            for d in s.execute(
                select(Document)
                .where(Document.family_id == family_id)
                .order_by(Document.ingested_at)
            ).scalars()
        ]


def document_text(document_id: str) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, document_id)
        if d is None:
            raise LookupError(document_id)
        return {"id": d.id, "filename": d.filename, "text": d.raw_text}
