"""Starting follow-ups: each ready one becomes a chat turn once its chat is free.

The job reconciler calls :func:`start_ready` on every tick. Follow-ups in one chat
that are ready together share one turn, and a turn the model's API failed is tried
once more before the follow-up counts as failed. Queueing, listing and cancelling
follow-ups live in :mod:`follow_ups`, which the assistant's tools reach without
importing the chat machinery this module runs turns with.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa

from ai_almanac.server import tables
from ai_almanac.server.auth import stored_user_is_admin
from ai_almanac.server.db import get_db
from ai_almanac.server.tables import json_col

from . import chat_turns
from .chat_job_events import prompt_safe
from .chat_state import parse_llm_event, turn_in_flight, unprocessed_tool_calls
from .follow_ups import REPORT_INSTRUCTION
from .job_manager import TERMINAL_STATUSES
from .llm import deserialize_model_messages
from .workspace_display import job_name

logger = logging.getLogger(__name__)

_ERROR_MAX_CHARS = 300
# Longer than a claimed follow-up takes to start streaming its turn, retry included.
ABANDONED_AFTER = timedelta(minutes=10)
# Long enough for a network that dropped, e.g. as the computer woke, to come back.
RETRY_AFTER_SECONDS = 60
_running: set[asyncio.Task] = set()


def is_ready(jobs: list[dict]) -> bool:
    """Every job is terminal, and every completed one has its outputs published."""
    return bool(jobs) and all(
        job["status"] in TERMINAL_STATUSES
        and (job["status"] != "complete" or job["artifacts_published_at"])
        for job in jobs
    )


def continuation_message(instructions: list[str], jobs: list[dict], deleted: int = 0) -> str:
    # Sent as a user-role turn, so it says who wrote it, as platform_note does.
    lines = [
        "[Platform update — written by the platform, not the user] "
        "The runs you queued work on have finished:"
    ]
    for job in jobs:
        line = f'- {job["job_type"]} "{job["name"]}": {job["status"]}'
        if job["status"] == "failed" and job.get("error"):
            line += f" ({_error_line(job['error'])})"
        lines.append(line)
    if deleted:
        lines.append(f"- {deleted} of them {'was' if deleted == 1 else 'were'} deleted")
    lines += ["", *_carry_on(instructions)]
    lines.append(
        "If a run did not complete, explain what happened, in one reply, instead of carrying on."
    )
    return "\n".join(lines)


def _error_line(error: str) -> str:
    """A run's error as one short line: it can quote text from a dataset or a path."""
    first = error.strip().splitlines()[0] if error.strip() else ""
    return re.sub(r"\s+", " ", first)[:_ERROR_MAX_CHARS]


def instructions_to_carry_on(rows: list[dict]) -> list[str]:
    """What a turn is asked to do, each thing once.

    A run's default report is left out when the assistant queued work of its own on
    the same runs, which already says what to do with the results.
    """
    asked_for = [
        set(json_col(row["job_ids"], []))
        for row in rows
        if row["instruction"] != REPORT_INSTRUCTION
    ]

    def covered(row: dict) -> bool:
        return row["instruction"] == REPORT_INSTRUCTION and any(
            set(json_col(row["job_ids"], [])) <= jobs for jobs in asked_for
        )

    return list(dict.fromkeys(row["instruction"] for row in rows if not covered(row)))


def _carry_on(instructions: list[str]) -> list[str]:
    if len(instructions) == 1:
        return [f"Carry on with what you queued: {instructions[0]}"]
    return [
        "Carry on with what you queued, in order:",
        *(f"{n}. {instruction}" for n, instruction in enumerate(instructions, 1)),
    ]


def awaiting_input(transcript: list[dict], provider_state: list, last_active: object) -> bool:
    """A turn is streaming, or a step the assistant asked about still waits on the user."""
    return turn_in_flight(transcript, last_active) or bool(unprocessed_tool_calls(provider_state))


