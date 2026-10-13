"""Test fixtures.

Every test gets its own data dir holding a copy of a migrated, region-seeded
SQLite template, with `db.engine` rebound to it, and the settings singleton and
auth caches restored afterwards — so no test can see another's rows, files, or
config. The session's temp root is removed when the session ends.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="ai-almanac-tests-"))
_TEMPLATE_DIR = _SESSION_ROOT / "template"
# Set before anything imports `ai_almanac`, so the import-time engine and
# settings never resolve the developer's real data dir.
os.environ["AI_ALMANAC_DATA_DIR"] = str(_TEMPLATE_DIR)
# Blank values outrank `.env`, so a developer's database or output dir there
# can't pull tests out of the per-test data dir.
os.environ["DATABASE_URL"] = ""
os.environ["OUTPUT_DIR"] = ""
os.environ["RUNNER_MODE"] = "stub"
# Chat routers refuse to operate without an LLM URL; tests mock the client.
os.environ["LLM_BASE_URL"] = "http://test-llm.local"


def pytest_unconfigure(config: pytest.Config) -> None:
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


@pytest_asyncio.fixture(scope="session")
async def _template_data_dir() -> Path:
    """Migrate and seed one data dir per session for tests to copy."""
    from ai_almanac.paths import ensure_layout
    from ai_almanac.server import db
    from ai_almanac.server.app import _apply_migrations
    from ai_almanac.server.services.region_catalog import seed_packaged_regions

    ensure_layout()
    _apply_migrations()
    await seed_packaged_regions()
    await db.engine.dispose()
    return _TEMPLATE_DIR


@pytest_asyncio.fixture(autouse=True)
async def isolated_data_dir(
    _template_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Path]:
    """A private copy of the template data dir, with the app's engine bound to it."""
    from ai_almanac.server import db

    data_dir = _SESSION_ROOT / f"test-{uuid4().hex}"
    shutil.copytree(_template_data_dir, data_dir)
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(data_dir))
    engine = db._make_engine()
    monkeypatch.setattr(db, "engine", engine)
    yield data_dir
    await engine.dispose()
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def restore_settings() -> Iterator[None]:
    """Undo any in-place change to the settings singleton, including `reload_settings()`."""
    from ai_almanac.settings import settings

    fields = type(settings).model_fields
    snapshot = copy.deepcopy({name: getattr(settings, name) for name in fields})
    yield
    for name, value in snapshot.items():
        setattr(settings, name, value)


@pytest.fixture(autouse=True)
def clear_auth_caches() -> None:
    from ai_almanac.server import auth

    auth._globus_cache._entries.clear()
    auth._globus_groups_cache._entries.clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An async HTTP client wired to the FastAPI app (no lifespan; the
    per-test data dir already holds what it would set up)."""
    from ai_almanac.server.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def as_new_user() -> dict[str, str]:
    """Request headers that sign in as a fresh proxy-authenticated user."""
    return {"X-Forwarded-User": f"pytest-{uuid4()}"}


@pytest_asyncio.fixture
async def user_id(as_new_user: dict[str, str], client: httpx.AsyncClient) -> str:
    """Trigger user creation via a request and return the synthetic user's DB id."""
    from sqlalchemy import text

    from ai_almanac.server.db import get_db

    # Any authenticated GET triggers `get_or_create_user`.
    await client.get("/chat/sessions", headers=as_new_user)

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT id FROM users WHERE external_id = :eid"),
                    {"eid": as_new_user["X-Forwarded-User"]},
                )
            )
            .mappings()
            .fetchone()
        )
    assert row is not None, "user was not created"
    return row["id"]
