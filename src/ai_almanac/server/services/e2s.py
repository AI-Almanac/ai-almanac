"""Earth2Studio spatial metrics — RMSE, MAE, ACC, bias.

This optional script runs as a subprocess when Earth2Studio is installed in a
custom benchmark environment. The base Pixi environment contains ROMP only;
failure to run this optional phase does not fail the ROMP benchmark.

Inputs (from ROMP_* env vars):
  ROMP_OBS_DIR / ROMP_MODEL_DIR — directories of yearly `.nc` files
  ROMP_DIR_OUT                  — where to write `e2s_spatial_metrics_*.nc`
  ROMP_MODEL_NAME               — included in output filenames + attrs
  ROMP_OBS_VAR / ROMP_MODEL_VAR — variable names within the NetCDF files
  ROMP_TIME_START / ROMP_TIME_END — optional ISO date clip

Obs fetching note: the pre-rearchitecture stack had ARCO-ERA5 / CDS download
helpers that pulled observation data on-demand at job time. Local installs
expect obs files to be pre-staged in `ROMP_OBS_DIR` — users either point at
already-downloaded ERA5 data or use the synthetic `testdata/` bundles. The
ARCO/CDS fetch path will return when the local UI exposes a "download obs"
step that runs once per dataset rather than once per job.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

import xarray as xr

DIM_ALIASES = {
    "TIME": "time",
    "Time": "time",
    "LATITUDE": "lat",
    "latitude": "lat",
    "Latitude": "lat",
    "LONGITUDE": "lon",
    "longitude": "lon",
    "Longitude": "lon",
}


def _select_metric_variable(ds: xr.Dataset, preferred: str) -> xr.DataArray:
    if preferred in ds.data_vars:
        return _canonicalize_data_array(ds[preferred])
    numeric_vars = [
        name
        for name, da in ds.data_vars.items()
        if {"time", "lat", "lon"}.issubset(set(_canonicalize_dim(dim) for dim in da.dims))
    ]
    if not numeric_vars:
        raise RuntimeError(f"No metric variable found; preferred {preferred!r}")
    return _canonicalize_data_array(ds[numeric_vars[0]])


def _canonicalize_dim(dim: str) -> str:
    return DIM_ALIASES.get(dim, dim)


def _canonicalize_data_array(da: xr.DataArray) -> xr.DataArray:
    rename = {dim: _canonicalize_dim(dim) for dim in da.dims if _canonicalize_dim(dim) != dim}
    result = da.rename(rename) if rename else da
    missing = {"time", "lat", "lon"} - set(result.dims)
    if missing:
        raise RuntimeError(f"Metric variable {da.name!r} is missing dimensions: {sorted(missing)}")
    result = result.transpose("time", "lat", "lon")
    if result.lat.values[0] > result.lat.values[-1]:
        result = result.sortby("lat")
    if result.lon.values[0] > result.lon.values[-1]:
        result = result.sortby("lon")
    return result


def _clip_time_range(da: xr.DataArray) -> xr.DataArray:
    start = os.environ.get("ROMP_START_DATE")
    end = os.environ.get("ROMP_END_DATE")
    if start or end:
        return da.sel(time=slice(start, end))
    return da


def _with_e2s_variable_dim(da: xr.DataArray, variable_name: str) -> xr.DataArray:
    if "variable" in da.dims:
        return da.transpose("time", "variable", "lat", "lon")
    return da.expand_dims(variable=[variable_name]).transpose("time", "variable", "lat", "lon")


def _to_e2s_tensor_and_coords(da: xr.DataArray):
    import torch

    coords = OrderedDict((dim, da.coords[dim].values) for dim in da.dims)
    tensor = torch.as_tensor(da.values, dtype=torch.float32)
    return tensor, coords


def _spatial_data_array_from_e2s(result, coords, metric_name: str) -> xr.DataArray:
    values = result.detach().cpu().numpy()
    dims = list(coords)
    da = xr.DataArray(
        values,
        coords={dim: coords[dim] for dim in dims},
        dims=dims,
        name=metric_name,
    )
    if "variable" in da.dims:
        da = da.isel(variable=0, drop=True)
    return da.transpose("lat", "lon")


def _compute_e2s_spatial_statistic(metric_name: str, model_da: xr.DataArray, obs_da: xr.DataArray):
    from earth2studio import statistics

    model_tensor, model_coords = _to_e2s_tensor_and_coords(model_da)
    obs_tensor, obs_coords = _to_e2s_tensor_and_coords(obs_da)

    if metric_name == "bias":
        statistic = statistics.mean(["time"])
        result, coords = statistic(model_tensor - obs_tensor, model_coords)
    else:
        statistic_cls = getattr(statistics, metric_name)
        statistic = statistic_cls(["time"])
        result, coords = statistic(model_tensor, model_coords, obs_tensor, obs_coords)

    return _spatial_data_array_from_e2s(result, coords, metric_name)


def main() -> None:
    obs_dir = Path(os.environ["ROMP_OBS_DIR"])
    model_dir = Path(os.environ["ROMP_MODEL_DIR"])
    out_dir = Path(os.environ["ROMP_DIR_OUT"])
    model_name = os.environ["ROMP_MODEL_NAME"]
    obs_var = os.environ.get("ROMP_OBS_VAR", "RAINFALL")
    model_var = os.environ.get("ROMP_MODEL_VAR", "tp")

    obs_files = sorted(obs_dir.glob("*.nc"))
    model_files = sorted(model_dir.glob("*.nc"))
    if not obs_files or not model_files:
        raise RuntimeError("Cannot compute Earth2Studio metrics without obs and model NetCDF files")

    print("==> Computing Earth2Studio metrics (RMSE, MAE, ACC, bias)...")
    obs_ds = xr.open_mfdataset(obs_files, combine="by_coords")
    model_ds = xr.open_mfdataset(model_files, combine="by_coords")
    try:
        obs_da = _clip_time_range(_select_metric_variable(obs_ds, obs_var))
        model_da = _clip_time_range(_select_metric_variable(model_ds, model_var))

        if not (obs_da.lat.equals(model_da.lat) and obs_da.lon.equals(model_da.lon)):
            model_da = model_da.interp(lat=obs_da.lat, lon=obs_da.lon, method="linear")

        obs_da, model_da = xr.align(obs_da, model_da, join="inner")
        if obs_da.sizes.get("time", 0) == 0:
            raise RuntimeError("No overlapping time steps for Earth2Studio metrics")

        obs_da = _with_e2s_variable_dim(obs_da, "precipitation")
        model_da = _with_e2s_variable_dim(model_da, "precipitation")

        out_ds = xr.Dataset(
            {
                metric: _compute_e2s_spatial_statistic(metric, model_da, obs_da)
                for metric in ("rmse", "mae", "bias", "acc")
            }
        )
        out_ds.attrs["model"] = model_name
        out_ds.attrs["verification_window"] = "all"

        out_path = out_dir / f"e2s_spatial_metrics_{model_name}_all.nc"
        out_ds.to_netcdf(out_path)
        print(f"Earth2Studio metrics saved to: {out_path}")
    finally:
        obs_ds.close()
        model_ds.close()


if __name__ == "__main__":
    main()
