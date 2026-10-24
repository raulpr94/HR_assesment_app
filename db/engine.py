"""SQLite engine and session factory. The storage engine is chosen here, in one place."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.schema import metadata

log = logging.getLogger(__name__)


def _enable_sqlite_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str) -> Engine:
    """Create an engine. ``sqlite://`` (no path) gives a shared in-memory DB for tests."""
    if url in ("sqlite://", "sqlite:///:memory:"):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


def engine_for_file(path: Path) -> Engine:
    return make_engine(f"sqlite:///{path.as_posix()}")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(engine: Engine) -> list[str]:
    """Create missing tables (idempotent). Returns the names of tables it created."""
    existing = set(inspect(engine).get_table_names())
    metadata.create_all(engine)
    created = [t for t in metadata.tables if t not in existing]
    if created:
        log.debug("Created tables: %s", ", ".join(created))
    return created


def schema_is_ready(engine: Engine) -> bool:
    existing = set(inspect(engine).get_table_names())
    return set(metadata.tables).issubset(existing)
