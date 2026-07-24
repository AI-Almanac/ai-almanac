"""Phase 6 (1/2) — job visibility, read authorization, and sharing."""

from __future__ import annotations

import sqlite3
import uuid

import httpx
import pytest

from ai_almanac.paths import database_path
from ai_almanac.settings import settings


def _insert(
    external_id: str,
    user_id: str,
    job_id: str,
    *,
    visibility: str = "private",
    status: str = "complete",
) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, external_id, created_at) VALUES (?, ?, ?)",
            (user_id, external_id, "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO jobs (id, user_id, dataset_id, status, visibility, created_at) "
            "VALUES (?, ?, 'ds', ?, ?, '2026-01-01T00:00:00')",
            (job_id, user_id, status, visibility),
        )
        conn.commit()


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "boss")
    monkeypatch.setattr(settings, "admin_emails", "")


async def _owned_job(visibility: str = "private", status: str = "complete") -> str:
    owner_uid = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    _insert(f"owner-{owner_uid}", owner_uid, job_id, visibility=visibility, status=status)
    return job_id, f"owner-{owner_uid}"


@pytest.mark.asyncio
async def test_private_job_hidden_from_other_users(client: httpx.AsyncClient, proxy) -> None:
    job_id, _owner = await _owned_job(visibility="private")
    resp = await client.get(f"/jobs/{job_id}", headers={"X-Forwarded-User": "intruder"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_reads_own_private_job(client: httpx.AsyncClient, proxy) -> None:
    job_id, owner = await _owned_job(visibility="private")
    resp = await client.get(f"/jobs/{job_id}", headers={"X-Forwarded-User": owner})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_shared_job_readable_by_other_users(client: httpx.AsyncClient, proxy) -> None:
    job_id, _owner = await _owned_job(visibility="shared")
    resp = await client.get(f"/jobs/{job_id}", headers={"X-Forwarded-User": "reader"})
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "shared"
    assert resp.json()["is_owner"] is False


@pytest.mark.asyncio
async def test_admin_reads_any_private_job(client: httpx.AsyncClient, proxy) -> None:
    job_id, _owner = await _owned_job(visibility="private")
    resp = await client.get(f"/jobs/{job_id}", headers={"X-Forwarded-User": "boss"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_owner_can_share_and_unshare(client: httpx.AsyncClient, proxy) -> None:
    job_id, owner = await _owned_job(visibility="private")
    shared = await client.post(f"/jobs/{job_id}/share", headers={"X-Forwarded-User": owner})
    assert shared.status_code == 200
    assert shared.json()["visibility"] == "shared"

    unshared = await client.post(f"/jobs/{job_id}/unshare", headers={"X-Forwarded-User": owner})
    assert unshared.json()["visibility"] == "private"


@pytest.mark.asyncio
async def test_non_owner_cannot_share(client: httpx.AsyncClient, proxy) -> None:
    job_id, _owner = await _owned_job(visibility="private")
    resp = await client.post(f"/jobs/{job_id}/share", headers={"X-Forwarded-User": "intruder"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sharing_is_read_only_no_cancel_or_delete(client: httpx.AsyncClient, proxy) -> None:
    job_id, _owner = await _owned_job(visibility="shared", status="running")
    cancel = await client.post(f"/jobs/{job_id}/cancel", headers={"X-Forwarded-User": "reader"})
    assert cancel.status_code == 404
    delete = await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": "reader"})
    assert delete.status_code == 404


class _User:
    def __init__(self, user_id: str, is_admin: bool = False) -> None:
        self.id = user_id
        self.is_admin = is_admin


@pytest.mark.asyncio
async def test_readable_job_ids_follows_read_rules(client: httpx.AsyncClient, proxy) -> None:
    from ai_almanac.server.services.job_access import readable_job_ids

    private_id, _ = await _owned_job(visibility="private")
    shared_id, _ = await _owned_job(visibility="shared")

    reader = _User("someone-else")
    readable = await readable_job_ids([private_id, shared_id, "missing"], reader)
    assert readable == {shared_id}

    admin = _User("admin-user", is_admin=True)
    readable = await readable_job_ids([private_id, shared_id], admin)
    assert readable == {private_id, shared_id}
