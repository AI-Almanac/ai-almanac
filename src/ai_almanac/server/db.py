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


async def get_or_create_user(
    conn: AsyncConnection, external_id: str, email: str | None = None
) -> dict:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM users WHERE external_id = :eid"),
                {"eid": external_id},
            )
        )
        .mappings()
        .fetchone()
    )
    if row:
        return dict(row)
    user_id = str(uuid.uuid4())
    result = await conn.execute(
        text(
            "INSERT INTO users (id, external_id, email, created_at) "
            "VALUES (:id, :eid, :email, :now) RETURNING *"
        ),
        {
            "id": user_id,
            "eid": external_id,
            "email": email,
            "now": datetime.now(UTC).isoformat(),
        },
    )
    return dict(result.mappings().fetchone())
