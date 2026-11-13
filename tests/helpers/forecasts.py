"""A trained blend with a finished forecast, submitted without reaching a runner."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from ai_almanac.server.services import job_submission
from ai_almanac.server.services.execution import RunnerHandle
from ai_almanac.server.services.job_submission import BlendCreate, BlendParams
from ai_almanac.server.tables import jobs
from tests.helpers.records import link_forecast_model, seed_source


class FakeRunner:
    name = "modal"

    async def submit(self, request):  # noqa: ANN001 - test double
        return RunnerHandle(runner="modal", external_id="fc-test")


async def forecast_a_linked_blend(user_id: str, monkeypatch, today: date) -> tuple[str, str]:
    """A completed forecast, planned on `today`, of a trained blend whose members
    are linked to live models: (blend id, forecast id)."""
    from ai_almanac.server.db import get_db

    monkeypatch.setattr(job_submission, "utc_today", lambda: today)
    obs_id = await seed_source("obs", "IMD India", "gs://data/obs/india", grid_step=0.25)
    aifs_id = await seed_source(
        "model", "AIFS v2 India 0.25", "gs://data/models/aifs2", grid_step=0.25
    )
    graphcast_id = await seed_source(
        "model", "GraphCast India 0.25", "gs://data/models/graphcast", grid_step=0.25
    )
    await link_forecast_model(aifs_id, "aifs2")
    await link_forecast_model(graphcast_id, "graphcast_operational")
    blend = await job_submission.create_blend_for_user(
        BlendCreate(
            name="india",
            obs_dataset_id=obs_id,
            model_ids=[aifs_id, graphcast_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )
    async with get_db() as conn:
        await conn.execute(sa.update(jobs).where(jobs.c.id == blend.id).values(status="complete"))
    forecast = await job_submission.create_forecast_for_user(
        job_submission.ForecastCreate(blend_id=blend.id),
        user_id,
    )
    async with get_db() as conn:
        await conn.execute(
            sa.update(jobs).where(jobs.c.id == forecast.id).values(status="complete")
        )
    return blend.id, forecast.id


async def live_model_archive(name: str = "AIFS v2 India 0.25") -> str:
    return await seed_source(
        "model",
        name,
        "gs://data/models/aifs2",
        grid_step=0.25,
        extra_metadata={"forecast_model_id": "aifs2", "init_days": "0,3"},
    )
