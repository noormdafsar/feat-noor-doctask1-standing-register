from __future__ import annotations

import contextlib
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Behaviour 9 leans on real row locks, so every unit of work gets its own
    # connection rather than sharing one across threads.
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def advisory_lock(s: Session, key: str) -> None:
    """Transaction-scoped exclusive lock, keyed by family.

    Released automatically at COMMIT or ROLLBACK, so a killed process cannot
    strand the lock -- which matters because behaviour 2 kills processes on purpose.
    """
    s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


# Every service in the compose file may start at the same moment and every one of
# them wants the schema to exist. create_all's checkfirst is not safe against a
# concurrent creator, so DDL is serialised behind one lock.
SCHEMA_LOCK = 9133771


def ensure_database() -> None:
    """Create the configured database if it is not there yet.

    The behaviour suite runs against its own database so it can truncate freely
    without fighting the watcher, which is polling the live one every few seconds.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    target = url.database
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target}"'))
    admin.dispose()


@contextlib.contextmanager
def schema_lock() -> Iterator[object]:
    """Session-scoped advisory lock, held on an AUTOCOMMIT connection.

    Deliberately NOT pg_advisory_xact_lock. LangGraph's checkpointer setup runs
    CREATE INDEX CONCURRENTLY, which waits for every concurrent transaction to
    finish -- including the one that would be holding a transaction-scoped lock
    while waiting for that very setup to return. That is a genuine deadlock and it
    hung the whole stack once. Autocommit means no transaction is ever open here.
    """
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": SCHEMA_LOCK})
        yield conn
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCHEMA_LOCK})
        finally:
            conn.close()


def init_db() -> None:
    from app.db import models  # noqa: F401  (registers mappings)
    from app.db.base import Base

    with schema_lock() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(conn)
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS chunk_tsv_idx ON chunk USING GIN (tsv)")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS chunk_embedding_idx ON chunk "
                "USING hnsw (embedding vector_cosine_ops)"
            )
        )
