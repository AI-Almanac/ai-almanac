"""Per-grid-point metric maps for a benchmark job.

Every metric NetCDF of a job is parsed once into `JobMapData` and stored with
the job's outputs; map and grid-cell requests read that instead of re-opening
the NetCDFs (~0.3 s each, serialized behind the HDF5 lock).

All functions here are synchronous and intended to be called via
asyncio.to_thread() from the async route handlers in routers/jobs.py.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import PurePosixPath

import numpy as np
from pydantic import BaseModel

from .job_cache import read_or_build
from .metrics import UNIT_MAP
from .storage import StorageBackend

MAP_DATA_NAME = "map_grids.v1.json"
BASELINE_MODEL = "climatology"
# ponytail: 4 decimals keeps days/fractions/mm exact enough for a colour scale
# and halves the stored and served size; raise it if a metric needs more.
_DECIMALS = 4

Rows = list[list[float | None]]


class GridFile(BaseModel):
    """Every 2D variable of one metrics NetCDF (one model × verification window)."""

    model: str
    window: str
    lats: list[float]
    lons: list[float]
    variables: dict[str, Rows]


class JobMapData(BaseModel):
    files: list[GridFile]

    def find(self, model: str, window: str) -> GridFile | None:
        window = _normalize_window(window)
        return next((f for f in self.files if f.model == model and f.window == window), None)


class JobGridResponse(BaseModel):
    job_id: str
    model: str
    window: str
    metric: str
    lats: list[float]
    lons: list[float]
    values: Rows
    unit: str
    min: float
    max: float


class JobGridsResponse(BaseModel):
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
# Building from NetCDFs
# ---------------------------------------------------------------------------


def _normalize_window(window: str) -> str:
    return window.replace(",", "-")


def _is_annual_mae(var: str) -> bool:
    return var.startswith("mae_") and var.removeprefix("mae_").isdigit()


def _model_and_window(path) -> tuple[str, str]:
    """`[e2s_]spatial_metrics_{model}_{window}.nc` → (model, window); models may contain `_`."""
    stem = PurePosixPath(str(path)).name.removesuffix(".nc")
    stem = stem.removeprefix("e2s_").removeprefix("spatial_metrics_")
    model, window = stem.rsplit("_", 1)
    return model, _normalize_window(window)


def _rows(arr: np.ndarray) -> Rows:
    rounded = np.round(arr, _DECIMALS)
    return np.where(np.isnan(rounded), None, rounded).tolist()


def _grid_variables(ds) -> dict[str, Rows]:
    variables: dict[str, Rows] = {}
    for name, da in ds.data_vars.items():
        if not ("lat" in da.dims and "lon" in da.dims and np.issubdtype(da.dtype, np.number)):
            continue
        arr = da.transpose("lat", "lon", ...).squeeze().values.astype(float)
        if arr.ndim == 2:
            variables[str(name)] = _rows(arr)
    return variables


def _grid_file(storage: StorageBackend, path) -> GridFile:
    model, window = _model_and_window(path)
    ds = storage.open_nc_dataset(path)
    try:
        return GridFile(
            model=model,
            window=window,
            lats=[float(v) for v in ds.lat.values],
            lons=[float(v) for v in ds.lon.values],
            variables=_grid_variables(ds),
        )
    finally:
        ds.close()


def build_job_map_data(job_id: str, storage: StorageBackend) -> JobMapData:
    paths = storage.list_nc_output_files(job_id)
    if not paths:
        raise FileNotFoundError(f"No metric grids found for job {job_id}")
    return JobMapData(files=[_grid_file(storage, path) for path in paths])


@lru_cache(maxsize=32)
def load_job_map_data(job_id: str, storage: StorageBackend) -> JobMapData:
    return read_or_build(
        storage, job_id, MAP_DATA_NAME, JobMapData, lambda: build_job_map_data(job_id, storage)
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _grid_response(job_id: str, grid: GridFile, metric: str, values: Rows) -> JobGridResponse:
    valid = np.array(values, dtype=float)
    valid = valid[~np.isnan(valid)]
    return JobGridResponse(
        job_id=job_id,
        model=grid.model,
        window=grid.window,
        metric=metric,
        lats=grid.lats,
        lons=grid.lons,
        values=values,
        unit=UNIT_MAP.get(metric, "days"),
        min=float(valid.min()) if len(valid) else 0.0,
        max=float(valid.max()) if len(valid) else 0.0,
    )


def job_grids(job_id: str, data: JobMapData) -> JobGridsResponse:
    """Every mappable metric grid of the job; per-year MAE stays behind for cells."""
    return JobGridsResponse(
        job_id=job_id,
        grids=[
            _grid_response(job_id, grid, metric, values)
            for grid in data.files
            for metric, values in grid.variables.items()
            if not _is_annual_mae(metric)
        ],
    )


def _nearest(coords: list[float], value: float) -> int:
    return int(np.abs(np.asarray(coords) - value).argmin())


def _cell_value(grid: GridFile | None, var: str, lat: float, lon: float) -> float | None:
    if grid is None or var not in grid.variables:
        return None
    return grid.variables[var][_nearest(grid.lats, lat)][_nearest(grid.lons, lon)]


def _delta(model_value: float | None, baseline_value: float | None) -> float | None:
    if model_value is None or baseline_value is None:
        return None
    return model_value - baseline_value


def _shared_variables(model: GridFile, baseline: GridFile | None) -> set[str]:
    names = set(model.variables)
    return names & set(baseline.variables) if baseline is not None else names


def job_cell(
    job_id: str,
    data: JobMapData,
    model: str,
    window: str,
    lat: float,
    lon: float,
    baseline_model: str = BASELINE_MODEL,
) -> JobCellResponse:
    """Return model-vs-baseline metrics for the grid cell nearest (lat, lon)."""
    model_grid = data.find(model, window)
    if model_grid is None:
        raise FileNotFoundError(f"Grid file for {model}/{window} not found")
    baseline_grid = data.find(baseline_model, window)

    cell_lat = model_grid.lats[_nearest(model_grid.lats, lat)]
    cell_lon = model_grid.lons[_nearest(model_grid.lons, lon)]

    def compare(var: str) -> tuple[float | None, float | None, float | None]:
        model_value = _cell_value(model_grid, var, cell_lat, cell_lon)
        baseline_value = _cell_value(baseline_grid, var, cell_lat, cell_lon)
        return model_value, baseline_value, _delta(model_value, baseline_value)

    shared = _shared_variables(model_grid, baseline_grid)
    metrics = {}
    for metric in sorted(v for v in shared if not _is_annual_mae(v)):
        model_value, baseline_value, delta = compare(metric)
        metrics[metric] = CellMetricComparison(
            model=model_value,
            baseline=baseline_value,
            delta=delta,
            unit=UNIT_MAP.get(metric, "days"),
        )

    years = sorted(int(v.removeprefix("mae_")) for v in shared if _is_annual_mae(v))
    mae_series = []
    for year in years:
        model_value, baseline_value, delta = compare(f"mae_{year}")
        mae_series.append(
            CellMaePoint(year=year, model=model_value, baseline=baseline_value, delta=delta)
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
