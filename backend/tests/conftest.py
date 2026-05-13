from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def _pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def _test_engine(_pg_container: PostgresContainer) -> AsyncEngine:
    sync_url = _pg_container.get_connection_url()
    async_url = sync_url.replace("psycopg2", "asyncpg")

    # Run Alembic migrations via subprocess so DATABASE_URL is picked up
    # by app.config.Settings without polluting the test process env.
    backend_dir = os.path.join(os.path.dirname(__file__), "..")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": sync_url},
        check=True,
    )

    return create_async_engine(async_url, pool_pre_ping=True)


@pytest_asyncio.fixture
async def client(
    _test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    import app.database as db_mod
    from app.main import app

    monkeypatch.setattr(db_mod, "engine", _test_engine)
    monkeypatch.setattr(
        "app.services.llm.settings.llm_base_url", "http://test-llm.local"
    )
    monkeypatch.setattr("app.auth.settings.globus_client_id", "")
    monkeypatch.setattr("app.auth.settings.globus_client_secret", "")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest_asyncio.fixture
async def auth_headers(_test_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    external_id = f"pytest-chat-{uuid4()}"
    headers = {"Authorization": f"Bearer {external_id}"}
    try:
        yield headers
    finally:
        async with _test_engine.begin() as conn:
            user_row = (
                (
                    await conn.execute(
                        text("SELECT id FROM users WHERE external_id = :eid"),
                        {"eid": external_id},
                    )
                )
                .mappings()
                .fetchone()
            )
            if not user_row:
                return
            uid = user_row["id"]
            await conn.execute(
                text("DELETE FROM chat_artifacts WHERE user_id = :uid"), {"uid": uid}
            )
            await conn.execute(
                text("DELETE FROM chat_sessions WHERE user_id = :uid"), {"uid": uid}
            )
            await conn.execute(
                text("DELETE FROM jobs WHERE user_id = :uid"), {"uid": uid}
            )
            await conn.execute(
                text("DELETE FROM datasets WHERE user_id = :uid"), {"uid": uid}
            )
            await conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})


@pytest_asyncio.fixture
async def user_id(
    auth_headers: dict[str, str],
    client: httpx.AsyncClient,
    _test_engine: AsyncEngine,
) -> str:
    # Trigger user creation via the auth dependency
    response = await client.get("/chat/sessions", headers=auth_headers)
    assert response.status_code == 200
    external_id = auth_headers["Authorization"].removeprefix("Bearer ")
    async with _test_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT id FROM users WHERE external_id = :eid"),
                    {"eid": external_id},
                )
            )
            .mappings()
            .fetchone()
        )
    assert row is not None
    return row["id"]
