"""GCS data-source validation — `gs://` sources are inspected like local ones.

A fake storage backend lists `gs://` identifiers and opens the matching real
NetCDF fixtures from `testdata/`, so the full inference path (coverage years,
spatial bounds, variable check) runs without real GCS.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import xarray as xr

from ai_almanac.server.services import data_sources
from ai_almanac.server.services import storage as storage_mod

_OBS_DIR = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"
_MODEL_DIR = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"


class _FakeGcsStorage:
    """Maps a gs:// prefix onto a local fixture directory."""

    def __init__(self, local_dir: Path, gcs_prefix: str) -> None:
        self._local_dir = local_dir
        self._gcs_prefix = gcs_prefix.rstrip("/")

    def list_dataset_files(self, path: str, glob: str) -> list[str]:
        return sorted(
            f"{self._gcs_prefix}/{p.name}" for p in self._local_dir.glob(glob) if p.is_file()
        )

    def open_nc_dataset(self, identifier: str):
        return xr.load_dataset(self._local_dir / identifier.rsplit("/", 1)[-1])


@pytest.mark.asyncio
async def test_gcs_obs_source_validates_and_infers_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeGcsStorage(_OBS_DIR, "gs://bucket/ethiopia/obs")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake)

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
    fake = _FakeGcsStorage(_MODEL_DIR, "gs://bucket/ethiopia/fuxi")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake)

    status, error, normalized = await data_sources.validate_source(
        "model",
        "gs://bucket/ethiopia/fuxi",
        {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"},
    )

    assert status == "ready", error
    assert normalized["init_days_source"] == "inferred"
    assert normalized["init_days"] == "2,5"


@pytest.mark.asyncio
async def test_gcs_source_with_no_matching_objects_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeGcsStorage(_OBS_DIR, "gs://bucket/ethiopia/obs")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake)

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
    fake = _FakeGcsStorage(_OBS_DIR, "gs://bucket/ethiopia/obs")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake)

    status, error, _ = await data_sources.validate_source(
        "obs",
        "gs://bucket/ethiopia/obs",
        {"obs_file_pattern": "{}.nc", "obs_var": "NOT_A_VAR"},
    )

    assert status == "invalid"
    assert "was not found" in error
