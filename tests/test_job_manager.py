from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _no_detached_supervisors(monkeypatch: pytest.MonkeyPatch) -> None:
    """reconcile_jobs relaunches queued rows; a real launch spawns a detached
    supervisor that outlives the test. Tests that care about launches patch over this."""
    from ai_almanac.server.services import job_manager

    async def no_launch(_job_id: str) -> None:
        pass

    monkeypatch.setattr(job_manager, "launch_job", no_launch)


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
                "worker_pid, workload_pid, process_group_id, heartbeat_at) "
                "VALUES (:id, :uid, 'source', :status, :config, :now, :worker_pid, "
                ":workload_pid, :process_group_id, :heartbeat_at)"
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
                "heartbeat_at": values.get("heartbeat_at"),
            },
        )
    return job_id


async def _job_row(job_id: str) -> dict:
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (await conn.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}))
            .mappings()
            .one()
        )
    return dict(row)


@pytest.mark.asyncio
async def test_cancel_job_persists_requested_state(
    client: httpx.AsyncClient,
    as_new_user: dict[str, str],
    user_id: str,
) -> None:
    job_id = await _insert_job(user_id, "running")

    response = await client.post(f"/jobs/{job_id}/cancel", headers=as_new_user)

    assert response.status_code == 200
    assert response.json()["status"] == "canceling"
    persisted = await client.get(f"/jobs/{job_id}", headers=as_new_user)
    assert persisted.json()["status"] == "canceling"


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


@pytest.mark.parametrize(
    ("status", "supervisor_alive", "final_status", "error"),
    [
        ("running", False, "failed", "Job supervisor exited unexpectedly."),
        # A live supervisor pid is not enough — a stale heartbeat means hung.
        ("running", True, "failed", "Job supervisor exited unexpectedly."),
        ("canceling", False, "canceled", None),
    ],
    ids=["supervisor-gone", "supervisor-hung", "canceling-supervisor-gone"],
)
@pytest.mark.asyncio
async def test_reconcile_finalizes_jobs_whose_supervisor_is_dead(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    supervisor_alive: bool,
    final_status: str,
    error: str | None,
) -> None:
    import os
    from datetime import timedelta

    from ai_almanac.server.services import job_manager

    if supervisor_alive:
        stale = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        job_id = await _insert_job(user_id, status, worker_pid=os.getpid(), heartbeat_at=stale)
    else:
        job_id = await _insert_job(user_id, status, worker_pid=999_999_999)
        monkeypatch.setattr(job_manager, "_process_exists", lambda pid: False)

    await job_manager.reconcile_jobs()

    row = await _job_row(job_id)
    assert row["status"] == final_status
    assert row["error"] == error


