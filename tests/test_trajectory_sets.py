from __future__ import annotations

import datetime as dt

import pytest

from ai_almanac.server.db import engine, get_or_create_user
from ai_almanac.server.services import trajectory_sets as ts


@pytest.mark.asyncio
async def test_create_or_get_is_idempotent_and_gates_on_coverage():
    async with engine.begin() as conn:
        user = await get_or_create_user(conn, external_id="traj-test-user")
        uid = user["id"]
        key = dict(model_name="fuxi", init_source="gfs", season="2026")
        make = dict(
            model_id="fuxi",
            triggered_by_user_id=uid,
            storage_prefix="gs://bucket/season-forecasts",
        )

        first = await ts.create_or_get_set(conn, **make, **key)
        again = await ts.create_or_get_set(conn, **make, **key)
        assert first["id"] == again["id"], "same triple must not start a duplicate set"

        needed = [dt.date(2026, 5, 4), dt.date(2026, 5, 7)]

        # Pending + no coverage → not ready.
        assert await ts.set_is_ready(conn, needed_dates=needed, **key) is False

        await ts.mark_dates_covered(conn, first["id"], [dt.date(2026, 5, 4)])
        await ts.set_status(conn, first["id"], "complete")
        # Complete but a needed date is still missing → not ready.
        assert await ts.set_is_ready(conn, needed_dates=needed, **key) is False

        # Accepts a string date form too; now fully covered → ready.
        await ts.mark_dates_covered(conn, first["id"], ["2026-05-07"])
        assert await ts.set_is_ready(conn, needed_dates=needed, **key) is True
        # A subset of covered dates is trivially ready.
        assert await ts.set_is_ready(conn, needed_dates=[dt.date(2026, 5, 4)], **key) is True


@pytest.mark.asyncio
async def test_mark_coverage_from_config_completes_and_satisfies_gate():
    """The async (Modal reconciler) marker must cover exactly the dates the
    submit-time gate computes from the same config — otherwise a completed set
    never reads as ready and every run re-triggers a GPU rollout."""
    from ai_almanac.server.services.forecast_pipeline import season_covered_dates

    config = {
        "job_type": "forecast",
        "init_source": "gfs",
        "season": "2024",
        "season_start_month_day": "05-01",
        "season_model_params": {"fuxi": {"init_days": "0,3"}},
        "forecast_model_ids": ["fuxi"],
        "max_issue_dates": None,
    }
    key = dict(model_name="fuxi", init_source="gfs", season="2024")

    async with engine.begin() as conn:
        user = await get_or_create_user(conn, external_id="traj-cover-user")
        await ts.create_or_get_set(
            conn,
            model_id="fuxi",
            triggered_by_user_id=user["id"],
            storage_prefix="gs://bucket/season-forecasts",
            **key,
        )
        needed = season_covered_dates(config)["fuxi"]
        assert await ts.set_is_ready(conn, needed_dates=needed, **key) is False

        await ts.mark_coverage_from_config(conn, config)

        row = await ts.get_set(conn, **key)
        assert row["status"] == "complete"
        assert await ts.set_is_ready(conn, needed_dates=needed, **key) is True


@pytest.mark.asyncio
async def test_unknown_set_is_never_ready():
    async with engine.begin() as conn:
        ready = await ts.set_is_ready(
            conn,
            model_name="nope",
            init_source="gfs",
            season="2026",
            needed_dates=[dt.date(2026, 5, 4)],
        )
        assert ready is False
