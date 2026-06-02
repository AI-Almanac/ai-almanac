from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from app.services.runner import (
    ModalRunner,
    _build_modal_local_bundle,
    _modal_local_runtime_env,
    _romp_config_override_lines,
    _romp_entry_command,
)

GRAPHICS_OVERRIDES = (
    "plot_spatial_far_mr_mae = False",
    "plot_heatmap_bss_auc = False",
    "plot_reliability = False",
    "plot_portrait = False",
    "plot_climatology_onset = False",
    "plot_panel_heatmap_error = False",
    "plot_panel_heatmap_skill = False",
    "plot_bar_bss_rpss_auc = False",
)


def tar_names(bundle: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
        return set(tar.getnames())


def test_modal_local_bundle_packages_obs_and_requested_model_years(
    tmp_path: Path,
) -> None:
    obs_dir = tmp_path / "obs"
    model_dir = tmp_path / "model"
    obs_dir.mkdir()
    model_dir.mkdir()
    (obs_dir / "1998.nc").write_text("obs-1998")
    (obs_dir / "1999.nc").write_text("obs-1999")
    (model_dir / "1998.nc").write_text("model-1998")
    (model_dir / "1999.nc").write_text("model-1999")
    (model_dir / "2000.nc").write_text("model-2000")

    bundle = _build_modal_local_bundle(
        {
            "obs_dir": str(obs_dir),
            "model_dir": str(model_dir),
            "dataset_config": {"provider": "local"},
            "romp_params": {"start_date": "1998-05-01", "end_date": "1999-07-31"},
        }
    )

    assert tar_names(bundle) == {
        "obs/1998.nc",
        "obs/1999.nc",
        "model/1998.nc",
        "model/1999.nc",
    }


def test_modal_local_bundle_requires_requested_model_years(tmp_path: Path) -> None:
    obs_dir = tmp_path / "obs"
    model_dir = tmp_path / "model"
    obs_dir.mkdir()
    model_dir.mkdir()
    (obs_dir / "1998.nc").write_text("obs-1998")
    (model_dir / "2000.nc").write_text("model-2000")

    with pytest.raises(ValueError, match="1998.nc"):
        _build_modal_local_bundle(
            {
                "obs_dir": str(obs_dir),
                "model_dir": str(model_dir),
                "dataset_config": {"provider": "local"},
                "romp_params": {"start_date": "1998-05-01", "end_date": "1998-07-31"},
            }
        )


def test_modal_local_bundle_packages_only_model_for_earth2studio_dataset(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "1998.nc").write_text("model-1998")
    (model_dir / "1999.nc").write_text("model-1999")

    bundle = _build_modal_local_bundle(
        {
            "obs_dir": None,
            "model_dir": str(model_dir),
            "dataset_config": {"provider": "earth2studio"},
            "romp_params": {"start_date": "1998-05-01", "end_date": "1998-07-31"},
        }
    )

    assert tar_names(bundle) == {"model/1998.nc"}


def test_modal_local_bundle_packages_only_model_for_arco_dataset(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "1998.nc").write_text("model-1998")
    (model_dir / "1999.nc").write_text("model-1999")

    bundle = _build_modal_local_bundle(
        {
            "obs_dir": None,
            "model_dir": str(model_dir),
            "dataset_config": {"provider": "era5_arco"},
            "romp_params": {"start_date": "1998-05-01", "end_date": "1998-07-31"},
        }
    )

    assert tar_names(bundle) == {"model/1998.nc"}


def test_modal_local_runtime_env_requires_cdsapi_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.cdsapi_key", "")

    with pytest.raises(ValueError, match="CDSAPI_KEY"):
        _modal_local_runtime_env({"dataset_config": {"provider": "earth2studio"}})


def test_modal_local_runtime_env_forwards_cdsapi_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.cdsapi_key", "test-key")
    monkeypatch.setattr(
        "app.config.settings.cdsapi_url", "https://cds.example.test/api"
    )

    assert _modal_local_runtime_env(
        {"dataset_config": {"provider": "earth2studio"}}
    ) == {"CDSAPI_URL": "https://cds.example.test/api", "CDSAPI_KEY": "test-key"}


def test_romp_config_overrides_disable_custom_region_climatology_plot() -> None:
    overrides = _romp_config_override_lines(
        {
            "ROMP_REGION": "custom",
            "ROMP_LAT_MIN": "20.0",
            "ROMP_LAT_MAX": "27.0",
            "ROMP_LON_MIN": "88.0",
            "ROMP_LON_MAX": "93.0",
            "ROMP_LAND_ONLY": "false",
        }
    )

    assert "lat_min = 20.0" in overrides
    assert "lon_max = 93.0" in overrides
    assert "land_only = False" in overrides
    for line in GRAPHICS_OVERRIDES:
        assert line in overrides


def test_romp_entry_command_skips_e2s_metrics_by_default() -> None:
    command = _romp_entry_command("plot_climatology_onset = False")

    assert command[0] == "-c"
    assert "generate_config.py" in command[1]
    assert "ALMANAC_ROMP_OVERRIDES" in command[1]
    assert "momp-run -p" in command[1]
    assert "python3 /almanac/e2s_metrics_runner.py" not in command[1]


def test_romp_entry_command_runs_e2s_metrics_when_enabled() -> None:
    command = _romp_entry_command(
        "plot_climatology_onset = False", compute_e2s_metrics=True
    )

    assert command[0] == "-c"
    assert "generate_config.py" in command[1]
    assert "ALMANAC_ROMP_OVERRIDES" in command[1]
    assert "momp-run -p" in command[1]
    assert "python3 /almanac/e2s_metrics_runner.py" in command[1]
    assert "Earth2Studio metrics failed" in command[1]


def test_modal_runner_rejects_local_input_paths() -> None:
    runner = ModalRunner(outputs_bucket="outputs-bucket", job_timeout_seconds=60)

    error = runner._preflight_error(
        {
            "obs_dir": "/romp-data/ethiopia/obs",
            "model_dir": "gs://bucket/models/fuxi",
            "dataset_config": {"provider": "local"},
        }
    )

    assert error is not None
    assert "JOB_RUNNER=modal requires obs_dir" in error
    assert "modal-local" in error


def test_modal_runner_allows_remote_obs_with_gcs_model_path() -> None:
    runner = ModalRunner(outputs_bucket="outputs-bucket", job_timeout_seconds=60)

    error = runner._preflight_error(
        {
            "obs_dir": None,
            "model_dir": "gs://bucket/models/fuxi",
            "dataset_config": {"provider": "era5_arco"},
        }
    )

    assert error is None


def test_modal_runner_requires_outputs_bucket() -> None:
    runner = ModalRunner(outputs_bucket="", job_timeout_seconds=60)

    error = runner._preflight_error(
        {
            "obs_dir": "gs://bucket/obs",
            "model_dir": "gs://bucket/models/fuxi",
            "dataset_config": {"provider": "local"},
        }
    )

    assert error is not None
    assert "GCS_OUTPUTS_BUCKET" in error
