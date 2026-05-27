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
import tempfile
import tarfile
import zipfile
from pathlib import Path

import modal

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
E2S_SECRET_NAME = os.environ.get("ALMANAC_MODAL_E2S_SECRET_NAME", "e2s-credentials")


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


def _run_generated_code(code: str, compute_call: str, extra_env: dict[str, str] | None = None, timeout: int = 90) -> dict:
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
        enriched_artifacts.append({
            **artifact,
            "data": path.read_bytes(),
            "media_type": artifact.get("media_type") or _media_type_for_filename(filename),
        })
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

# Extends the ROMP image with earth2studio for obs fetching and extended metrics.
# Requires secret "e2s-credentials" with CDSAPI_KEY (create with placeholder if not using ERA5).
benchmark_image = romp_image.pip_install("earth2studio[data]")


# ---------------------------------------------------------------------------
# Shared staging helpers
# ---------------------------------------------------------------------------


def _stage_gcs_prefix(client, uri: str, local_dir: Path) -> int:
    """Download all direct children of a GCS prefix into local_dir."""
    from google.cloud.storage import transfer_manager

    bucket_name, _, prefix = uri.removeprefix("gs://").partition("/")
    prefix = prefix.rstrip("/") + "/"
    bucket = client.bucket(bucket_name)
    names = [
        blob.name[len(prefix):]
        for blob in client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        if blob.name[len(prefix):] and not blob.name[len(prefix):].endswith("/")
    ]
    if names:
        results = transfer_manager.download_many_to_path(
            bucket, names, str(local_dir), blob_name_prefix=prefix, worker_type="thread",
        )
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                print(f"  obs FAILED: {name}: {result}")
    return len(names)


def _stage_gcs_years(client, uri: str, local_dir: Path, start_year: int, end_year: int) -> int:
    """Download {year}.nc files from a GCS prefix filtered to the given year range."""
    from google.cloud.storage import transfer_manager

    bucket_name, _, prefix = uri.removeprefix("gs://").partition("/")
    prefix = prefix.rstrip("/") + "/"
    year_names = {f"{y}.nc" for y in range(start_year, end_year + 1)}
    bucket = client.bucket(bucket_name)
    names = [
        blob.name[len(prefix):]
        for blob in client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        if blob.name[len(prefix):] in year_names
    ]
    if names:
        results = transfer_manager.download_many_to_path(
            bucket, names, str(local_dir), blob_name_prefix=prefix, worker_type="thread",
        )
        for name, result in zip(names, results):
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


def _fetch_e2s_obs(dataset_config: dict, romp_params: dict, local_obs: Path) -> None:
    """Fetch obs via earth2studio DataSource and write annual {year}.nc files."""
    import importlib
    from collections import defaultdict
    from datetime import datetime, timedelta

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

    start_str = romp_params.get("start_date", "2019-05-01")
    end_str = romp_params.get("end_date", "2024-07-31")
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")

    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)

    print(f"==> Fetching e2s obs ({e2s_class_name}, var={precip_var}): {len(dates)} dates")
    _configure_cdsapi_from_env()

    if e2s_class_name == "CDS" and precip_var in {"tp", "total_precipitation"}:
        _fetch_era5_daily_precip_from_cds(
            dataset_config=dataset_config,
            romp_params=romp_params,
            local_obs=local_obs,
            start=start,
            end=end,
        )
        return

    data_module = importlib.import_module("earth2studio.data")
    try:
        DataSource = getattr(data_module, e2s_class_name)
    except AttributeError as exc:
        available = ", ".join(
            sorted(name for name in dir(data_module) if name[:1].isupper())
        )
        raise AttributeError(
            f"Earth2Studio data source {configured_class_name!r} resolved to "
            f"{e2s_class_name!r}, but it is not available. Available sources: {available}"
        ) from exc
    data_source = DataSource()

    by_year: dict[int, list] = defaultdict(list)
    chunk = 365
    for i in range(0, len(dates), chunk):
        batch = dates[i : i + chunk]
        print(f"  batch: {batch[0].date()} — {batch[-1].date()}")
        da = data_source(batch, [precip_var])  # (time, variable, lat, lon)
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
        ds_year = xr.concat(slices, dim="time").to_dataset(name=obs_var)
        out_path = local_obs / f"{year}.nc"
        ds_year.to_netcdf(out_path)
        print(f"  wrote: {out_path.name} ({len(slices)} days)")


