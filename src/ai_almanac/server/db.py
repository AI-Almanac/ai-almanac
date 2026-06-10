"""Database — SQLAlchemy async Core with SQLite by default.

The default URL is `sqlite+aiosqlite:///<data-dir>/almanac.db` (computed via
`settings.resolve_database_url()`). Override `DATABASE_URL` for non-default
backends (e.g. Postgres for shared public deployments).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from ai_almanac.settings import settings


def _make_engine():
    url = make_url(settings.resolve_database_url())
    if url.drivername == "postgresql":
        # Bind a bare `postgresql://` URL to the installed async driver
        # (psycopg3) instead of SQLAlchemy's asyncpg default.
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername.startswith("sqlite"):
        return create_async_engine(
            url,
            pool_pre_ping=True,
            connect_args={"timeout": 30},
        )
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=10,
    )


engine = _make_engine()


@asynccontextmanager
async def get_db():
    """Yield a SQLAlchemy AsyncConnection inside a transaction."""
    async with engine.begin() as conn:
        yield conn


async def lock_for_update(conn: AsyncConnection) -> str:
    """Acquire the dialect's write lock and return its SELECT lock clause."""
    if conn.dialect.name == "sqlite":
        await conn.exec_driver_sql("BEGIN IMMEDIATE")
        return ""
    return " FOR UPDATE"


async def get_or_create_user(
    conn: AsyncConnection,
    external_id: str,
    email: str | None = None,
    *,
    issuer: str = "",
    subject: str | None = None,
    display_name: str | None = None,
    groups: list[str] | None = None,
) -> dict:
    now = datetime.now(UTC).isoformat()
    params = {
        "id": str(uuid.uuid4()),
        "eid": external_id,
        "issuer": issuer,
        "subject": subject or external_id,
        "email": email,
        "display_name": display_name,
        "groups": __import__("json").dumps(groups or []),
        "now": now,
    }
    dialect = conn.dialect.name
    if dialect == "postgresql":
        statement = """
            INSERT INTO users (
                id, external_id, issuer, subject, email, display_name,
                groups, status, created_at, last_login_at
            )
            VALUES (
                :id, :eid, :issuer, :subject, :email, :display_name,
                CAST(:groups AS JSON), 'active', :now, :now
            )
            ON CONFLICT (external_id) DO UPDATE SET
                issuer = EXCLUDED.issuer,
                subject = EXCLUDED.subject,
                email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                groups = EXCLUDED.groups,
                last_login_at = EXCLUDED.last_login_at
            RETURNING *
        """
    else:
        statement = """
            INSERT INTO users (
                id, external_id, issuer, subject, email, display_name,
                groups, status, created_at, last_login_at
            )
            VALUES (
                :id, :eid, :issuer, :subject, :email, :display_name,
                :groups, 'active', :now, :now
            )
            ON CONFLICT (external_id) DO UPDATE SET
                issuer = excluded.issuer,
                subject = excluded.subject,
                email = excluded.email,
                display_name = excluded.display_name,
                groups = excluded.groups,
                last_login_at = excluded.last_login_at
            RETURNING *
        """
    result = await conn.execute(
        text(statement),
        params,
    )
    return dict(result.mappings().fetchone())
