from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr


def load_modal_app():
    path = Path(__file__).parents[2] / "modal" / "app.py"
    spec = importlib.util.spec_from_file_location("almanac_modal_app", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_canonicalize_data_array_accepts_era5_daily_dimension_names() -> None:
    modal_app = load_modal_app()
    da = xr.DataArray(
        np.ones((2, 2, 2)),
        dims=("TIME", "LATITUDE", "LONGITUDE"),
        coords={
            "TIME": np.array(["2020-06-02", "2020-06-01"], dtype="datetime64[ns]"),
            "LATITUDE": [11.0, 10.0],
            "LONGITUDE": [41.0, 40.0],
        },
        name="RAINFALL",
    )

    result = modal_app._canonicalize_data_array(da, "RAINFALL")

    assert result.dims == ("time", "lat", "lon")
    assert result.time.values.astype("datetime64[D]").astype(str).tolist() == [
        "2020-06-01",
        "2020-06-02",
    ]
    assert result.lat.values.tolist() == [10.0, 11.0]
    assert result.lon.values.tolist() == [40.0, 41.0]


def test_clip_time_range_limits_e2s_metric_inputs_to_job_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import e2s_metrics_runner

    da = xr.DataArray(
        np.arange(4),
        dims=("time",),
        coords={
            "time": np.array(
                ["2020-05-30", "2020-06-01", "2020-06-02", "2020-06-04"],
                dtype="datetime64[ns]",
            )
        },
    )
    monkeypatch.setenv("ROMP_START_DATE", "2020-06-01")
    monkeypatch.setenv("ROMP_END_DATE", "2020-06-02")

    result = e2s_metrics_runner._clip_time_range(da)

    assert result.values.tolist() == [1, 2]


def test_required_obs_date_ranges_include_climatology_and_end_buffer() -> None:
    modal_app = load_modal_app()

    result = modal_app._required_obs_date_ranges(
        {
            "start_date": "2019-05-01",
            "end_date": "2024-07-31",
            "start_year_clim": 1998,
            "end_year_clim": 2024,
        },
        {"obs_end_buffer_days": 47},
    )

    assert len(result) == 27
    assert result[0] == (datetime(1998, 5, 1), datetime(1998, 9, 16))
    assert result[-1] == (datetime(2024, 5, 1), datetime(2024, 9, 16))


def test_monthly_date_ranges_use_exact_day_labels() -> None:
    modal_app = load_modal_app()

    result = modal_app._monthly_date_ranges(
        datetime(2020, 5, 15), datetime(2020, 7, 2)
    )

    assert result == [
        (2020, 5, [str(day).zfill(2) for day in range(15, 32)]),
        (2020, 6, [str(day).zfill(2) for day in range(1, 31)]),
        (2020, 7, ["01", "02"]),
    ]


def test_split_gcs_uri_rejects_local_paths() -> None:
    modal_app = load_modal_app()

    with pytest.raises(ValueError, match="modal-local"):
        modal_app._split_gcs_uri("/romp-data/ethiopia/obs", "obs_dir")


def test_upload_run_log_skips_empty_outputs_bucket(capsys) -> None:
    modal_app = load_modal_app()

    modal_app._upload_run_log_to_gcs(
        client=object(),
        outputs_bucket="",
        job_id="job-1",
        log_text="failure log",
    )

    assert "cannot upload run log" in capsys.readouterr().out


def test_patch_romp_config_disables_custom_region_climatology_plot(tmp_path: Path) -> None:
    modal_app = load_modal_app()
    config_path = tmp_path / "romp_job.in"
    config_path.write_text("region = 'custom'\n")

    modal_app._patch_romp_config(
        str(config_path),
        {
            "ROMP_REGION": "custom",
            "ROMP_LAT_MIN": "20.0",
            "ROMP_LAT_MAX": "27.0",
            "ROMP_LON_MIN": "88.0",
            "ROMP_LON_MAX": "93.0",
        },
    )

    config = config_path.read_text()

    assert "lat_min = 20.0" in config
    assert "lon_max = 93.0" in config
    assert "plot_climatology_onset = False" in config
    assert "plot_spatial_far_mr_mae = False" in config
    assert "plot_panel_heatmap_error = False" in config


def test_fetch_era5_daily_precip_from_arco_writes_romp_annual_files(
    tmp_path: Path, monkeypatch
) -> None:
    modal_app = load_modal_app()
    times = np.array(
        [
            "2020-05-01T00:00",
            "2020-05-01T01:00",
            "2020-05-02T00:00",
            "2020-05-02T01:00",
        ],
        dtype="datetime64[ns]",
    )
    ds = xr.Dataset(
        {
            "total_precipitation": (
                ("time", "latitude", "longitude"),
                np.ones((4, 2, 2), dtype=float),
            )
        },
        coords={
            "time": times,
            "latitude": [11.0, 10.0],
            "longitude": [40.0, 41.0],
        },
        attrs={
            "valid_time_start": "2020-05-01T00:00:00",
            "valid_time_stop": "2020-05-02T01:00:00",
        },
    )

    monkeypatch.setattr(xr, "open_zarr", lambda *args, **kwargs: ds)

    modal_app._fetch_era5_daily_precip_from_arco(
        {
            "precip_var": "total_precipitation",
            "unit_cvt": 1000.0,
            "lat_bounds": [10, 11],
            "lon_bounds": [40, 41],
            "obs_end_buffer_days": 0,
        },
        {
            "obs_var": "RAINFALL",
            "start_date": "2020-05-01",
            "end_date": "2020-05-02",
            "start_year_clim": 2020,
            "end_year_clim": 2020,
        },
        tmp_path,
    )

    result = xr.open_dataset(tmp_path / "2020.nc")
    try:
        assert result["RAINFALL"].dims == ("TIME", "LATITUDE", "LONGITUDE")
        assert result.sizes["TIME"] == 2
        assert result["RAINFALL"].isel(TIME=0).values.tolist() == [
            [2000.0, 2000.0],
            [2000.0, 2000.0],
        ]
    finally:
        result.close()


def test_e2s_metrics_runner_computes_known_error_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("earth2studio")
    from app.services import e2s_metrics_runner

    obs_dir = tmp_path / "obs"
    model_dir = tmp_path / "model"
    out_dir = tmp_path / "output"
    obs_dir.mkdir()
    model_dir.mkdir()
    out_dir.mkdir()

    time = np.array(["2020-06-01", "2020-06-02", "2020-06-03"], dtype="datetime64[ns]")
    lat = np.array([10.0, 11.0])
    lon = np.array([40.0, 41.0])
    obs = np.array(
        [
            [[1.0, 2.0], [3.0, 4.0]],
            [[2.0, 3.0], [4.0, 5.0]],
            [[3.0, 4.0], [5.0, 6.0]],
        ]
    )
    err = np.array(
        [
            [[1.0, -1.0], [2.0, -2.0]],
            [[2.0, -2.0], [4.0, -4.0]],
            [[3.0, -3.0], [6.0, -6.0]],
        ]
    )
    model = obs + err

    xr.Dataset(
        {"RAINFALL": (("time", "lat", "lon"), obs)},
        coords={"time": time, "lat": lat, "lon": lon},
    ).to_netcdf(obs_dir / "2020.nc")
    xr.Dataset(
        {"tp": (("time", "lat", "lon"), model)},
        coords={"time": time, "lat": lat, "lon": lon},
    ).to_netcdf(model_dir / "2020.nc")

    monkeypatch.setenv("ROMP_OBS_DIR", str(obs_dir))
    monkeypatch.setenv("ROMP_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("ROMP_DIR_OUT", str(out_dir))
    monkeypatch.setenv("ROMP_MODEL_NAME", "e2s-test")
    monkeypatch.setenv("ROMP_OBS_VAR", "RAINFALL")
    monkeypatch.setenv("ROMP_MODEL_VAR", "tp")

    e2s_metrics_runner.main()

    result = xr.open_dataset(out_dir / "e2s_spatial_metrics_e2s-test_all.nc")
    try:
        np.testing.assert_allclose(result["mae"].values, np.mean(np.abs(err), axis=0))
        np.testing.assert_allclose(result["bias"].values, np.mean(err, axis=0))
        np.testing.assert_allclose(result["rmse"].values, np.sqrt(np.mean(err**2, axis=0)))
        assert result["acc"].dims == ("lat", "lon")
    finally:
        result.close()
