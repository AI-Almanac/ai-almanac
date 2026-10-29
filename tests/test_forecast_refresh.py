from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from ai_almanac.server.db import get_db, get_or_create_user
from ai_almanac.server.services import job_submission
from ai_almanac.server.tables import jobs


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
    captured: dict = {}

    async def fake_create(body, user_id, *, is_admin=False):
        captured["body"] = body
        return job_submission.ForecastOut(
            id="fc-zero-2",
            blend_id=body.blend_id,
            status="queued",
            forecast_model_ids=[],
            created_at="2026-05-10T01:00:00",
        )

    monkeypatch.setattr(job_submission, "create_forecast_for_user", fake_create)

    await job_submission.refresh_forecast_for_user("fc-zero", user["id"])

    assert captured["body"].params.max_issue_dates is None


@pytest.mark.asyncio
async def test_refresh_replays_each_models_init_source_and_member_count(monkeypatch):
    async with get_db() as conn:
        user = await get_or_create_user(conn, external_id="refresh-user-per-model")
        config = {
            "job_type": "forecast",
            "blend_id": "blend-123",
            "forecast_model_ids": ["aifs_ensv2", "graphcast"],
            "init_source": None,
            "init_sources": {"aifs_ensv2": "ifs", "graphcast": "gfs"},
            "ensemble_members": {"aifs_ensv2": 8},
        }
        await conn.execute(
            sa.insert(jobs).values(
                id="fc-per-model",
                user_id=user["id"],
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
        return job_submission.ForecastOut(
            id="fc-2", blend_id=body.blend_id, status="queued", forecast_model_ids=[], created_at=""
        )

    monkeypatch.setattr(job_submission, "create_forecast_for_user", fake_create)

    await job_submission.refresh_forecast_for_user("fc-per-model", user["id"])

    params = captured["body"].params
    assert params.init_source is None
    assert params.init_sources == {"aifs_ensv2": "ifs", "graphcast": "gfs"}
    assert params.ensemble_members == {"aifs_ensv2": 8}


def test_a_forecast_saved_with_one_init_source_reports_it_for_every_model():
    row = {
        "id": "fc-old",
        "status": "complete",
        "created_at": "2026-05-10T00:00:00",
        "config_json": json.dumps(
            {"blend_id": "b", "forecast_model_ids": ["fuxi", "graphcast"], "init_source": "ifs"}
        ),
    }

    out = job_submission.forecast_row_to_out(row, None)

    assert out.init_source == "ifs"
    assert out.init_sources == {"fuxi": "ifs", "graphcast": "ifs"}
    assert out.ensemble_members == {}