async def _jobs(conn, job_ids: list[str]) -> list[dict]:
    """Each job's state, named as the user knows it: the turn they start is told
    names, never ids, and finds a run's id in the workspace's run list."""
    jobs = tables.jobs
    rows = await conn.execute(
        sa.select(
            jobs.c.id,
            jobs.c.job_type,
            jobs.c.config_json,
            jobs.c.status,
            jobs.c.error,
            jobs.c.artifacts_published_at,
        ).where(jobs.c.id.in_(job_ids))
    )
    return [
        {
            **row,
            "name": prompt_safe(job_name(json_col(row["config_json"], {})) or ""),
        }
        for row in rows.mappings()
    ]


ChatStatus = Literal["gone", "busy", "idle"]


async def _chat_status(conn, session_id: str) -> ChatStatus:
    """Whether a follow-up's chat was deleted, is busy with the user, or can take a turn."""
    row = (
        (
            await conn.execute(
                sa.text(
                    "SELECT transcript, provider_state, updated_at FROM chat_sessions "
                    "WHERE id = :id"
                ),
                {"id": session_id},
            )
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return "gone"
    busy = awaiting_input(
        json_col(row["transcript"], []),
        deserialize_model_messages(row["provider_state"]),
        row["updated_at"],
    )
    return "busy" if busy else "idle"


async def _claim(conn, follow_up_id: str) -> bool:
    follow_ups = tables.follow_ups
    result = await conn.execute(
        sa.update(follow_ups)
        .where(follow_ups.c.id == follow_up_id, follow_ups.c.status == "waiting")
        .values(status="running", started_at=_now())
    )
    return result.rowcount == 1


async def _cancel_waiting(conn, follow_up_ids: list[str]) -> None:
    """Drop follow-ups with nothing left to continue: their chat, or every run they wait
    on, was deleted."""
    follow_ups = tables.follow_ups
    await conn.execute(
        sa.update(follow_ups)
        .where(follow_ups.c.id.in_(follow_up_ids), follow_ups.c.status == "waiting")
        .values(status="canceled", completed_at=_now())
    )


@dataclass(frozen=True)
class Batch:
    """Ready follow-ups of one chat, started as a single turn."""

    session_id: str
    user_id: str
    follow_up_ids: list[str]
    message: str

    @property
    def trigger(self) -> str:
        return f"follow_up:{','.join(self.follow_up_ids)}"


def batch(rows: list[dict], jobs_by_id: dict[str, dict]) -> Batch:
    """One turn for a chat's ready follow-ups, in the order they were queued. A run
    deleted since it was queued is only counted: there is nothing left to report on."""
    rows = sorted(rows, key=lambda row: row["created_at"])
    job_ids = list(dict.fromkeys(j for row in rows for j in json_col(row["job_ids"], [])))
    remaining = [jobs_by_id[j] for j in job_ids if j in jobs_by_id]
    return Batch(
        session_id=rows[0]["session_id"],
        user_id=rows[0]["user_id"],
        follow_up_ids=[row["id"] for row in rows],
        message=continuation_message(
            instructions_to_carry_on(rows), remaining, deleted=len(job_ids) - len(remaining)
        ),
    )


async def start_ready() -> None:
    """Start every waiting follow-up whose jobs are done and whose chat is idle.

    A chat mid-turn or waiting on an approval is left for a later tick, so a
    follow-up never talks over the user or silently denies their pending step.
    """
    follow_ups = tables.follow_ups
    await fail_abandoned()
    async with get_db() as conn:
        waiting = (
            (await conn.execute(sa.select(follow_ups).where(follow_ups.c.status == "waiting")))
            .mappings()
            .fetchall()
        )
        ready: dict[str, list[dict]] = {}
        jobs_by_id: dict[str, dict] = {}
        all_deleted: list[str] = []
        for row in waiting:
            jobs = await _jobs(conn, json_col(row["job_ids"], []))
            if not jobs:
                all_deleted.append(row["id"])
            elif is_ready(jobs):
                ready.setdefault(row["session_id"], []).append(dict(row))
                jobs_by_id.update((job["id"], job) for job in jobs)
        if all_deleted:
            await _cancel_waiting(conn, all_deleted)
        batches = []
        for session_id, rows in ready.items():
            # One chat's follow-ups failing to start must not hold up every other chat's.
            try:
                async with conn.begin_nested():
                    started = await _start_chat(conn, session_id, rows, jobs_by_id)
            except Exception:
                logger.exception("Could not start a chat's follow-ups")
                continue
            if started is not None:
                batches.append(started)
    for each in batches:
        task = asyncio.create_task(_run(each))
        _running.add(task)
        task.add_done_callback(_running.discard)


async def _start_chat(
    conn, session_id: str, rows: list[dict], jobs_by_id: dict[str, dict]
) -> Batch | None:
    """Claim a chat's ready follow-ups as one batch; cancel them if the chat is gone."""
    status = await _chat_status(conn, session_id)
    if status == "gone":
        await _cancel_waiting(conn, [row["id"] for row in rows])
        return None
    if status != "idle":
        return None
    claimed = [row for row in rows if await _claim(conn, row["id"])]
    return batch(claimed, jobs_by_id) if claimed else None


async def fail_abandoned(now: datetime | None = None) -> None:
    """Fail running follow-ups whose turn died, e.g. when the server restarted mid-turn.

    A live follow-up keeps its chat streaming, on whichever instance runs it. One
    claimed a while ago whose chat has gone idle lost its task and would otherwise
    read as running for good.
    """
    follow_ups = tables.follow_ups
    cutoff = ((now or datetime.now(UTC)) - ABANDONED_AFTER).isoformat()
    async with get_db() as conn:
        stale = (
            await conn.execute(
                sa.select(follow_ups.c.id, follow_ups.c.session_id).where(
                    follow_ups.c.status == "running", follow_ups.c.started_at < cutoff
                )
            )
        ).fetchall()
        for follow_up_id, session_id in stale:
            if await _chat_status(conn, session_id) == "busy":
                continue
            await conn.execute(
                sa.update(follow_ups)
                .where(follow_ups.c.id == follow_up_id, follow_ups.c.status == "running")
                .values(status="failed", completed_at=_now())
            )
            logger.warning("Follow-up %s was abandoned mid-turn; marked failed", follow_up_id)


async def _owner_is_admin(user_id: str) -> bool:
    """Whether the chat's owner is an admin, so a follow-up sees the rulesets they do."""
    async with get_db() as conn:
        row = (
            (await conn.execute(sa.select(tables.users).where(tables.users.c.id == user_id)))
            .mappings()
            .fetchone()
        )
    return row is not None and stored_user_is_admin(row)


async def _run(batch: Batch) -> None:
    outcome = await _turn_outcome(batch)
    if outcome == "provider_error":
        await asyncio.sleep(RETRY_AFTER_SECONDS)
        if await chat_turns.discard_failed_exchange(batch.session_id, batch.trigger):
            outcome = await _turn_outcome(batch)
    async with get_db() as conn:
        await conn.execute(
            sa.update(tables.follow_ups)
            .where(tables.follow_ups.c.id.in_(batch.follow_up_ids))
            .values(status="done" if outcome == "done" else "failed", completed_at=_now())
        )


async def _turn_outcome(batch: Batch) -> str:
    """``done``, the error type the turn ended with, or ``failed`` if it never ended."""
    outcome = "failed"
    try:
        async for event in chat_turns.stream_chat_turn(
            batch.session_id,
            batch.user_id,
            batch.message,
            None,
            for_admin=await _owner_is_admin(batch.user_id),
            trigger=batch.trigger,
        ):
            data = parse_llm_event(event.removeprefix("data: "))
            if data and data.get("type") == "done":
                outcome = "done"
            elif data and data.get("type") == "error":
                outcome = data.get("error_type") or "failed"
    except Exception:
        logger.exception("Follow-up turn %s failed", batch.trigger)
    return outcome


def _now() -> str:
    return datetime.now(UTC).isoformat()
