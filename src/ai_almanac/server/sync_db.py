"""Synchronous, driver-agnostic database access for the detached supervisor
and workload processes.

The supervisor (`ai-almanac execute-job`) and workload (`run-job-workload`) run
as separate processes outside the API event loop, so they use a synchronous
SQLAlchemy engine bound to the configured database — SQLite for personal
installs, PostgreSQL for shared deployments — rather than the async engine in
`server/db.py`.

The engine is configured so the supervisor's capacity-claim critical sections
serialize correctly on either backend:

- On SQLite every transaction begins with ``BEGIN IMMEDIATE`` (the write lock is
  taken up front), which serializes claimants on the single-file database.
- On PostgreSQL the claim takes a transaction-scoped advisory lock so two
  supervisors never both observe free capacity.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from ai_almanac.server.database_urls import sync_database_url
from ai_almanac.settings import settings

# Fixed key for the advisory lock that serializes capacity claims on PostgreSQL.
_CAPACITY_LOCK_KEY = 0x414C4D4E  # "ALMN"

_engine: Engine | None = None
_engine_url: str | None = None


def _sync_url(async_url: str) -> str:
    return sync_database_url(async_url)


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()
        # Hand transaction control to SQLAlchemy so our explicit BEGIN applies.
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):  # noqa: ANN001
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def sync_engine() -> Engine:
    """Return the process-wide synchronous engine for the configured database."""
    global _engine, _engine_url
    url = _sync_url(settings.resolve_database_url())
    if _engine is not None and _engine_url == url:
        return _engine
    if _engine is not None:
        _engine.dispose()
    is_sqlite = url.startswith("sqlite")
    _engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={"timeout": 30} if is_sqlite else {},
    )
    if is_sqlite:
        _configure_sqlite(_engine)
    _engine_url = url
    return _engine


def lock_capacity(conn: Connection) -> None:
    """Serialize a capacity-claim critical section. Call at the top of a
    transaction; the lock releases when the transaction ends.

    On SQLite the write lock is already held (the engine begins transactions
    with ``BEGIN IMMEDIATE``). On PostgreSQL take a transaction-scoped advisory
    lock so concurrent supervisors don't both observe free capacity.
    """
    if conn.dialect.name == "postgresql":
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CAPACITY_LOCK_KEY}
        )
