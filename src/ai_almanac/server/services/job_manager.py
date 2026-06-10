"""Durable local job lifecycle management."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.storage import get_storage
from ai_almanac.server.sync_db import lock_capacity, sync_engine
from ai_almanac.settings import settings

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
            (
                await conn.execute(
                    text("SELECT * FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            return None
        row = dict(row)
        if row["status"] in TERMINAL_STATUSES:
            return row
        result = await conn.execute(
            text(
                "UPDATE jobs SET status = 'canceling', cancel_requested_at = :now "
                "WHERE id = :id RETURNING *"
            ),
            {"id": job_id, "now": _now()},
        )
        return dict(result.mappings().fetchone())


async def request_cancel(job_id: str, user_id: str) -> dict | None:
    """Cancel a job the user owns. Returns None if it is not theirs."""
    async with get_db() as conn:
        owned = (
            await conn.execute(
                text("SELECT id FROM jobs WHERE id = :id AND user_id = :uid"),
                {"id": job_id, "uid": user_id},
            )
        ).fetchone()
    if not owned:
        return None
    return await signal_cancel(job_id)


async def reconcile_jobs() -> None:
    """Recover queued jobs and finalize supervisors that disappeared."""
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, status, worker_pid, workload_pid, process_group_id "
                        ", heartbeat_at "
                        "FROM jobs WHERE status IN ('queued', 'starting', 'running', 'canceling')"
                    )
                )
            )
            .mappings()
            .fetchall()
        )

    for raw in rows:
        row = dict(raw)
        if row["status"] == "queued":
            if _process_exists(row.get("worker_pid")) and _heartbeat_is_fresh(
                row.get("heartbeat_at")
            ):
                continue
            async with get_db() as conn:
                await conn.execute(
                    text(
                        "UPDATE jobs SET worker_id = NULL, worker_pid = NULL, "
                        "heartbeat_at = NULL WHERE id = :id AND status = 'queued'"
                    ),
                    {"id": row["id"]},
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
                text(
                    "UPDATE jobs SET status = :status, completed_at = :now, error = :error "
                    "WHERE id = :id AND status IN ('starting', 'running', 'canceling')"
                ),
                {
                    "id": row["id"],
                    "status": final_status,
                    "now": _now(),
                    "error": error,
                },
            )


def _register_supervisor(engine, job_id: str, worker_id: str) -> bool:
    with engine.begin() as conn:
        lock_capacity(conn)
        row = (
            conn.execute(
                text(
                    "SELECT status, worker_id, worker_pid, heartbeat_at "
                    "FROM jobs WHERE id = :id"
                ),
                {"id": job_id},
            )
            .mappings()
            .fetchone()
        )
        if not row or row["status"] not in ("queued", "canceling"):
            return False
        if row["status"] == "canceling":
            conn.execute(
                text(
                    "UPDATE jobs SET status = 'canceled', completed_at = :now "
                    "WHERE id = :id"
                ),
                {"now": _now(), "id": job_id},
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
            text(
                "UPDATE jobs SET worker_id = :wid, worker_pid = :pid, "
                "heartbeat_at = :now WHERE id = :id"
            ),
            {"wid": worker_id, "pid": os.getpid(), "now": _now(), "id": job_id},
        )
        return True


def _claim_capacity(engine, job_id: str, worker_id: str) -> bool:
    with engine.begin() as conn:
        lock_capacity(conn)
        row = (
            conn.execute(
                text("SELECT status, worker_id FROM jobs WHERE id = :id"),
                {"id": job_id},
            )
            .mappings()
            .fetchone()
        )
        if not row or row["status"] not in ("queued", "canceling"):
            return False
        if row["status"] == "canceling":
            conn.execute(
                text(
                    "UPDATE jobs SET status = 'canceled', completed_at = :now "
                    "WHERE id = :id"
                ),
                {"now": _now(), "id": job_id},
            )
            return False
        if row["worker_id"] != worker_id:
            return False
        active = conn.execute(
            text(
                "SELECT COUNT(*) FROM jobs "
                "WHERE status IN ('starting', 'running') AND id != :id"
            ),
            {"id": job_id},
        ).scalar()
        if active >= settings.max_local_jobs:
            return False
        now = _now()
        conn.execute(
            text(
                "UPDATE jobs SET status = 'starting', worker_id = :wid, "
                "worker_pid = :pid, heartbeat_at = :now, "
                "started_at = COALESCE(started_at, :now) WHERE id = :id"
            ),
            {"wid": worker_id, "pid": os.getpid(), "now": now, "id": job_id},
        )
        return True


def _heartbeat(engine, job_id: str, worker_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET heartbeat_at = :now "
                "WHERE id = :id AND worker_id = :wid"
            ),
            {"now": _now(), "id": job_id, "wid": worker_id},
        )


def _cancel_requested(engine, job_id: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT cancel_requested_at FROM jobs WHERE id = :id"),
            {"id": job_id},
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
                    text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}
                ).fetchone()
                if not row or row[0] != "queued":
                    return
                conn.execute(
                    text(
                        "UPDATE jobs SET heartbeat_at = :now "
                        "WHERE id = :id AND worker_id = :wid"
                    ),
                    {"now": _now(), "id": job_id, "wid": worker_id},
                )
            time.sleep(1)

        log_path = get_storage().log_path(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        workload = _start_workload(job_id, log_path)
        pgid = workload.pid
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE jobs SET status = 'running', workload_pid = :wp, "
                    "process_group_id = :pg, heartbeat_at = :now "
                    "WHERE id = :id AND worker_id = :wid"
                ),
                {
                    "wp": workload.pid,
                    "pg": pgid,
                    "now": _now(),
                    "id": job_id,
                    "wid": worker_id,
                },
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
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE jobs SET status = :status, completed_at = :now, "
                    "heartbeat_at = :now, exit_code = :ec, error = :error "
                    "WHERE id = :id AND worker_id = :wid"
                ),
                {
                    "status": status,
                    "now": _now(),
                    "ec": exit_code,
                    "error": error,
                    "id": job_id,
                    "wid": worker_id,
                },
            )
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE jobs SET status = 'failed', completed_at = :now, "
                    "error = :error WHERE id = :id"
                ),
                {
                    "now": _now(),
                    "error": f"{type(exc).__name__}: {exc}",
                    "id": job_id,
                },
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
