"""Spatial metrics aggregation from ROMP and Earth2Studio output NetCDF files.

All functions here are synchronous and intended to be called via
asyncio.to_thread() from the async route handlers in routers/jobs.py.
"""

from __future__ import annotations

from pydantic import BaseModel

from .storage import StorageBackend

# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

UNIT_MAP: dict[str, str] = {
    "false_alarm_rate": "fraction",
    "miss_rate": "fraction",
    "rmse": "mm",
    "mae": "mm",
    "acc": "dimensionless",
    "bias": "mm",
}


class MetricStats(BaseModel):
    mean: float
    min: float
    max: float
    p25: float
    p50: float
    p75: float
    p90: float
    unit: str


class WindowMetrics(BaseModel):
    window: str
    model: str
    tolerance_days: int | None
    metrics: dict[str, MetricStats]


class GridInfo(BaseModel):
    lats: list[float]
    lons: list[float]


class JobMetrics(BaseModel):
    job_id: str
    windows: list[WindowMetrics]
    grid: GridInfo | None = None
    bbox: dict | None = None


class JobGridResponse(BaseModel):
    job_id: str
    model: str
    window: str
    metric: str
    lats: list[float]
    lons: list[float]
    values: list[list[float | None]]
    unit: str
    min: float
    max: float


class CellMetricComparison(BaseModel):
    model: float | None
    baseline: float | None
    delta: float | None
    unit: str


class CellMaePoint(BaseModel):
    year: int
    model: float | None
    baseline: float | None
    delta: float | None


class JobCellResponse(BaseModel):
    job_id: str
    model: str
    window: str
    requested_lat: float
    requested_lon: float
    lat: float
    lon: float
    metrics: dict[str, CellMetricComparison]
    mae_series: list[CellMaePoint]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_nc_files(storage: StorageBackend, job_id: str) -> list:
    return storage.list_nc_output_files(job_id)


