"""`open_nc_dataset` loads whole files, so it refuses files too large to hold."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_almanac.server.services import storage as storage_mod
from ai_almanac.server.services.storage import LocalStorage
from tests.range_read_fs import gcs_storage_over

_FIXTURE_DIR = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"
_FIXTURE = sorted(_FIXTURE_DIR.glob("*.nc"))[0]
_GCS_PREFIX = "gs://bucket/fuxi"


@pytest.fixture
def load_limit_below_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_mod, "_MAX_EAGER_NC_BYTES", _FIXTURE.stat().st_size - 1)


def test_gcs_file_under_the_limit_loads_whole() -> None:
    storage, _ = gcs_storage_over(_FIXTURE_DIR, _GCS_PREFIX)

    dataset = storage.open_nc_dataset(f"{_GCS_PREFIX}/{_FIXTURE.name}")

    assert "tp" in dataset.data_vars


@pytest.mark.usefixtures("load_limit_below_fixture")
def test_gcs_file_over_the_limit_is_refused_before_it_is_downloaded() -> None:
    storage, fs = gcs_storage_over(_FIXTURE_DIR, _GCS_PREFIX)

    with pytest.raises(ValueError, match="limit for loading a NetCDF file whole"):
        storage.open_nc_dataset(f"{_GCS_PREFIX}/{_FIXTURE.name}")

    assert fs.bytes_fetched == 0


@pytest.mark.usefixtures("load_limit_below_fixture")
def test_local_file_over_the_limit_is_refused(tmp_path: Path) -> None:
    storage = LocalStorage(upload_dir=tmp_path / "uploads", job_outputs_dir=tmp_path / "jobs")

    with pytest.raises(ValueError, match="limit for loading a NetCDF file whole"):
        storage.open_nc_dataset(_FIXTURE)
