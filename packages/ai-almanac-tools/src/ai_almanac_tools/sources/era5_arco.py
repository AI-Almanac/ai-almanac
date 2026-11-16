"""ERA5 hourly reanalysis from Google's public Analysis-Ready, Cloud-Optimized (ARCO) store."""

from __future__ import annotations

import gc
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import fsspec
import pandas as pd
import xarray as xr

from ..bbox import BBox
from ..contract import rename_to_canonical
from .catalog import Fetched, FetchRequest, Source, Variable

URL = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
PRECIP_VARIABLE = "total_precipitation"
METRES_TO_MM = 1000.0

_VARIABLE_ALIASES = {
    "tp": ("tp", "total_precipitation"),
    "total_precipitation": ("total_precipitation", "tp"),
}

logger = logging.getLogger(__name__)


def open_hourly(url: str = URL, variable: str = PRECIP_VARIABLE) -> xr.DataArray:
    """Lazily open one hourly variable, trimmed to the store's valid time range."""
    _limit_zarr_concurrency()
    # trust_env lets a sandbox's HTTPS proxy, the only way out of it, carry the reads.
    storage_options = {"token": "anon", "session_kwargs": {"trust_env": True}}
    ds = xr.open_zarr(url, chunks=None, storage_options=storage_options)
    valid_start, valid_stop = ds.attrs.get("valid_time_start"), ds.attrs.get("valid_time_stop")
    if valid_start and valid_stop:
        ds = ds.sel(time=slice(valid_start, valid_stop))
    return rename_to_canonical(ds[_resolve_variable(ds, variable)])


def record_end(url: str = URL) -> date:
    """The last day of the record, from the store's 176-byte root attributes."""
    with fsspec.open(f"{url}/.zattrs", token="anon") as handle:
        return date.fromisoformat(json.load(handle)["valid_time_stop"][:10])


def full_days(hourly: xr.DataArray) -> tuple[date, date]:
    """The first and last days for which ``hourly`` has all 24 hours."""
    first, last = pd.Timestamp(hourly.time.values[0]), pd.Timestamp(hourly.time.values[-1])
    first_day = first.date() if first.hour == 0 else first.date() + timedelta(days=1)
    last_day = last.date() if last.hour == 23 else last.date() - timedelta(days=1)
    return first_day, last_day


def require_coverage(hourly: xr.DataArray, start: date, end: date) -> None:
    """Raise if any day in ``start``..``end`` lacks a full day of hourly data.

    A day outside the store would otherwise sum zero hours and read as 0 mm of rain.
    """
    first_day, last_day = full_days(hourly)
    if start < first_day or end > last_day:
        raise ValueError(f"ERA5 ARCO covers {first_day} to {last_day}; requested {start} to {end}")


def write_daily_months(
    hourly: xr.DataArray,
    start: date,
    end: date,
    out_dir: Path,
    *,
    bbox: BBox | None = None,
    scale: float = METRES_TO_MM,
    name: str = "precip",
) -> dict[int, list[Path]]:
    """Write daily totals of ``hourly`` for ``start``..``end``, one NetCDF file per month.

    Only one month is held in memory at a time. Returns the files grouped by year.
    """
    require_coverage(hourly, start, end)
    box = bbox or BBox(lat=None, lon=None)
    by_year: dict[int, list[Path]] = {}
    for first, last in month_spans(start, end):
        logger.info("  month %s: %s to %s, %s", f"{first:%Y-%m}", first, last, box)
        days = []
        for day in pd.date_range(first, last, freq="D").date:
            logger.info("    loading ARCO day %s", day)
            days.append(daily_total(hourly, day, box, scale))
            gc.collect()
        path = out_dir / f".era5_arco_{first:%Y_%m}.nc"
        xr.concat(days, dim="time").to_dataset(name=name).to_netcdf(path)
        logger.info("    wrote temporary %s", path.name)
        by_year.setdefault(first.year, []).append(path)
        del days
        gc.collect()
    return by_year


def daily_total(hourly: xr.DataArray, day: date, bbox: BBox, scale: float) -> xr.DataArray:
    """Sum the 24 hourly values of ``day`` inside ``bbox``, loaded, with a length-1 time."""
    midnight = datetime(day.year, day.month, day.day)
    window = bbox.select(hourly.sel(time=slice(midnight, midnight + timedelta(hours=23))))
    window = window.transpose("time", "lat", "lon").sortby(["lat", "lon"])
    total = (window.astype("float32").sum(dim="time") * scale).astype("float32").load()
    return total.expand_dims(time=[pd.Timestamp(day)])


def month_spans(start: date, end: date) -> list[tuple[date, date]]:
    """Split ``start``..``end`` (inclusive) into per-month (first, last) day pairs."""
    spans = []
    first = start
    while first <= end:
        next_month = (first.replace(day=1) + timedelta(days=32)).replace(day=1)
        last = min(end, next_month - timedelta(days=1))
        spans.append((first, last))
        first = next_month
    return spans


def _resolve_variable(ds: xr.Dataset, configured: str) -> str:
    for name in _VARIABLE_ALIASES.get(configured, (configured,)):
        if name in ds.data_vars:
            return name
    available = ", ".join(sorted(map(str, ds.data_vars)))
    raise KeyError(
        f"ARCO ERA5 variable {configured!r} is not available. Available variables: {available}"
    )


def _limit_zarr_concurrency() -> None:
    """One concurrent chunk read keeps a season's fetch inside a job's memory limit."""
    import zarr

    os.environ.setdefault("ZARR_ASYNC_CONCURRENCY", "1")
    zarr.config.set({"async.concurrency": 1})


def fetch(request: FetchRequest, out_dir: Path, scratch_dir: Path) -> Fetched:
    """Daily total precipitation for ``request`` as one NetCDF file in ``out_dir``."""
    hourly = open_hourly()
    months = write_daily_months(
        hourly, request.start, request.end, scratch_dir, bbox=request.bbox, name="precip"
    )
    parts = []
    for path in (path for year in sorted(months) for path in months[year]):
        with xr.open_dataset(path) as month:
            parts.append(month["precip"].load())
        path.unlink()
    precip = xr.concat(parts, dim="time")
    precip.attrs.update(units="mm day-1", long_name="Daily total precipitation")
    out = precip.to_dataset().assign_attrs(source=SOURCE.description, source_url=URL)
    path = out_dir / f"era5_arco_precip_{request.start}_{request.end}.nc"
    out.to_netcdf(path)
    return Fetched(path, source_version=f"record through {full_days(hourly)[1]}")


SOURCE = Source(
    name="era5_arco",
    version="1.0.0",
    description="ERA5 reanalysis (ECMWF) from Google's ARCO store: 0.25° global grid, 1940 to about three months ago",
    hosts=("storage.googleapis.com",),
    grid_deg=0.25,
    variables={"precip": Variable("Daily total precipitation", "mm day-1")},
    fetch=fetch,
    record_end=record_end,
)
