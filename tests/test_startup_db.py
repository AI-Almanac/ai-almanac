"""Startup database connectivity check.

`wait_for_database` runs early in the FastAPI lifespan so the server exits
nonzero (instead of serving errors) when the database is unreachable — e.g.
after a host reboot where Compose restart policies ignore `depends_on`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from ai_almanac.server import db


async def test_wait_for_database_succeeds_against_test_database():
    await db.wait_for_database(attempts=1, delay=0)


async def test_wait_for_database_raises_when_unreachable(monkeypatch):
    unreachable = create_async_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nodb",
        pool_timeout=1,
    )
    monkeypatch.setattr(db, "engine", unreachable)
    try:
        with pytest.raises(RuntimeError, match="database unreachable after 2 attempts"):
            await db.wait_for_database(attempts=2, delay=0.01)
    finally:
        await unreachable.dispose()
