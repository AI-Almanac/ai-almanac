"""Job read/modify authorization shared by the HTTP, WebSocket, and chat paths."""

from __future__ import annotations

from typing import Protocol

import sqlalchemy as sa

from ai_almanac.server.db import get_db
from ai_almanac.server.tables import jobs


class JobUser(Protocol):
    id: str
    is_admin: bool


def can_read(job: dict, user: JobUser) -> bool:
    """Owner, admin, or anyone when the job is shared read-only."""
    return (
        user.is_admin
        or job.get("user_id") == user.id
        or (job.get("visibility") or "private") == "shared"
    )


def can_modify(job: dict, user: JobUser) -> bool:
    """Owner or admin. Sharing is read-only and never grants this."""
    return user.is_admin or job.get("user_id") == user.id


async def fetch_job(job_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (await conn.execute(sa.select(jobs).where(jobs.c.id == job_id)))
            .mappings()
            .fetchone()
        )
    return dict(row) if row else None


async def readable_job_ids(job_ids: list[str], user: JobUser) -> set[str]:
    """The subset of ``job_ids`` the user is allowed to read."""
    if not job_ids:
        return set()
    query = sa.select(jobs.c.id, jobs.c.user_id, jobs.c.visibility).where(
        jobs.c.id.in_(job_ids)
    )
    async with get_db() as conn:
        rows = (await conn.execute(query)).mappings().fetchall()
    return {row["id"] for row in rows if can_read(dict(row), user)}
