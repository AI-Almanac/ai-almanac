"""Durable local job lifecycle management."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from ai_almanac.server.db import get_db
from ai_almanac.server.services import trajectory_sets
from ai_almanac.server.services.storage import get_storage
from ai_almanac.server.sync_db import lock_capacity, sync_engine
from ai_almanac.server.tables import jobs
from ai_almanac.settings import settings

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "starting", "running", "canceling")
TERMINAL_STATUSES = ("complete", "failed", "canceled")
HEARTBEAT_TIMEOUT_SECONDS = 15


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _process_exists(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _heartbeat_is_fresh(value: str | None) -> bool:
    if not value:
        return False
    try:
        heartbeat = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (datetime.now(UTC) - heartbeat).total_seconds() <= HEARTBEAT_TIMEOUT_SECONDS


class LocalProcessProvisioner:
    """Launch one detached supervisor process per job."""

    def launch(self, job_id: str) -> None:
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [sys.executable, "-m", "ai_almanac", "execute-job", job_id],
            **kwargs,
        )


async def launch_job(job_id: str) -> None:
    await asyncio.to_thread(LocalProcessProvisioner().launch, job_id)


async def signal_cancel(job_id: str) -> dict | None:
    """Flag an active job for cancellation (the supervisor polls this flag).

    No authorization — callers (router, runner) own the access decision.
    Returns the job row, or None if the job does not exist.
    """
    async with get_db() as conn:
        row = (
            (await conn.execute(sa.select(jobs).where(jobs.c.id == job_id)))
            .mappings()
            .fetchone()
        )
        if not row:
            return None
        row = dict(row)
        if row["status"] in TERMINAL_STATUSES:
            return row
        result = await conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(status="canceling", cancel_requested_at=_now())
            .returning(jobs)
        )
        return dict(result.mappings().fetchone())


async def request_cancel(job_id: str, user_id: str) -> dict | None:
    """Cancel a job the user owns. Returns None if it is not theirs."""
    async with get_db() as conn:
        owned = (
            await conn.execute(
                sa.select(jobs.c.id).where(
                    jobs.c.id == job_id, jobs.c.user_id == user_id
                )
            )
        ).fetchone()
    if not owned:
        return None
    return await signal_cancel(job_id)


async def _finalize_remote_job(job_id: str, status: str, error: str | None) -> None:
    async with get_db() as conn:
        result = await conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id, jobs.c.status.in_(ACTIVE_STATUSES))
            .values(status=status, completed_at=_now(), error=error)
            .returning(jobs.c.config_json)
        )
        row = result.mappings().fetchone()
        if status != "complete" or row is None:
            return
        config = json.loads(row["config_json"] or "{}")
        if config.get("job_type") != "forecast":
            return
        # The Modal rollout populated the shared trajectory store; record
        # coverage so later runs score against it GPU-free. Bookkeeping only —
        # never fail an otherwise-successful forecast over it.
        try:
            await trajectory_sets.mark_coverage_from_config(conn, config)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to mark trajectory coverage for %s", job_id)


async def _modal_failure_log(job_id: str) -> str:
    """Build a failure message, enriched with the tail of the GCS run log."""
    try:
        log = await asyncio.to_thread(get_storage().read_log, job_id)
    except Exception:  # noqa: BLE001 — the log is best-effort enrichment
        return "Modal job failed."
    tail = "\n".join(log.strip().splitlines()[-20:])
    return f"Modal job failed.\n\nLast run log lines:\n{tail}" if tail else "Modal job failed."


async def _reconcile_modal_job(row: dict) -> None:
    """Track a Modal-backed job by polling the runner (no local supervisor)."""
    handle_data = row.get("runner_handle")
    if not handle_data:
        return  # submitted but no handle persisted yet; pick it up next tick

    from ai_almanac.server.services.execution import RunnerHandle
    from ai_almanac.server.services.modal_runner import get_modal_runner

    handle = RunnerHandle.from_dict(handle_data)
    runner = get_modal_runner()

    if row["status"] == "canceling":
        try:
            await runner.cancel(handle)
        except Exception:  # noqa: BLE001 — finalize regardless of cancel outcome
            logger.exception("Failed to cancel Modal job %s", row["id"])
        await _finalize_remote_job(row["id"], "canceled", None)
        return

    snapshot = await runner.inspect(handle)
    if snapshot.status == "complete":
        await _finalize_remote_job(row["id"], "complete", None)
    elif snapshot.status == "failed":
        await _finalize_remote_job(row["id"], "failed", await _modal_failure_log(row["id"]))
    # running/unknown: leave the job active for the next reconcile pass


async def reconcile_jobs() -> None:
    """Recover queued jobs and finalize supervisors that disappeared."""
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.select(
                        jobs.c.id,
                        jobs.c.status,
                        jobs.c.runner,
                        jobs.c.runner_handle,
                        jobs.c.worker_pid,
                        jobs.c.workload_pid,
                        jobs.c.process_group_id,
                        jobs.c.heartbeat_at,
                    ).where(jobs.c.status.in_(ACTIVE_STATUSES))
                )
            )
            .mappings()
            .fetchall()
        )

    for raw in rows:
        row = dict(raw)
        # Remote runners have no local supervisor; reconcile them from the
        # backend instead of process liveness.
        if row.get("runner") == "modal":
            await _reconcile_modal_job(row)
            continue
        if row["status"] == "queued":
            if _process_exists(row.get("worker_pid")) and _heartbeat_is_fresh(
                row.get("heartbeat_at")
            ):
                continue
            async with get_db() as conn:
                await conn.execute(
                    sa.update(jobs)
                    .where(jobs.c.id == row["id"], jobs.c.status == "queued")
                    .values(worker_id=None, worker_pid=None, heartbeat_at=None)
                )
            await launch_job(row["id"])
            continue
        if _process_exists(row.get("worker_pid")) and _heartbeat_is_fresh(
            row.get("heartbeat_at")
        ):
            continue
        _terminate_process_group(row.get("process_group_id"), row.get("workload_pid"))
        final_status = "canceled" if row["status"] == "canceling" else "failed"
        error = None if final_status == "canceled" else "Job supervisor exited unexpectedly."
        async with get_db() as conn:
            await conn.execute(
                sa.update(jobs)
                .where(
                    jobs.c.id == row["id"],
                    jobs.c.status.in_(("starting", "running", "canceling")),
                )
                .values(status=final_status, completed_at=_now(), error=error)
            )


def _register_supervisor(engine, job_id: str, worker_id: str) -> bool:
    with engine.begin() as conn:
        lock_capacity(conn)
        row = (
            conn.execute(
                sa.select(
                    jobs.c.status,
                    jobs.c.worker_id,
                    jobs.c.worker_pid,
                    jobs.c.heartbeat_at,
                ).where(jobs.c.id == job_id)
            )
            .mappings()
            .fetchone()
        )
        if not row or row["status"] not in ("queued", "canceling"):
            return False
        if row["status"] == "canceling":
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="canceled", completed_at=_now())
            )
            return False
        if (
            row["worker_id"]
            and row["worker_id"] != worker_id
            and _process_exists(row["worker_pid"])
            and _heartbeat_is_fresh(row["heartbeat_at"])
        ):
            return False
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(worker_id=worker_id, worker_pid=os.getpid(), heartbeat_at=_now())
        )
        return True


def _claim_capacity(engine, job_id: str, worker_id: str) -> bool:
    with engine.begin() as conn:
        lock_capacity(conn)
        row = (
            conn.execute(
                sa.select(jobs.c.status, jobs.c.worker_id).where(jobs.c.id == job_id)
            )
            .mappings()
            .fetchone()
        )
        if not row or row["status"] not in ("queued", "canceling"):
            return False
        if row["status"] == "canceling":
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="canceled", completed_at=_now())
            )
            return False
        if row["worker_id"] != worker_id:
            return False
        # Only jobs whose supervisor is still heartbeating consume capacity;
        # a crashed job must not block the queue until reconciliation runs.
        active_rows = conn.execute(
            sa.select(jobs.c.heartbeat_at).where(
                jobs.c.status.in_(("starting", "running")), jobs.c.id != job_id
            )
        ).fetchall()
        active = sum(1 for (heartbeat,) in active_rows if _heartbeat_is_fresh(heartbeat))
        if active >= settings.max_local_jobs:
            return False
        now = _now()
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(
                status="starting",
                worker_id=worker_id,
                worker_pid=os.getpid(),
                heartbeat_at=now,
                started_at=sa.func.coalesce(jobs.c.started_at, now),
            )
        )
        return True


def _heartbeat(engine, job_id: str, worker_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id, jobs.c.worker_id == worker_id)
            .values(heartbeat_at=_now())
        )


def _cancel_requested(engine, job_id: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            sa.select(jobs.c.cancel_requested_at).where(jobs.c.id == job_id)
        ).fetchone()
    return bool(row and row[0])


def execute_job(job_id: str) -> None:
    """Supervisor entry point. Runs independently from the API server."""
    worker_id = str(uuid.uuid4())
    engine = sync_engine()
    try:
        if not _register_supervisor(engine, job_id, worker_id):
            return
        while not _claim_capacity(engine, job_id, worker_id):
            with engine.begin() as conn:
                row = conn.execute(
                    sa.select(jobs.c.status).where(jobs.c.id == job_id)
                ).fetchone()
                if not row or row[0] != "queued":
                    return
                conn.execute(
                    sa.update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.worker_id == worker_id)
                    .values(heartbeat_at=_now())
                )
            time.sleep(1)

        log_path = get_storage().log_path(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        workload = _start_workload(job_id, log_path)
        pgid = workload.pid
        with engine.begin() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id, jobs.c.worker_id == worker_id)
                .values(
                    status="running",
                    workload_pid=workload.pid,
                    process_group_id=pgid,
                    heartbeat_at=_now(),
                )
            )

        canceled = False
        while workload.poll() is None:
            if _cancel_requested(engine, job_id):
                canceled = True
                _terminate_process_group(pgid, workload.pid)
                break
            _heartbeat(engine, job_id, worker_id)
            time.sleep(1)

        try:
            exit_code = workload.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_process_group(pgid, workload.pid)
            exit_code = workload.wait()

        if canceled:
            status, error = "canceled", None
        elif exit_code == 0:
            status, error = "complete", None
        else:
            status = "failed"
            error = f"Job workload exited with code {exit_code}; see {log_path}."
        now = _now()
        with engine.begin() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id, jobs.c.worker_id == worker_id)
                .values(
                    status=status,
                    completed_at=now,
                    heartbeat_at=now,
                    exit_code=exit_code,
                    error=error,
                )
            )
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    status="failed",
                    completed_at=_now(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        raise


def _start_workload(job_id: str, log_path: Path) -> subprocess.Popen:
    log_file = log_path.open("w")
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [sys.executable, "-m", "ai_almanac", "run-job-workload", job_id],
        **kwargs,
    )
    log_file.close()
    return process


def _terminate_process_group(pgid: int | None, pid: int | None) -> None:
    try:
        if os.name == "nt":
            if pid:
                os.kill(pid, signal.SIGTERM)
        elif pgid:
            os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_process_group(pgid: int | None, pid: int | None) -> None:
    try:
        if os.name == "nt":
            if pid:
                os.kill(pid, signal.SIGTERM)
        elif pgid:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
