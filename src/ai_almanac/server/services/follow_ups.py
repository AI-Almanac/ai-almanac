"""Assistant work queued in a workspace chat until the jobs it needs finish.

After submitting runs whose results it needs, the assistant queues a follow-up
instead of asking the user to report back. The job reconciler starts each
follow-up as a new chat turn once every job is terminal and each completed
job's outputs are registered in the workspace, so the turn can read them.
Follow-ups in one chat that are ready together share one turn, and a turn the
model's API failed is tried once more before the follow-up counts as failed.

A run submitted from a chat gets a follow-up without the assistant asking: a
report on how it went, one per submission, which the user can cancel like any
other.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
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
from .job_manager import TERMINAL_STATUSES
from .llm import deserialize_model_messages
from .workspace_display import job_name
from .workspaces import linked_job_ids_query

logger = logging.getLogger(__name__)

MAX_INSTRUCTION_CHARS = 2000
# Read by the user in the workspace's queue as well as by the assistant.
REPORT_INSTRUCTION = "Report how each finished run went: one sentence, then its headline numbers."
# Longer than a claimed follow-up takes to start streaming its turn, retry included.
ABANDONED_AFTER = timedelta(minutes=10)
# Long enough for a network that dropped, e.g. as the computer woke, to come back.
RETRY_AFTER_SECONDS = 60
_running: set[asyncio.Task] = set()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def is_ready(jobs: list[dict]) -> bool:
    """Every job is terminal, and every completed one has its outputs published."""
    return bool(jobs) and all(
        job["status"] in TERMINAL_STATUSES
        and (job["status"] != "complete" or job["artifacts_published_at"])
        for job in jobs
    )


def continuation_message(instructions: list[str], jobs: list[dict]) -> str:
    lines = ["The runs you queued work on have finished:"]
    for job in jobs:
        line = f'- {job["job_type"]} "{job["name"]}": {job["status"]}'
        if job["status"] == "failed" and job.get("error"):
            line += f" ({job['error'].strip().splitlines()[0]})"
        lines.append(line)
    lines += ["", *_carry_on(instructions)]
    lines.append(
        "If a run did not complete, explain what happened, in one reply, instead of carrying on."
    )
    return "\n".join(lines)


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


async def queue(
    workspace_id: str, session_id: str, user_id: str, job_ids: list[str], instruction: str
) -> dict:
    instruction = instruction.strip()
    if not instruction:
        return {"error": "Say what to do once the runs finish"}
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        return {"error": f"Keep the instruction under {MAX_INSTRUCTION_CHARS} characters"}
    job_ids = list(dict.fromkeys(job_ids))
    async with get_db() as conn:
        linked = set(
            (
                await conn.execute(
                    sa.select(tables.jobs.c.id).where(
                        tables.jobs.c.id.in_(job_ids),
                        tables.jobs.c.user_id == user_id,
                        tables.jobs.c.id.in_(linked_job_ids_query(sa.literal(workspace_id))),
                    )
                )
            ).scalars()
        )
        if not job_ids or (missing := [job_id for job_id in job_ids if job_id not in linked]):
            return {
                "error": "Only jobs linked to this workspace can be waited on",
                "not_linked": missing if job_ids else [],
            }
        row = {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "session_id": session_id,
            "user_id": user_id,
            "job_ids": job_ids,
            "instruction": instruction,
            "status": "waiting",
            "created_at": _now(),
        }
        await conn.execute(sa.insert(tables.follow_ups).values(**row))
    return {"queued": row["id"], "waiting_on": job_ids}


async def report_when_finished(
    workspace_id: str, session_id: str, user_id: str, job_ids: list[str]
) -> dict | None:
    """Queue the default report on runs just submitted from a chat.

    One follow-up for the whole submission, so a benchmark of several models is
    reported once. Nothing is queued when every run has already ended, e.g. a
    forecast served from stored results, which the submitting turn reports itself.
    """
    async with get_db() as conn:
        still_going = (
            await conn.execute(
                sa.select(tables.jobs.c.id).where(
                    tables.jobs.c.id.in_(job_ids),
                    tables.jobs.c.status.not_in(TERMINAL_STATUSES),
                )
            )
        ).first()
    if still_going is None:
        return None
    return await queue(workspace_id, session_id, user_id, job_ids, REPORT_INSTRUCTION)


async def list_for_workspace(workspace_id: str) -> list[dict]:
    follow_ups = tables.follow_ups
    async with get_db() as conn:
        rows = await conn.execute(
            sa.select(
                follow_ups.c.id,
                follow_ups.c.session_id,
                follow_ups.c.job_ids,
                follow_ups.c.instruction,
                follow_ups.c.status,
                follow_ups.c.created_at,
            )
            .where(follow_ups.c.workspace_id == workspace_id)
            .order_by(follow_ups.c.created_at)
        )
        return [{**row, "job_ids": json_col(row["job_ids"], [])} for row in rows.mappings()]


async def cancel(workspace_id: str, follow_up_id: str, user_id: str) -> bool:
    follow_ups = tables.follow_ups
    async with get_db() as conn:
        result = await conn.execute(
            sa.update(follow_ups)
            .where(
                follow_ups.c.id == follow_up_id,
                follow_ups.c.workspace_id == workspace_id,
                follow_ups.c.user_id == user_id,
                follow_ups.c.status == "waiting",
            )
            .values(status="canceled", completed_at=_now())
        )
        return result.rowcount == 1


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
    """Drop follow-ups whose chat was deleted: there is no conversation left to continue."""
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
    """One turn for a chat's ready follow-ups, in the order they were queued."""
    rows = sorted(rows, key=lambda row: row["created_at"])
    job_ids = list(dict.fromkeys(j for row in rows for j in json_col(row["job_ids"], [])))
    return Batch(
        session_id=rows[0]["session_id"],
        user_id=rows[0]["user_id"],
        follow_up_ids=[row["id"] for row in rows],
        message=continuation_message(
            instructions_to_carry_on(rows), [jobs_by_id[j] for j in job_ids]
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
        for row in waiting:
            jobs = await _jobs(conn, json_col(row["job_ids"], []))
            if is_ready(jobs):
                ready.setdefault(row["session_id"], []).append(dict(row))
                jobs_by_id.update((job["id"], job) for job in jobs)
        batches = []
        for session_id, rows in ready.items():
            status = await _chat_status(conn, session_id)
            if status == "gone":
                await _cancel_waiting(conn, [row["id"] for row in rows])
            elif status == "idle":
                claimed = [row for row in rows if await _claim(conn, row["id"])]
                if claimed:
                    batches.append(batch(claimed, jobs_by_id))
    for each in batches:
        task = asyncio.create_task(_run(each))
        _running.add(task)
        task.add_done_callback(_running.discard)


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
