"""Live forecast jobs.

A forecast is a `job_type='forecast'` job that runs a completed blend's
models forward against live GFS conditions and scores them against the
blend's trained weights. Status, logs, and output artifacts (per-model COGs,
the blended probability output) are served by the shared `/jobs/{id}`
endpoints; this router only handles submission, listing, and the model
registry.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, status

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.db import get_db
from ai_almanac.server.services.forecast_models import load_forecast_model_registry
from ai_almanac.server.services.job_submission import (
    ForecastCreate,
    ForecastOut,
    create_forecast_for_user,
    forecast_row_to_out,
)
from ai_almanac.server.tables import jobs

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@router.post("", response_model=ForecastOut, status_code=status.HTTP_201_CREATED)
async def create_forecast(body: ForecastCreate, user: CurrentUser):
    return await create_forecast_for_user(body, user.id)


@router.get("", response_model=list[ForecastOut])
async def list_forecasts(user: CurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.select(jobs)
                    .where(jobs.c.user_id == user.id, jobs.c.job_type == "forecast")
                    .order_by(jobs.c.created_at.desc())
                )
            )
            .mappings()
            .fetchall()
        )
    return [forecast_row_to_out(dict(r), user.id) for r in rows]


@router.get("/models")
async def list_forecast_models():
    return await load_forecast_model_registry()
