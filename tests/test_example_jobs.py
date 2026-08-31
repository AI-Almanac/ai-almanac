"""Example jobs: admin-promoted results every user sees, hidden (not deleted) per user."""

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
    job_type: str = "benchmark",
    run_id: str | None = None,
) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, external_id, created_at) VALUES (?, ?, ?)",
            (user_id, external_id, "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO jobs (id, user_id, dataset_id, status, visibility, job_type, run_id, "
            "created_at) VALUES (?, ?, 'ds', ?, ?, ?, ?, '2026-01-01T00:00:00')",
            (job_id, user_id, status, visibility, job_type, run_id),
        )
        conn.commit()


def _job_row(job_id: str) -> tuple | None:
    with sqlite3.connect(database_path()) as conn:
        return conn.execute("SELECT id, visibility FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _hidden_rows(job_id: str) -> list[tuple]:
    with sqlite3.connect(database_path()) as conn:
        return conn.execute(
            "SELECT user_id FROM user_hidden_jobs WHERE job_id = ?", (job_id,)
        ).fetchall()


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "boss")
    monkeypatch.setattr(settings, "admin_emails", "")


def _example_job(job_type: str = "benchmark", run_id: str | None = None) -> tuple[str, str]:
    owner_uid = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    if job_type == "benchmark" and run_id is None:
        run_id = str(uuid.uuid4())
    _insert(
        f"owner-{owner_uid}",
        owner_uid,
        job_id,
        visibility="example",
        job_type=job_type,
        run_id=run_id,
    )
    return job_id, f"owner-{owner_uid}"


@pytest.mark.asyncio
async def test_example_appears_in_every_users_lists(client: httpx.AsyncClient, proxy) -> None:
    bench_id, _ = _example_job("benchmark")
    blend_id, _ = _example_job("blend")
    forecast_id, _ = _example_job("forecast")
    headers = {"X-Forwarded-User": "newcomer"}

    jobs = await client.get("/jobs", headers=headers)
    assert bench_id in {j["id"] for j in jobs.json()}
    listed = next(j for j in jobs.json() if j["id"] == bench_id)
    assert listed["is_owner"] is False
    assert listed["visibility"] == "example"

    blends = await client.get("/blends", headers=headers)
    assert blend_id in {b["id"] for b in blends.json()}

    forecasts = await client.get("/forecasts", headers=headers)
    assert forecast_id in {f["id"] for f in forecasts.json()}


@pytest.mark.asyncio
async def test_private_and_shared_jobs_stay_out_of_other_lists(
    client: httpx.AsyncClient, proxy
) -> None:
    owner_uid = str(uuid.uuid4())
    private_id, shared_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert("someone", owner_uid, private_id, visibility="private", run_id=str(uuid.uuid4()))
    _insert("someone", owner_uid, shared_id, visibility="shared", run_id=str(uuid.uuid4()))

    jobs = await client.get("/jobs", headers={"X-Forwarded-User": "newcomer"})
    ids = {j["id"] for j in jobs.json()}
    assert private_id not in ids
    assert shared_id not in ids


@pytest.mark.asyncio
async def test_delete_example_hides_only_for_caller(client: httpx.AsyncClient, proxy) -> None:
    job_id, _ = _example_job("benchmark")

    resp = await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": "alice"})
    assert resp.status_code == 204
    assert _job_row(job_id) is not None

    alice_jobs = await client.get("/jobs", headers={"X-Forwarded-User": "alice"})
    assert job_id not in {j["id"] for j in alice_jobs.json()}

    bob_jobs = await client.get("/jobs", headers={"X-Forwarded-User": "bob"})
    assert job_id in {j["id"] for j in bob_jobs.json()}

    # Deleting again is a no-op, not an error.
    again = await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": "alice"})
    assert again.status_code == 204


@pytest.mark.asyncio
async def test_delete_example_hides_even_for_owner_and_admin(
    client: httpx.AsyncClient, proxy
) -> None:
    job_id, owner = _example_job("benchmark")

    resp = await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": owner})
    assert resp.status_code == 204
    assert _job_row(job_id) is not None

    resp = await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": "boss"})
    assert resp.status_code == 204
    assert _job_row(job_id) is not None


@pytest.mark.asyncio
async def test_demoted_example_hard_deletes_and_clears_hides(
    client: httpx.AsyncClient, proxy
) -> None:
    job_id, owner = _example_job("benchmark")
    await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": "alice"})
    assert _hidden_rows(job_id)

    demoted = await client.post(f"/jobs/{job_id}/unshare", headers={"X-Forwarded-User": owner})
    assert demoted.json()["visibility"] == "private"

    resp = await client.delete(f"/jobs/{job_id}", headers={"X-Forwarded-User": owner})
    assert resp.status_code == 204
    assert _job_row(job_id) is None
    assert _hidden_rows(job_id) == []


@pytest.mark.asyncio
async def test_promote_requires_admin_and_complete(client: httpx.AsyncClient, proxy) -> None:
    owner_uid = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    _insert("plain-user", owner_uid, job_id, run_id=str(uuid.uuid4()))

    resp = await client.post(f"/jobs/{job_id}/example", headers={"X-Forwarded-User": "plain-user"})
    assert resp.status_code == 403

    running_id = str(uuid.uuid4())
    _insert("plain-user", owner_uid, running_id, status="running", run_id=str(uuid.uuid4()))
    resp = await client.post(f"/jobs/{running_id}/example", headers={"X-Forwarded-User": "boss"})
    assert resp.status_code == 409

    resp = await client.post(f"/jobs/{job_id}/example", headers={"X-Forwarded-User": "boss"})
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "example"


