from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _insert_job(user_id: str, status: str, **values) -> str:
    from ai_almanac.server.db import get_db

    job_id = values.pop("job_id", f"job-{status}-{datetime.now(UTC).timestamp()}")
    config = json.dumps(values.get("config", {}))
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs "
                "(id, user_id, dataset_id, status, config_json, created_at, "
                "worker_pid, workload_pid, process_group_id) "
                "VALUES (:id, :uid, 'source', :status, :config, :now, :worker_pid, "
                ":workload_pid, :process_group_id)"
            ),
            {
                "id": job_id,
                "uid": user_id,
                "status": status,
                "config": config,
                "now": _now(),
                "worker_pid": values.get("worker_pid"),
                "workload_pid": values.get("workload_pid"),
                "process_group_id": values.get("process_group_id"),
            },
        )
    return job_id


@pytest.mark.asyncio
async def test_cancel_job_persists_requested_state(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    user_id: str,
) -> None:
    job_id = await _insert_job(user_id, "running")

    response = await client.post(f"/jobs/{job_id}/cancel", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "canceling"
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT status, cancel_requested_at FROM jobs WHERE id = :id"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "canceling"
    assert row["cancel_requested_at"] is not None


@pytest.mark.asyncio
async def test_reconcile_relaunches_queued_jobs(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _insert_job(user_id, "queued")
    launched: list[str] = []

    async def fake_launch(candidate: str) -> None:
        launched.append(candidate)

    monkeypatch.setattr(job_manager, "launch_job", fake_launch)
    await job_manager.reconcile_jobs()

    assert job_id in launched


@pytest.mark.asyncio
async def test_reconcile_marks_missing_supervisor_failed(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _insert_job(user_id, "running", worker_pid=999_999_999)
    monkeypatch.setattr(job_manager, "_process_exists", lambda pid: False)
    await job_manager.reconcile_jobs()

    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status, error FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "failed"
    assert row["error"] == "Job supervisor exited unexpectedly."


@pytest.mark.asyncio
async def test_supervisor_runs_stub_workload_to_completion(user_id: str) -> None:
    from ai_almanac.server.services.job_manager import execute_job

    job_id = await _insert_job(
        user_id,
        "queued",
        config={"model_name": "supervisor-test"},
    )
    await asyncio.to_thread(execute_job, job_id)

    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status, exit_code FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "complete"
    assert row["exit_code"] == 0


@pytest.mark.asyncio
async def test_supervisor_cancels_running_workload(user_id: str) -> None:
    from ai_almanac.server.services.job_manager import execute_job, request_cancel

    job_id = await _insert_job(
        user_id,
        "queued",
        config={"model_name": "cancel-test"},
    )
    supervisor = asyncio.create_task(asyncio.to_thread(execute_job, job_id))

    from ai_almanac.server.db import get_db

    for _ in range(50):
        async with get_db() as conn:
            status = (
                await conn.execute(
                    text("SELECT status FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            ).scalar_one()
        if status == "running":
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("job did not enter running state")

    await request_cancel(job_id, user_id)
    await asyncio.wait_for(supervisor, timeout=15)

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "canceled"
