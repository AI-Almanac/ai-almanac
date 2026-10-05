"""Spatial metrics aggregation from ROMP and Earth2Studio output NetCDF files.

Per-grid-point maps and cell lookups live in map_data.py.

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