def _fetch_era5_daily_precip_from_cds(
    dataset_config: dict,
    romp_params: dict,
    local_obs: Path,
    start,
    end,
) -> None:
    """Fetch ERA5 daily total precipitation directly from CDS daily statistics."""
    import cdsapi
    import xarray as xr

    obs_var = romp_params.get("obs_var", "RAINFALL")
    unit_cvt = float(dataset_config.get("unit_cvt", 1000.0))
    lat_bounds = dataset_config.get("lat_bounds")
    lon_bounds = dataset_config.get("lon_bounds")

    client = cdsapi.Client()
    dataset = "derived-era5-single-levels-daily-statistics"
    years = range(start.year, end.year + 1)
    print(
        "==> Fetching ERA5 daily total precipitation from CDS daily statistics "
        f"({start.year}–{end.year})"
    )

    for year in years:
        year_start = max(start, start.__class__(year, 1, 1))
        year_end = min(end, end.__class__(year, 12, 31))
        months = [f"{month:02d}" for month in range(year_start.month, year_end.month + 1)]
        days = [f"{day:02d}" for day in range(1, 32)]

        request = {
            "product_type": "reanalysis",
            "variable": ["total_precipitation"],
            "year": str(year),
            "month": months,
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

        target = local_obs / f"era5_daily_precip_{year}.zip"
        print(
            f"  requesting {year}: {len(months)} months, {len(days)} day labels, "
            f"area={request.get('area', 'global')}"
        )
        client.retrieve(dataset, request, str(target))

        nc_files: list[Path] = []
        if zipfile.is_zipfile(target):
            extract_dir = local_obs / f"era5_daily_precip_{year}"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target) as archive:
                archive.extractall(extract_dir)
            nc_files = sorted(extract_dir.glob("*.nc"))
        else:
            nc_files = [target]

        if not nc_files:
            raise RuntimeError(f"CDS daily precipitation request for {year} returned no NetCDF files")

        datasets = [xr.open_dataset(path) for path in nc_files]
        try:
            ds = xr.merge(datasets) if len(datasets) > 1 else datasets[0]
            data_var = "tp" if "tp" in ds.data_vars else "total_precipitation"
            if data_var not in ds.data_vars:
                data_var = next(iter(ds.data_vars))
            da = ds[data_var].astype(float) * unit_cvt

            rename = {}
            for coord in da.coords:
                lower = coord.lower()
                if lower in {"valid_time", "time"}:
                    rename[coord] = "TIME"
                elif lower in {"latitude", "lat"}:
                    rename[coord] = "LATITUDE"
                elif lower in {"longitude", "lon"}:
                    rename[coord] = "LONGITUDE"
            da = da.rename(rename)

            if "TIME" not in da.dims:
                time_dim = next(dim for dim in da.dims if dim.lower() in {"time", "valid_time"})
                da = da.rename({time_dim: "TIME"})
            if "LATITUDE" not in da.dims:
                lat_dim = next(dim for dim in da.dims if dim.lower() in {"lat", "latitude"})
                da = da.rename({lat_dim: "LATITUDE"})
            if "LONGITUDE" not in da.dims:
                lon_dim = next(dim for dim in da.dims if dim.lower() in {"lon", "longitude"})
                da = da.rename({lon_dim: "LONGITUDE"})

            da = da.transpose("TIME", "LATITUDE", "LONGITUDE")
            da.name = obs_var
            out_ds = da.to_dataset()
            out_ds[obs_var].attrs.update({"units": "mm/day", "time_step": "day"})
            out_path = local_obs / f"{year}.nc"
            out_ds.to_netcdf(out_path)
            print(f"  wrote: {out_path.name} ({out_ds.sizes.get('TIME', 0)} days)")
        finally:
            for ds_part in datasets:
                ds_part.close()