def _open_nc(storage: StorageBackend, path):
    return storage.open_nc_dataset(path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_job_metrics(
    job_id: str,
    storage: StorageBackend,
    lat_min: float | None = None,
    lat_max: float | None = None,
    lon_min: float | None = None,
    lon_max: float | None = None,
) -> JobMetrics:
    """Aggregate spatial_metrics_*.nc and e2s_spatial_metrics_*.nc files into stats."""
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy/xarray not available on this server")

    nc_files = _get_nc_files(storage, job_id)
    has_bbox = any(v is not None for v in (lat_min, lat_max, lon_min, lon_max))

    windows: list[WindowMetrics] = []
    grid_info: GridInfo | None = None
    actual_bbox: dict | None = None

    for nc_file in nc_files:
        ds = _open_nc(storage, nc_file)

        if grid_info is None and "lat" in ds.coords and "lon" in ds.coords:
            grid_info = GridInfo(
                lats=[float(v) for v in sorted(ds.lat.values)],
                lons=[float(v) for v in sorted(ds.lon.values)],
            )

        if has_bbox:
            lats = ds.lat.values
            lons = ds.lon.values
            if lat_min is not None:
                lats = lats[lats >= lat_min]
            if lat_max is not None:
                lats = lats[lats <= lat_max]
            if lon_min is not None:
                lons = lons[lons >= lon_min]
            if lon_max is not None:
                lons = lons[lons <= lon_max]
            ds = ds.sel(lat=lats, lon=lons)

        if actual_bbox is None and "lat" in ds.coords and "lon" in ds.coords:
            lats = ds.lat.values
            lons = ds.lon.values
            if len(lats) and len(lons):
                actual_bbox = {
                    "lat_min": float(lats.min()),
                    "lat_max": float(lats.max()),
                    "lon_min": float(lons.min()),
                    "lon_max": float(lons.max()),
                }

        model = str(ds.attrs.get("model", ""))
        window_label = str(ds.attrs.get("verification_window", "")).replace(",", "-")
        tolerance_days = (
            int(ds.attrs["tolerance_days"]) if "tolerance_days" in ds.attrs else None
        )

        metrics: dict[str, MetricStats] = {}
        for var in ds.data_vars:
            arr = ds[var].values.astype(float)
            valid = arr[~np.isnan(arr)]
            if len(valid) == 0:
                continue
            var_str = str(var)
            metrics[var_str] = MetricStats(
                mean=float(np.mean(valid)),
                min=float(np.min(valid)),
                max=float(np.max(valid)),
                p25=float(np.percentile(valid, 25)),
                p50=float(np.percentile(valid, 50)),
                p75=float(np.percentile(valid, 75)),
                p90=float(np.percentile(valid, 90)),
                unit=UNIT_MAP.get(var_str, "days"),
            )
        ds.close()
        windows.append(
            WindowMetrics(
                window=window_label,
                model=model,
                tolerance_days=tolerance_days,
                metrics=metrics,
            )
        )

    windows.sort(key=lambda w: (w.model == "climatology", w.window))
    return JobMetrics(job_id=job_id, windows=windows, grid=grid_info, bbox=actual_bbox)


def compute_job_grid(
    job_id: str,
    storage: StorageBackend,
    model: str,
    window: str,
    metric: str,
) -> JobGridResponse:
    """Load a single metrics NetCDF file and return the raw 2D grid."""
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy/xarray not available on this server")

    match = storage.find_nc_output_file(job_id, model, window)
    if not match:
        raise FileNotFoundError(f"Grid file for {model}/{window} not found")

    ds = _open_nc(storage, match)
    if metric not in ds.data_vars:
        ds.close()
        raise KeyError(f"Metric {metric!r} not found in grid")

    da = ds[metric]
    if "lat" in da.dims and "lon" in da.dims:
        da = da.transpose("lat", "lon")
    da = da.squeeze()

    lats = [float(v) for v in da.lat.values]
    lons = [float(v) for v in da.lon.values]
    arr = da.values.astype(float)

    if arr.ndim != 2:
        ds.close()
        raise ValueError(f"Expected 2D array, got {arr.ndim}D {arr.shape}")

    values = np.where(np.isnan(arr), None, arr).tolist()
    unit = UNIT_MAP.get(metric, "days")
    valid = arr[~np.isnan(arr)]
    v_min = float(valid.min()) if len(valid) > 0 else 0.0
    v_max = float(valid.max()) if len(valid) > 0 else 0.0

    ds.close()
    return JobGridResponse(
        job_id=job_id,
        model=model,
        window=window,
        metric=metric,
        lats=lats,
        lons=lons,
        values=values,
        unit=unit,
        min=v_min,
        max=v_max,
    )


def compute_job_cell(
    job_id: str,
    storage: StorageBackend,
    model: str,
    window: str,
    lat: float,
    lon: float,
    baseline_model: str = "climatology",
) -> JobCellResponse:
    """Return model-vs-baseline metrics for the nearest grid cell."""
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy/xarray not available on this server")

    model_match = storage.find_nc_output_file(job_id, model, window)
    if not model_match:
        raise FileNotFoundError(f"Grid file for {model}/{window} not found")

    baseline_match = storage.find_nc_output_file(job_id, baseline_model, window)

    ds_model = _open_nc(storage, model_match)
    ds_baseline = _open_nc(storage, baseline_match) if baseline_match else None

    try:
        lats = ds_model.lat.values.astype(float)
        lons = ds_model.lon.values.astype(float)
        lat_idx = int(np.abs(lats - lat).argmin())
        lon_idx = int(np.abs(lons - lon).argmin())
        cell_lat = float(lats[lat_idx])
        cell_lon = float(lons[lon_idx])

        def read_cell(ds, var: str) -> float | None:
            if var not in ds.data_vars:
                return None
            da = ds[var]
            if "lat" in da.dims and "lon" in da.dims:
                da = da.transpose("lat", "lon")
            ds_lats = da.lat.values.astype(float)
            ds_lons = da.lon.values.astype(float)
            ds_lat_idx = int(np.abs(ds_lats - cell_lat).argmin())
            ds_lon_idx = int(np.abs(ds_lons - cell_lon).argmin())
            value = float(da.values[ds_lat_idx, ds_lon_idx])
            return None if np.isnan(value) else value

        def is_numeric_grid_var(ds, var: str) -> bool:
            da = ds[var]
            return (
                "lat" in da.dims
                and "lon" in da.dims
                and np.issubdtype(da.dtype, np.number)
            )

        def is_annual_mae(var: str) -> bool:
            return var.startswith("mae_") and var.removeprefix("mae_").isdigit()

        def delta(
            model_value: float | None, baseline_value: float | None
        ) -> float | None:
            if model_value is None or baseline_value is None:
                return None
            return model_value - baseline_value

        metrics: dict[str, CellMetricComparison] = {}
        model_vars = {
            str(var)
            for var in ds_model.data_vars
            if is_numeric_grid_var(ds_model, str(var)) and not is_annual_mae(str(var))
        }
        if ds_baseline is not None:
            baseline_vars = {
                str(var)
                for var in ds_baseline.data_vars
                if is_numeric_grid_var(ds_baseline, str(var))
                and not is_annual_mae(str(var))
            }
            metric_vars = sorted(model_vars.intersection(baseline_vars))
        else:
            metric_vars = sorted(model_vars)

        for metric in metric_vars:
            model_value = read_cell(ds_model, metric)
            baseline_value = (
                read_cell(ds_baseline, metric) if ds_baseline is not None else None
            )
            metrics[metric] = CellMetricComparison(
                model=model_value,
                baseline=baseline_value,
                delta=delta(model_value, baseline_value),
                unit=UNIT_MAP.get(metric, "days"),
            )

        if ds_baseline is not None:
            annual_vars = set(ds_model.data_vars).intersection(ds_baseline.data_vars)
        else:
            annual_vars = set(ds_model.data_vars)
        years = sorted(
            int(str(var).removeprefix("mae_"))
            for var in annual_vars
            if is_annual_mae(str(var))
        )
        mae_series = []
        for year in years:
            var = f"mae_{year}"
            model_value = read_cell(ds_model, var)
            baseline_value = (
                read_cell(ds_baseline, var) if ds_baseline is not None else None
            )
            mae_series.append(
                CellMaePoint(
                    year=year,
                    model=model_value,
                    baseline=baseline_value,
                    delta=delta(model_value, baseline_value),
                )
            )
    finally:
        ds_model.close()
        if ds_baseline is not None:
            ds_baseline.close()

    return JobCellResponse(
        job_id=job_id,
        model=model,
        window=window,
        requested_lat=lat,
        requested_lon=lon,
        lat=cell_lat,
        lon=cell_lon,
        metrics=metrics,
        mae_series=mae_series,
    )
