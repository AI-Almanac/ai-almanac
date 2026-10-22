"""Following a blend's day-level blend, which trains in its own Modal call.

modal/blending_app.py's run_blend spawns run_day_level_blend before it trains
the week-level blend, and records that call in DAY_LEVEL_CALL_MARKER under the
job's prefix. The job completes with the week-level blend; from then on this
module follows the day-level call to its end. When the week-level blend does not
complete, the day-level call is stopped. The marker is read for every outcome
because it is the one place the call id exists when run_blend fails or is
canceled.

Local runs train the day-level blend inside the job, so they leave the
day-level columns NULL and their outputs are indexed with the job's own.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import sqlalchemy as sa

from ai_almanac.server.db import get_db
from ai_almanac.server.services.execution import RunnerHandle
from ai_almanac.server.services.storage import get_storage
from ai_almanac.server.tables import jobs

logger = logging.getLogger(__name__)

# Written by modal/blending_app.py; the two must name the same objects.
DAY_LEVEL_CALL_MARKER = "work/day_level_call.json"
DAY_LEVEL_RUN_LOG = "day_level_run.log"

RUNNING, COMPLETE, FAILED, CANCELED = "running", "complete", "failed", "canceled"
ACTIVE_STATUSES = (RUNNING,)

NEVER_STARTED = "The day-level blend never started; the run log has the details."
STOPPED_WITH_JOB = "Stopped because the week-level blend did not complete."


def _now() -> str:
    return datetime.now(UTC).isoformat()


def trains_apart(config: dict) -> bool:
    """Whether a job's day-level blend trains in its own Modal call."""
    return config.get("job_type") == "blend" and bool(config.get("train_day_level_blend"))


def parse_call_marker(text: str) -> str | None:
    """The call id in a DAY_LEVEL_CALL_MARKER, or None for an absent or unreadable one."""
    if not text.strip():
        return None
    try:
        call_id = json.loads(text).get("call_id")
    except (ValueError, AttributeError):
        return None
    return call_id if isinstance(call_id, str) and call_id else None


def hand_off_values(weekly_status: str, call_id: str | None, now: str) -> dict | None:
    """The day-level columns to set once the week-level blend reaches
    weekly_status, or None when there is nothing to follow or stop."""
    if weekly_status == "complete":
        if call_id is None:
            return {
                "day_level_status": FAILED,
                "day_level_error": NEVER_STARTED,
                "day_level_completed_at": now,
            }
        return {"day_level_status": RUNNING, "day_level_call_id": call_id}
    if call_id is None:
        return None
    return {
        "day_level_status": CANCELED,
        "day_level_call_id": call_id,
        "day_level_error": STOPPED_WITH_JOB,
        "day_level_completed_at": now,
    }


def failure_message(log: str) -> str:
    tail = "\n".join(log.strip().splitlines()[-20:])
    if not tail:
        return "The day-level blend failed."
    return f"The day-level blend failed.\n\nLast run log lines:\n{tail}"


def _handle(call_id: str) -> RunnerHandle:
    return RunnerHandle(runner="modal", external_id=call_id, metadata={})


async def _read_job_text(job_id: str, name: str) -> str:
    try:
        return await asyncio.to_thread(get_storage().read_job_text, job_id, name)
    except Exception:  # noqa: BLE001 — an unreadable file reads as absent
        logger.exception("Could not read %s for job %s", name, job_id)
        return ""


async def _job_config(job_id: str) -> dict:
    async with get_db() as conn:
        row = (
            await conn.execute(sa.select(jobs.c.config_json).where(jobs.c.id == job_id))
        ).fetchone()
    return json.loads(row[0] or "{}") if row else {}


async def hand_off(job_id: str, weekly_status: str) -> None:
    """Follow the day-level call once the week-level blend completes, or stop it
    when the week-level blend failed or was canceled. Idempotent: only the first
    hand-off for a job writes."""
    from ai_almanac.server.services.modal_runner import get_modal_runner

    if not trains_apart(await _job_config(job_id)):
        return
    call_id = parse_call_marker(await _read_job_text(job_id, DAY_LEVEL_CALL_MARKER))
    values = hand_off_values(weekly_status, call_id, _now())
    if values is None:
        return
    if values["day_level_status"] == CANCELED:
        try:
            await get_modal_runner().cancel(_handle(call_id))
        except Exception:  # noqa: BLE001 — record the stop regardless
            logger.exception("Failed to cancel day-level call %s of job %s", call_id, job_id)
    async with get_db() as conn:
        await conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id, jobs.c.day_level_status.is_(None))
            .values(**values)
        )


async def _finish(job_id: str, status: str, error: str | None) -> None:
    async with get_db() as conn:
        await conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id, jobs.c.day_level_status.in_(ACTIVE_STATUSES))
            .values(day_level_status=status, day_level_error=error, day_level_completed_at=_now())
        )


async def reconcile_day_level_blends() -> None:
    """Record the end of every day-level call still training."""
    from ai_almanac.server.services.modal_runner import get_modal_runner

    async with get_db() as conn:
        rows = (
            await conn.execute(
                sa.select(jobs.c.id, jobs.c.day_level_call_id).where(
                    jobs.c.day_level_status.in_(ACTIVE_STATUSES)
                )
            )
        ).fetchall()
    runner = get_modal_runner()
    for job_id, call_id in rows:
        snapshot = await runner.inspect(_handle(call_id))
        if snapshot.status == "complete":
            await _finish(job_id, COMPLETE, None)
        elif snapshot.status == "failed":
            log = await _read_job_text(job_id, DAY_LEVEL_RUN_LOG)
            await _finish(job_id, FAILED, failure_message(log))