@pytest.mark.asyncio
async def test_promote_covers_completed_run_group_siblings(
    client: httpx.AsyncClient, proxy
) -> None:
    owner_uid = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    first, sibling, failed = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    _insert("runner", owner_uid, first, run_id=run_id)
    _insert("runner", owner_uid, sibling, run_id=run_id)
    _insert("runner", owner_uid, failed, status="failed", run_id=run_id)
    # run_id is client-supplied: another user's job reusing it must not be
    # swept into the example when the run is promoted.
    interloper_job = str(uuid.uuid4())
    _insert("interloper", str(uuid.uuid4()), interloper_job, run_id=run_id)

    resp = await client.post(f"/jobs/{first}/example", headers={"X-Forwarded-User": "boss"})
    assert resp.status_code == 200
    assert _job_row(sibling)[1] == "example"
    assert _job_row(failed)[1] == "private"
    assert _job_row(interloper_job)[1] == "private"

    with sqlite3.connect(database_path()) as conn:
        audited = {
            row[0]
            for row in conn.execute(
                "SELECT resource_id FROM audit_events WHERE event_type = 'job.example'"
            )
        }
    assert {first, sibling} <= audited


@pytest.mark.asyncio
async def test_example_readable_by_non_owner(client: httpx.AsyncClient, proxy) -> None:
    job_id, _ = _example_job("benchmark")
    resp = await client.get(f"/jobs/{job_id}", headers={"X-Forwarded-User": "reader"})
    assert resp.status_code == 200
    assert resp.json()["is_owner"] is False


@pytest.mark.asyncio
async def test_example_blend_usable_as_parent(client: httpx.AsyncClient, proxy) -> None:
    from ai_almanac.server.services.job_submission import _resolve_parent_blend

    blend_id, _ = _example_job("blend")
    row = await _resolve_parent_blend(blend_id, "some-other-user-id")
    assert row["id"] == blend_id


# ---------------------------------------------------------------------------
# Anonymous viewing (no credential presented at all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_lists_examples_only(client: httpx.AsyncClient, proxy) -> None:
    bench_id, _ = _example_job("benchmark")
    blend_id, _ = _example_job("blend")
    forecast_id, _ = _example_job("forecast")
    owner_uid = str(uuid.uuid4())
    private_id, shared_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert("someone", owner_uid, private_id, visibility="private", run_id=str(uuid.uuid4()))
    _insert("someone", owner_uid, shared_id, visibility="shared", run_id=str(uuid.uuid4()))

    jobs = await client.get("/jobs")
    assert jobs.status_code == 200
    listed = {j["id"]: j for j in jobs.json()}
    assert bench_id in listed
    assert listed[bench_id]["is_owner"] is False
    assert private_id not in listed
    assert shared_id not in listed

    blends = await client.get("/blends")
    assert blend_id in {b["id"] for b in blends.json()}

    forecasts = await client.get("/forecasts")
    assert forecast_id in {f["id"] for f in forecasts.json()}


@pytest.mark.asyncio
async def test_anonymous_reads_example_but_not_private_or_shared(
    client: httpx.AsyncClient, proxy
) -> None:
    example_id, _ = _example_job("benchmark")
    owner_uid = str(uuid.uuid4())
    private_id, shared_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert("someone", owner_uid, private_id, visibility="private", run_id=str(uuid.uuid4()))
    _insert("someone", owner_uid, shared_id, visibility="shared", run_id=str(uuid.uuid4()))

    resp = await client.get(f"/jobs/{example_id}")
    assert resp.status_code == 200
    assert resp.json()["is_owner"] is False
    assert (await client.get(f"/jobs/{private_id}")).status_code == 404
    # 'shared' means shared with authenticated users, not the public.
    assert (await client.get(f"/jobs/{shared_id}")).status_code == 404


@pytest.mark.asyncio
async def test_anonymous_mutations_rejected(client: httpx.AsyncClient, proxy) -> None:
    example_id, _ = _example_job("benchmark")
    assert (await client.delete(f"/jobs/{example_id}")).status_code == 401
    assert (await client.post("/jobs", json={})).status_code == 401
    assert (await client.post(f"/jobs/{example_id}/example")).status_code == 401


@pytest.mark.asyncio
async def test_anonymous_gets_regions_and_me(client: httpx.AsyncClient, proxy) -> None:
    assert (await client.get("/regions")).status_code == 200

    me = await client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["anonymous"] is True
    assert body["id"] is None
    assert body["role"] == "user"
    assert body["capabilities"]["can_admin"] is False

    authed = await client.get("/auth/me", headers={"X-Forwarded-User": "somebody"})
    assert authed.json()["anonymous"] is False


@pytest.mark.asyncio
async def test_invalid_globus_token_still_401s(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A presented-but-bad credential must 401 (feeding the SPA's token
    refresh), never silently downgrade to the anonymous view."""
    from ai_almanac.server import auth as auth_module

    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(
        auth_module, "_introspect_globus_token", lambda token: {"active": False, "sub": None}
    )

    bad = await client.get("/jobs", headers={"Authorization": "Bearer expired-token"})
    assert bad.status_code == 401

    anonymous = await client.get("/jobs")
    assert anonymous.status_code == 200
