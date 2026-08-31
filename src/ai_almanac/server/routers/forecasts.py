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
from fastapi import APIRouter, Depends, status

from ai_almanac.server.auth import AdminUser, CurrentUser, require_forecasting
from ai_almanac.server.db import get_db
from ai_almanac.server.services import job_access, trajectory_sets
from ai_almanac.server.services.forecast_models import (
    load_forecast_model_registry,
    load_init_sources,
)
from ai_almanac.server.services.job_submission import (
    ForecastCreate,
    ForecastOut,
    create_forecast_for_user,
    forecast_row_to_out,
    refresh_forecast_for_user,
)
from ai_almanac.server.tables import jobs

# The whole router is gated on the forecasting feature flag (on by default).
# User endpoints still require an authenticated user (CurrentUser); the
# trajectory-generation view stays admin-only (AdminUser).
router = APIRouter(
    prefix="/forecasts", tags=["forecasts"], dependencies=[Depends(require_forecasting)]
)


@router.post("", response_model=ForecastOut, status_code=status.HTTP_201_CREATED)
async def create_forecast(body: ForecastCreate, user: CurrentUser):
    return await create_forecast_for_user(body, user.id)


@router.post(
    "/{forecast_id}/refresh",
    response_model=ForecastOut,
    status_code=status.HTTP_201_CREATED,
)
async def refresh_forecast(forecast_id: str, user: CurrentUser):
    """Re-run a forecast with its original spec so it reuses the cached season."""
    return await refresh_forecast_for_user(forecast_id, user.id)


@router.get("/trajectories")
async def list_trajectory_sets(_admin: AdminUser):
    """Trajectory-set coverage for the admin generation view."""
    async with get_db() as conn:
        return await trajectory_sets.list_sets(conn)


@router.get("", response_model=list[ForecastOut])
async def list_forecasts(user: CurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.select(jobs)
                    .where(job_access.listing_filter(user.id), jobs.c.job_type == "forecast")
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


@router.get("/init-sources")
async def list_init_sources():
    return load_init_sources()
