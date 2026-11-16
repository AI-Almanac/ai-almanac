"""Assistant work queued in a workspace chat until the jobs it needs finish.

After submitting runs whose results it needs, the assistant queues a follow-up
instead of asking the user to report back. The job reconciler starts each
follow-up as a new chat turn once every job is terminal and each completed
job's outputs are registered in the workspace, so the turn can read them
(:mod:`follow_up_runs`).

A run submitted from a chat gets a follow-up without the assistant asking: a
report on how it went, one per submission, which the user can cancel like any
other.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from ai_almanac.server import tables
from ai_almanac.server.db import get_db
from ai_almanac.server.tables import json_col

from .job_manager import TERMINAL_STATUSES
from .workspaces import linked_job_ids_query

MAX_INSTRUCTION_CHARS = 2000
# Read by the user in the workspace's queue as well as by the assistant.
REPORT_INSTRUCTION = "Report how each finished run went: one sentence, then its headline numbers."


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        missing = [job_id for job_id in job_ids if job_id not in linked]
        if not job_ids or missing:
            return {
                "error": "Only jobs linked to this workspace can be waited on",
                "not_linked": missing,
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
