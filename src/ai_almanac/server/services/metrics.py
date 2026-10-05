"""Spatial metrics aggregation from ROMP and Earth2Studio output NetCDF files.

All functions here are synchronous and intended to be called via
asyncio.to_thread() from the async route handlers in routers/jobs.py.
"""

from __future__ import annotations

from functools import lru_cache

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


class JobGrids(BaseModel):
    """Every map-ready grid for one job, built once from its metrics NetCDFs and stored."""

    job_id: str
    grids: list[JobGridResponse]


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
    except ImportError as exc:
        raise RuntimeError("numpy/xarray not available on this server") from exc

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
        tolerance_days = int(ds.attrs["tolerance_days"]) if "tolerance_days" in ds.attrs else None

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


# Rounded so the stored payload stays small; maps and tooltips never show more precision.
_GRID_DECIMALS = 4


def _is_annual_mae(var: str) -> bool:
    return var.startswith("mae_") and var.removeprefix("mae_").isdigit()


def _is_numeric_grid_var(ds, var: str) -> bool:
    import numpy as np

    da = ds[var]
    return "lat" in da.dims and "lon" in da.dims and np.issubdtype(da.dtype, np.number)


def _map_vars(ds) -> list[str]:
    """Grid variables the map can draw; per-year MAE stays out because only the cell
    inspector's time series reads it."""
    return [
        str(var)
        for var in ds.data_vars
        if _is_numeric_grid_var(ds, str(var)) and not _is_annual_mae(str(var))
    ]


def _grid_response(job_id: str, model: str, window: str, metric: str, da) -> JobGridResponse:
    import numpy as np

    da = da.transpose("lat", "lon").squeeze()
    arr = da.values.astype(float)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D array for {metric!r}, got {arr.ndim}D {arr.shape}")
    valid = arr[~np.isnan(arr)]
    return JobGridResponse(
        job_id=job_id,
        model=model,
        window=window,
        metric=metric,
        lats=[float(v) for v in da.lat.values],
        lons=[float(v) for v in da.lon.values],
        values=np.where(np.isnan(arr), None, arr.round(_GRID_DECIMALS)).tolist(),
        unit=UNIT_MAP.get(metric, "days"),
        min=float(valid.min()) if len(valid) else 0.0,
        max=float(valid.max()) if len(valid) else 0.0,
    )


def build_job_grids(job_id: str, storage: StorageBackend) -> JobGrids:
    """Read every metrics NetCDF for a job once and collect all its map grids."""
    grids: list[JobGridResponse] = []
    for nc_file in _get_nc_files(storage, job_id):
        ds = _open_nc(storage, nc_file)
        model = str(ds.attrs.get("model", ""))
        window = str(ds.attrs.get("verification_window", "")).replace(",", "-")
        grids.extend(_grid_response(job_id, model, window, var, ds[var]) for var in _map_vars(ds))
        ds.close()
    return JobGrids(job_id=job_id, grids=grids)


# ponytail: per-instance cache of whole parsed files (~1 MB each); outputs of
# complete jobs never change, so there is no invalidation.
@lru_cache(maxsize=32)
def _metrics_file(storage: StorageBackend, job_id: str, model: str, window: str):
    path = storage.find_nc_output_file(job_id, model, window)
    return _open_nc(storage, path) if path else None


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
    import numpy as np

    ds_model = _metrics_file(storage, job_id, model, window)
    if ds_model is None:
        raise FileNotFoundError(f"Grid file for {model}/{window} not found")
    ds_baseline = _metrics_file(storage, job_id, baseline_model, window)

    lats = ds_model.lat.values.astype(float)
    lons = ds_model.lon.values.astype(float)
    cell_lat = float(lats[int(np.abs(lats - lat).argmin())])
    cell_lon = float(lons[int(np.abs(lons - lon).argmin())])

    def read_cell(ds, var: str) -> float | None:
        if ds is None or var not in ds.data_vars:
            return None
        da = ds[var].transpose("lat", "lon")
        ds_lat_idx = int(np.abs(da.lat.values.astype(float) - cell_lat).argmin())
        ds_lon_idx = int(np.abs(da.lon.values.astype(float) - cell_lon).argmin())
        value = float(da.values[ds_lat_idx, ds_lon_idx])
        return None if np.isnan(value) else value

    def delta(model_value: float | None, baseline_value: float | None) -> float | None:
        if model_value is None or baseline_value is None:
            return None
        return model_value - baseline_value

    metric_vars = set(_map_vars(ds_model))
    annual_vars = {str(var) for var in ds_model.data_vars if _is_annual_mae(str(var))}
    if ds_baseline is not None:
        metric_vars &= set(_map_vars(ds_baseline))
        annual_vars &= {str(var) for var in ds_baseline.data_vars}

    metrics: dict[str, CellMetricComparison] = {}
    for metric in sorted(metric_vars):
        model_value = read_cell(ds_model, metric)
        baseline_value = read_cell(ds_baseline, metric)
        metrics[metric] = CellMetricComparison(
            model=model_value,
            baseline=baseline_value,
            delta=delta(model_value, baseline_value),
            unit=UNIT_MAP.get(metric, "days"),
        )

    mae_series = []
    for year in sorted(int(var.removeprefix("mae_")) for var in annual_vars):
        model_value = read_cell(ds_model, f"mae_{year}")
        baseline_value = read_cell(ds_baseline, f"mae_{year}")
        mae_series.append(
            CellMaePoint(
                year=year,
                model=model_value,
                baseline=baseline_value,
                delta=delta(model_value, baseline_value),
            )
        )

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
