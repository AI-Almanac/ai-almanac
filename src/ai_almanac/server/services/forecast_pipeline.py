"""Live AI weather forecast generation — earth2studio inference + rendering.

Deliberately self-contained: only stdlib and scientific-stack imports
(earth2studio, xarray, numpy, rasterio), never anything else from
`ai_almanac` — this is what lets the exact same file run in two places:

  - Bundled as a single file into the Modal image (see modal/forecasts_app.py,
    which is a thin wrapper handling only Modal-specific plumbing: GPU/image
    selection, Volume scratch space, GCS staging/upload, cross-app calls).
  - Imported normally inside the local `forecast` pixi environment (see
    envs/forecast_entrypoint.py), which has no Modal dependency at all.

Callers resolve model registry entries (server/config/forecast_models.yaml)
and any local/GCS staging themselves and pass plain dicts/Paths in — this
module has no opinion on where the model registry or job config come from.
"""

from __future__ import annotations

import datetime as dt
import gc
import io
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

UTC = dt.UTC

# Bump when the reduction logic, units, or on-disk schema of a cached
# trajectory changes so stale entries become misses instead of being served
# with the wrong shape (D4). It is part of the cache key.
TRAJECTORY_CACHE_VERSION = 1

# Single canonical rollout horizon for the trajectory store (D4). The store
# always holds the full trajectory, so lead is a fixed key segment rather than
# a variable that splits the same rollout across `lead30d`/`lead45d` keys.
CANONICAL_LEAD_DAY = 45


# ---------------------------------------------------------------------------
# earth2studio inference
# ---------------------------------------------------------------------------


def resolve_data_source(init_source: str):
    """Map an init-source name to its earth2studio data source object.

    The init source is part of a trajectory's identity (D6): the same model and
    date initialized from GFS versus ERA5 are different assets, so serving one
    for the other silently corrupts results. Register new earth2studio sources
    (ARCO/ERA5, IFS, GEFS, ...) here by adding to the builder map.
    """
    known = ("gfs",)  # extend as earth2studio sources are wired in (ERA5/ARCO, IFS, GEFS)
    name = init_source.lower()
    if name not in known:
        raise ValueError(
            f"Unknown forecast init source {init_source!r}; known: {sorted(known)}"
        )
    from earth2studio.data import GFS

    return {"gfs": GFS}[name]()


def load_model(model_class: str):
    import torch
    from earth2studio.models import px

    torch.set_default_device("cuda")
    cls = getattr(px, model_class)
    package = cls.load_default_package()
    return cls.load_model(package)


