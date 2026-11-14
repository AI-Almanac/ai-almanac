"""A user's recently finished jobs: the history a run's expected duration is read from
(``run_summaries.expected_seconds``).

History is read per owner and per kind of job, so one busy owner, or one kind of job
run often, never crowds another's past runs out of the rows read.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa

from ai_almanac.server import tables
from ai_almanac.server.db import get_db

from .run_summaries import FINISHED

# How far back an estimate looks through one owner's finished jobs of one kind. An
# estimate uses the ten most recent comparable runs, so this leaves room for runs of
# other regions, areas and sizes in between.
HISTORY_JOBS = 40

Owner = tuple[str, str]
"""A user id and a job type: whose history, of which kind of job."""


async def finished_jobs_of(owners: Iterable[Owner]) -> dict[Owner, list[dict]]:
    """Each owner's most recently finished jobs of one kind, newest first. Each job
    carries ``job_type``, ``status``, ``config`` and its ``created_at``, ``started_at``
    and ``completed_at``."""
    wanted = set(owners)
    if not wanted:
        return {}
    async with get_db() as conn:
        return {owner: await _finished_jobs(conn, *owner) for owner in wanted}


async def _finished_jobs(conn, user_id: str, job_type: str) -> list[dict]:
    jobs = tables.jobs
    result = await conn.execute(
        sa.select(
            jobs.c.job_type,
            jobs.c.status,
            jobs.c.config_json,
            jobs.c.created_at,
            jobs.c.started_at,
            jobs.c.completed_at,
        )
        .where(
            jobs.c.user_id == user_id,
            jobs.c.job_type == job_type,
            jobs.c.status == FINISHED,
            jobs.c.completed_at.is_not(None),
        )
        .order_by(jobs.c.completed_at.desc())
        .limit(HISTORY_JOBS)
    )
    rows = [dict(row) for row in result.mappings()]
    for row in rows:
        row["config"] = tables.json_col(row.pop("config_json"), {})
    return rows
