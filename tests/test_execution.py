"""The JobRunner contract as implemented by the LocalProcessRunner."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

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


@pytest.mark.asyncio
async def test_a_submitted_job_is_tracked_by_its_durable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="queued")
    runner = LocalProcessRunner()

    handle = await runner.submit(
        ExecutionRequest(job_id=job_id, workspace=Path("/tmp"), bundle_path=Path("/tmp"))
    )

    assert (await runner.inspect(handle)).status == "queued"


@pytest.mark.asyncio
async def test_inspect_reports_durable_status() -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="running")
    snapshot = await LocalProcessRunner().inspect(RunnerHandle(runner="local", external_id=job_id))
    assert snapshot.status == "running"


@pytest.mark.asyncio
async def test_inspect_unknown_for_missing_job() -> None:
    snapshot = await LocalProcessRunner().inspect(RunnerHandle(runner="local", external_id="ghost"))
    assert snapshot.status == "unknown"


@pytest.mark.asyncio
async def test_cancel_flags_active_job() -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="running")
    await LocalProcessRunner().cancel(RunnerHandle(runner="local", external_id=job_id))
    with sqlite3.connect(database_path()) as conn:
        status, cancel_at = conn.execute(
            "SELECT status, cancel_requested_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    assert status == "canceling"
    assert cancel_at is not None
