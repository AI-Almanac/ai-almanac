"""Phase 5 — JobRunner contract and the LocalProcessRunner."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import httpx
import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services.execution import (
    ExecutionRequest,
    RunnerHandle,
)
from ai_almanac.server.services.local_runner import LocalProcessRunner


def _insert_job(job_id: str, status: str = "running") -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, external_id, created_at) "
            "VALUES ('runner-u', 'runner-u', '2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO jobs (id, user_id, dataset_id, status, created_at) "
            "VALUES (?, 'runner-u', 'ds', ?, '2026-01-01T00:00:00')",
            (job_id, status),
        )
        conn.commit()


def test_runner_handle_roundtrips() -> None:
    handle = RunnerHandle(runner="local", external_id="j1", metadata={"pgid": 42})
    assert RunnerHandle.from_dict(handle.as_dict()) == handle


@pytest.mark.asyncio
async def test_submit_launches_and_returns_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: dict[str, str] = {}

    async def fake_launch(job_id: str) -> None:
        launched["job_id"] = job_id

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    handle = await LocalProcessRunner().submit(
        ExecutionRequest(job_id="job-x", workspace=Path("/tmp"), bundle_path=Path("/tmp"))
    )
    assert launched["job_id"] == "job-x"
    assert handle.runner == "local"
    assert handle.external_id == "job-x"


@pytest.mark.asyncio
async def test_inspect_reports_durable_status(client: httpx.AsyncClient) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="running")
    snapshot = await LocalProcessRunner().inspect(RunnerHandle(runner="local", external_id=job_id))
    assert snapshot.status == "running"


@pytest.mark.asyncio
async def test_inspect_unknown_for_missing_job(client: httpx.AsyncClient) -> None:
    snapshot = await LocalProcessRunner().inspect(RunnerHandle(runner="local", external_id="ghost"))
    assert snapshot.status == "unknown"


@pytest.mark.asyncio
async def test_cancel_flags_active_job(client: httpx.AsyncClient) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="running")
    await LocalProcessRunner().cancel(RunnerHandle(runner="local", external_id=job_id))
    with sqlite3.connect(database_path()) as conn:
        status, cancel_at = conn.execute(
            "SELECT status, cancel_requested_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    assert status == "canceling"
    assert cancel_at is not None
