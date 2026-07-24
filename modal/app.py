"""
Modal app for AI Almanac.

Defines the ROMP job runner as a Modal function. Deploy with:
    modal deploy modal/app.py

The backend calls this via modal.Function.from_name() — no direct import needed.

Production GCS secrets (create once via CLI):
    modal secret create gcp-service-account SERVICE_ACCOUNT_JSON="$(cat key.json)"
    modal secret create gcr-credentials REGISTRY_USERNAME="_json_key" REGISTRY_PASSWORD="$(cat key.json)"

For GCS-free dev deployment, set:
    ALMANAC_MODAL_ENABLE_GCS=0
    ALMANAC_MODAL_ROMP_IMAGE_URI=<image Modal can pull>
    ALMANAC_MODAL_GCR_SECRET_NAME=   # empty when the image does not need auth
"""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    import xarray as xr

app = modal.App("almanac-romp")

ENABLE_GCS_FUNCTIONS = os.environ.get("ALMANAC_MODAL_ENABLE_GCS", "1").lower() not in {
    "0",
    "false",
    "no",
}
ROMP_IMAGE_URI = os.environ.get(
    "ALMANAC_MODAL_ROMP_IMAGE_URI",
    "us-central1-docker.pkg.dev/ai-almanac/almanac/romp:latest",
)
GCR_SECRET_NAME = os.environ.get("ALMANAC_MODAL_GCR_SECRET_NAME", "gcr-credentials")
GCP_SECRET_NAME = os.environ.get("ALMANAC_MODAL_GCP_SECRET_NAME", "gcp-service-account")
E2S_SECRET_NAME = os.environ.get("ALMANAC_MODAL_E2S_SECRET_NAME", "")