def _compute_extended_metrics(
    model_name: str,
    local_obs: Path,
    local_model: Path,
    local_out: Path,
    romp_params: dict,
) -> None:
    """Compute spatial RMSE, MAE, ACC, and bias from staged obs/model netCDF files."""
    import numpy as np
    import xarray as xr

    obs_var = romp_params.get("obs_var", "RAINFALL")
    model_var = romp_params.get("model_var", "tp")

    obs_files = sorted(local_obs.glob("*.nc"))
    model_files = sorted(local_model.glob("*.nc"))
    if not obs_files or not model_files:
        print("  skipping extended metrics: insufficient staged files")
        return

    print("==> Computing extended metrics (RMSE, MAE, ACC, bias)...")
    try:
        obs_ds = xr.open_mfdataset(obs_files, combine="by_coords")
        model_ds = xr.open_mfdataset(model_files, combine="by_coords")

        obs_var_actual = obs_var if obs_var in obs_ds else list(obs_ds.data_vars)[0]
        model_var_actual = model_var if model_var in model_ds else list(model_ds.data_vars)[0]

        obs_da = obs_ds[obs_var_actual].astype(float)
        model_da = model_ds[model_var_actual].astype(float)

        # Regrid model to obs grid if resolutions differ
        lat_dim = "lat" if "lat" in obs_da.dims else "latitude"
        lon_dim = "lon" if "lon" in obs_da.dims else "longitude"
        model_lat = "lat" if "lat" in model_da.dims else "latitude"
        model_lon = "lon" if "lon" in model_da.dims else "longitude"
        if not (
            obs_da[lat_dim].equals(model_da[model_lat])
            and obs_da[lon_dim].equals(model_da[model_lon])
        ):
            model_da = model_da.interp(
                {model_lat: obs_da[lat_dim], model_lon: obs_da[lon_dim]},
                method="linear",
            )

        obs_da, model_da = xr.align(obs_da, model_da, join="inner")
        if len(obs_da.time) == 0:
            print("  no overlapping time steps — skipping extended metrics")
            return

        diff = model_da - obs_da
        rmse = np.sqrt((diff**2).mean(dim="time"))
        mae = abs(diff).mean(dim="time")
        bias = diff.mean(dim="time")

        clim = obs_da.mean(dim="time")
        obs_anom = obs_da - clim
        model_anom = model_da - clim
        cov = (obs_anom * model_anom).mean(dim="time")
        denom = obs_anom.std(dim="time") * model_anom.std(dim="time")
        acc = xr.where(denom > 1e-10, cov / denom, float("nan"))

        out_ds = xr.Dataset({"rmse": rmse, "mae": mae, "bias": bias, "acc": acc})
        out_ds.attrs["model"] = model_name
        out_ds.attrs["verification_window"] = "all"

        out_path = local_out / f"e2s_spatial_metrics_{model_name}_all.nc"
        out_ds.to_netcdf(out_path)
        print(f"  extended metrics saved: {out_path.name}")

    except Exception as exc:
        import traceback

        print(f"  WARNING: extended metrics failed: {exc}")
        traceback.print_exc()


