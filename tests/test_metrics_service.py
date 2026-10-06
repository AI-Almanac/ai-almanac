from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest
import xarray as xr
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.metrics import (
    build_job_grids,
    compute_job_cell,
    compute_job_metrics,
)
from ai_almanac.server.services.storage import get_storage


class FakeStorage:
    def __init__(self, root: Path):
        self.root = root

    def list_nc_output_files(self, job_id: str) -> list[Path]:
        output = self.root / job_id / "output"
        return sorted(output.glob("spatial_metrics_*.nc")) + sorted(
            output.glob("e2s_spatial_metrics_*.nc")
        )

    def find_nc_output_file(self, job_id: str, model: str, window: str) -> str | None:
        output = self.root / job_id / "output"
        for prefix in ("spatial_metrics", "e2s_spatial_metrics"):
            for candidate_window in (window, window.replace("-", ",")):
                matches = list(output.glob(f"{prefix}_{model}_{candidate_window}.nc"))
                if matches:
                    return str(matches[0])
        return None

    def open_nc_dataset(self, path):
        return xr.load_dataset(path)


def write_metrics_file(
    root: Path,
    job_id: str,
    filename: str,
    model: str,
    window: str,
    variables: dict[str, np.ndarray],
) -> None:
    output = root / job_id / "output"
    output.mkdir(parents=True, exist_ok=True)
    lats = np.array([10.0, 11.0])
    lons = np.array([40.0, 41.0])
    ds = xr.Dataset(
        {name: (("lat", "lon"), values) for name, values in variables.items()},
        coords={"lat": lats, "lon": lons},
        attrs={"model": model, "verification_window": window},
    )
    ds.to_netcdf(output / filename)


def test_compute_job_metrics_includes_e2s_all_window_units(tmp_path: Path) -> None:
    job_id = "job-1"
    write_metrics_file(
        tmp_path,
        job_id,
        "e2s_spatial_metrics_aifs_all.nc",
        "aifs",
        "all",
        {
            "rmse": np.array([[1.0, 2.0], [3.0, 4.0]]),
            "mae": np.array([[0.5, 1.5], [2.5, 3.5]]),
            "bias": np.array([[-1.0, 0.0], [1.0, 2.0]]),
            "acc": np.array([[0.1, 0.2], [0.3, 0.4]]),
        },
    )

    result = compute_job_metrics(job_id, FakeStorage(tmp_path))

    window = result.windows[0]
    assert window.window == "all"
    assert set(window.metrics) == {"rmse", "mae", "bias", "acc"}
    assert window.metrics["mae"].unit == "mm"
    assert window.metrics["acc"].unit == "dimensionless"


def test_build_job_grids_collects_map_metrics_from_every_file(tmp_path: Path) -> None:
    job_id = "job-1"
    write_metrics_file(
        tmp_path,
        job_id,
        "spatial_metrics_aifs_1,15.nc",
        "aifs",
        "1,15",
        {
            "miss_rate": np.array([[0.123456789, np.nan], [0.5, 1.0]]),
            "mae_2001": np.array([[1.0, 2.0], [3.0, 4.0]]),
        },
    )
    write_metrics_file(
        tmp_path,
        job_id,
        "e2s_spatial_metrics_aifs_all.nc",
        "aifs",
        "all",
        {"rmse": np.array([[1.0, 2.0], [3.0, 4.0]])},
    )

    result = build_job_grids(job_id, FakeStorage(tmp_path))

    by_key = {(g.window, g.metric): g for g in result.grids}
    assert set(by_key) == {("1-15", "miss_rate"), ("all", "rmse")}
    miss_rate = by_key[("1-15", "miss_rate")]
    assert miss_rate.values == [[0.1235, None], [0.5, 1.0]]
    assert (miss_rate.min, miss_rate.unit) == (0.123456789, "fraction")
    assert by_key[("all", "rmse")].unit == "mm"


