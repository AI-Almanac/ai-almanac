from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest
import xarray as xr
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.blend_forecast import parse_blend_forecast
from ai_almanac.server.services.map_data import (
    build_job_map_data,
    job_cell,
    job_grids,
    load_job_map_data,
)
from ai_almanac.server.services.storage import LocalStorage, get_storage

JOB_ID = "job-1"


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    load_job_map_data.cache_clear()
    return LocalStorage(upload_dir=tmp_path / "uploads", job_outputs_dir=tmp_path / "outputs")


def write_metrics_file(storage: LocalStorage, filename: str, variables: dict) -> None:
    output = storage.job_dir(JOB_ID) / "output"
    output.mkdir(parents=True, exist_ok=True)
    xr.Dataset(
        {
            name: (("lat", "lon"), np.asarray(values, dtype=float))
            for name, values in variables.items()
        },
        coords={"lat": [10.0, 11.0], "lon": [40.0, 41.0]},
    ).to_netcdf(output / filename)


def grid(response, model: str, window: str, metric: str):
    return next(
        g for g in response.grids if (g.model, g.window, g.metric) == (model, window, metric)
    )


def test_grids_cover_every_model_window_and_metric_except_annual_mae(storage) -> None:
    write_metrics_file(
        storage,
        "spatial_metrics_AIFS_Single_v2_1-15.nc",
        {"miss_rate": [[0.1, np.nan], [0.3, 0.4]], "mae_2001": [[1, 2], [3, 4]]},
    )
    write_metrics_file(storage, "e2s_spatial_metrics_aifs_all.nc", {"rmse": [[1, 2], [3, 4]]})

    response = job_grids(JOB_ID, build_job_map_data(JOB_ID, storage))

    assert {(g.model, g.window, g.metric) for g in response.grids} == {
        ("AIFS_Single_v2", "1-15", "miss_rate"),
        ("aifs", "all", "rmse"),
    }
    miss = grid(response, "AIFS_Single_v2", "1-15", "miss_rate")
    assert miss.values == [[0.1, None], [0.3, 0.4]]
    assert (miss.min, miss.max, miss.unit) == (0.1, 0.4, "fraction")
    assert grid(response, "aifs", "all", "rmse").unit == "mm"


def test_map_data_is_stored_and_reused_without_reopening_netcdfs(storage, monkeypatch) -> None:
    write_metrics_file(
        storage, "spatial_metrics_aifs_1-15.nc", {"miss_rate": [[0.1, 0.2], [0.3, 0.4]]}
    )
    first = load_job_map_data(JOB_ID, storage)
    load_job_map_data.cache_clear()  # as after a restart or on another instance

    def fail_if_reopened(path):
        raise AssertionError("stored map data should be read instead of the NetCDF")

    monkeypatch.setattr(storage, "open_nc_dataset", fail_if_reopened)

    assert load_job_map_data(JOB_ID, storage) == first


def test_job_without_metric_files_is_not_found(storage) -> None:
    with pytest.raises(FileNotFoundError):
        load_job_map_data(JOB_ID, storage)


def test_cell_compares_shared_metrics_and_annual_mae_with_baseline(storage) -> None:
    write_metrics_file(
        storage,
        "spatial_metrics_aifs_1,15.nc",
        {"mean_mae": [[2, 3], [4, 5]], "rmse": [[1, 2], [3, 4]], "mae_2001": [[5, 5], [5, 5]]},
    )
    write_metrics_file(
        storage,
        "spatial_metrics_climatology_1-15.nc",
        {"mean_mae": [[1, 1], [1, 1]], "mae_2001": [[2, 2], [2, 2]], "acc": [[0, 0], [0, 0]]},
    )

    cell = job_cell(JOB_ID, build_job_map_data(JOB_ID, storage), "aifs", "1,15", 10.9, 40.2)

    assert (cell.lat, cell.lon) == (11.0, 40.0)
    assert set(cell.metrics) == {"mean_mae"}
    assert (cell.metrics["mean_mae"].model, cell.metrics["mean_mae"].delta) == (4.0, 3.0)
    assert [(p.year, p.model, p.baseline, p.delta) for p in cell.mae_series] == [
        (2001, 5.0, 2.0, 3.0)
    ]


def test_cell_without_baseline_reports_model_values_only(storage) -> None:
    write_metrics_file(
        storage, "e2s_spatial_metrics_aifs_all.nc", {"acc": [[0.1, 0.2], [0.3, 0.4]]}
    )

    cell = job_cell(JOB_ID, build_job_map_data(JOB_ID, storage), "aifs", "all", 10.1, 40.2)

    assert cell.metrics["acc"].model == 0.1
    assert cell.metrics["acc"].baseline is None
    assert cell.metrics["acc"].delta is None


def test_cell_for_unknown_model_is_not_found(storage) -> None:
    write_metrics_file(storage, "spatial_metrics_aifs_1-15.nc", {"miss_rate": [[0, 0], [0, 0]]})

    with pytest.raises(FileNotFoundError):
        job_cell(JOB_ID, build_job_map_data(JOB_ID, storage), "fuxi", "1-15", 10, 40)


def test_blend_forecast_groups_probabilities_by_point_and_issue_date() -> None:
    csv_text = (
        "id,time,lat,lon,onset_threshold,cv_week1,cv_week2,cv_week3,cv_week4,cv_later\n"
        "9.0_38.5,2024-06-01,9.0,38.5,20,0.12345,0.2,0.3,0.1,0.27655\n"
        "9.0_38.5,2024-06-08,9.0,38.5,20,0.5,0.5,0,0,0\n"
        "9.5_39.0,2024-06-08,,,20,1,0,0,0,0\n"
    )

    forecast = parse_blend_forecast(csv_text)

    assert forecast.issue_dates == ["2024-06-01", "2024-06-08"]
    assert forecast.onset_threshold == 20.0
    first, second = forecast.points
    assert first.probs == [[0.123, 0.2, 0.3, 0.1, 0.277], [0.5, 0.5, 0.0, 0.0, 0.0]]
    assert (second.lat, second.lon) == (9.5, 39.0)
    assert second.probs[0] == [0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_grids_and_cell_endpoints_serve_a_completed_job(client: httpx.AsyncClient) -> None:
    await client.get("/jobs")
    job_id = "map-endpoint-job"
    async with get_db() as conn:
        user_id = (
            await conn.execute(text("SELECT id FROM users WHERE external_id = 'local'"))
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO jobs (id, user_id, dataset_id, status, config_json, created_at) "
                "VALUES (:id, :uid, 'dataset-1', 'complete', '{}', :created_at)"
            ),
            {"id": job_id, "uid": user_id, "created_at": datetime.now(UTC).isoformat()},
        )
    output = get_storage().job_dir(job_id) / "output"
    output.mkdir(parents=True, exist_ok=True)
    xr.Dataset(
        {"miss_rate": (("lat", "lon"), np.array([[0.1, 0.2], [0.3, 0.4]]))},
        coords={"lat": [10.0, 11.0], "lon": [40.0, 41.0]},
    ).to_netcdf(output / "spatial_metrics_aifs_1-15.nc")

    grids = await client.get(f"/jobs/{job_id}/grids")
    cell = await client.get(
        f"/jobs/{job_id}/cell", params={"model": "aifs", "window": "1-15", "lat": 11, "lon": 41}
    )

    assert grids.status_code == 200
    assert [(g["model"], g["window"], g["metric"]) for g in grids.json()["grids"]] == [
        ("aifs", "1-15", "miss_rate")
    ]
    assert cell.status_code == 200
    assert cell.json()["metrics"]["miss_rate"]["model"] == 0.4
