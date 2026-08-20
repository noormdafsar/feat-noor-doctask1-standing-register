from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

# The suite runs against real Postgres and the real graph. The only thing faked is
# the model provider -- see tests/README.md for why that line is drawn there.
os.environ.setdefault("MODEL_BACKEND", "fixture")

from app.config import settings  # noqa: E402
from app.db.session import engine, ensure_database, init_db  # noqa: E402
from app.domain import families  # noqa: E402
from app.graph.build import setup_checkpointer  # noqa: E402

CORPUS = Path(settings.corpus_dir)

DOMAIN_TABLES = [
    "approval_item",
    "approval_batch",
    "model_call",
    "node_result",
    "step",
    "run",
    "finding",
    "conflict",
    "register_row_version",
    "register_row",
    "gap",
    "fact_span",
    "fact",
    "chunk",
    "block",
    "document",
    "family",
]

CHECKPOINT_TABLES = ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    ensure_database()
    init_db()
    setup_checkpointer()


@pytest.fixture(autouse=True)
def reset_db():
    """Every test starts from an empty database.

    Family slugs are meaningful (the fixture provider keys grounded verdicts on
    them), so tests cannot be isolated by using unique slugs -- they are isolated
    by truncation instead.
    """
    _truncate()
    yield


def _truncate() -> None:
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE " + ", ".join(DOMAIN_TABLES) + " RESTART IDENTITY CASCADE")
        )
        for t in CHECKPOINT_TABLES:
            try:
                conn.execute(text(f"TRUNCATE {t} CASCADE"))
            except Exception:  # noqa: BLE001  -- table may not exist yet
                pass


def seed_family(slug: str, extra: list[Path] | None = None) -> str:
    """Ingest one corpus directory, plus any held-back documents the test needs."""
    fam = families.ensure_family(slug, slug)
    files = sorted(p for p in (CORPUS / slug).iterdir() if p.is_file())
    families.ingest_paths(fam, files + list(extra or []))
    return fam


def arrival(name: str) -> Path:
    return CORPUS / "_arrivals" / "acme-v1" / name


def held_back(folder: str, name: str) -> Path:
    return CORPUS / folder / name
