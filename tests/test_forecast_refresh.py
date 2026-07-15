from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from ai_almanac.server.db import engine, get_or_create_user
from ai_almanac.server.services import job_submission
from ai_almanac.server.tables import jobs


@pytest.mark.asyncio
async def test_refresh_replays_original_params(monkeypatch):
    """An update must re-run with the ORIGINAL init source/window/time so it
    lands on the same trajectory set and reuses the cache — not fall back to
    submission defaults (gfs, full season)."""
    async with engine.begin() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user")
        uid = user["id"]
        config = {
            "job_type": "forecast",
            "blend_id": "blend-123",
            "forecast_model_ids": ["fuxi"],
            "init_source": "era5",
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

    captured: dict = {}

    async def fake_create(body, user_id, *, is_admin=False):
        captured["body"] = body
        captured["user_id"] = user_id
        return job_submission.ForecastOut(
            id="fc-2",
            blend_id=body.blend_id,
            status="queued",
            forecast_model_ids=body.forecast_model_ids or [],
            created_at="2026-05-10T01:00:00",
        )

    monkeypatch.setattr(job_submission, "create_forecast_for_user", fake_create)

    await job_submission.refresh_forecast_for_user("fc-1", uid)

    body = captured["body"]
    assert body.blend_id == "blend-123"
    assert body.forecast_model_ids == ["fuxi"]
    assert body.params.init_source == "era5"
    assert body.params.init_time == "2026-05-01T00:00:00"
    assert body.params.max_issue_dates == 3


@pytest.mark.asyncio
async def test_refresh_unknown_forecast_404():
    async with engine.begin() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user-2")
    with pytest.raises(Exception) as exc:
        await job_submission.refresh_forecast_for_user("nope", user["id"])
    assert "404" in str(exc.value) or "Unknown forecast" in str(exc.value)
