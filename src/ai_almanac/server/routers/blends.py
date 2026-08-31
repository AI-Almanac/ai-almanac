"""Blend training jobs.

A blend is a `job_type='blend'` job that trains forecast blending weights.
Status, logs, and the weight artifacts are served by the shared
`/jobs/{id}` endpoints; this router only handles submission and listing in the
user's "blends" mental model.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, status

from ai_almanac.server.auth import CurrentUser, OptionalCurrentUser
from ai_almanac.server.db import get_db
from ai_almanac.server.services import job_access
from ai_almanac.server.services.job_submission import (
    BlendCreate,
    BlendOut,
    blend_row_to_out,
    create_blend_for_user,
)
from ai_almanac.server.tables import jobs

router = APIRouter(prefix="/blends", tags=["blends"])


@router.post("", response_model=BlendOut, status_code=status.HTTP_201_CREATED)
async def create_blend(body: BlendCreate, user: CurrentUser):
    return await create_blend_for_user(body, user.id)


@router.get("", response_model=list[BlendOut])
async def list_blends(user: OptionalCurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.select(jobs)
                    .where(
                        job_access.listing_filter(user.id if user else None),
                        jobs.c.job_type == "blend",
                    )
                    .order_by(jobs.c.created_at.desc())
                )
            )
            .mappings()
            .fetchall()
        )
    return [blend_row_to_out(dict(r), user.id if user else "") for r in rows]
