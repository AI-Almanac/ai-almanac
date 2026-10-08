"""Blend forecast map payloads: CSV reshaping and the per-view endpoint.

Parser tests run on inline CSVs. Endpoint tests write the forecast CSVs to the
local artifact store, index them, and read them back through the API.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from ai_almanac.server.services.blend_forecast import (
    DAILY_COLUMNS,
    WEEKLY_COLUMNS,
    UnsupportedForecastView,
    available_views,
    parse_blend_forecast,
    parse_forecast_view,
)

_CSV = """id,time,lat,lon,onset_threshold,cv_week1,cv_week2,cv_week3,cv_week4,cv_later
9.0_39.0,2024-06-01,9.0,39.0,20,0.123456,0.2,0.3,0.1,0.276544
9.0_39.0,2024-06-08,9.0,39.0,20,0.5,0.5,,0,0
8.5_40.0,2024-06-08,,,20,1,0,0,0,0
"""

_WEEKLY_FILE = "blended_forecast_probabilities.csv"
_DAILY_FILE = "daily_onset_probabilities.csv"


def _daily_csv() -> str:
    """One point, one issue date: day d carries d/1000 and Later the remainder."""
    days = [d / 1000 for d in range(1, 29)]
    later = round(1 - sum(days), 3)
    weeks = [round(sum(days[w * 7 : (w + 1) * 7]), 3) for w in range(4)]
    header = ["id", "time", "lat", "lon", *DAILY_COLUMNS, *WEEKLY_COLUMNS]
    row = ["Ada’a", "2024-06-01", "8.9", "38.8", *days, later, *weeks, later]
    return ",".join(header) + "\n" + ",".join(str(v) for v in row) + "\n"


def test_parse_blend_forecast_aligns_points_on_issue_dates() -> None:
    result = parse_blend_forecast(_CSV, WEEKLY_COLUMNS)

    assert result["issue_dates"] == ["2024-06-01", "2024-06-08"]
    assert result["onset_threshold"] == 20.0
    first, second = result["points"]
    assert (first["lat"], first["lon"]) == (9.0, 39.0)
    assert first["probs"] == [[0.123, 0.2, 0.3, 0.1, 0.277], [0.5, 0.5, 0.0, 0.0, 0.0]]
    # Coordinates fall back to the point id; a missing issue date reads as no forecast.
    assert (second["lat"], second["lon"]) == (8.5, 40.0)
    assert second["probs"] == [[0.0] * 5, [1.0, 0.0, 0.0, 0.0, 0.0]]


def test_parse_blend_forecast_reads_daily_columns_in_lead_order() -> None:
    result = parse_blend_forecast(_daily_csv(), DAILY_COLUMNS)

    (point,) = result["points"]
    (probs,) = point["probs"]
    assert len(probs) == 29
    assert probs[:3] == [0.001, 0.002, 0.003]
    assert probs[27] == 0.028
    assert probs[28] == pytest.approx(1 - sum(d / 1000 for d in range(1, 29)), abs=1e-3)
    # No threshold column in the daily CSV.
    assert result["onset_threshold"] is None


def test_parse_blend_forecast_reads_weekly_sums_from_the_daily_csv() -> None:
    (point,) = parse_blend_forecast(_daily_csv(), WEEKLY_COLUMNS)["points"]

    assert point["probs"][0][0] == round(sum(d / 1000 for d in range(1, 8)), 3)


def test_weekly_blend_has_no_daily_view() -> None:
    with pytest.raises(UnsupportedForecastView):
        parse_forecast_view("weekly_model", "daily")


def test_default_view_keeps_its_original_payload_name() -> None:
    assert parse_forecast_view("weekly_model", "weekly").payload_name == "blend_forecast.v1.json"
    assert (
        parse_forecast_view("daily_model", "daily").payload_name
        == "blend_forecast.daily_model.daily.v1.json"
    )


def test_available_views_follow_the_csvs_present() -> None:
    assert available_views([_WEEKLY_FILE, "other.csv"]) == [
        {"model": "weekly_model", "resolution": "weekly"}
    ]
    assert available_views([_DAILY_FILE, _WEEKLY_FILE]) == [
        {"model": "weekly_model", "resolution": "weekly"},
        {"model": "daily_model", "resolution": "weekly"},
        {"model": "daily_model", "resolution": "daily"},
    ]


async def _insert_forecast_job(user_id: str, job_id: str) -> None:
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs (id, user_id, dataset_id, job_type, status, "
                "config_json, created_at) VALUES (:id, :uid, 'obs-1', 'forecast', "
                "'complete', '{}', :now)"
            ),
            {"id": job_id, "uid": user_id, "now": datetime.now(UTC).isoformat()},
        )


async def _publish_output(job_id: str, filename: str, body: str) -> None:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services.storage import get_storage

    path = get_storage().result_file_path(job_id, "output", filename)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO job_artifacts (id, job_id, kind, filename, media_type, "
                "size_bytes, checksum, storage_key, created_at) VALUES "
                "(:id, :job_id, 'output', :filename, 'text/csv', :size, 'x', :key, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "job_id": job_id,
                "filename": filename,
                "size": len(body),
                "key": f"{job_id}/output/{filename}",
                "now": datetime.now(UTC).isoformat(),
            },
        )


async def _forecast_job(user_id: str, *files: tuple[str, str]) -> str:
    job_id = str(uuid.uuid4())
    await _insert_forecast_job(user_id, job_id)
    for filename, body in files:
        await _publish_output(job_id, filename, body)
    return job_id


@pytest.mark.asyncio
async def test_blend_forecast_defaults_to_the_weekly_blend(
    client, user_id: str, auth_headers: dict[str, str]
) -> None:
    job_id = await _forecast_job(user_id, (_WEEKLY_FILE, _CSV))

    resp = await client.get(f"/jobs/{job_id}/blend-forecast", headers=auth_headers)

    body = resp.json()
    assert resp.status_code == 200
    assert body["issue_dates"] == ["2024-06-01", "2024-06-08"]
    assert len(body["points"][0]["probs"][0]) == 5
    assert body["available_views"] == [{"model": "weekly_model", "resolution": "weekly"}]


@pytest.mark.asyncio
async def test_blend_forecast_serves_each_view_from_its_csv(
    client, user_id: str, auth_headers: dict[str, str]
) -> None:
    job_id = await _forecast_job(user_id, (_WEEKLY_FILE, _CSV), (_DAILY_FILE, _daily_csv()))

    async def probs(model: str, resolution: str) -> list[float]:
        resp = await client.get(
            f"/jobs/{job_id}/blend-forecast",
            params={"model": model, "resolution": resolution},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["available_views"]) == 3
        return resp.json()["points"][0]["probs"][0]

    assert await probs("weekly_model", "weekly") == [0.123, 0.2, 0.3, 0.1, 0.277]
    assert len(await probs("daily_model", "weekly")) == 5
    assert len(await probs("daily_model", "daily")) == 29


@pytest.mark.asyncio
async def test_blend_forecast_without_the_daily_csv_is_empty_for_daily_views(
    client, user_id: str, auth_headers: dict[str, str]
) -> None:
    job_id = await _forecast_job(user_id, (_WEEKLY_FILE, _CSV))

    resp = await client.get(
        f"/jobs/{job_id}/blend-forecast",
        params={"model": "daily_model", "resolution": "daily"},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["points"] == []


@pytest.mark.asyncio
async def test_blend_forecast_rejects_unsupported_views(
    client, user_id: str, auth_headers: dict[str, str]
) -> None:
    job_id = await _forecast_job(user_id, (_WEEKLY_FILE, _CSV))
    url = f"/jobs/{job_id}/blend-forecast"

    daily_weekly_blend = await client.get(
        url, params={"model": "weekly_model", "resolution": "daily"}, headers=auth_headers
    )
    unknown_model = await client.get(url, params={"model": "forest"}, headers=auth_headers)

    assert daily_weekly_blend.status_code == 422
    assert unknown_model.status_code == 422