def _media_type_for_filename(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    return "application/octet-stream"


def _build_runner_script(code: str, compute_call: str) -> str:
    return f"""\
import json
import os
import traceback
from pathlib import Path

ARTIFACT_DIR = Path(os.environ["ARTIFACT_DIR"])
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def media_type_for_filename(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    return "application/octet-stream"

def save_figure(fig, filename="figure.webp", format=None, label=None, **savefig_kwargs):
    if format is None:
        suffix = Path(filename).suffix.lower()
        format = suffix.lstrip(".") if suffix else "webp"
    path = ARTIFACT_DIR / filename
    defaults = {{"dpi": 150, "bbox_inches": "tight"}}
    defaults.update(savefig_kwargs)
    fig.savefig(path, format=format, **defaults)
    return {{
        "kind": "figure",
        "filename": filename,
        "label": label,
        "media_type": media_type_for_filename(filename),
    }}

{code}

try:
    result = {compute_call}
    if not isinstance(result, dict):
        result = {{"value": result}}
    artifacts = []
    if isinstance(result.get("artifacts"), list):
        artifacts.extend(result.pop("artifacts"))
    forbidden_keys = {{"image", "image_data", "figure", "figure_data"}}
    bad_keys = sorted(key for key in result.keys() if key in forbidden_keys)
    if bad_keys:
        raise ValueError(
            "Do not return base64 or inline image data. "
            "Use save_figure(...) and return the artifact under 'artifacts'. "
            f"Forbidden keys: {{', '.join(bad_keys)}}"
        )
    print(json.dumps({{"ok": True, "result": result, "artifacts": artifacts}}))
except Exception as exc:
    print(json.dumps({{"ok": False, "error": str(exc), "traceback": traceback.format_exc()}}))
"""


def _run_generated_code(
    code: str, compute_call: str, extra_env: dict[str, str] | None = None, timeout: int = 90
) -> dict:
    import json as _json

    artifact_dir = Path(tempfile.mkdtemp(prefix="chat-artifacts-"))
    runner_script = _build_runner_script(code, compute_call)
    env = dict(os.environ)
    env["ARTIFACT_DIR"] = str(artifact_dir)
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        ["python", "-c", runner_script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if not stdout.strip():
        return {"ok": False, "error": stderr or "Generated code produced no output"}

    try:
        payload = _json.loads(stdout.strip())
    except _json.JSONDecodeError:
        return {"ok": False, "error": f"Non-JSON output: {stdout[:500]}", "stderr": stderr[:500]}

    if not isinstance(payload, dict):
        return {"ok": False, "error": "Generated code returned an invalid payload"}
    if not payload.get("ok"):
        return payload

    enriched_artifacts = []
    for artifact in payload.get("artifacts", []):
        filename = artifact.get("filename")
        if not filename:
            continue
        path = artifact_dir / filename
        if not path.exists():
            return {"ok": False, "error": f"Artifact file not found: {filename}"}
        enriched_artifacts.append(
            {
                **artifact,
                "data": path.read_bytes(),
                "media_type": artifact.get("media_type") or _media_type_for_filename(filename),
            }
        )
    payload["artifacts"] = enriched_artifacts
    return payload


@app.local_entrypoint()
def test(job_id: str, config_json: str, outputs_bucket: str):
    """CLI smoke-test entry: modal run modal/app.py --job-id=x --config-json='{...}' --outputs-bucket=y"""
    import json

    run_benchmark.remote(job_id, json.loads(config_json), outputs_bucket)


# Use the base ROMP image — the wrapper image's entrypoint would run at container
# start before Modal's runner, causing failures. The Modal function handles all
# staging itself so the wrapper is not needed.
romp_image = modal.Image.from_registry(
    ROMP_IMAGE_URI,
    secret=modal.Secret.from_name(GCR_SECRET_NAME) if GCR_SECRET_NAME else None,
).dockerfile_commands(
    [
        # Clear the relative-path entrypoint so Modal can layer on top without
        # it trying to exec scripts/entrypoint.sh from the wrong working directory.
        "ENTRYPOINT []",
        "CMD []",
        "RUN pip install --no-cache-dir google-cloud-storage",
    ]
)

gcp_secret = modal.Secret.from_name(GCP_SECRET_NAME) if ENABLE_GCS_FUNCTIONS else None
e2s_secret = modal.Secret.from_name(E2S_SECRET_NAME) if E2S_SECRET_NAME else None

E2S_METRICS_RUNNER = (
    Path(__file__).resolve().parents[1] / "backend/app/services/e2s_metrics_runner.py"
)

# Extends the ROMP image with earth2studio for metrics and public data readers.
benchmark_image = romp_image.pip_install("earth2studio[data]", "gcsfs", "zarr").add_local_file(
    E2S_METRICS_RUNNER, "/almanac/e2s_metrics_runner.py"
)


# ---------------------------------------------------------------------------
# Shared staging helpers
# ---------------------------------------------------------------------------


def _split_gcs_uri(uri: str, label: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(
            f"{label} must be a GCS URI for Modal production runs; got {uri!r}. "
            "Use JOB_RUNNER=modal-local for local filesystem inputs."
        )
    bucket_name, _, prefix = uri.removeprefix("gs://").partition("/")
    if not bucket_name:
        raise ValueError(f"{label} has no bucket name: {uri!r}")
    return bucket_name, prefix


def _stage_gcs_prefix(client, uri: str, local_dir: Path) -> int:
    """Download all direct children of a GCS prefix into local_dir."""
    from google.cloud.storage import transfer_manager

    bucket_name, prefix = _split_gcs_uri(uri, "obs_dir")
    prefix = prefix.rstrip("/") + "/"
    bucket = client.bucket(bucket_name)
    names = [
        blob.name[len(prefix) :]
        for blob in client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        if blob.name[len(prefix) :] and not blob.name[len(prefix) :].endswith("/")
    ]
    if names:
        results = transfer_manager.download_many_to_path(
            bucket,
            names,
            str(local_dir),
            blob_name_prefix=prefix,
            worker_type="thread",
        )
        for name, result in zip(names, results, strict=False):
            if isinstance(result, Exception):
                print(f"  obs FAILED: {name}: {result}")
    return len(names)


def _stage_gcs_years(client, uri: str, local_dir: Path, start_year: int, end_year: int) -> int:
    """Download {year}.nc files from a GCS prefix filtered to the given year range."""
    from google.cloud.storage import transfer_manager

    bucket_name, prefix = _split_gcs_uri(uri, "model_dir")
    prefix = prefix.rstrip("/") + "/"
    year_names = {f"{y}.nc" for y in range(start_year, end_year + 1)}
    bucket = client.bucket(bucket_name)
    names = [
        blob.name[len(prefix) :]
        for blob in client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        if blob.name[len(prefix) :] in year_names
    ]
    if names:
        results = transfer_manager.download_many_to_path(
            bucket,
            names,
            str(local_dir),
            blob_name_prefix=prefix,
            worker_type="thread",
        )
        for name, result in zip(names, results, strict=False):
            if isinstance(result, Exception):
                print(f"  model FAILED: {name}: {result}")
    return len(names)


def _extract_local_bundle(bundle: bytes, stage_root: Path) -> tuple[Path, Path]:
    """Extract a backend-created obs/model tarball for GCS-free dev runs."""
    bundle_path = stage_root / "inputs.tar.gz"
    bundle_path.write_bytes(bundle)
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (stage_root / member.name).resolve()
            if not str(target).startswith(str(stage_root.resolve())):
                raise ValueError(f"Unsafe path in input bundle: {member.name}")
        tar.extractall(stage_root)
    return stage_root / "obs", stage_root / "model"


def _log(message: str) -> None:
    print(message, flush=True)


class _LogTee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _size_summary(obj) -> str:
    sizes = getattr(obj, "sizes", {})
    if not sizes:
        return "size=unknown"
    return ", ".join(f"{name}={value}" for name, value in sizes.items())


def _configure_cdsapi_from_env() -> None:
    """Materialize CDSAPI_URL/CDSAPI_KEY into the rc file cdsapi expects."""
    cdsapi_key = os.environ.get("CDSAPI_KEY")
    if not cdsapi_key:
        return

    cdsapi_url = os.environ.get("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
    rc_path = Path(os.environ.get("CDSAPI_RC", str(Path.home() / ".cdsapirc")))
    rc_path.parent.mkdir(parents=True, exist_ok=True)
    rc_path.write_text(f"url: {cdsapi_url}\nkey: {cdsapi_key}\n")
    rc_path.chmod(0o600)
    os.environ["CDSAPI_RC"] = str(rc_path)


def _required_obs_date_ranges(
    romp_params: dict,
    dataset_config: dict,
) -> list[tuple[datetime, datetime]]:
    start = datetime.strptime(romp_params.get("start_date", "2019-05-01"), "%Y-%m-%d")
    end = datetime.strptime(romp_params.get("end_date", "2024-07-31"), "%Y-%m-%d")
    start_year = int(romp_params.get("start_year_clim", start.year))
    end_year = int(romp_params.get("end_year_clim", end.year))
    end_buffer_days = int(dataset_config.get("obs_end_buffer_days", 47))

    fallback_date = romp_params.get("fallback_date")
    if isinstance(fallback_date, str):
        fallback_month_day = datetime.strptime(fallback_date, "%m-%d")
        start_month, start_day = min(
            (start.month, start.day),
            (fallback_month_day.month, fallback_month_day.day),
        )
    else:
        start_month, start_day = start.month, start.day

    years = range(min(start.year, start_year), max(end.year, end_year) + 1)
    return [
        (
            datetime(year, start_month, start_day),
            datetime(year, end.month, end.day) + timedelta(days=end_buffer_days),
        )
        for year in years
    ]


def _daily_dates(ranges: list[tuple[datetime, datetime]]) -> list[datetime]:
    dates: list[datetime] = []
    seen: set[datetime] = set()
    for start, end in ranges:
        current = start
        while current <= end:
            if current not in seen:
                seen.add(current)
                dates.append(current)
            current += timedelta(days=1)
    return sorted(dates)


def _monthly_date_ranges(
    start: datetime,
    end: datetime,
) -> list[tuple[int, int, list[str]]]:
    current = datetime(start.year, start.month, 1)
    result: list[tuple[int, int, list[str]]] = []
    while current <= end:
        if current.month == 12:
            next_month = datetime(current.year + 1, 1, 1)
        else:
            next_month = datetime(current.year, current.month + 1, 1)
        month_start = max(start, current)
        month_end = min(end, next_month - timedelta(days=1))
        days = [f"{day:02d}" for day in range(month_start.day, month_end.day + 1)]
        result.append((current.year, current.month, days))
        current = next_month
    return result


def _remote_obs_provider(config: dict) -> str:
    return str(config.get("provider", "local")).lower()


def _uses_remote_obs(config: dict) -> bool:
    return _remote_obs_provider(config) in {"earth2studio", "era5_arco"}


def _fetch_remote_obs(dataset_config: dict, romp_params: dict, local_obs: Path) -> None:
    provider = _remote_obs_provider(dataset_config)
    _log(f"==> Preparing remote obs provider={provider}")
    if provider == "era5_arco":
        _fetch_era5_daily_precip_from_arco(dataset_config, romp_params, local_obs)
        return
    if provider == "earth2studio":
        _fetch_e2s_obs(dataset_config, romp_params, local_obs)
        return
    raise ValueError(f"Unsupported remote obs provider: {provider!r}")


def _fetch_e2s_obs(dataset_config: dict, romp_params: dict, local_obs: Path) -> None:
    """Fetch obs via earth2studio DataSource and write annual {year}.nc files."""
    import importlib
    from collections import defaultdict

    import pandas as pd
    import xarray as xr

    configured_class_name = dataset_config.get("e2s_class", "CDS")
    data_source_aliases = {
        # Earth2Studio exposes ERA5 reanalysis through the CDS data source.
        "ERA5": "CDS",
    }
    e2s_class_name = data_source_aliases.get(configured_class_name, configured_class_name)
    precip_var = dataset_config.get("precip_var", "tp")
    unit_cvt = float(dataset_config.get("unit_cvt", 1.0))
    obs_var = romp_params.get("obs_var", "RAINFALL")
    lat_bounds = dataset_config.get("lat_bounds")
    lon_bounds = dataset_config.get("lon_bounds")
    ranges = _required_obs_date_ranges(romp_params, dataset_config)
    dates = _daily_dates(ranges)

    _log(f"==> Fetching e2s obs ({e2s_class_name}, var={precip_var}): {len(dates)} dates")
    _configure_cdsapi_from_env()

    if e2s_class_name == "CDS" and precip_var in {"tp", "total_precipitation"}:
        _fetch_era5_daily_precip_from_cds(
            dataset_config=dataset_config,
            romp_params=romp_params,
            local_obs=local_obs,
            ranges=ranges,
        )
        return

    data_module = importlib.import_module("earth2studio.data")
    try:
        DataSource = getattr(data_module, e2s_class_name)
    except AttributeError as exc:
        available = ", ".join(sorted(name for name in dir(data_module) if name[:1].isupper()))
        raise AttributeError(
            f"Earth2Studio data source {configured_class_name!r} resolved to "
            f"{e2s_class_name!r}, but it is not available. Available sources: {available}"
        ) from exc
    data_source = DataSource()

    by_year: dict[int, list] = defaultdict(list)
    chunk = 365
    for i in range(0, len(dates), chunk):
        batch = dates[i : i + chunk]
        _log(f"  e2s batch: {batch[0].date()} to {batch[-1].date()}")
        da = data_source(batch, [precip_var])  # (time, variable, lat, lon)
        _log(f"  e2s batch loaded: {_size_summary(da)}")
        da = da * unit_cvt

        lat_dim = "lat" if "lat" in da.dims else "latitude"
        lon_dim = "lon" if "lon" in da.dims else "longitude"
        if lat_bounds:
            da = da.sel({lat_dim: slice(*lat_bounds)})
        if lon_bounds:
            da = da.sel({lon_dim: slice(*lon_bounds)})

        precip = da.sel(variable=precip_var).drop_vars("variable")
        for t_val in precip.time.values:
            year = pd.Timestamp(t_val).year
            by_year[year].append(precip.sel(time=t_val))

    for year, slices in sorted(by_year.items()):
        by_year[year] = [_canonicalize_data_array(xr.concat(slices, dim="time"), obs_var)]
    _write_romp_daily_obs_files(by_year, obs_var, local_obs)


def _coord_slice(values, bounds: list[float] | tuple[float, float]):
    low, high = min(bounds), max(bounds)
    if len(values) >= 2 and float(values[0]) > float(values[-1]):
        return slice(high, low)
    return slice(low, high)


def _select_lat_lon_bounds(da, lat_bounds, lon_bounds):
    selectors = {}
    if lat_bounds:
        selectors["lat"] = _coord_slice(da["lat"].values, lat_bounds)
    if lon_bounds:
        selectors["lon"] = _coord_slice(da["lon"].values, lon_bounds)
    if not selectors:
        return da
    return da.sel(selectors)


def _era5_arco_variable(ds, configured_name: str) -> str:
    aliases = {
        "tp": ["tp", "total_precipitation"],
        "total_precipitation": ["total_precipitation", "tp"],
    }
    for name in aliases.get(configured_name, [configured_name]):
        if name in ds.data_vars:
            return name
    available = ", ".join(sorted(ds.data_vars))
    raise KeyError(
        f"ARCO ERA5 variable {configured_name!r} is not available. Available variables: {available}"
    )


def _rename_canonical_coords(da):
    rename = {
        name: canonical
        for name in set(da.dims).union(da.coords)
        if (canonical := _canonical_dim_name(str(name))) != name
    }
    return da.rename(rename)


def _write_romp_daily_obs_files(by_year: dict[int, list], obs_var: str, local_obs: Path) -> None:
    import numpy as np
    import pandas as pd
    import xarray as xr

    for year, parts in sorted(by_year.items()):
        _log(f"  writing obs file for {year}: {len(parts)} daily chunks")
        da = xr.concat(parts, dim="time").sortby("time")
        _, unique_indexes = np.unique(pd.to_datetime(da.time.values), return_index=True)
        da = da.isel(time=sorted(unique_indexes))
        da = da.rename({"time": "TIME", "lat": "LATITUDE", "lon": "LONGITUDE"})
        da = da.transpose("TIME", "LATITUDE", "LONGITUDE")
        da.name = obs_var
        out_ds = da.to_dataset()
        out_ds[obs_var].attrs.update({"units": "mm/day", "time_step": "day"})
        out_path = local_obs / f"{year}.nc"
        _log(f"  saving {out_path.name}: {_size_summary(out_ds)}")
        out_ds.to_netcdf(out_path)
        _log(f"  wrote: {out_path.name} ({out_ds.sizes.get('TIME', 0)} days)")


def _write_romp_daily_obs_file(year: int, da, obs_var: str, local_obs: Path) -> None:
    _write_romp_daily_obs_files({year: [da]}, obs_var, local_obs)


def _configure_zarr_for_low_memory() -> None:
    os.environ.setdefault("ZARR_ASYNC_CONCURRENCY", "1")
    try:
        import zarr

        zarr.config.set({"async.concurrency": 1})
    except Exception as exc:
        _log(f"  zarr low-memory config not applied: {exc}")


def _load_arco_daily_precip_day(
    source,
    day: datetime,
    unit_cvt: float,
    lat_bounds,
    lon_bounds,
):
    import gc

    import pandas as pd

    day_end = day + timedelta(hours=23)
    hourly = source.sel(time=slice(day, day_end))
    hourly = _select_lat_lon_bounds(hourly, lat_bounds, lon_bounds)
    hourly = hourly.transpose("time", "lat", "lon").sortby(["lat", "lon"])
    daily = (hourly.astype("float32").sum(dim="time") * unit_cvt).astype("float32").load()
    daily = daily.expand_dims(time=[pd.Timestamp(day)])
    del hourly
    gc.collect()
    return daily


def _write_arco_month_file(
    source,
    year: int,
    month: int,
    days: list[str],
    unit_cvt: float,
    lat_bounds,
    lon_bounds,
    obs_var: str,
    local_obs: Path,
) -> Path:
    import gc

    import xarray as xr

    daily_parts = []
    for day_label in days:
        day = datetime(year, month, int(day_label))
        _log(f"    loading ARCO day {day.date()}")
        daily_parts.append(
            _load_arco_daily_precip_day(source, day, unit_cvt, lat_bounds, lon_bounds)
        )

    month_daily = xr.concat(daily_parts, dim="time").sortby("time")
    month_path = local_obs / f".era5_arco_{year}_{month:02d}.nc"
    month_daily.to_dataset(name=obs_var).to_netcdf(month_path)
    _log(f"    wrote temporary {month_path.name}: {_size_summary(month_daily)}")
    del daily_parts, month_daily
    gc.collect()
    return month_path


def _fetch_era5_daily_precip_from_arco(
    dataset_config: dict,
    romp_params: dict,
    local_obs: Path,
) -> None:
    import gc

    import xarray as xr

    source_url = dataset_config.get(
        "arco_url",
        "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
    )
    precip_var = dataset_config.get("precip_var", "total_precipitation")
    obs_var = romp_params.get("obs_var", "RAINFALL")
    unit_cvt = float(dataset_config.get("unit_cvt", 1000.0))
    lat_bounds = dataset_config.get("lat_bounds")
    lon_bounds = dataset_config.get("lon_bounds")
    ranges = _required_obs_date_ranges(romp_params, dataset_config)

    first_start = min(start for start, _ in ranges)
    last_end = max(end for _, end in ranges)
    requested_days = sum((end - start).days + 1 for start, end in ranges)
    _log(
        "==> Fetching ERA5 daily total precipitation from ARCO ERA5 "
        f"({first_start.date()} to {last_end.date()}, "
        f"windows={len(ranges)}, days={requested_days})"
    )

    _configure_zarr_for_low_memory()

    for index, (start, end) in enumerate(ranges, start=1):
        _log(f"  ARCO window {index}/{len(ranges)}: processing {start.date()} to {end.date()}")

        # Open a fresh store for each window so the Zarr chunk cache is bounded to
        # one season's worth of reads and is fully released when we close the store.
        _log(f"  opening ARCO Zarr store: {source_url}")
        ds = xr.open_zarr(source_url, chunks=None, storage_options={"token": "anon"})
        _log(f"  opened ARCO store: variables={len(ds.data_vars)}, {_size_summary(ds)}")
        valid_start = ds.attrs.get("valid_time_start")
        valid_stop = ds.attrs.get("valid_time_stop")
        if valid_start and valid_stop:
            ds = ds.sel(time=slice(valid_start, valid_stop))
        data_var = _era5_arco_variable(ds, precip_var)
        source = _rename_canonical_coords(ds[data_var])
        _log(f"  prepared ARCO source variable: {_size_summary(source)}")

        month_paths: dict[int, list[Path]] = {}
        for year, month, days in _monthly_date_ranges(start, end):
            month_start = datetime(year, month, int(days[0]))
            month_end = datetime(year, month, int(days[-1]))
            _log(
                f"  ARCO window {index}/{len(ranges)} month {year}-{month:02d}: "
                f"selecting {month_start.date()} to {month_end.date()}, "
                f"lat={lat_bounds or 'all'} lon={lon_bounds or 'all'}"
            )
            month_path = _write_arco_month_file(
                source=source,
                year=year,
                month=month,
                days=days,
                unit_cvt=unit_cvt,
                lat_bounds=lat_bounds,
                lon_bounds=lon_bounds,
                obs_var=obs_var,
                local_obs=local_obs,
            )
            month_paths.setdefault(year, []).append(month_path)
            gc.collect()

        # Close the store before merging month files; the merge reads only from disk.
        ds.close()
        del source, ds
        gc.collect()

        for year, paths in sorted(month_paths.items()):
            datasets = [xr.open_dataset(path) for path in paths]
            try:
                parts = [
                    _canonicalize_data_array(ds_part[obs_var].astype(float), obs_var).load()
                    for ds_part in datasets
                ]
            finally:
                for ds_part in datasets:
                    ds_part.close()
            _write_romp_daily_obs_files({year: parts}, obs_var, local_obs)
            for path in paths:
                path.unlink(missing_ok=True)
            del datasets, parts
            gc.collect()


def _fetch_cds_month(
    client,
    dataset_name: str,
    year: int,
    month: int,
    days: list[str],
    window_start: datetime,
    window_end: datetime,
    unit_cvt: float,
    lat_bounds,
    lon_bounds,
    local_obs: Path,
) -> xr.DataArray:
    """Download one month from CDS, load it into memory, and delete all temp files."""
    import shutil

    import xarray as xr

    request: dict = {
        "product_type": "reanalysis",
        "variable": ["total_precipitation"],
        "year": str(year),
        "month": [f"{month:02d}"],
        "day": days,
        "daily_statistic": "daily_sum",
        "time_zone": "utc+00:00",
        "frequency": "1_hourly",
        "data_format": "netcdf",
        "download_format": "zip",
    }
    if lat_bounds and lon_bounds:
        south, north = min(lat_bounds), max(lat_bounds)
        west, east = min(lon_bounds), max(lon_bounds)
        request["area"] = [north, west, south, east]

    target = local_obs / f"era5_daily_precip_{year}_{month:02d}.zip"
    extract_dir = local_obs / f"era5_daily_precip_{year}_{month:02d}"
    print(
        f"  requesting {year}-{month:02d}: {len(days)} days, area={request.get('area', 'global')}"
    )

    try:
        client.retrieve(dataset_name, request, str(target))

        nc_files: list[Path] = []
        if zipfile.is_zipfile(target):
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target) as archive:
                archive.extractall(extract_dir)
            nc_files = sorted(extract_dir.glob("*.nc"))
        else:
            nc_files = [target]

        if not nc_files:
            raise RuntimeError(
                f"CDS daily precipitation request for {year}-{month:02d} returned no NetCDF files"
            )

        datasets = [xr.open_dataset(path) for path in nc_files]
        try:
            ds = xr.merge(datasets) if len(datasets) > 1 else datasets[0]
            data_var = "tp" if "tp" in ds.data_vars else "total_precipitation"
            if data_var not in ds.data_vars:
                data_var = next(iter(ds.data_vars))
            da = _canonicalize_data_array(ds[data_var].astype(float), data_var)
            da = da.sel(time=slice(window_start, window_end)) * unit_cvt
            return da.load()
        finally:
            for ds_part in datasets:
                ds_part.close()
    finally:
        # Delete temp files immediately so disk usage doesn't accumulate across months.
        target.unlink(missing_ok=True)
        if extract_dir.exists():
            shutil.rmtree(extract_dir)


def _fetch_era5_daily_precip_from_cds(
    dataset_config: dict,
    romp_params: dict,
    local_obs: Path,
    ranges: list[tuple[datetime, datetime]],
) -> None:
    """Fetch ERA5 daily total precipitation directly from CDS daily statistics."""
    import gc

    import cdsapi

    obs_var = romp_params.get("obs_var", "RAINFALL")
    unit_cvt = float(dataset_config.get("unit_cvt", 1000.0))
    lat_bounds = dataset_config.get("lat_bounds")
    lon_bounds = dataset_config.get("lon_bounds")

    client = cdsapi.Client()
    dataset_name = "derived-era5-single-levels-daily-statistics"
    first_start = min(start for start, _ in ranges)
    last_end = max(end for _, end in ranges)
    print(
        "==> Fetching ERA5 daily total precipitation from CDS daily statistics "
        f"({first_start.date()} to {last_end.date()})"
    )

    # Group months by year so each year's data can be written and freed before
    # the next year begins. Each entry is (window_start, window_end, month, days).
    months_by_year: dict[int, list[tuple[datetime, datetime, int, list[str]]]] = {}
    for start, end in ranges:
        for year, month, days in _monthly_date_ranges(start, end):
            months_by_year.setdefault(year, []).append((start, end, month, days))

    for year in sorted(months_by_year):
        year_parts = []
        for window_start, window_end, month, days in months_by_year[year]:
            da = _fetch_cds_month(
                client,
                dataset_name,
                year,
                month,
                days,
                window_start,
                window_end,
                unit_cvt,
                lat_bounds,
                lon_bounds,
                local_obs,
            )
            year_parts.append(da)
        _write_romp_daily_obs_files({year: year_parts}, obs_var, local_obs)
        del year_parts
        gc.collect()


def _canonical_dim_name(name: str) -> str:
    lower = name.lower()
    if lower in {"time", "valid_time"}:
        return "time"
    if lower in {"lat", "latitude"}:
        return "lat"
    if lower in {"lon", "longitude"}:
        return "lon"
    return name


def _canonicalize_data_array(da, variable_name: str):
    rename = {
        name: canonical
        for name in set(da.dims).union(da.coords)
        if (canonical := _canonical_dim_name(str(name))) != name
    }
    da = da.rename(rename)
    missing = {"time", "lat", "lon"} - set(da.dims)
    if missing:
        raise ValueError(f"{variable_name} is missing required dimensions: {sorted(missing)}")
    return da.transpose("time", "lat", "lon").sortby(["time", "lat", "lon"])


def _select_metric_variable(ds, preferred_name: str):
    name = preferred_name if preferred_name in ds.data_vars else next(iter(ds.data_vars))
    return _canonicalize_data_array(ds[name].astype(float), str(name))


def _stage_paths(stage_root: Path) -> tuple[Path, Path, Path, Path]:
    local_obs = stage_root / "obs"
    local_model = stage_root / "model"
    local_out = stage_root / "output"
    local_fig = stage_root / "figure"
    for path in (local_obs, local_model, local_out, local_fig):
        path.mkdir(parents=True, exist_ok=True)
    return local_obs, local_model, local_out, local_fig


def _romp_env(
    config: dict,
    local_obs: Path,
    local_model: Path,
    local_out: Path,
    local_fig: Path,
) -> dict:
    romp_params = config.get("romp_params", {})
    return {
        **os.environ,
        "ROMP_OBS_DIR": str(local_obs),
        "ROMP_MODEL_DIR": str(local_model),
        "ROMP_MODEL_NAME": config["model_name"],
        "ROMP_DIR_OUT": str(local_out),
        "ROMP_DIR_FIG": str(local_fig),
        **{f"ROMP_{k.upper()}": str(v) for k, v in romp_params.items() if v is not None},
    }


def _patch_romp_config(config_path: str, env: dict) -> None:
    """Append region fields as a safety override after generate_config.py runs.

    generate_config.py already writes lat_min/lat_max/lon_min/lon_max and
    land_only/shp_only from env vars, but appending here ensures any future
    version mismatch doesn't silently fall back to wrong defaults.
    Last-assignment wins when ROMP exec()s the config file.
    """
    extra: list[str] = [
        "plot_spatial_far_mr_mae = False",
        "plot_heatmap_bss_auc = False",
        "plot_reliability = False",
        "plot_portrait = False",
        "plot_climatology_onset = False",
        "plot_panel_heatmap_error = False",
        "plot_panel_heatmap_skill = False",
        "plot_bar_bss_rpss_auc = False",
    ]

    for env_key, cfg_key in (
        ("ROMP_LAND_ONLY", "land_only"),
        ("ROMP_SHP_ONLY", "shp_only"),
    ):
        val = env.get(env_key)
        if val is not None:
            bool_val = "False" if str(val).lower() in ("false", "0", "no") else "True"
            extra.append(f"{cfg_key} = {bool_val}")

    for env_key, cfg_key in (
        ("ROMP_LAT_MIN", "lat_min"),
        ("ROMP_LAT_MAX", "lat_max"),
        ("ROMP_LON_MIN", "lon_min"),
        ("ROMP_LON_MAX", "lon_max"),
    ):
        val = env.get(env_key)
        if val is not None:
            extra.append(f"{cfg_key} = {val}")

    with open(config_path, "a") as f:
        f.write("\n# Almanac runner overrides\n")
        for line in extra:
            f.write(line + "\n")


def _run_subprocess(cmd: list[str], env: dict, capture_output: bool) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, env=env, capture_output=capture_output, text=capture_output)
    if capture_output:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
    return result


def _run_romp_entry(env: dict, local_out: Path, capture_output: bool) -> None:
    config_path = env.get("ROMP_CONFIG_PATH", "/tmp/romp_job.in")

    print("==> Generating config from environment...")
    gen = _run_subprocess(
        ["python3", "/app/scripts/generate_config.py"],
        env=env,
        capture_output=capture_output,
    )
    if gen.returncode != 0:
        raise RuntimeError(f"Config generation failed with code {gen.returncode}")

    _patch_romp_config(config_path, env)

    print("==> Starting ROMP...")
    result = _run_subprocess(
        ["momp-run", "-p", config_path],
        env=env,
        capture_output=capture_output,
    )
    if result.returncode not in (0, -11, 139):
        raise RuntimeError(f"ROMP exited with code {result.returncode}")
    if result.returncode in (-11, 139) and not any(local_out.iterdir()):
        raise RuntimeError("ROMP segfaulted with no output")


def _run_staged_benchmark(
    job_id: str,
    config: dict,
    local_obs: Path,
    local_model: Path,
    local_out: Path,
    local_fig: Path,
    capture_output: bool = False,
) -> None:
    env = _romp_env(config, local_obs, local_model, local_out, local_fig)
    print(f"==> Running benchmark for {job_id}")
    print(f"    obs files: {sum(1 for _ in local_obs.iterdir())}")
    print(f"    model files: {sum(1 for _ in local_model.iterdir())}")
    _run_romp_entry(
        env,
        local_out,
        capture_output,
    )
    if config.get("compute_e2s_metrics"):
        try:
            result = _run_subprocess(
                ["python3", "/almanac/e2s_metrics_runner.py"],
                env=env,
                capture_output=capture_output,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Earth2Studio metrics exited with code {result.returncode}")
        except Exception as exc:
            print(
                "WARNING: Earth2Studio metrics failed; "
                f"ROMP outputs are still available. Error: {exc}"
            )


def _write_gcp_credentials_from_secret() -> None:
    sa_json = os.environ["SERVICE_ACCOUNT_JSON"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_key_path = f.name
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key_path


def _stage_gcs_benchmark_inputs(
    client,
    config: dict,
    local_obs: Path,
    local_model: Path,
) -> None:
    romp_params = config.get("romp_params", {})
    dataset_config = config.get("dataset_config", {})
    start_year = int((romp_params.get("start_date") or "1990-01-01")[:4])
    end_year = int((romp_params.get("end_date") or "2024-01-01")[:4])

    if _uses_remote_obs(dataset_config):
        _fetch_remote_obs(dataset_config, romp_params, local_obs)
    else:
        obs_uri = config["obs_dir"]
        print(f"==> Staging obs from {obs_uri}")
        count = _stage_gcs_prefix(client, obs_uri, local_obs)
        print(f"    obs staged: {count} files")

    model_uri = config["model_dir"]
    print(f"==> Staging model ({start_year}-{end_year}) from {model_uri}")
    count = _stage_gcs_years(client, model_uri, local_model, start_year, end_year)
    print(f"    model staged: {count} files")


def _upload_outputs_to_gcs(
    client,
    outputs_bucket: str,
    job_id: str,
    local_out: Path,
    local_fig: Path,
) -> None:
    from google.cloud.storage import transfer_manager

    if not outputs_bucket:
        raise ValueError("outputs_bucket is required for Modal production runs")
    print(f"==> Uploading outputs to gs://{outputs_bucket}/{job_id}/")
    out_bucket = client.bucket(outputs_bucket)
    for kind, local_dir in (("output", local_out), ("figure", local_fig)):
        files = [f.name for f in local_dir.iterdir() if f.is_file()]
        if not files:
            continue
        results = transfer_manager.upload_many_from_filenames(
            out_bucket,
            files,
            source_directory=str(local_dir),
            blob_name_prefix=f"{job_id}/{kind}/",
            worker_type="thread",
        )
        for name, upload_result in zip(files, results, strict=False):
            if isinstance(upload_result, Exception):
                print(f"  upload FAILED: {kind}/{name}: {upload_result}")
            else:
                print(f"  uploaded: {kind}/{name}")


def _upload_run_log_to_gcs(
    client,
    outputs_bucket: str,
    job_id: str,
    log_text: str,
) -> None:
    if not log_text or not outputs_bucket:
        if log_text and not outputs_bucket:
            _log("WARNING: outputs_bucket is empty; cannot upload run log")
        return
    bucket = client.bucket(outputs_bucket)
    bucket.blob(f"{job_id}/run.log").upload_from_string(
        log_text,
        content_type="text/plain",
    )
    _log(f"==> Uploaded run log to gs://{outputs_bucket}/{job_id}/run.log")


def _capture_output_files(local_out: Path, local_fig: Path) -> list[dict]:
    files: list[dict] = []
    for kind, local_dir in (("output", local_out), ("figure", local_fig)):
        for path in sorted(local_dir.iterdir()):
            if path.is_file():
                files.append(
                    {
                        "kind": kind,
                        "filename": path.name,
                        "data": path.read_bytes(),
                    }
                )
                print(f"  captured: {kind}/{path.name}")
    return files


if ENABLE_GCS_FUNCTIONS:

    @app.function(
        image=benchmark_image,
        cpu=(6, 12),
        memory=(16384, 32768),
        timeout=7200,
        secrets=[secret for secret in (gcp_secret, e2s_secret) if secret is not None],
    )
    def run_benchmark(job_id: str, config: dict, outputs_bucket: str) -> None:
        """
        Unified benchmark runner: prepares obs (GCS or remote source), runs ROMP,
        computes extended metrics (RMSE, MAE, ACC, bias).

        Required secrets:
          gcp-service-account — SERVICE_ACCOUNT_JSON
          e2s-credentials     — CDSAPI_KEY (only needed for Earth2Studio/CDS datasets)
        """
        import io as _io
        import sys
        import traceback
        from contextlib import redirect_stderr, redirect_stdout

        from google.cloud import storage as gcs

        log_buffer = _io.StringIO()
        client = None
        failure: Exception | None = None

        with (
            redirect_stdout(_LogTee(sys.stdout, log_buffer)),
            redirect_stderr(_LogTee(sys.stderr, log_buffer)),
        ):
            try:
                _write_gcp_credentials_from_secret()
                client = gcs.Client()
                local_obs, local_model, local_out, local_fig = _stage_paths(Path("/tmp/romp_stage"))

                _stage_gcs_benchmark_inputs(client, config, local_obs, local_model)
                _run_staged_benchmark(job_id, config, local_obs, local_model, local_out, local_fig)
                _upload_outputs_to_gcs(client, outputs_bucket, job_id, local_out, local_fig)
                print("==> Done.")
            except Exception as exc:
                failure = exc
                traceback.print_exc()
            finally:
                if client is not None:
                    try:
                        _upload_run_log_to_gcs(
                            client, outputs_bucket, job_id, log_buffer.getvalue()
                        )
                    except Exception:
                        traceback.print_exc()

        if failure is not None:
            raise RuntimeError(
                f"Benchmark job {job_id} failed; see run.log for details: {failure}"
            ) from failure


@app.function(
    image=benchmark_image,
    cpu=(6, 12),
    memory=(16384, 32768),
    timeout=7200,
)
def run_benchmark_local(
    job_id: str,
    config: dict,
    input_bundle: bytes,
    runtime_env: dict[str, str] | None = None,
) -> dict:
    """
    Run a benchmark from backend-supplied local inputs and return result files.

    This dev path intentionally avoids GCS and GCP secrets. It expects the
    backend to package local model files, plus local obs files for non-E2S
    datasets, into a tarball with top-level `model/` and optional `obs/`
    directories. For E2S datasets, obs are fetched inside Modal.
    """
    import io as _io
    import sys
    import traceback
    from contextlib import redirect_stderr, redirect_stdout

    log_buffer = _io.StringIO()
    files: list[dict] = []

    try:
        with (
            redirect_stdout(_LogTee(sys.stdout, log_buffer)),
            redirect_stderr(_LogTee(sys.stderr, log_buffer)),
        ):
            romp_params = config.get("romp_params", {})
            dataset_config = config.get("dataset_config", {})
            if runtime_env:
                os.environ.update(
                    {key: value for key, value in runtime_env.items() if value is not None}
                )

            stage_root = Path(tempfile.mkdtemp(prefix=f"romp-local-{job_id}-"))
            local_obs, local_model = _extract_local_bundle(input_bundle, stage_root)
            local_out = stage_root / "output"
            local_fig = stage_root / "figure"
            for path in (local_obs, local_model, local_out, local_fig):
                path.mkdir(parents=True, exist_ok=True)

            if _uses_remote_obs(dataset_config):
                _fetch_remote_obs(dataset_config, romp_params, local_obs)

            _run_staged_benchmark(
                job_id,
                config,
                local_obs,
                local_model,
                local_out,
                local_fig,
                capture_output=True,
            )
            files = _capture_output_files(local_out, local_fig)

            print("==> Done.")
    except Exception as exc:
        traceback.print_exc(file=log_buffer)
        return {"ok": False, "error": str(exc), "log": log_buffer.getvalue(), "files": files}

    return {"ok": True, "log": log_buffer.getvalue(), "files": files}


# ---------------------------------------------------------------------------
# Sandboxed code execution
# ---------------------------------------------------------------------------

# Minimal image for sandboxed code: scientific Python stack only, no GCS credentials.
_sandbox_image = modal.Image.debian_slim().pip_install(
    "xarray", "numpy", "h5netcdf", "scipy", "pandas", "matplotlib", "Pillow"
)


@app.function(
    image=_sandbox_image,
    cpu=1,
    memory=2048,
    timeout=120,
)
def run_code_sandbox(code: str) -> dict:
    """
    Run arbitrary Python code in an isolated sandbox with no network access.

    `code` must define a function:
        def compute() -> dict:
            ...

    Returns {"ok": true, "result": {...}, "artifacts": [...]} or
    {"ok": false, "error": "..."}.
    Available libraries: xarray, numpy, scipy, pandas, matplotlib.
    """
    return _run_generated_code(code, "compute()", timeout=90)


@app.function(
    image=romp_image,  # needs GCS to stage files
    cpu=2,
    memory=8192,
    timeout=300,
    secrets=[gcp_secret] if ENABLE_GCS_FUNCTIONS else [],
)
def run_code(job_id: str, outputs_bucket: str, code: str) -> dict:
    """
    Download NC output files for job_id from GCS, then run LLM-generated code
    in an isolated Modal Sandbox with no network access.

    `code` must define a function:
        def compute(nc_dir: str) -> dict:
            ...

    Returns {"ok": true, "result": {...}, "artifacts": [...]} or
    {"ok": false, "error": "..."}.
    """
    from google.cloud import storage as gcs
    from google.cloud.storage import transfer_manager

    # Authenticate — write SA key to a temp file for the GCS client, then
    # remove it before running user code so the subprocess can't read it.
    sa_json = os.environ["SERVICE_ACCOUNT_JSON"]
    sa_key_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(sa_json)
            sa_key_path = f.name
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key_path

        client = gcs.Client()
        bucket = client.bucket(outputs_bucket)
        prefix = f"{job_id}/output/"
        nc_names = [
            blob.name[len(prefix) :]
            for blob in client.list_blobs(outputs_bucket, prefix=prefix)
            if blob.name.endswith(".nc")
        ]

        local_nc = Path("/tmp/sandbox_nc")
        local_nc.mkdir(parents=True, exist_ok=True)

        if nc_names:
            results = transfer_manager.download_many_to_path(
                bucket,
                nc_names,
                str(local_nc),
                blob_name_prefix=prefix,
                worker_type="thread",
            )
            for name, result in zip(nc_names, results, strict=False):
                if isinstance(result, Exception):
                    return {"ok": False, "error": f"Failed to download {name}: {result}"}

        if not nc_names:
            return {"ok": False, "error": "No NC output files found for this job"}
    finally:
        # Remove credentials from disk and environment before executing user code.
        if sa_key_path:
            Path(sa_key_path).unlink(missing_ok=True)
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GOOGLE_APPLICATION_CREDENTIALS", "SERVICE_ACCOUNT_JSON"}
    }
    return _run_generated_code(
        code,
        f"compute({str(local_nc)!r})",
        extra_env=env,
        timeout=120,
    )
