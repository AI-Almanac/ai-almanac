from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from ai_almanac.server.db import get_db, get_or_create_user
from ai_almanac.server.services import job_submission
from ai_almanac.server.tables import jobs


def _capture_rebuilt_request(monkeypatch) -> dict:
    """Stub building and submitting the refreshed job, capturing the request
    the refresh rebuilt from the saved forecast."""
    captured: dict = {}

    async def fake_build(body, user_id):
        captured["body"] = body
        return job_submission.ForecastJob(
            dataset_id="ds-1", run_id=body.run_id, plan=None, config={"season_plan": {}}
        )

    async def fake_submit(job, user_id):
        return job_submission.ForecastOut(
            id="fc-new",
            blend_id="blend-123",
            status="queued",
            forecast_model_ids=[],
            created_at="2026-05-10T01:00:00",
        )

    monkeypatch.setattr(job_submission, "build_forecast_job", fake_build)
    monkeypatch.setattr(job_submission, "submit_forecast_job", fake_submit)
    return captured


@pytest.mark.asyncio
async def test_refresh_replays_original_params(monkeypatch):
    """An update must re-run with the ORIGINAL init source/window/time so it
    lands on the same trajectory set and reuses the cache — not fall back to
    submission defaults (gfs, full season)."""
    async with get_db() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user")
        uid = user["id"]
        config = {
            "job_type": "forecast",
            "blend_id": "blend-123",
            "forecast_model_ids": ["fuxi"],
            "init_source": "ifs",
            "init_time": "2026-05-01T00:00:00",
            "max_issue_dates": 3,
            "lead_hours": [24, 48],
            "variables": ["tp"],
        }
        await conn.execute(
            sa.insert(jobs).values(
                id="fc-1",
                user_id=uid,
                dataset_id="ds-1",
                job_type="forecast",
                status="complete",
                config_json=json.dumps(config),
                created_at="2026-05-10T00:00:00",
            )
        )

    captured = _capture_rebuilt_request(monkeypatch)

    await job_submission.refresh_forecast_for_user("fc-1", uid)

    body = captured["body"]
    assert body.blend_id == "blend-123"
    assert body.forecast_model_ids == ["fuxi"]
    assert body.params.init_source == "ifs"
    assert body.params.init_time == "2026-05-01T00:00:00"
    assert body.params.max_issue_dates == 3


@pytest.mark.asyncio
async def test_refresh_unknown_forecast_404():
    async with get_db() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user-2")
    with pytest.raises(HTTPException) as exc:
        await job_submission.refresh_forecast_for_user("nope", user["id"])
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_refresh_refuses_a_forecast_saved_with_a_retired_init_source():
    """Jobs saved before init sources were restricted re-run as a clear 409,
    not a server error."""
    async with get_db() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user-3")
        await conn.execute(
            sa.insert(jobs).values(
                id="fc-era5",
                user_id=user["id"],
                dataset_id="ds-1",
                job_type="forecast",
                status="complete",
                config_json=json.dumps({"blend_id": "blend-123", "init_source": "era5"}),
                created_at="2026-05-10T00:00:00",
            )
        )

    with pytest.raises(HTTPException) as exc:
        await job_submission.refresh_forecast_for_user("fc-era5", user["id"])
    assert exc.value.status_code == 409
    assert "init_source" in exc.value.detail


@pytest.mark.asyncio
async def test_refresh_reads_a_saved_zero_issue_date_trim_as_no_trim(monkeypatch):
    async with get_db() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user-4")
        await conn.execute(
            sa.insert(jobs).values(
                id="fc-zero",
                user_id=user["id"],
                dataset_id="ds-1",
                job_type="forecast",
                status="complete",
                config_json=json.dumps({"blend_id": "blend-123", "max_issue_dates": 0}),
                created_at="2026-05-10T00:00:00",
            )
        )
    captured = _capture_rebuilt_request(monkeypatch)

    await job_submission.refresh_forecast_for_user("fc-zero", user["id"])

    assert captured["body"].params.max_issue_dates is None