if ENABLE_GCS_FUNCTIONS:

    @app.function(
        image=benchmark_image,
        cpu=(6, 12),
        memory=(16384, 32768),
        timeout=7200,
        secrets=[gcp_secret, modal.Secret.from_name(E2S_SECRET_NAME)],
    )
    def run_benchmark(job_id: str, config: dict, outputs_bucket: str) -> None:
        """
        Unified benchmark runner: prepares obs (GCS or earth2studio), runs ROMP,
        computes extended metrics (RMSE, MAE, ACC, bias).

        Required secrets:
          gcp-service-account — SERVICE_ACCOUNT_JSON
          e2s-credentials     — CDSAPI_KEY (use placeholder value if not using ERA5 datasets)
        """
        from google.cloud import storage as gcs

        sa_json = os.environ["SERVICE_ACCOUNT_JSON"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(sa_json)
            sa_key_path = f.name
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key_path

        romp_params = config.get("romp_params", {})
        dataset_config = config.get("dataset_config", {})
        start_year = int((romp_params.get("start_date") or "1990-01-01")[:4])
        end_year = int((romp_params.get("end_date") or "2024-01-01")[:4])

        stage_root = Path("/tmp/romp_stage")
        local_obs = stage_root / "obs"
        local_model = stage_root / "model"
        local_out = stage_root / "output"
        local_fig = stage_root / "figure"
        for d in (local_obs, local_model, local_out, local_fig):
            d.mkdir(parents=True, exist_ok=True)

        client = gcs.Client()

        if dataset_config.get("provider") == "earth2studio":
            _fetch_e2s_obs(dataset_config, romp_params, local_obs)
        else:
            obs_uri = config["obs_dir"]
            print(f"==> Staging obs from {obs_uri}")
            count = _stage_gcs_prefix(client, obs_uri, local_obs)
            print(f"    obs staged: {count} files")

        model_uri = config["model_dir"]
        print(f"==> Staging model ({start_year}–{end_year}) from {model_uri}")
        count = _stage_gcs_years(client, model_uri, local_model, start_year, end_year)
        print(f"    model staged: {count} files")

        env = {
            **os.environ,
            "ROMP_OBS_DIR": str(local_obs),
            "ROMP_MODEL_DIR": str(local_model),
            "ROMP_MODEL_NAME": config["model_name"],
            "ROMP_DIR_OUT": str(local_out),
            "ROMP_DIR_FIG": str(local_fig),
            **{f"ROMP_{k.upper()}": str(v) for k, v in romp_params.items() if v is not None},
        }
        print("==> Running ROMP...")
        result = subprocess.run(["/app/scripts/entrypoint.sh"], env=env, capture_output=False)
        if result.returncode not in (0, -11, 139):
            raise RuntimeError(f"ROMP exited with code {result.returncode}")
        if result.returncode in (-11, 139) and not any(local_out.iterdir()):
            raise RuntimeError("ROMP segfaulted with no output")

        _compute_extended_metrics(config["model_name"], local_obs, local_model, local_out, romp_params)

        from google.cloud.storage import transfer_manager

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
            for name, upload_result in zip(files, results):
                if isinstance(upload_result, Exception):
                    print(f"  upload FAILED: {kind}/{name}: {upload_result}")
                else:
                    print(f"  uploaded: {kind}/{name}")

        print("==> Done.")


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
    from contextlib import redirect_stderr, redirect_stdout
    import io as _io
    import traceback

    log_buffer = _io.StringIO()
    files: list[dict] = []

    try:
        with redirect_stdout(log_buffer), redirect_stderr(log_buffer):
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
            for d in (local_obs, local_out, local_fig):
                d.mkdir(parents=True, exist_ok=True)

            if dataset_config.get("provider") == "earth2studio":
                _fetch_e2s_obs(dataset_config, romp_params, local_obs)

            print(f"==> Running local Modal benchmark for {job_id}")
            print(f"    obs files: {sum(1 for _ in local_obs.iterdir())}")
            print(f"    model files: {sum(1 for _ in local_model.iterdir())}")

            env = {
                **os.environ,
                "ROMP_OBS_DIR": str(local_obs),
                "ROMP_MODEL_DIR": str(local_model),
                "ROMP_MODEL_NAME": config["model_name"],
                "ROMP_DIR_OUT": str(local_out),
                "ROMP_DIR_FIG": str(local_fig),
                **{
                    f"ROMP_{k.upper()}": str(v)
                    for k, v in romp_params.items()
                    if v is not None
                },
            }

            result = subprocess.run(
                ["/app/scripts/entrypoint.sh"],
                env=env,
                capture_output=True,
                text=True,
            )
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="")
            if result.returncode not in (0, -11, 139):
                raise RuntimeError(f"ROMP exited with code {result.returncode}")
            if result.returncode in (-11, 139) and not any(local_out.iterdir()):
                raise RuntimeError("ROMP segfaulted with no output")

            _compute_extended_metrics(
                config["model_name"], local_obs, local_model, local_out, romp_params
            )

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

            print("==> Done.")
    except Exception as exc:
        traceback.print_exc(file=log_buffer)
        return {"ok": False, "error": str(exc), "log": log_buffer.getvalue(), "files": files}

    return {"ok": True, "log": log_buffer.getvalue(), "files": files}


