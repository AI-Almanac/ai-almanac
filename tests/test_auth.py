"""Phase 2 — identity parsing, role policies, and WebSocket authorization."""

from __future__ import annotations

import sqlite3
import uuid

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ai_almanac.paths import database_path
from ai_almanac.server.auth import enforce_deployment_invariants
from ai_almanac.settings import settings

# ---------------------------------------------------------------------------
# /auth/me — identity and capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_me_personal_defaults_to_local_admin(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/auth/me")).json()
    assert body["subject"] == "local"
    assert body["role"] == "admin"
    assert body["deployment_mode"] == "personal"
    assert body["capabilities"]["can_admin"] is True


@pytest.mark.asyncio
async def test_auth_me_personal_honors_proxy_header(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.get("/auth/me", headers={"X-Forwarded-User": "alice"})
    ).json()
    assert body["subject"] == "alice"
    # Personal mode: the operator owns the box, so they are admin regardless.
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_proxy_rejects_missing_identity(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_proxy_non_admin_is_user(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    body = (
        await client.get("/auth/me", headers={"X-Forwarded-User": "bob"})
    ).json()
    assert body["role"] == "user"
    assert body["capabilities"]["can_admin"] is False


@pytest.mark.asyncio
async def test_auth_me_proxy_admin_by_subject(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "carol,dave")
    body = (
        await client.get("/auth/me", headers={"X-Forwarded-User": "carol"})
    ).json()
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_proxy_admin_by_email_case_insensitive(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_emails", "Boss@Example.com")
    body = (
        await client.get(
            "/auth/me",
            headers={
                "X-Forwarded-User": "erin",
                "X-Forwarded-Email": "boss@example.com",
            },
        )
    ).json()
    assert body["role"] == "admin"


# ---------------------------------------------------------------------------
# Shared-mode startup invariants
# ---------------------------------------------------------------------------


def test_enforce_personal_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    enforce_deployment_invariants()  # must not raise


def test_enforce_shared_rejects_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "none")  # enforce mutates this
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "database_url", "")  # resolves to SQLite
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        enforce_deployment_invariants()


def test_enforce_shared_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "none")  # enforce mutates this
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    with pytest.raises(RuntimeError, match="admin"):
        enforce_deployment_invariants()


def test_enforce_shared_hardens_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "enable_fs_browser", True)
    monkeypatch.setattr(settings, "enable_run_code", True)

    enforce_deployment_invariants()

    assert settings.auth_mode == "proxy"
    assert settings.enable_fs_browser is False
    assert settings.enable_run_code is False


# ---------------------------------------------------------------------------
# Admin gating — settings, fs browser, and catalog mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_open_to_admin_in_personal(client: httpx.AsyncClient) -> None:
    # Personal mode: the local operator is admin, so settings stay accessible.
    assert (await client.get("/settings")).status_code == 200


@pytest.mark.asyncio
async def test_settings_rejects_missing_identity_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    assert (await client.get("/settings")).status_code == 401


@pytest.mark.asyncio
async def test_settings_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    resp = await client.get("/settings", headers={"X-Forwarded-User": "rando"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_fs_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.get("/fs/quick-paths", headers={"X-Forwarded-User": "rando"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_region_list_allowed_for_non_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.get("/regions", headers={"X-Forwarded-User": "rando"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_region_delete_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.delete(
        "/regions/anything", headers={"X-Forwarded-User": "rando"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_data_source_delete_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.delete(
        "/data-sources/anything", headers={"X-Forwarded-User": "rando"}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# WebSocket authorization (previously unauthenticated)
# ---------------------------------------------------------------------------


def _insert_owned_job(external_id: str, user_id: str, job_id: str) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, external_id, created_at) VALUES (?, ?, ?)",
            (user_id, external_id, "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO jobs (id, user_id, dataset_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, user_id, "ds", "running", "2026-01-01T00:00:00"),
        )
        conn.commit()


def test_ws_rejects_missing_identity_in_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.app import app

    with TestClient(app) as tc:
        monkeypatch.setattr(settings, "auth_mode", "proxy")
        with pytest.raises(WebSocketDisconnect), tc.websocket_connect(
            "/jobs/anything/stream"
        ):
            pass


def test_ws_rejects_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.server.app import app

    owner_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    with TestClient(app) as tc:
        monkeypatch.setattr(settings, "auth_mode", "proxy")
        _insert_owned_job(f"owner-{owner_id}", owner_id, job_id)
        with pytest.raises(WebSocketDisconnect), tc.websocket_connect(
            f"/jobs/{job_id}/stream", headers={"X-Forwarded-User": "intruder"}
        ) as ws:
            ws.receive_json()


def test_ws_allows_owner() -> None:
    from ai_almanac.server.app import app

    owner_id = str(uuid.uuid4())
    external_id = f"owner-{owner_id}"
    job_id = str(uuid.uuid4())
    with TestClient(app) as tc:
        _insert_owned_job(external_id, owner_id, job_id)
        with tc.websocket_connect(
            f"/jobs/{job_id}/stream", headers={"X-Forwarded-User": external_id}
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] in ("status", "log", "done")