@pytest.mark.asyncio
async def test_reconcile_leaves_healthy_jobs_alone(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from ai_almanac.server.services import job_manager

    launched: list[str] = []

    async def fake_launch(candidate: str) -> None:
        launched.append(candidate)

    monkeypatch.setattr(job_manager, "launch_job", fake_launch)
    running_id = await _insert_job(user_id, "running", worker_pid=os.getpid(), heartbeat_at=_now())
    queued_id = await _insert_job(user_id, "queued", worker_pid=os.getpid(), heartbeat_at=_now())

    await job_manager.reconcile_jobs()

    assert (await _job_row(running_id))["status"] == "running"
    queued = await _job_row(queued_id)
    assert queued["status"] == "queued"
    assert queued["worker_pid"] is not None  # not reset for relaunch
    assert launched == []


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


def _start_unending_workload(_job_id: str, _log_path: Path) -> subprocess.Popen:
    """A workload that only stops when signalled. The stub workload finishes in
    about two supervisor polls, so a cancel that lands after the first poll would
    race its natural exit and the job would end ``complete``."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        start_new_session=True,
    )


@pytest.mark.asyncio
async def test_supervisor_cancels_running_workload(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server.services import job_manager

    workloads: list[subprocess.Popen] = []
    loop = asyncio.get_running_loop()
    workload_started = asyncio.Event()

    def start_workload(job_id: str, log_path: Path) -> subprocess.Popen:
        workloads.append(_start_unending_workload(job_id, log_path))
        loop.call_soon_threadsafe(workload_started.set)
        return workloads[-1]

    monkeypatch.setattr(job_manager, "_start_workload", start_workload)
    job_id = await _insert_job(user_id, "queued", config={"model_name": "cancel-test"})
    supervisor = asyncio.create_task(asyncio.to_thread(job_manager.execute_job, job_id))
    try:
        # Once the workload is up, the supervisor stops it as soon as it sees
        # cancel_requested_at, even if its own "running" write lands after the
        # cancel's "canceling". Racing the supervisor task fails fast, with its
        # own error, if it exits without starting the workload.
        started = asyncio.create_task(workload_started.wait())
        await asyncio.wait({started, supervisor}, timeout=60, return_when=asyncio.FIRST_COMPLETED)
        started.cancel()
        if not workload_started.is_set():
            if supervisor.done():
                supervisor.result()
            pytest.fail("supervisor did not start the workload")

        await job_manager.request_cancel(job_id, user_id)
        await asyncio.wait_for(supervisor, timeout=15)

        assert (await _job_row(job_id))["status"] == "canceled"
        assert workloads[0].poll() is not None
    finally:
        for workload in workloads:
            workload.kill()


# ---------------------------------------------------------------------------
# Modal-backed job reconciliation (no local supervisor)
# ---------------------------------------------------------------------------


class _FakeModalRunner:
    def __init__(self, status: str) -> None:
        self._status = status
        self.canceled = False

    async def inspect(self, handle):
        from ai_almanac.server.services.execution import ExecutionSnapshot

        return ExecutionSnapshot(status=self._status)

    async def cancel(self, handle):
        self.canceled = True


async def _make_modal_job(user_id: str, status: str) -> str:
    import sqlalchemy as sa

    from ai_almanac.server.db import get_db
    from ai_almanac.server.tables import jobs

    job_id = await _insert_job(user_id, status)
    async with get_db() as conn:
        await conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(
                runner="modal",
                runner_handle={"runner": "modal", "external_id": "fc-1", "metadata": {}},
            )
        )
    return job_id


def _patch_modal_runner(monkeypatch, runner) -> None:
    from ai_almanac.server.services import modal_runner

    monkeypatch.setattr(modal_runner, "get_modal_runner", lambda: runner)


@pytest.mark.parametrize(
    ("status", "remote_status", "final_status"),
    [
        ("running", "complete", "complete"),
        ("running", "failed", "failed"),
        ("running", "running", "running"),
        ("running", "unknown", "running"),
        ("canceling", "running", "canceled"),
    ],
)
@pytest.mark.asyncio
async def test_reconcile_follows_the_modal_job(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    remote_status: str,
    final_status: str,
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _make_modal_job(user_id, status)
    runner = _FakeModalRunner(remote_status)
    _patch_modal_runner(monkeypatch, runner)

    await job_manager.reconcile_jobs()

    row = await _job_row(job_id)
    assert row["status"] == final_status
    assert runner.canceled is (status == "canceling")
    assert (row["completed_at"] is not None) is (final_status != "running")
    assert bool(row["error"]) is (final_status == "failed")


@pytest.mark.asyncio
async def test_reconcile_escalates_a_job_whose_modal_status_stays_unreadable(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from ai_almanac.server.services import job_manager

    job_id = await _make_modal_job(user_id, "running")
    _patch_modal_runner(monkeypatch, _FakeModalRunner("unknown"))
    monkeypatch.setattr(job_manager, "UNKNOWN_POLL_ESCALATION", 3)

    with caplog.at_level(logging.WARNING, logger=job_manager.logger.name):
        for _ in range(3):
            await job_manager.reconcile_jobs()

    levels = [record.levelno for record in caplog.records if job_id in record.getMessage()]
    assert levels == [logging.WARNING, logging.WARNING, logging.ERROR]
    assert (await _job_row(job_id))["status"] == "running"