@app.function(
    image=romp_image,
    cpu=(6, 12),
    memory=(16384, 32768),  # 16 GiB request, 32Gib limit
    timeout=3600,
    secrets=[gcp_secret] if ENABLE_GCS_FUNCTIONS else [],
)
def run_romp(job_id: str, config: dict, outputs_bucket: str) -> None:
    """
    Run ROMP for a single job.

    Replicates the staging logic from docker/romp-wrapper/entrypoint.sh, then
    calls /app/scripts/entrypoint.sh (the inner ROMP entry) with the same env
    vars that DockerRunner uses.

    Raises on failure so the caller's poll loop can catch it.
    """
    from google.cloud import storage as gcs
    from google.cloud.storage import transfer_manager

    # Write SA key so google-cloud-storage can authenticate.
    sa_json = os.environ["SERVICE_ACCOUNT_JSON"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_key_path = f.name
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key_path

    romp_params = config.get("romp_params", {})
    start_year = int((romp_params.get("start_date") or "1990-01-01")[:4])
    end_year = int((romp_params.get("end_date") or "2024-01-01")[:4])

    stage_root = Path("/tmp/romp_stage")
    local_obs = stage_root / "obs"
    local_model = stage_root / "model"
    local_out = stage_root / "output"
    local_fig = stage_root / "figure"
    for d in (local_obs, local_model, local_out, local_fig):
        d.mkdir(parents=True, exist_ok=True)

    client = gcs.Client()

    # --- Stage obs (all files in prefix, downloaded in parallel) ---
    obs_uri = config["obs_dir"]  # gs://bucket/prefix
    print(f"==> Staging obs from {obs_uri}")
    bucket_name, _, prefix = obs_uri.removeprefix("gs://").partition("/")
    prefix = prefix.rstrip("/") + "/"
    obs_bucket = client.bucket(bucket_name)
    obs_names = [
        blob.name[len(prefix):]
        for blob in client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        if blob.name[len(prefix):] and not blob.name[len(prefix):].endswith("/")
    ]
    if obs_names:
        results = transfer_manager.download_many_to_path(
            obs_bucket, obs_names, str(local_obs), blob_name_prefix=prefix, worker_type="thread",
        )
        for name, result in zip(obs_names, results):
            if isinstance(result, Exception):
                print(f"  obs FAILED: {name}: {result}")
            else:
                print(f"  obs: {name}")
    print(f"    obs staged: {sum(1 for _ in local_obs.iterdir())} files")

    # --- Stage model (list prefix once, filter by year range, download in parallel) ---
    model_uri = config["model_dir"]  # gs://bucket/prefix
    print(f"==> Staging model ({start_year}–{end_year}) from {model_uri}")
    bucket_name, _, prefix = model_uri.removeprefix("gs://").partition("/")
    prefix = prefix.rstrip("/") + "/"
    year_names = {f"{year}.nc" for year in range(start_year, end_year + 1)}
    model_bucket = client.bucket(bucket_name)
    model_names = [
        blob.name[len(prefix):]
        for blob in client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        if blob.name[len(prefix):] in year_names
    ]
    if model_names:
        results = transfer_manager.download_many_to_path(
            model_bucket, model_names, str(local_model), blob_name_prefix=prefix, worker_type="thread",
        )
        for name, result in zip(model_names, results):
            if isinstance(result, Exception):
                print(f"  model FAILED: {name}: {result}")
            else:
                print(f"  model: {name}")
    print(f"    model staged: {sum(1 for _ in local_model.iterdir())} files")

    # --- Build ROMP env (matches DockerRunner env construction) ---
    env = {
        **os.environ,
        "ROMP_OBS_DIR": str(local_obs),
        "ROMP_MODEL_DIR": str(local_model),
        "ROMP_MODEL_NAME": config["model_name"],
        "ROMP_DIR_OUT": str(local_out),
        "ROMP_DIR_FIG": str(local_fig),
        **{
            f"ROMP_{k.upper()}": str(v) for k, v in romp_params.items() if v is not None
        },
    }

    # --- Run ROMP ---
    print("==> Running ROMP...")
    result = subprocess.run(
        ["/app/scripts/entrypoint.sh"],
        env=env,
        capture_output=False,  # stream stdout/stderr to Modal logs
    )

    # Treat SIGSEGV as success if outputs exist (matches DockerRunner behaviour).
    if result.returncode not in (0, -11, 139):
        raise RuntimeError(f"ROMP exited with code {result.returncode}")
    if result.returncode in (-11, 139) and not any(local_out.iterdir()):
        raise RuntimeError("ROMP segfaulted with no output")

    # --- Upload outputs to GCS (parallel) ---
    print(f"==> Uploading outputs to gs://{outputs_bucket}/{job_id}/")
    out_bucket = client.bucket(outputs_bucket)
    for kind, local_dir in (("output", local_out), ("figure", local_fig)):
        files = [f.name for f in local_dir.iterdir() if f.is_file()]
        if not files:
            continue
        results = transfer_manager.upload_many_from_filenames(
            out_bucket, files, source_directory=str(local_dir),
            blob_name_prefix=f"{job_id}/{kind}/", worker_type="thread",
        )
        for name, result in zip(files, results):
            if isinstance(result, Exception):
                print(f"  upload FAILED: {kind}/{name}: {result}")
            else:
                print(f"  uploaded: {kind}/{name}")

    print("==> Done.")


# ---------------------------------------------------------------------------
# Sandboxed code execution
# ---------------------------------------------------------------------------

# Minimal image for sandboxed code: scientific Python stack only, no GCS credentials.
_sandbox_image = (
    modal.Image.debian_slim()
    .pip_install("xarray", "numpy", "h5netcdf", "scipy", "pandas", "matplotlib", "Pillow")
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
            blob.name[len(prefix):]
            for blob in client.list_blobs(outputs_bucket, prefix=prefix)
            if blob.name.endswith(".nc")
        ]

        local_nc = Path("/tmp/sandbox_nc")
        local_nc.mkdir(parents=True, exist_ok=True)

        if nc_names:
            results = transfer_manager.download_many_to_path(
                bucket, nc_names, str(local_nc), blob_name_prefix=prefix, worker_type="thread",
            )
            for name, result in zip(nc_names, results):
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
        k: v for k, v in os.environ.items()
        if k not in {"GOOGLE_APPLICATION_CREDENTIALS", "SERVICE_ACCOUNT_JSON"}
    }
    return _run_generated_code(
        code,
        f'compute({str(local_nc)!r})',
        extra_env=env,
        timeout=120,
    )