@pytest.mark.asyncio
async def test_grids_endpoint_serves_stored_payload_without_rebuilding(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await client.get("/jobs")
    job_id = "stored-grids-job"
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
    output_dir = Path(get_storage().job_output_uri(job_id)[0])
    write_metrics_file(
        output_dir.parent.parent,
        job_id,
        "e2s_spatial_metrics_aifs_all.nc",
        "aifs",
        "all",
        {"rmse": np.array([[1.0, 2.0], [3.0, 4.0]])},
    )

    first = await client.get(f"/jobs/{job_id}/grids")

    def fail_if_rebuilt(*args, **kwargs):
        raise AssertionError("stored grids should be served without reading the NetCDFs")

    monkeypatch.setattr("ai_almanac.server.routers.jobs.build_job_grids", fail_if_rebuilt)
    second = await client.get(f"/jobs/{job_id}/grids")

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert [g["metric"] for g in second.json()["grids"]] == ["rmse"]


def test_compute_job_cell_compares_shared_dynamic_metrics(tmp_path: Path) -> None:
    job_id = "job-1"
    write_metrics_file(
        tmp_path,
        job_id,
        "e2s_spatial_metrics_aifs_all.nc",
        "aifs",
        "all",
        {
            "rmse": np.array([[1.0, 2.0], [3.0, 4.0]]),
            "mae": np.array([[2.0, 3.0], [4.0, 5.0]]),
        },
    )
    write_metrics_file(
        tmp_path,
        job_id,
        "e2s_spatial_metrics_climatology_all.nc",
        "climatology",
        "all",
        {
            "rmse": np.array([[0.5, 1.0], [1.5, 2.0]]),
            "mae": np.array([[1.0, 1.0], [1.0, 1.0]]),
            "acc": np.array([[0.1, 0.1], [0.1, 0.1]]),
        },
    )

    result = compute_job_cell(job_id, FakeStorage(tmp_path), "aifs", "all", 10.1, 40.2)

    assert set(result.metrics) == {"rmse", "mae"}
    assert result.metrics["rmse"].model == 1.0
    assert result.metrics["rmse"].baseline == 0.5
    assert result.metrics["rmse"].delta == 0.5
    assert result.metrics["mae"].unit == "mm"
    assert result.mae_series == []


def test_compute_job_cell_returns_model_metrics_without_baseline(
    tmp_path: Path,
) -> None:
    job_id = "job-1"
    write_metrics_file(
        tmp_path,
        job_id,
        "e2s_spatial_metrics_aifs_all.nc",
        "aifs",
        "all",
        {"acc": np.array([[0.1, 0.2], [0.3, 0.4]])},
    )

    result = compute_job_cell(job_id, FakeStorage(tmp_path), "aifs", "all", 10.1, 40.2)

    assert set(result.metrics) == {"acc"}
    assert result.metrics["acc"].model == 0.1
    assert result.metrics["acc"].baseline is None
    assert result.metrics["acc"].delta is None


@pytest.mark.asyncio
async def test_metrics_endpoint_loads_serialized_cache_after_restart(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await client.get("/jobs")
    job_id = "persisted-metrics-job"
    cached_metrics = {
        "job_id": job_id,
        "windows": [],
        "grid": None,
        "bbox": None,
    }
    async with get_db() as conn:
        user_id = (
            await conn.execute(text("SELECT id FROM users WHERE external_id = 'local'"))
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO jobs "
                "(id, user_id, dataset_id, status, config_json, created_at, metrics_cache) "
                "VALUES (:id, :uid, 'dataset-1', 'complete', '{}', :created_at, :cache)"
            ),
            {
                "id": job_id,
                "uid": user_id,
                "created_at": datetime.now(UTC).isoformat(),
                "cache": json.dumps(cached_metrics),
            },
        )

    def fail_if_recomputed(*args, **kwargs):
        raise AssertionError("persisted metrics should be returned without recomputing")

    monkeypatch.setattr(
        "ai_almanac.server.routers.jobs.compute_job_metrics",
        fail_if_recomputed,
    )

    response = await client.get(f"/jobs/{job_id}/metrics")

    assert response.status_code == 200
    assert response.json() == cached_metrics
    jobs_response = await client.get("/jobs")
    assert job_id not in {job["id"] for job in jobs_response.json()}
