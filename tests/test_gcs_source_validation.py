"""GCS data-source validation — `gs://` sources are inspected like local ones.

The real `GCSStorage` runs over a filesystem that serves `testdata/` fixtures
through gcsfs-style ranged reads, so the full inference path (coverage years,
spatial bounds, variable check) runs without real GCS, and tests can see how
much of each object validation fetched.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ai_almanac.server.services import data_sources
from ai_almanac.server.services import storage as storage_mod
from tests.range_read_fs import gcs_storage_over

_OBS_DIR = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"
_MODEL_DIR = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"
_ARCHIVE_PREFIX = "gs://bucket/aifs-ens"
_MODEL_METADATA = {"file_pattern": "{}.nc", "model_var": "tp"}


@pytest.fixture(scope="module")
def ensemble_archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An AIFS-ENS-shaped reforecast year whose data dwarfs its header."""
    issue_dates = pd.to_datetime([f"2001-{m:02d}-{d:02d}" for m in (5, 6) for d in (1, 4, 8, 11)])
    lead_times = pd.to_timedelta(np.arange(1, 47), unit="D")
    shape = (4, len(issue_dates), len(lead_times), 40, 40)
    values = np.random.default_rng(0).random(shape, dtype=np.float32) / 100
    archive = xr.Dataset(
        {"tp": (("number", "time", "step", "latitude", "longitude"), values, {"units": "m"})},
        coords={
            "number": np.arange(shape[0]),
            "time": issue_dates,
            "step": lead_times,
            "latitude": np.linspace(15.0, 5.25, 40),
            "longitude": np.linspace(33.0, 42.75, 40),
        },
    )
    path = tmp_path_factory.mktemp("aifs-ens") / "2001.nc"
    archive.to_netcdf(
        path,
        engine="h5netcdf",
        encoding={"tp": {"zlib": True, "chunksizes": (1, 1, len(lead_times), 40, 40)}},
    )
    return path


@pytest.fixture
def archive_storage(ensemble_archive: Path, monkeypatch: pytest.MonkeyPatch):
    # Small blocks keep the fixture small while still dwarfing what is read.
    monkeypatch.setattr(storage_mod, "_METADATA_BLOCK_BYTES", 64 * 2**10)
    storage, fs = gcs_storage_over(ensemble_archive.parent, _ARCHIVE_PREFIX)
    monkeypatch.setattr(storage_mod, "get_storage", lambda: storage)
    return fs


async def _validate_archive() -> tuple[str, str | None, dict]:
    return await data_sources.validate_source("model", _ARCHIVE_PREFIX, _MODEL_METADATA)


@pytest.mark.asyncio
async def test_gcs_obs_source_validates_and_infers_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, _ = gcs_storage_over(_OBS_DIR, "gs://bucket/ethiopia/obs")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: storage)

    status, error, normalized = await data_sources.validate_source(
        "obs",
        "gs://bucket/ethiopia/obs",
        {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
    )

    assert status == "ready", error
    assert normalized["start_year"] == 1998
    assert normalized["end_year"] == 2000
    assert set(normalized["spatial_bounds"]) == {"lat_min", "lat_max", "lon_min", "lon_max"}


@pytest.mark.asyncio
async def test_gcs_model_source_infers_init_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, _ = gcs_storage_over(_MODEL_DIR, "gs://bucket/ethiopia/fuxi")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: storage)

    status, error, normalized = await data_sources.validate_source(
        "model",
        "gs://bucket/ethiopia/fuxi",
        {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"},
    )

    assert status == "ready", error
    assert normalized["init_days_source"] == "inferred"
    assert normalized["init_days"] == "2,5"


@pytest.mark.asyncio
async def test_gcs_ensemble_archive_layout_is_inferred_from_its_header(
    archive_storage,
) -> None:
    status, error, normalized = await _validate_archive()

    assert status == "ready", error
    assert normalized["forecast_dims"] == {
        "lat": "latitude",
        "lon": "longitude",
        "init_time": "time",
        "step": "step",
        "member": "number",
    }
    assert normalized["probabilistic"] is True
    assert normalized["unit_cvt"] == 1000.0
    assert normalized["init_month_days"] == [
        "05-01",
        "05-04",
        "05-08",
        "05-11",
        "06-01",
        "06-04",
        "06-08",
        "06-11",
    ]
    assert (normalized["start_date"], normalized["end_date"]) == ("2001-05-01", "2001-06-11")
    assert normalized["grid_step_deg"] == 0.25
    assert normalized["spatial_bounds"] == {
        "lat_min": 5.25,
        "lat_max": 15.0,
        "lon_min": 33.0,
        "lon_max": 42.75,
    }


@pytest.mark.asyncio
async def test_gcs_archive_inspection_matches_a_full_load(
    archive_storage, ensemble_archive: Path
) -> None:
    _, _, from_header = await _validate_archive()

    _, _, from_full_load = data_sources._finalize_inspection(
        "model", _MODEL_METADATA, [ensemble_archive], lambda: xr.load_dataset(ensemble_archive)
    )

    assert from_header == from_full_load


@pytest.mark.asyncio
async def test_gcs_archive_validation_fetches_a_small_part_of_the_object(
    archive_storage, ensemble_archive: Path
) -> None:
    status, error, _ = await _validate_archive()

    assert status == "ready", error
    assert archive_storage.bytes_fetched < ensemble_archive.stat().st_size / 10


@pytest.mark.asyncio
async def test_gcs_archive_validation_does_not_hold_the_data_in_memory(
    archive_storage, ensemble_archive: Path
) -> None:
    tracemalloc.start()
    try:
        status, error, _ = await _validate_archive()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert status == "ready", error
    assert peak < ensemble_archive.stat().st_size / 10


@pytest.mark.asyncio
async def test_gcs_source_with_no_matching_objects_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, _ = gcs_storage_over(_OBS_DIR, "gs://bucket/ethiopia/obs")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: storage)

    status, error, _ = await data_sources.validate_source(
        "obs",
        "gs://bucket/ethiopia/obs",
        {"obs_file_pattern": "missing_{}.grib", "obs_var": "RAINFALL"},
    )

    assert status == "invalid"
    assert "No files match" in error


@pytest.mark.asyncio
async def test_gcs_source_with_wrong_variable_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, _ = gcs_storage_over(_OBS_DIR, "gs://bucket/ethiopia/obs")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: storage)

    status, error, _ = await data_sources.validate_source(
        "obs",
        "gs://bucket/ethiopia/obs",
        {"obs_file_pattern": "{}.nc", "obs_var": "NOT_A_VAR"},
    )

    assert status == "invalid"
    assert "was not found" in error