def select_latest_init_time(config: dict) -> list[str]:
    if config.get("init_time"):
        return [config["init_time"]]
    # earth2studio data sources generally align operational inputs to synoptic
    # hours. GFS publication lags the nominal cycle, so back off one full
    # synoptic cycle so an automatic run doesn't select an index NOAA hasn't
    # published yet.
    now = dt.datetime.now(UTC) - dt.timedelta(hours=6)
    hour = (now.hour // 6) * 6
    rounded = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return [rounded.strftime("%Y-%m-%dT%H:%M:%S")]


def lead_steps(lead_hours: list[int], model_class: str) -> tuple[int, int]:
    step_hours = 12 if model_class == "GenCastMini" else 6
    return max(lead_hours) // step_hours, step_hours


def run_forecast_inference(config: dict, model_entry: dict, zarr_path: Path) -> dict:
    """Run one AI weather model against the configured init conditions; write
    its raw output to zarr_path (a plain local path — Modal mode points this at
    Volume-backed scratch space, local mode at a plain temp dir)."""
    from earth2studio.io import ZarrBackend
    from earth2studio.run import deterministic

    model_class = model_entry["earth2studio_class"]
    model = load_model(model_class)
    data = resolve_data_source(config.get("init_source", "gfs"))
    init_time = select_latest_init_time(config)
    nsteps, step_hours = lead_steps(config["lead_hours"], model_class)
    zarr_path.parent.mkdir(parents=True, exist_ok=True)
    io_backend = ZarrBackend(str(zarr_path))
    deterministic(init_time, nsteps, model, data, io_backend)
    return {"init_time": init_time[0], "native_step_hours": step_hours}


# ---------------------------------------------------------------------------
# Season-long inference for live blend scoring.
#
# The blend's onset-detection pipeline expects one forecast *issued* every
# few days across a season (time dim) each with a multi-week lead trajectory
# (day dim) — see testdata/ethiopia/fuxi/2000.nc: time=26 issue dates x
# day=0..45. A single earth2studio run only gives one issue date with a
# short lead window, so this loops the model across the season-to-date on
# the same weekday cadence its historical archive was built on.
# ---------------------------------------------------------------------------


def season_issue_dates(
    season_start_month_day: str, init_weekdays: list[int], year: int
) -> list[dt.date]:
    """Calendar dates in [season_start, today] whose weekday is in init_weekdays.

    Python weekday numbering (Monday=0..Sunday=6), matching this repo's
    convention for model initialization weekdays elsewhere in the codebase.
    """
    month, day = (int(part) for part in season_start_month_day.split("-"))
    start = dt.date(year, month, day)
    today = dt.datetime.now(UTC).date()
    end = min(today, dt.date(year, 12, 31))
    dates = []
    current = start
    while current <= end:
        if current.weekday() in init_weekdays:
            dates.append(current)
        current += dt.timedelta(days=1)
    return dates


def _daily_precip_trajectory(
    model, data, issue_date: dt.date, max_lead_day: int, step_hours: int, scratch_root: Path
):
    """Run one issue date out to max_lead_day and reduce to one daily precip
    total per lead day (ROMP's `day` dimension), at the model's native grid.
    """
    import shutil

    import numpy as np
    import xarray as xr
    from earth2studio.io import ZarrBackend
    from earth2studio.run import deterministic

    nsteps = (max_lead_day * 24) // step_hours
    zarr_path = scratch_root / f"{issue_date.isoformat()}.zarr"
    io_backend = ZarrBackend(str(zarr_path))
    t0 = time.perf_counter()
    deterministic([issue_date.strftime("%Y-%m-%dT%H:%M:%S")], nsteps, model, data, io_backend)
    print(f"    rollout done in {time.perf_counter() - t0:.1f}s", flush=True)
    t0 = time.perf_counter()

    # Open lazily — do NOT call .load() on the full dataset. At global
    # resolution a model like AIFS/FuXi writes 10-20 GB across all pressure
    # levels and variables; loading everything before selecting tp causes OOM
    # after just a few rollouts. Instead, select tp lazily and .compute() one
    # day's sum at a time so peak memory per rollout is ~tp-only.
    dataset = xr.open_zarr(zarr_path)
    tp = select_variable(dataset, "tp").squeeze()
    lat_name, lon_name = lat_lon_names(tp)
    # unit_cvt (from the archived data source's metadata, applied by the
    # caller) converts the model's native units to the mm/day convention
    # ROMP/blending expect.
    # ponytail: native model grid, not reprojected to match the historical
    # archive's exact lat/lon cells — the blend joins by rounded lat/lon id,
    # so close-enough grids still join, just not cell-for-cell identical.
    # Upgrade: regrid to the archived source's exact grid if join quality
    # against historical years turns out to matter.
    lead_hours_coord = (tp["lead_time"].values / np.timedelta64(1, "h")).astype(int)
    daily_totals = []
    for lead_day in range(max_lead_day + 1):
        day_start = lead_day * 24
        day_end = day_start + 24
        mask = (lead_hours_coord >= day_start) & (lead_hours_coord < day_end)
        if not mask.any():
            daily_totals.append(xr.full_like(tp.isel(lead_time=0), np.nan).compute())
            continue
        window = tp.isel(lead_time=np.where(mask)[0])
        # tp06/tp12 are period accumulations (precip since the *previous*
        # step, resetting every step), not cumulative-since-init — so a
        # day's total is the sum of the steps inside it, not a diff across
        # days.
        daily_totals.append(window.sum(dim="lead_time").compute())

    # Release all zarr file handles before rmtree so nothing is left open.
    dataset.close()
    del tp
    print(f"    zarr reduce done in {time.perf_counter() - t0:.1f}s", flush=True)
    shutil.rmtree(zarr_path, ignore_errors=True)

    stacked = xr.concat(daily_totals, dim="day")
    stacked = stacked.assign_coords(day=list(range(max_lead_day + 1)))
    per_day = stacked.clip(min=0)
    result = per_day.transpose("day", lat_name, lon_name)
    return result


def select_lat_lon_bounds(data_array, bounds: dict):
    lat_name, lon_name = lat_lon_names(data_array)
    lat_bounds = (bounds.get("lat_min"), bounds.get("lat_max"))
    lon_bounds = (bounds.get("lon_min"), bounds.get("lon_max"))
    if None in lat_bounds or None in lon_bounds:
        return data_array
    lat_values = data_array[lat_name].values
    reverse = bool(len(lat_values) >= 2 and lat_values[0] > lat_values[-1])
    lat_slice = slice(*sorted(lat_bounds, reverse=reverse))
    return data_array.sel({lat_name: lat_slice, lon_name: slice(min(lon_bounds), max(lon_bounds))})


def cached_trajectory(
    cache_dir: Path | str | None,
    model_id: str,
    init_source: str,
    issue_date: dt.date,
    compute,
):
    """Read-through cache for one issue date's daily-precip trajectory.

    A rollout initialized from a past date is deterministic (fixed weights,
    archived init conditions), so its reduced trajectory never changes — cache
    it and every later forecast job skips that GPU run. Cached at the model's
    native grid and units (unit_cvt/bounds are applied downstream), so one
    entry serves any blend using the model.

    The key is hardened per D4: it carries the init source (a GFS rollout is a
    different asset than the same date from ERA5), a reduction/schema version
    so bumps invalidate stale entries, and the fixed canonical `lead45d`
    horizon so shorter windows never split the same rollout across keys.

    cache_dir may be a local Path or a gs:// URI for durable cloud storage.
    """
    import uuid

    import xarray as xr

    if cache_dir is None:
        return compute(), False

    cache_uri = str(cache_dir)
    rel = (
        f"{model_id}/{init_source}/v{TRAJECTORY_CACHE_VERSION}"
        f"/lead{CANONICAL_LEAD_DAY}d/{issue_date.isoformat()}.nc"
    )

    if cache_uri.startswith("gs://"):
        import gcsfs

        fs = gcsfs.GCSFileSystem()
        # ponytail: no eviction — a season accumulates at most ~26 entries per
        # model; add cleanup if the bucket grows unbounded at season boundaries.
        cache_path = f"{cache_uri.rstrip('/')}/{rel}"
        if fs.exists(cache_path):
            return xr.open_dataarray(cache_path, engine="h5netcdf").load(), True
        trajectory = compute()
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            trajectory.rename("tp").to_netcdf(
                tmp_path, encoding={"tp": {"zlib": True, "complevel": 1}}
            )
            fs.put(str(tmp_path), cache_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return trajectory, False

    # Local path branch — atomic rename avoids partial reads on cache miss.
    cache_file = Path(cache_dir) / rel
    if cache_file.exists():
        return xr.open_dataarray(cache_file).load(), True
    trajectory = compute()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_file.with_name(f".{cache_file.name}.{uuid.uuid4().hex}.tmp")
    trajectory.rename("tp").to_netcdf(
        tmp_path, encoding={"tp": {"zlib": True, "complevel": 1}}
    )
    os.replace(tmp_path, cache_file)
    return trajectory, False


def generate_season_forecast_netcdf(
    model_entry: dict,
    config: dict,
    season_params: dict,
    scratch_root: Path,
    out_path: Path,
    cache_dir: Path | str | None = None,
) -> Path:
    """Loop one model across the current season's issue dates and write
    out_path as a NetCDF matching the historical `{year}.nc` schema (time x
    day x lat x lon, variable tp). Caller decides scratch_root/out_path —
    Modal mode uses container-local temp dirs, local mode plain temp dirs.
    With cache_dir set, past issue dates are served from cache and only new
    ones are rolled out (see cached_trajectory). cache_dir may be a local
    Path or a gs:// URI.
    """
    import numpy as np
    import xarray as xr

    model_class = model_entry["earth2studio_class"]
    # D4: the store always holds the full canonical 45-day trajectory, so shorter
    # windows never split the same rollout across cache keys. The max_lead_day
    # knob is retired for the season path; use max_issue_dates to trim smoke cost.
    lead_day = CANONICAL_LEAD_DAY
    init_source = config.get("init_source", "gfs")
    init_weekdays = [int(d) for d in str(season_params.get("init_days") or "0,3").split(",")]
    unit_cvt = float(season_params.get("unit_cvt", 1.0))
    bounds = season_params.get("spatial_bounds")

    year = dt.datetime.now(UTC).year
    issue_dates = season_issue_dates(
        config.get("season_start_month_day") or "05-01", init_weekdays, year
    )
    if not issue_dates:
        raise ValueError(f"No issue dates for season {year} yet")
    max_issue_dates = config.get("max_issue_dates")
    if max_issue_dates:
        # Smoke-test knob: score against only the most recent N issue dates
        # instead of the whole season-to-date, to validate the pipeline
        # end-to-end without paying for a full season's worth of rollouts.
        issue_dates = issue_dates[-int(max_issue_dates):]

    _, step_hours = lead_steps([lead_day * 24], model_class)
    nsteps = (lead_day * 24) // step_hours
    scratch_root.mkdir(parents=True, exist_ok=True)

    # Loading model weights is expensive; defer until the first cache miss so
    # a fully cached season never touches the GPU.
    loaded: dict[str, Any] = {}

    def _rollout(issue_date: dt.date):
        if not loaded:
            loaded["model"] = load_model(model_class)
            loaded["data"] = resolve_data_source(init_source)
        return _daily_precip_trajectory(
            loaded["model"], loaded["data"], issue_date, lead_day, step_hours, scratch_root
        )

    print(
        f"==> Season loop: {len(issue_dates)} issue date(s) "
        f"({issue_dates[0]}..{issue_dates[-1]}), {nsteps} rollout steps "
        f"({step_hours}h each) per issue date",
        flush=True,
    )
    slice_paths: list[Path] = []
    for i, issue_date in enumerate(issue_dates, start=1):
        t0 = time.perf_counter()
        print(f"  [{i}/{len(issue_dates)}] season inference: issue date {issue_date}", flush=True)
        trajectory, from_cache = cached_trajectory(
            cache_dir,
            model_entry["id"],
            init_source,
            issue_date,
            lambda date=issue_date: _rollout(date),
        )
        print(
            f"  [{i}/{len(issue_dates)}] "
            f"{'cached' if from_cache else f'done in {time.perf_counter() - t0:.1f}s'}",
            flush=True,
        )
        # Write each slice to scratch immediately and release the in-memory
        # array — accumulating all N trajectories before xr.concat causes OOM
        # on long seasons. open_mfdataset reassembles lazily via dask below.
        traj = trajectory.assign_coords(time=np.datetime64(issue_date)) * unit_cvt
        if bounds:
            traj = select_lat_lon_bounds(traj, bounds)
        slice_path = scratch_root / f"slice_{i:03d}.nc"
        traj.rename("tp").to_netcdf(slice_path)
        slice_paths.append(slice_path)
        del trajectory, traj
        gc.collect()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with xr.open_mfdataset(slice_paths, concat_dim="time", combine="nested") as ds:
        ds.to_netcdf(out_path)
    return out_path


def bundle_files(files: list[Path]) -> bytes:
    """Tar.gz a set of files, matching modal/blending_app.py's forecast_bundles
    convention (one bundle per model, containing its `{year}.nc` files)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=path.name)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Variable extraction + COG rendering (map-visualization deliverable)
# ---------------------------------------------------------------------------


def variable_candidates(variable: str) -> tuple[str, ...]:
    known = {
        "t2m": ("t2m", "2t", "t2m_0m", "temperature_2m"),
        "u10": ("u10", "10u", "u10m", "u-component_of_wind_10m"),
        "v10": ("v10", "10v", "v10m", "v-component_of_wind_10m"),
        "tcwv": ("tcwv", "pwat"),
        "msl": ("msl", "prmsl"),
        "u850": ("u850",),
        "v850": ("v850",),
        "q850": ("q850",),
        "z500": ("z500",),
        # AIFS/FuXi/GraphCastSmall name their precip output "tp06" (6h
        # accumulation); GenCastMini's 12h-step output is "tp12". None of the
        # registered models expose a plain "tp".
        "tp": ("tp06", "tp12", "tp"),
    }
    return known.get(variable, (variable,))


def variable_unit(variable: str) -> str:
    return {
        "t2m": "K",
        "wind10m": "m/s",
        "wind850m": "m/s",
        "tcwv": "kg/m²",
        "msl": "Pa",
        "u850": "m/s",
        "v850": "m/s",
        "q850": "kg/kg",
        "z500": "m²/s²",
    }.get(variable, "")


def array_with_variable_coord(dataset):
    for name in ("fields", "output", "forecast"):
        if name in dataset and "variable" in dataset[name].coords:
            return dataset[name]
    for data_array in dataset.data_vars.values():
        if "variable" in data_array.coords:
            return data_array
    return None


def select_variable(dataset, variable: str):
    candidates = variable_candidates(variable)
    data_array = array_with_variable_coord(dataset)
    if data_array is not None:
        available = [str(v) for v in data_array.coords["variable"].values]
        for candidate in candidates:
            if candidate in available:
                return data_array.sel(variable=candidate)
        raise KeyError(f"None of {candidates} found in variables {available[:12]}")

    for candidate in candidates:
        if candidate in dataset.data_vars:
            return dataset[candidate]
    available = list(dataset.data_vars)
    raise KeyError(f"None of {candidates} found in forecast arrays {available[:12]}")


def select_lead(data_array, lead_hour: int):
    import numpy as np

    if "lead_time" not in data_array.coords:
        return data_array
    target = np.timedelta64(lead_hour, "h")
    return data_array.sel(lead_time=target, method="nearest")


def lat_lon_names(data_array) -> tuple[str, str]:
    lat_name = next((n for n in ("lat", "latitude", "y") if n in data_array.coords), None)
    lon_name = next((n for n in ("lon", "longitude", "x") if n in data_array.coords), None)
    if lat_name is None or lon_name is None:
        raise KeyError("Forecast output is missing latitude/longitude coordinates")
    return lat_name, lon_name


def field_for_frame(dataset, variable: str, lead_hour: int):
    import numpy as np

    if variable == "wind10m":
        u = select_lead(select_variable(dataset, "u10"), lead_hour)
        v = select_lead(select_variable(dataset, "v10"), lead_hour)
        return np.sqrt(u.squeeze() ** 2 + v.squeeze() ** 2)
    if variable == "wind850m":
        u = select_lead(select_variable(dataset, "u850"), lead_hour)
        v = select_lead(select_variable(dataset, "v850"), lead_hour)
        return np.sqrt(u.squeeze() ** 2 + v.squeeze() ** 2)
    return select_lead(select_variable(dataset, variable), lead_hour).squeeze()


def grid_values(field, max_lat: int = 360, max_lon: int = 720):
    import numpy as np

    lat_name, lon_name = lat_lon_names(field)
    grid = field.squeeze()
    for dim in list(grid.dims):
        if dim not in {lat_name, lon_name}:
            grid = grid.isel({dim: 0})
    grid = grid.transpose(lat_name, lon_name)
    lat_step = max(1, int(np.ceil(grid.sizes[lat_name] / max_lat)))
    lon_step = max(1, int(np.ceil(grid.sizes[lon_name] / max_lon)))
    grid = grid.isel({lat_name: slice(None, None, lat_step), lon_name: slice(None, None, lon_step)})
    values = np.asarray(grid.values, dtype="<f4")
    lats = np.asarray(grid[lat_name].values, dtype=float)
    lons = np.asarray(grid[lon_name].values, dtype=float)
    normalized_lons = ((lons + 180.0) % 360.0) - 180.0
    lon_order = np.argsort(normalized_lons)
    values = values[:, lon_order]
    lons = normalized_lons[lon_order]
    if lats[0] < lats[-1]:
        values = values[::-1, :]
        lats = lats[::-1]
    return values, lats, lons


def write_cog(values, bounds_lonlat: tuple[float, float, float, float], cog_path: Path):
    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    west, south, east, north = bounds_lonlat
    height, width = values.shape
    src_transform = from_bounds(west, south, east, north, width, height)
    src_crs = CRS.from_epsg(4326)
    dst_crs = CRS.from_epsg(3857)
    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs, dst_crs, width, height, west, south, east, north
    )
    dst = np.full((dst_height, dst_width), np.nan, dtype="float32")
    reproject(
        values.astype("float32"),
        dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )

    cog_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        cog_path,
        "w",
        driver="GTiff",
        height=dst_height,
        width=dst_width,
        count=1,
        dtype="float32",
        crs=dst_crs,
        transform=dst_transform,
        nodata=np.nan,
        tiled=True,
        blockxsize=256,
        blockysize=256,
        compress="DEFLATE",
        predictor=3,
        BIGTIFF="IF_SAFER",
    ) as dataset:
        dataset.write(dst, 1)
        dataset.build_overviews([2, 4, 8, 16], Resampling.average)
        dataset.update_tags(ns="rio_overview", resampling="average")
    return cog_path


def _render_variable_products(
    dataset, variable: str, lead_hours: list[int], product_root: Path
) -> dict[str, dict] | None:
    import logging

    import numpy as np

    logger = logging.getLogger(__name__)
    fields: dict[int, tuple] = {}
    mins: list[float] = []
    maxes: list[float] = []

    for lead_hour in lead_hours:
        try:
            values, lats, lons = grid_values(field_for_frame(dataset, variable, lead_hour))
        except Exception as exc:
            logger.warning("Skipping %s at lead %dh: %s", variable, lead_hour, exc)
            continue
        finite = values[np.isfinite(values)]
        if finite.size:
            p2, p98 = np.percentile(finite, [2, 98])
            mins.append(float(p2))
            maxes.append(float(p98))
        bounds_lonlat = (
            float(np.nanmin(lons)),
            float(max(np.nanmin(lats), -85.05112878)),
            float(np.nanmax(lons)),
            float(min(np.nanmax(lats), 85.05112878)),
        )
        fields[lead_hour] = (values, bounds_lonlat)

    if not mins or not maxes or not fields:
        return None

    minimum = min(mins)
    maximum = max(maxes)
    products: dict[str, dict] = {}

    for lead_hour, (values, bounds_lonlat) in fields.items():
        cog_path = product_root / "rasters" / variable / f"{lead_hour}.tif"
        write_cog(values, bounds_lonlat, cog_path)
        products[str(lead_hour)] = {
            "unit": variable_unit(variable),
            "crs": "EPSG:3857",
            "cog": f"rasters/{variable}/{lead_hour}.tif",
            "bounds_lonlat": list(bounds_lonlat),
            "min": minimum,
            "max": maximum,
        }

    return products


def write_map_products(config: dict, zarr_path: Path, product_root: Path) -> dict:
    """One COG per (variable, lead_hour). TiTiler serves tiles from these at request time."""
    import logging

    import xarray as xr

    logger = logging.getLogger(__name__)
    dataset = xr.open_zarr(zarr_path)
    products: dict[str, dict[str, dict]] = {}

    for variable in config["variables"]:
        variable_products = _render_variable_products(
            dataset, variable, config["lead_hours"], product_root
        )
        if variable_products is not None:
            products[variable] = variable_products
        else:
            logger.warning("No products rendered for variable %s — skipping", variable)

    return products


def render_forecast_products(
    config: dict,
    model_id: str,
    model_entry: dict,
    run_info: dict,
    zarr_path: Path,
    product_root: Path,
) -> dict[str, Any]:
    """Render COGs + manifest.json for one model's forecast into product_root
    (a plain local dir — caller uploads/copies it wherever job outputs live)."""
    manifest = {
        "job_id": config.get("job_id"),
        "model_id": model_id,
        "model_name": model_entry["display_name"],
        "init_time": run_info.get("init_time"),
        "native_step_hours": run_info.get("native_step_hours"),
        "variables": config["variables"],
        "lead_hours": config["lead_hours"],
        "data_source": "GFS",
        "created_at": dt.datetime.now(UTC).isoformat(),
    }
    manifest["map_products"] = write_map_products(config, zarr_path, product_root)
    (product_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
