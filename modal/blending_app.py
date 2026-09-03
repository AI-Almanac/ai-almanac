"""Modal test harness for the onset blending package.

This app is intentionally isolated from the platform's ROMP runner. It lets us
upload a small local NetCDF bundle to Modal, inspect the files in the remote
runtime, and probe the blending package's current NetCDF reader.

Examples:
    uv run modal run modal/blending_app.py::inspect_local_netcdfs \
        --input-dir /Users/hayden/code/ROMP/data/ethiopia/aifs \
        --year 2024

    uv run modal run modal/blending_app.py::probe_blending_forecast_reader \
        --input-dir /Users/hayden/code/ROMP/data/ethiopia/aifs \
        --year 2024

    uv run modal run modal/blending_app.py::probe_lat_lon_onset_processing \
        --input-dir /Users/hayden/code/ROMP/data/ethiopia/aifs \
        --year 2024

    uv run modal run modal/blending_app.py::probe_lat_lon_ground_truth_processing \
        --input-dir /Users/hayden/code/ROMP/data/ethiopia/obs \
        --year 2024

    uv run modal run modal/blending_app.py::build_lat_lon_intermediates \
        --obs-dir /Users/hayden/code/ROMP/data/india/imd_rainfall_data/2p0 \
        --forecast-inputs aifs=/Users/hayden/code/ROMP/data/india/aifs_daily,gencast=/Users/hayden/code/ROMP/data/india/gencast \
        --obs-years 2000:2024 \
        --forecast-years 2024

The image clones onset_blending-adm3 at a pinned commit by default. Override
these during Modal build if needed:
    ALMANAC_BLENDING_REPO_URL=https://github.com/hholb/onset_blending-adm3.git
    ALMANAC_BLENDING_REPO_REF=2a59cec0680dcfb575104fa03b59ee64dc110f82
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import tempfile
from contextlib import suppress
from pathlib import Path

import modal

APP_NAME = "almanac-blending"
BLENDING_ROOT = Path(os.environ.get("ALMANAC_BLENDING_ROOT", "/opt/onset_blending"))
DEFAULT_LOCAL_DATA_DIR = Path("/Users/hayden/code/ROMP/data")
DEFAULT_REPO_URL = "https://github.com/hholb/onset_blending-adm3.git"
# Keep in sync with ai_almanac.envs.manager.BLENDING_REPO_REF (local blend env).
# See docs/onset-blending-haiyang-integration.md for the pin history.
DEFAULT_REPO_REF = "2a59cec0680dcfb575104fa03b59ee64dc110f82"

# Worker count handed to 1_blend_evaluation.py --cores. train_blending_model_bundle
# runs via .local() inside run_blend's container, so size to run_blend's cpu request.
RUN_BLEND_CPU = 4
RUN_BLEND_TRAINING_CORES = RUN_BLEND_CPU

# Written by train_blending_model_bundle's final fit; applied by
# apply_blend_coefs_bundle to score live seasons without retraining.
FINAL_COEF_FILENAME = "coefs_blended_model_global_final.pkl"

# Bump to invalidate cached blend intermediates when the builder's schema or
# hardcoded onset options change; onset_blending code bumps invalidate via the
# repo-ref key segment instead.
BLEND_INTERMEDIATES_CACHE_VERSION = 1
GCP_SECRET_NAME = "gcp-service-account"
ADM3_DOMAIN_REGIONS = {"ethiopia"}


app = modal.App(APP_NAME)


def _image() -> modal.Image:
    repo_url = os.environ.get("ALMANAC_BLENDING_REPO_URL", DEFAULT_REPO_URL)
    repo_ref = os.environ.get("ALMANAC_BLENDING_REPO_REF", DEFAULT_REPO_REF)

    return (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install(
            "build-essential",
            "git",
            "libgeos-dev",
            "libproj-dev",
            "proj-data",
            "proj-bin",
        )
        .pip_install("uv")
        .run_commands(
            f"git clone --depth 1 {repo_url} {BLENDING_ROOT}",
            f"cd {BLENDING_ROOT} && git fetch --depth 1 origin {repo_ref}",
            f"cd {BLENDING_ROOT} && git checkout {repo_ref}",
            f"cd {BLENDING_ROOT} && uv pip install --system -r requirements.txt",
        )
        .pip_install("google-cloud-storage")
    )


blending_image = _image()
gcp_secret = modal.Secret.from_name(GCP_SECRET_NAME)


# ---------------------------------------------------------------------------
# Production (GCS) plumbing — mirrors modal/app.py's benchmark runner so blend
# jobs stage inputs from GCS and publish weight artifacts back to GCS.
# ---------------------------------------------------------------------------


class _LogTee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)

    def flush(self):
        for stream in self._streams:
            with suppress(Exception):
                stream.flush()


def _write_gcp_credentials_from_secret() -> None:
    sa_json = os.environ["SERVICE_ACCOUNT_JSON"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_key_path = f.name
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key_path


def _split_gcs_uri(uri: str, label: str) -> tuple[str, str]:
    if not str(uri).startswith("gs://"):
        raise ValueError(f"{label} must be a gs:// URI for Modal runs; got {uri!r}")
    bucket_name, _, prefix = uri.removeprefix("gs://").partition("/")
    if not bucket_name:
        raise ValueError(f"{label} has no bucket name: {uri!r}")
    return bucket_name, prefix


def _stage_gcs_prefix(client, uri: str, local_dir: Path, label: str) -> int:
    """Download all direct children of a GCS prefix into local_dir."""
    from google.cloud.storage import transfer_manager

    bucket_name, prefix = _split_gcs_uri(uri, label)
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
        for name, result in zip(names, results, strict=True):
            if isinstance(result, Exception):
                print(f"  {label} FAILED: {name}: {result}")
    return len(names)


def _stage_uris(client, uris, local_dir: Path, label: str) -> int:
    """Download an explicit list of gs:// file URIs into local_dir.

    The server pre-resolves the exact per-year files to stage (see
    ``data_catalog.year_uris``), so the run just fetches the URIs it is handed —
    no prefix listing or year filtering here. A URI missing its object on the
    bucket raises, so a partial year set fails loudly instead of training short.
    """
    count = 0
    for uri in uris:
        bucket_name, blob_path = _split_gcs_uri(uri, label)
        if not blob_path:
            raise ValueError(f"{label} URI has no object path: {uri!r}")
        blob = client.bucket(bucket_name).blob(blob_path)
        if not blob.exists():
            raise FileNotFoundError(f"{label} file not found: {uri}")
        blob.download_to_filename(str(local_dir / Path(blob_path).name))
        count += 1
    return count


def _upload_output_dir_to_gcs(client, outputs_bucket: str, job_id: str, local_dir: Path) -> None:
    from google.cloud.storage import transfer_manager

    if not outputs_bucket:
        raise ValueError("outputs_bucket is required for Modal production runs")
    files = [f.name for f in sorted(local_dir.iterdir()) if f.is_file()]
    if not files:
        return
    print(f"==> Uploading {len(files)} artifacts to gs://{outputs_bucket}/{job_id}/output/")
    bucket = client.bucket(outputs_bucket)
    results = transfer_manager.upload_many_from_filenames(
        bucket,
        files,
        source_directory=str(local_dir),
        blob_name_prefix=f"{job_id}/output/",
        worker_type="thread",
    )
    for name, result in zip(files, results, strict=True):
        if isinstance(result, Exception):
            print(f"  upload FAILED: output/{name}: {result}")
        else:
            print(f"  uploaded: output/{name}")


def _upload_run_log_to_gcs(client, outputs_bucket: str, job_id: str, text: str) -> None:
    if not text or not outputs_bucket:
        return
    client.bucket(outputs_bucket).blob(f"{job_id}/run.log").upload_from_string(
        text, content_type="text/plain"
    )


def _print_manifest_tails(manifest: dict, label: str) -> None:
    """Print a failed pipeline's captured subprocess output into the run log.

    The training subprocesses run with capture_output=True, so their
    stdout/stderr only exist in the manifest tails — without this, a failed
    run's actual error never reaches run.log or the Modal container log.
    """
    print(
        f"==> {label} failed: returncode={manifest.get('returncode')}"
        f" final_fit_returncode={manifest.get('final_fit_returncode')}"
    )
    for stream in ("stdout_tail", "stderr_tail"):
        lines = manifest.get(stream) or []
        if lines:
            print(f"----- {stream} -----")
            for line in lines:
                print(line)
    print(f"----- end {label} output -----")


def _read_tar_member_bytes(tar_bytes: bytes, member_name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        extracted = tar.extractfile(member_name)
        if extracted is None:
            raise FileNotFoundError(f"{member_name!r} missing from intermediates bundle")
        return extracted.read()


_cache_gcs_client = None
_blending_repo_ref_cached: str | None = None


def _cache_gcs_blob(gs_uri: str):
    """Resolve a gs:// URI to a Blob, reusing one client across cache lookups."""
    global _cache_gcs_client
    from google.cloud import storage as gcs

    if _cache_gcs_client is None:
        _cache_gcs_client = gcs.Client()
    bucket_name, key = _split_gcs_uri(gs_uri, "cache_dir")
    return _cache_gcs_client.bucket(bucket_name).blob(key)


def _blending_repo_ref() -> str:
    """The onset_blending checkout's commit — processing output depends on the
    science code, so it belongs in every cache key. Both the Modal image and
    the local managed checkout keep .git metadata."""
    global _blending_repo_ref_cached
    if _blending_repo_ref_cached is None:
        import subprocess

        try:
            _blending_repo_ref_cached = subprocess.run(
                ["git", "-C", str(BLENDING_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except Exception:
            _blending_repo_ref_cached = os.environ.get(
                "ALMANAC_BLENDING_REPO_REF", DEFAULT_REPO_REF
            )
    return _blending_repo_ref_cached


def _file_sha256(path: Path) -> str:
    import hashlib

    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _cached_pickle(cache_dir: str | None, scope: str, key_material: dict, compute):
    """Read-through cache for one blend intermediate; returns (obj, was_cached).

    cache_dir is a local path or gs:// URI ending in .../blend-intermediates,
    or None to disable. Keys are content hashes (input-file sha256 +
    preprocessing params) plus the onset_blending ref and a schema version, so
    re-uploaded files, param changes, and science-code bumps all miss instead
    of returning stale results. Entries are self-produced pickles in our own
    bucket/data dir — the same trust model as combined_wide.pkl.
    """
    import hashlib
    import pickle
    import uuid

    if not cache_dir:
        return compute(), False

    digest = hashlib.sha256(
        json.dumps(key_material, sort_keys=True, default=str).encode()
    ).hexdigest()
    rel = f"v{BLEND_INTERMEDIATES_CACHE_VERSION}/{_blending_repo_ref()[:12]}/{scope}/{digest}.pkl"
    cache_uri = str(cache_dir)

    # ponytail: no eviction — entries are one small pickle per (file, params,
    # ref); stale ref prefixes are deletable by hand if the bucket ever grows.
    if cache_uri.startswith("gs://"):
        blob = _cache_gcs_blob(f"{cache_uri.rstrip('/')}/{rel}")
        if blob.exists():
            print(f"==> cache hit {scope} {digest[:8]}", flush=True)
            return pickle.loads(blob.download_as_bytes()), True
        print(f"==> cache miss {scope} {digest[:8]}", flush=True)
        obj = compute()
        blob.upload_from_string(pickle.dumps(obj))
        return obj, False

    cache_file = Path(cache_uri) / rel
    if cache_file.exists():
        print(f"==> cache hit {scope} {digest[:8]}", flush=True)
        with cache_file.open("rb") as f:
            return pickle.load(f), True
    print(f"==> cache miss {scope} {digest[:8]}", flush=True)
    obj = compute()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_file.with_name(f".{cache_file.name}.{uuid.uuid4().hex}.tmp")
    with tmp_path.open("wb") as f:
        pickle.dump(obj, f)
    os.replace(tmp_path, cache_file)
    return obj, False


@app.function(
    image=blending_image,
    cpu=(RUN_BLEND_CPU, 8),
    memory=(16384, 32768),
    timeout=21600,  # 6h ceiling. The build/train phases run via .local() in THIS container, so this is the only timeout that applies; billed on actual runtime, not the ceiling.
    secrets=[gcp_secret],
)
def run_blend(job_id: str, config: dict, outputs_bucket: str) -> None:
    """Stage obs + forecasts from GCS, build intermediates, train the blend, and
    publish weights/summary artifacts to gs://{outputs_bucket}/{job_id}/output/.

    Shares the (job_id, config, outputs_bucket) signature with run_benchmark so
    the platform's ModalRunner dispatches it the same way.
    """
    import sys
    import time
    import traceback
    from contextlib import redirect_stderr, redirect_stdout

    from google.cloud import storage as gcs

    log_buffer = io.StringIO()
    client = None
    failure: Exception | None = None

    with (
        redirect_stdout(_LogTee(sys.stdout, log_buffer)),
        redirect_stderr(_LogTee(sys.stderr, log_buffer)),
    ):
        try:
            _write_gcp_credentials_from_secret()
            client = gcs.Client()

            params = config.get("blend_params") or {}
            model_names = config["model_names"]
            # Per-model {year}.nc URIs, year-filtered and backend-resolved by the
            # server (data_catalog.year_uris). The run stages exactly these.
            model_files = config["model_files"]
            missing = [name for name in model_names if not model_files.get(name)]
            if missing:
                raise ValueError(f"No staging files provided for models: {missing}")

            stage_root = Path(tempfile.mkdtemp(prefix="blend-prod-"))
            obs_local = stage_root / "obs"
            obs_local.mkdir()
            # Obs is staged in full: the climatology needs the historical record,
            # and obs files are ~MB/year (forecasts are GB/year).
            print(f"==> Staging obs from {config['obs_dir']}")
            _stage_gcs_prefix(client, config["obs_dir"], obs_local, "obs")
            obs_bundle = _bundle_files(sorted(obs_local.glob("*.nc")))

            forecast_bundles: dict[str, bytes] = {}
            for key in model_names:
                model_local = stage_root / f"fc_{key}"
                model_local.mkdir()
                uris = model_files[key]
                print(f"==> Staging forecast {key}: {len(uris)} files")
                _stage_uris(client, uris, model_local, f"forecast {key}")
                forecast_bundles[key] = _bundle_files(sorted(model_local.glob("*.nc")))

            prep_kwargs = {
                k: params[k]
                for k in ("threshold_mm", "cutoff_month_day", "mok_month_day")
                if params.get(k) is not None
            }
            if config.get("region_id"):
                prep_kwargs["region_id"] = config["region_id"]
            cache_bucket = (config.get("gcs_cache_bucket") or "").strip()
            cache_dir = f"gs://{cache_bucket}/blend-intermediates" if cache_bucket else None
            print("==> Building blending intermediates")
            t0 = time.perf_counter()
            intermediates = build_lat_lon_intermediates_bundle.local(
                obs_bundle,
                forecast_bundles,
                return_outputs=True,
                cache_dir=cache_dir,
                **prep_kwargs,
            )
            print(f"==> Intermediates built in {time.perf_counter() - t0:.1f}s")
            combined = _read_tar_member_bytes(intermediates["outputs_tar"], "combined_wide.pkl")

            train_kwargs = {"cores": RUN_BLEND_TRAINING_CORES}
            if params.get("formula_text"):
                train_kwargs["formula_text"] = params["formula_text"]
            print("==> Training blend weights")
            t0 = time.perf_counter()
            training = train_blending_model_bundle.local(
                combined,
                model_names=model_names,
                training_years=_parse_years(params.get("training_years") or "") or [],
                cv_holdout_years=_parse_years(params.get("cv_holdout_years") or "") or [],
                true_holdout_years=_parse_years(params.get("true_holdout_years") or ""),
                return_outputs=True,
                **train_kwargs,
            )
            print(f"==> Training finished in {time.perf_counter() - t0:.1f}s")
            if not training["manifest"].get("ok"):
                _print_manifest_tails(training["manifest"], "Blend training pipeline")
                raise RuntimeError(
                    "Blend training pipeline failed; stderr tail printed above (see run.log)"
                )

            out_local = stage_root / "output"
            out_local.mkdir()
            (out_local / "combined_wide.pkl").write_bytes(combined)
            if training.get("outputs_tar"):
                with tarfile.open(fileobj=io.BytesIO(training["outputs_tar"]), mode="r:gz") as tar:
                    tar.extractall(out_local)
            _upload_output_dir_to_gcs(client, outputs_bucket, job_id, out_local)
            print("==> Done.")
        except Exception as exc:  # noqa: BLE001 — surfaced via run.log + raise
            failure = exc
            traceback.print_exc()
        finally:
            if client is not None:
                try:
                    _upload_run_log_to_gcs(client, outputs_bucket, job_id, log_buffer.getvalue())
                except Exception:
                    traceback.print_exc()

    if failure is not None:
        raise RuntimeError(
            f"Blend job {job_id} failed; see run.log for details: {failure}"
        ) from failure


def _candidate_files(input_dir: Path, year: int | None, max_files: int) -> list[Path]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input_dir is not a directory: {input_dir}")

    files = sorted(input_dir.glob("*.nc"))
    if year is not None:
        year_text = str(year)
        files = [path for path in files if year_text in path.name]

    if not files:
        label = f" for year {year}" if year is not None else ""
        raise FileNotFoundError(f"No .nc files found in {input_dir}{label}")

    return files[:max_files] if max_files > 0 else files


def _candidate_files_for_years(
    input_dir: Path,
    years: list[int] | None,
    max_files: int,
) -> list[Path]:
    if years is None:
        return _candidate_files(input_dir, None, max_files)

    files: list[Path] = []
    for year in years:
        files.extend(_candidate_files(input_dir, year, 0))
    files = sorted(dict.fromkeys(files))
    return files[:max_files] if max_files > 0 else files


def _bundle_files(files: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=path.name)
    return buffer.getvalue()


def _extract_bundle(input_bundle: bytes) -> Path:
    stage_root = Path(tempfile.mkdtemp(prefix="blend-test-"))
    bundle_path = stage_root / "inputs.tar.gz"
    bundle_path.write_bytes(input_bundle)
    input_dir = stage_root / "input"
    input_dir.mkdir()
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (input_dir / member.name).resolve()
            if not str(target).startswith(str(input_dir.resolve())):
                raise ValueError(f"Unsafe path in input bundle: {member.name}")
        tar.extractall(input_dir)
    return input_dir


def _tar_directory(directory: Path, include_names: set[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if include_names is not None and path.name not in include_names:
                continue
            tar.add(path, arcname=path.name)
    return buffer.getvalue()


def _shape(value) -> list[int]:
    return [int(dim) for dim in value.shape]


def _adm3_support_paths() -> tuple[Path, Path]:
    mapping = BLENDING_ROOT / "Monsoon_Data" / "grid_to_district_mapping.csv"
    dissemination = BLENDING_ROOT / "Monsoon_Data" / "dissemination_cells.csv"
    missing = [str(path) for path in (mapping, dissemination) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "ADM3 domain support files are missing from the blending checkout: "
            + ", ".join(missing)
        )
    return mapping, dissemination


def _should_use_adm3_domain(region_id: str | None, use_adm3_domain: bool | None) -> bool:
    if use_adm3_domain is not None:
        return bool(use_adm3_domain)
    return (region_id or "").strip().lower() in ADM3_DOMAIN_REGIONS


def _normalize_grid_dims(src: Path, dst: Path) -> Path:
    import xarray as xr

    with xr.open_dataset(src, mask_and_scale=True) as ds:
        rename = {}
        for name in ds.dims:
            lower = str(name).lower()
            if lower in {"latitude", "lat"} and name != "lat":
                rename[name] = "lat"
            elif lower in {"longitude", "lon"} and name != "lon":
                rename[name] = "lon"
        for name in ds.coords:
            lower = str(name).lower()
            if lower in {"latitude", "lat"} and name != "lat":
                rename[name] = "lat"
            elif lower in {"longitude", "lon"} and name != "lon":
                rename[name] = "lon"
        normalized = ds.rename(rename) if rename else ds
        normalized.to_netcdf(dst)
    return dst


def _has_adm3_dimension(path: Path) -> bool:
    import xarray as xr

    with xr.open_dataset(path, decode_times=False) as ds:
        names = {str(name).lower() for name in [*ds.dims, *ds.coords]}
    return bool(names & {"adm3", "adm3_name"})


def _remap_bundle_to_adm3(bundle: bytes, label: str, cache_dir: str | None = None) -> bytes:
    import sys

    sys.path.insert(0, str(BLENDING_ROOT))
    from utils.remap_nc import batch_aggregate_to_adm3_matrix

    mapping_path, _ = _adm3_support_paths()
    input_dir = _extract_bundle(bundle)
    remap_dir = Path(tempfile.mkdtemp(prefix=f"{label}-adm3-input-"))
    out_dir = Path(tempfile.mkdtemp(prefix=f"{label}-adm3-output-"))
    output_paths: list[Path] = []

    cache_prefix = None
    if cache_dir and str(cache_dir).startswith("gs://"):
        cache_prefix = (
            f"{str(cache_dir).rstrip('/')}"
            f"/v{BLEND_INTERMEDIATES_CACHE_VERSION}/{_blending_repo_ref()[:12]}/adm3_nc"
        )

    for src in sorted(input_dir.glob("*.nc")):
        target = out_dir / src.name

        if _has_adm3_dimension(src):
            target.write_bytes(src.read_bytes())
            output_paths.append(target)
            continue

        cached_bytes = None
        cache_blob = None
        if cache_prefix:
            file_hash = _file_sha256(src)
            cache_blob = _cache_gcs_blob(f"{cache_prefix}/{file_hash}.nc")
            if cache_blob.exists():
                print(f"==> cache hit adm3_nc {file_hash[:8]}", flush=True)
                cached_bytes = cache_blob.download_as_bytes()
            else:
                print(f"==> cache miss adm3_nc {file_hash[:8]}", flush=True)

        if cached_bytes is not None:
            target.write_bytes(cached_bytes)
        else:
            normalized = _normalize_grid_dims(src, remap_dir / src.name)
            batch_aggregate_to_adm3_matrix(
                str(remap_dir),
                str(mapping_path),
                input_file=str(normalized),
            )
            remapped = normalized.with_name(f"{normalized.stem}_adm3{normalized.suffix}")
            if not remapped.is_file():
                raise FileNotFoundError(f"ADM3 remap did not produce {remapped}")
            shutil.move(str(remapped), target)
            if cache_blob is not None:
                cache_blob.upload_from_string(
                    target.read_bytes(), content_type="application/octet-stream"
                )

        output_paths.append(target)

    if not output_paths:
        raise ValueError(f"No NetCDF files found while remapping {label} to ADM3")
    return _bundle_files(output_paths)


def _adm3_centroids():
    import pandas as pd

    mapping_path, _ = _adm3_support_paths()
    mapping = pd.read_csv(mapping_path).rename(columns={"latitude": "lat", "longitude": "lon"})
    required = {"adm3_name", "lat", "lon", "weight"}
    missing = required - set(mapping.columns)
    if missing:
        raise ValueError(f"ADM3 mapping is missing required columns: {', '.join(sorted(missing))}")

    mapping["weight"] = mapping["weight"].astype(float)
    mapping["weighted_lat"] = mapping["lat"].astype(float) * mapping["weight"]
    mapping["weighted_lon"] = mapping["lon"].astype(float) * mapping["weight"]
    grouped = mapping.groupby("adm3_name", as_index=False)[
        ["weight", "weighted_lat", "weighted_lon"]
    ].sum()
    grouped["lat"] = grouped["weighted_lat"] / grouped["weight"]
    grouped["lon"] = grouped["weighted_lon"] / grouped["weight"]
    return grouped.rename(columns={"adm3_name": "id"})[["id", "lat", "lon"]]


def _attach_adm3_centroids_to_csv(csv_bytes: bytes) -> bytes:
    import pandas as pd

    rows = pd.read_csv(io.BytesIO(csv_bytes))
    if {"lat", "lon"}.issubset(rows.columns):
        return csv_bytes
    if "id" not in rows.columns:
        raise ValueError("Cannot attach ADM3 centroids: prediction CSV has no id column")

    centroids = _adm3_centroids()
    out = rows.merge(centroids, on="id", how="left", validate="many_to_one")
    missing = out[out["lat"].isna() | out["lon"].isna()]["id"].drop_duplicates()
    if not missing.empty:
        sample = ", ".join(missing.astype(str).head(10).tolist())
        raise ValueError(f"ADM3 centroid mapping is missing prediction ids: {sample}")
    return out.to_csv(index=False).encode("utf-8")


def _add_lat_lon_id(df, precision: int):
    if "id" in df.columns:
        return df
    if "lat" not in df.columns or "lon" not in df.columns:
        raise ValueError("Cannot create id: expected lat and lon columns")
    df = df.copy()
    df["id"] = [
        f"{float(lat):.{precision}f}_{float(lon):.{precision}f}"
        for lat, lon in zip(df["lat"], df["lon"], strict=True)
    ]
    return df


def _parse_years(value: str) -> list[int] | None:
    stripped = value.strip()
    if not stripped:
        return None
    years: list[int] = []
    for part in stripped.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            start_text, end_text = token.split(":", 1)
            years.extend(range(int(start_text), int(end_text) + 1))
        else:
            years.append(int(token))
    return sorted(dict.fromkeys(years))


def _parse_forecast_inputs(value: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError("forecast_inputs must be comma-separated model=directory entries")
        name, path = token.split("=", 1)
        model_name = name.strip()
        if not model_name:
            raise ValueError(f"Missing model name in forecast input: {token!r}")
        result[model_name] = Path(path.strip()).expanduser()
    if not result:
        raise ValueError("At least one forecast input is required")
    return result


def _parse_model_names(value: str) -> list[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    if not names:
        raise ValueError("At least one model name is required")
    return names


def _copy_tar_member(input_tar: bytes, member_name: str, output_path: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(input_tar), mode="r:gz") as tar:
        try:
            member = tar.getmember(member_name)
        except KeyError as exc:
            raise FileNotFoundError(f"{member_name!r} was not found in artifact bundle") from exc
        extracted = tar.extractfile(member)
        if extracted is None:
            raise FileNotFoundError(f"{member_name!r} is not a regular file")
        output_path.write_bytes(extracted.read())


@app.function(image=blending_image, cpu=2, memory=4096, timeout=600)
def inspect_netcdf_bundle(input_bundle: bytes) -> dict:
    """Return dimensions, coordinates, and variable metadata for staged NetCDFs."""
    import xarray as xr

    input_dir = _extract_bundle(input_bundle)
    files = []
    for path in sorted(input_dir.glob("*.nc")):
        ds = xr.open_dataset(path)
        try:
            files.append(
                {
                    "filename": path.name,
                    "dims": {name: int(size) for name, size in ds.sizes.items()},
                    "coords": list(ds.coords),
                    "data_vars": {
                        name: {"dims": list(da.dims), "shape": _shape(da)}
                        for name, da in ds.data_vars.items()
                    },
                }
            )
        finally:
            ds.close()
    return {"files": files}


@app.function(image=blending_image, cpu=2, memory=8192, timeout=900)
def probe_forecast_reader_bundle(
    input_bundle: bytes,
    value_col: str = "tp",
    min_day: int = 1,
    max_day: int = 45,
) -> dict:
    """Run the blending repo's current forecast reader against staged NetCDFs."""
    import sys
    import traceback

    sys.path.insert(0, str(BLENDING_ROOT))
    from python.prepare_data.nc_utils import nc_read_forecast_wide

    input_dir = _extract_bundle(input_bundle)
    spec = {
        "input": {"value_col": value_col, "wide_day_dim": "day", "wide_prefix": "rain"},
        "dimensions": {
            "rename": {
                "time": "time",
                "day": "day",
                "lat": "lat",
                "lon": "lon",
                "number": "number",
                "sample": "number",
            }
        },
        "options": {"min_day": min_day, "max_day": max_day},
    }

    results = []
    for path in sorted(input_dir.glob("*.nc")):
        try:
            df = nc_read_forecast_wide(
                nc_path=str(path),
                var_name=value_col,
                dim_rename_map=spec["dimensions"]["rename"],
                spec=spec,
                day_dim="day",
                prefix="rain",
            )
            results.append(
                {
                    "filename": path.name,
                    "ok": True,
                    "rows": len(df),
                    "columns": list(df.columns[:30]),
                    "has_id": "id" in df.columns,
                    "sample_rows": df.head(3).to_dict(orient="records"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "filename": path.name,
                    "ok": False,
                    "error": str(exc),
                    "traceback_tail": traceback.format_exc().splitlines()[-12:],
                }
            )
    return {"results": results}


@app.function(image=blending_image, cpu=2, memory=8192, timeout=900)
def probe_lat_lon_onset_bundle(
    input_bundle: bytes,
    value_col: str = "tp",
    min_day: int = 1,
    max_day: int = 45,
    threshold_mm: float = 20.0,
    id_precision: int = 2,
    row_limit: int = 5000,
    mok_month_day: str | None = "06-01",
    sample_row_count: int = 0,
) -> dict:
    """Create lat_lon ids and run the forecast onset processing step."""
    import sys
    import traceback

    import pandas as pd

    sys.path.insert(0, str(BLENDING_ROOT))
    from python.prepare_data.nc_utils import (
        nc_read_forecast_wide,
        process_rainfall_forecast_id,
    )

    input_dir = _extract_bundle(input_bundle)
    spec = {
        "input": {"value_col": value_col, "wide_day_dim": "day", "wide_prefix": "rain"},
        "dimensions": {
            "rename": {
                "time": "time",
                "day": "day",
                "lat": "lat",
                "lon": "lon",
                "number": "number",
                "sample": "number",
            }
        },
        "options": {
            "min_day": min_day,
            "max_day": max_day,
            "window": 3,
            "onset_definition": {
                "wet_day_min_mm": 1.0,
                "follow_days": 21,
                "dry_spell": {
                    "mode": "consecutive_dry",
                    "min_dry_days": 5,
                    "dry_day_min_mm": 1.0,
                },
            },
        },
        "filter": {},
    }

    results = []
    for path in sorted(input_dir.glob("*.nc")):
        try:
            df = nc_read_forecast_wide(
                nc_path=str(path),
                var_name=value_col,
                dim_rename_map=spec["dimensions"]["rename"],
                spec=spec,
                day_dim="day",
                prefix="rain",
            )
            df = _add_lat_lon_id(df, precision=id_precision)
            rows_before_limit = len(df)
            if row_limit > 0:
                df = df.head(row_limit).copy()

            mok_dt = None
            if mok_month_day:
                years = sorted(int(year) for year in df["year"].dropna().unique())
                mok_dt = pd.DataFrame(
                    {
                        "year": years,
                        "mok_date": [
                            pd.Timestamp(f"{year}-{mok_month_day}").date() for year in years
                        ],
                    }
                )

            processed = process_rainfall_forecast_id(
                df,
                spec,
                mok_dt=mok_dt,
                thr_dt=float(threshold_mm),
            )["wide"]

            member_counts = None
            if "number" in df.columns:
                counts = df.groupby(["id", "time"]).size()
                member_counts = {
                    "min": int(counts.min()),
                    "max": int(counts.max()),
                    "mean": float(counts.mean()),
                }

            prob_cols = [col for col in processed.columns if col.startswith("predicted_prob_day_")]
            clim_prob_cols = [
                col
                for col in processed.columns
                if col.startswith("predicted_prob_clim_mok_date_day_")
            ]
            sd_cols = [col for col in processed.columns if col.startswith("forecast_rain_sd_day_")]
            sample_cols = [
                "id",
                "time",
                "forecast_rain_day_1",
                "forecast_rain_day_7",
                "forecast_rain_day_14",
                "forecast_rain_sd_day_1",
                "predicted_prob_day_1",
                "predicted_prob_day_7",
                "predicted_prob_day_14",
            ]
            sample_cols = [col for col in sample_cols if col in processed.columns]

            results.append(
                {
                    "filename": path.name,
                    "ok": True,
                    "rows_before_limit": rows_before_limit,
                    "rows_processed": len(df),
                    "wide_rows": len(processed),
                    "member_counts_per_id_time": member_counts,
                    "wide_columns": list(processed.columns[:40]),
                    "sample_ids": processed["id"].head(5).tolist()
                    if "id" in processed.columns
                    else [],
                    "nonzero_predicted_prob_cells": int((processed[prob_cols] > 0).sum().sum())
                    if prob_cols
                    else 0,
                    "nonzero_clim_mok_prob_cells": int((processed[clim_prob_cols] > 0).sum().sum())
                    if clim_prob_cols
                    else 0,
                    "non_null_sd_cells": int(processed[sd_cols].notna().sum().sum())
                    if sd_cols
                    else 0,
                    "sample_rows": processed[sample_cols]
                    .head(sample_row_count)
                    .to_dict(orient="records")
                    if sample_row_count > 0
                    else [],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "filename": path.name,
                    "ok": False,
                    "error": str(exc),
                    "traceback_tail": traceback.format_exc().splitlines()[-12:],
                }
            )
    return {"results": results}


@app.function(image=blending_image, cpu=2, memory=8192, timeout=900)
def probe_lat_lon_ground_truth_bundle(
    input_bundle: bytes,
    value_col: str = "RAINFALL",
    threshold_mm: float = 20.0,
    id_precision: int = 2,
    cutoff_month_day: str = "05-01",
    mok_month_day: str | None = "06-01",
    row_limit: int = 0,
    sample_row_count: int = 0,
) -> dict:
    """Create lat_lon ids and run the ground-truth onset processing step."""
    import sys
    import traceback

    import pandas as pd

    sys.path.insert(0, str(BLENDING_ROOT))
    from python.prepare_data.nc_utils import (
        nc_read_groundtruth_long,
        process_ground_truth_rainfall_id,
    )

    input_dir = _extract_bundle(input_bundle)
    spec = {
        "input": {"value_col": value_col},
        "dimensions": {
            "rename": {
                "TIME": "time",
                "time": "time",
                "LATITUDE": "lat",
                "latitude": "lat",
                "lat": "lat",
                "LONGITUDE": "lon",
                "longitude": "lon",
                "lon": "lon",
            }
        },
        "options": {
            "min_day": 1,
            "max_day": 45,
            "window": 3,
            "cutoff_month_day": cutoff_month_day,
            "onset_definition": {
                "wet_day_min_mm": 1.0,
                "follow_days": 21,
                "dry_spell": {
                    "mode": "consecutive_dry",
                    "min_dry_days": 5,
                    "dry_day_min_mm": 1.0,
                },
            },
        },
        "filter": {},
    }

    results = []
    for path in sorted(input_dir.glob("*.nc")):
        try:
            df = nc_read_groundtruth_long(
                nc_path=str(path),
                var_name=value_col,
                dim_rename_map=spec["dimensions"]["rename"],
            )
            df = _add_lat_lon_id(df, precision=id_precision)
            rows_before_limit = len(df)
            if row_limit > 0:
                df = df.head(row_limit).copy()

            mok_dt = None
            if mok_month_day:
                years = sorted(int(year) for year in df["year"].dropna().unique())
                mok_dt = pd.DataFrame(
                    {
                        "year": years,
                        "mok_date": [
                            pd.Timestamp(f"{year}-{mok_month_day}").date() for year in years
                        ],
                    }
                )

            processed = process_ground_truth_rainfall_id(
                df,
                spec,
                mok_dt=mok_dt,
                thr_dt=float(threshold_mm),
                value_col=value_col.lower(),
            )
            wide = processed["wide"]
            long = processed["long"]
            onset_days = wide["mr_onset_day"].dropna()
            sample_cols = [
                "id",
                "year",
                "mr_onset_idx",
                "mr_onset_date",
                "mr_onset_day",
                "cutoff_date",
            ]
            sample_cols = [col for col in sample_cols if col in wide.columns]
            results.append(
                {
                    "filename": path.name,
                    "ok": True,
                    "rows_before_limit": rows_before_limit,
                    "rows_processed": len(df),
                    "wide_rows": len(wide),
                    "long_rows": len(long),
                    "onset_count": int(wide["mr_onset_day"].notna().sum())
                    if "mr_onset_day" in wide.columns
                    else 0,
                    "onset_day_min": float(onset_days.min()) if len(onset_days) else None,
                    "onset_day_max": float(onset_days.max()) if len(onset_days) else None,
                    "sample_ids": wide["id"].head(5).tolist() if "id" in wide.columns else [],
                    "sample_rows": wide[sample_cols]
                    .head(sample_row_count)
                    .to_dict(orient="records")
                    if sample_row_count > 0
                    else [],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "filename": path.name,
                    "ok": False,
                    "error": str(exc),
                    "traceback_tail": traceback.format_exc().splitlines()[-12:],
                }
            )
    return {"results": results}


@app.function(image=blending_image, cpu=4, memory=16384, timeout=3600)
def build_lat_lon_intermediates_bundle(
    obs_bundle: bytes,
    forecast_bundles: dict[str, bytes],
    obs_value_col: str = "RAINFALL",
    forecast_value_col: str = "tp",
    min_day: int = 1,
    max_day: int = 45,
    threshold_mm: float = 20.0,
    id_precision: int = 2,
    cutoff_month_day: str = "05-01",
    mok_month_day: str | None = "06-01",
    include_long: bool = False,
    build_climatology: bool = True,
    build_combined: bool = True,
    climatology_train_year_min: int | None = None,
    climatology_train_year_max: int | None = None,
    climatology_test_year_min: int | None = None,
    climatology_test_year_max: int | None = None,
    min_onset_years: int = 10,
    forecast_window: int = 45,
    issue_end_month_day: str = "07-31",
    combine_join: str = "inner",
    trim_forecasts_after_true_onset: bool = True,
    region_id: str | None = None,
    use_adm3_domain: bool | None = None,
    return_outputs: bool = True,
    cache_dir: str | None = None,
) -> dict:
    """Build real blending intermediate pickle files from staged NetCDF bundles.

    cache_dir enables a read-through cache (local path or gs:// URI) of the
    per-file processed parts and the climatology — the expensive,
    weights-independent work — so repeat runs over the same inputs (live
    forecast updates especially) skip recomputation. See _cached_pickle.
    """
    import pickle
    import sys

    import pandas as pd

    sys.path.insert(0, str(BLENDING_ROOT))
    from python.prepare_data.nc_utils import (
        nc_read_forecast_wide,
        nc_read_groundtruth_long,
        process_ground_truth_rainfall_id,
        process_rainfall_forecast_id,
    )

    adm3_domain = _should_use_adm3_domain(region_id, use_adm3_domain)
    dissemination_path = None
    if adm3_domain:
        _, dissemination_path = _adm3_support_paths()
        print(
            f"==> Remapping {region_id or 'configured'} blend inputs to ADM3 domain",
            flush=True,
        )
        obs_bundle = _remap_bundle_to_adm3(obs_bundle, "obs", cache_dir=cache_dir)
        forecast_bundles = {
            model_name: _remap_bundle_to_adm3(bundle, f"forecast-{model_name}", cache_dir=cache_dir)
            for model_name, bundle in forecast_bundles.items()
        }

    output_dir = Path(tempfile.mkdtemp(prefix="blend-intermediates-"))
    obs_dir = _extract_bundle(obs_bundle)
    forecast_dirs = {
        model_name: _extract_bundle(bundle) for model_name, bundle in forecast_bundles.items()
    }

    onset_options = {
        "window": 3,
        "cutoff_month_day": cutoff_month_day,
        "onset_definition": {
            "wet_day_min_mm": 1.0,
            "follow_days": 21,
            "dry_spell": {
                "mode": "consecutive_dry",
                "min_dry_days": 5,
                "dry_day_min_mm": 1.0,
            },
        },
    }
    forecast_spec = {
        "input": {
            "value_col": forecast_value_col,
            "wide_day_dim": "day",
            "wide_prefix": "rain",
        },
        "dimensions": {
            "rename": {
                "time": "time",
                "day": "day",
                "lat": "lat",
                "lon": "lon",
                "adm3_name": "adm3_name",
                "adm3": "adm3_name",
                "number": "number",
                "sample": "number",
            }
        },
        "options": {**onset_options, "min_day": min_day, "max_day": max_day},
        "filter": {
            "dissemination_cells_file": str(dissemination_path) if dissemination_path else None
        },
    }
    obs_spec = {
        "input": {"value_col": obs_value_col},
        "dimensions": {
            "rename": {
                "TIME": "time",
                "time": "time",
                "LATITUDE": "lat",
                "latitude": "lat",
                "lat": "lat",
                "LONGITUDE": "lon",
                "longitude": "lon",
                "lon": "lon",
                "ADM3_NAME": "adm3_name",
                "adm3_name": "adm3_name",
                "adm3": "adm3_name",
            }
        },
        "options": {**onset_options, "min_day": min_day, "max_day": max_day},
        "filter": {
            "dissemination_cells_file": str(dissemination_path) if dissemination_path else None
        },
    }

    def mok_for(df) -> pd.DataFrame | None:
        if not mok_month_day:
            return None
        years = sorted(int(year) for year in df["year"].dropna().unique())
        return pd.DataFrame(
            {
                "year": years,
                "mok_date": [pd.Timestamp(f"{year}-{mok_month_day}").date() for year in years],
            }
        )

    manifest: dict = {
        "threshold_mm": float(threshold_mm),
        "id_precision": int(id_precision),
        "cutoff_month_day": cutoff_month_day,
        "mok_month_day": mok_month_day,
        "region_id": region_id,
        "adm3_domain": bool(adm3_domain),
        "climatology": {},
        "combined": {},
        "outputs": {},
        "obs": {},
        "forecasts": {},
    }

    def process_obs_file(path: Path) -> dict:
        df = nc_read_groundtruth_long(
            nc_path=str(path),
            var_name=obs_value_col,
            dim_rename_map=obs_spec["dimensions"]["rename"],
        )
        df = _add_lat_lon_id(df, precision=id_precision)
        return process_ground_truth_rainfall_id(
            df,
            obs_spec,
            mok_dt=mok_for(df),
            thr_dt=float(threshold_mm),
            value_col=obs_value_col.lower(),
        )

    # Everything that shapes a processed part besides the input file itself;
    # hardcoded onset options are covered by the repo-ref + version segments.
    static_cache_params = {
        "id_precision": int(id_precision),
        "threshold_mm": float(threshold_mm),
        "min_day": int(min_day),
        "max_day": int(max_day),
        "cutoff_month_day": cutoff_month_day,
        "mok_month_day": mok_month_day,
        "adm3_domain": bool(adm3_domain),
    }
    cache_hits = 0
    cache_misses = 0
    obs_file_digests: list[str] = []

    obs_wide_parts = []
    obs_long_parts = []
    for path in sorted(obs_dir.glob("*.nc")):
        if cache_dir:
            obs_file_digests.append(_file_sha256(path))
        if cache_dir and not include_long:
            wide, was_cached = _cached_pickle(
                cache_dir,
                "obs",
                {
                    **static_cache_params,
                    "obs_value_col": obs_value_col,
                    "file_sha256": obs_file_digests[-1],
                },
                lambda p=path: process_obs_file(p)["wide"],
            )
            cache_hits += was_cached
            cache_misses += not was_cached
            obs_wide_parts.append(wide)
        else:
            processed = process_obs_file(path)
            obs_wide_parts.append(processed["wide"])
            obs_long_parts.append(processed["long"])

    obs_wide = pd.concat(obs_wide_parts, ignore_index=True)
    obs_wide_path = output_dir / "ground_truth_wide.pkl"
    with obs_wide_path.open("wb") as f:
        pickle.dump(obs_wide, f)
    manifest["outputs"][obs_wide_path.name] = {"bytes": obs_wide_path.stat().st_size}
    onset_days = obs_wide["mr_onset_day"].dropna()
    manifest["obs"] = {
        "wide_rows": int(len(obs_wide)),
        "onset_count": int(obs_wide["mr_onset_day"].notna().sum()),
        "onset_day_min": float(onset_days.min()) if len(onset_days) else None,
        "onset_day_max": float(onset_days.max()) if len(onset_days) else None,
        "years": sorted(int(year) for year in obs_wide["year"].dropna().unique()),
        "sample_ids": obs_wide["id"].head(5).tolist(),
    }

    if include_long:
        obs_long = pd.concat(obs_long_parts, ignore_index=True)
        obs_long_path = output_dir / "ground_truth_long.pkl"
        with obs_long_path.open("wb") as f:
            pickle.dump(obs_long, f)
        manifest["outputs"][obs_long_path.name] = {"bytes": obs_long_path.stat().st_size}
        manifest["obs"]["long_rows"] = int(len(obs_long))

    def process_forecast_file(path: Path) -> dict:
        df = nc_read_forecast_wide(
            nc_path=str(path),
            var_name=forecast_value_col,
            dim_rename_map=forecast_spec["dimensions"]["rename"],
            spec=forecast_spec,
            day_dim="day",
            prefix="rain",
        )
        df = _add_lat_lon_id(df, precision=id_precision)
        member_counts = df.groupby(["id", "time"]).size().tolist() if "number" in df.columns else []
        processed = process_rainfall_forecast_id(
            df,
            forecast_spec,
            mok_dt=mok_for(df),
            thr_dt=float(threshold_mm),
        )
        return {"wide": processed["wide"], "member_counts": member_counts}

    for model_name, input_dir in forecast_dirs.items():
        forecast_wide_parts = []
        member_counts_all = []
        for path in sorted(input_dir.glob("*.nc")):
            # The forecast spec is model-agnostic, so the key needs no model
            # name: identical files reuse one entry across models.
            entry, was_cached = _cached_pickle(
                cache_dir,
                "fc",
                {
                    **static_cache_params,
                    "forecast_value_col": forecast_value_col,
                    "file_sha256": _file_sha256(path) if cache_dir else None,
                },
                lambda p=path: process_forecast_file(p),
            )
            cache_hits += was_cached
            cache_misses += not was_cached
            forecast_wide_parts.append(entry["wide"])
            member_counts_all.extend(entry["member_counts"])

        forecast_wide = pd.concat(forecast_wide_parts, ignore_index=True)
        forecast_path = output_dir / f"{model_name}_wide.pkl"
        with forecast_path.open("wb") as f:
            pickle.dump(forecast_wide, f)
        manifest["outputs"][forecast_path.name] = {"bytes": forecast_path.stat().st_size}
        prob_cols = [col for col in forecast_wide.columns if col.startswith("predicted_prob_day_")]
        sd_cols = [col for col in forecast_wide.columns if col.startswith("forecast_rain_sd_day_")]
        manifest["forecasts"][model_name] = {
            "wide_rows": int(len(forecast_wide)),
            "years": sorted(int(year) for year in forecast_wide["year"].dropna().unique()),
            "sample_ids": forecast_wide["id"].head(5).tolist(),
            "nonzero_predicted_prob_cells": int((forecast_wide[prob_cols] > 0).sum().sum())
            if prob_cols
            else 0,
            "non_null_sd_cells": int(forecast_wide[sd_cols].notna().sum().sum()) if sd_cols else 0,
            "member_counts_per_id_time": {
                "min": int(min(member_counts_all)),
                "max": int(max(member_counts_all)),
                "mean": float(sum(member_counts_all) / len(member_counts_all)),
            }
            if member_counts_all
            else None,
        }

    forecast_paths = {
        model_name: output_dir / f"{model_name}_wide.pkl" for model_name in forecast_dirs
    }

    if build_climatology:
        from python.prepare_data.climatology_utils import (
            build_issue_grid,
            compute_all_forecasts,
            filter_gt_training,
            read_gt_onset_from_tbl,
        )

        obs_years = sorted(int(year) for year in obs_wide["year"].dropna().unique())
        forecast_years = sorted(
            {
                int(year)
                for model_name in forecast_dirs
                for year in manifest["forecasts"][model_name]["years"]
            }
        )
        if not obs_years:
            raise ValueError("Cannot build climatology without obs years")
        if not forecast_years:
            raise ValueError("Cannot build climatology without forecast years")

        test_year_min = (
            int(climatology_test_year_min)
            if climatology_test_year_min is not None
            else min(forecast_years)
        )
        test_year_max = (
            int(climatology_test_year_max)
            if climatology_test_year_max is not None
            else max(forecast_years)
        )
        train_year_min = (
            int(climatology_train_year_min)
            if climatology_train_year_min is not None
            else min(obs_years)
        )
        default_train_max = min(test_year_min - 1, max(obs_years))
        if default_train_max < train_year_min:
            default_train_max = max(obs_years)
        train_year_max = (
            int(climatology_train_year_max)
            if climatology_train_year_max is not None
            else default_train_max
        )

        def compute_climatology() -> dict:
            gt = read_gt_onset_from_tbl(obs_wide, onset_col="mr_onset_day")
            gt_train = filter_gt_training(gt, train_year_min, train_year_max)
            onset_counts = gt_train.groupby("id")["onset_day"].size()
            eligible_ids = onset_counts[onset_counts >= int(min_onset_years)].index
            gt_train = gt_train[gt_train["id"].isin(eligible_ids)].copy()
            if gt_train.empty:
                raise ValueError(
                    "No cells have enough historical onset years for climatology. "
                    f"min_onset_years={min_onset_years}, "
                    f"train_years={train_year_min}:{train_year_max}"
                )

            issue_grid = build_issue_grid(
                test_year_min,
                test_year_max,
                cutoff_month_day,
                issue_end_month_day,
            )
            clim = compute_all_forecasts(
                gt_train,
                issue_grid,
                cutoff_month_day,
                int(forecast_window),
                horizons=None,
                conditional=True,
                cv_by_year=False,
            )["forecasts"]
            clim_unc = compute_all_forecasts(
                gt_train,
                issue_grid,
                cutoff_month_day,
                int(forecast_window),
                horizons=None,
                conditional=False,
                cv_by_year=False,
            )["forecasts"]
            return {
                "clim": clim,
                "clim_unc": clim_unc,
                "eligible_cells": int(len(eligible_ids)),
            }

        # Climatology derives only from obs_wide plus these params, so the
        # obs file digests fully identify its data input. The resolved year
        # windows are in the key: the live year moves test_year_max once per
        # season, so updates 2..N hit.
        climatology, was_cached = _cached_pickle(
            cache_dir,
            "clim",
            {
                **static_cache_params,
                "obs_value_col": obs_value_col,
                "obs_files_sha256": obs_file_digests,
                "issue_end_month_day": issue_end_month_day,
                "forecast_window": int(forecast_window),
                "min_onset_years": int(min_onset_years),
                "train_year_min": train_year_min,
                "train_year_max": train_year_max,
                "test_year_min": test_year_min,
                "test_year_max": test_year_max,
            },
            compute_climatology,
        )
        cache_hits += was_cached
        cache_misses += not was_cached
        clim = climatology["clim"]
        clim_unc = climatology["clim_unc"]

        clim_path = output_dir / "climatology_issue.pkl"
        clim_unc_path = output_dir / "climatology_issue_unc.pkl"
        with clim_path.open("wb") as f:
            pickle.dump(clim, f)
        with clim_unc_path.open("wb") as f:
            pickle.dump(clim_unc, f)
        manifest["outputs"][clim_path.name] = {"bytes": clim_path.stat().st_size}
        manifest["outputs"][clim_unc_path.name] = {"bytes": clim_unc_path.stat().st_size}
        manifest["climatology"] = {
            "train_year_min": train_year_min,
            "train_year_max": train_year_max,
            "test_year_min": test_year_min,
            "test_year_max": test_year_max,
            "min_onset_years": int(min_onset_years),
            "forecast_window": int(forecast_window),
            "issue_end_month_day": issue_end_month_day,
            "eligible_cells": climatology["eligible_cells"],
            "conditional_rows": int(len(clim)),
            "unconditional_rows": int(len(clim_unc)),
            "conditional_non_null_cells": int(
                clim.filter(regex=r"^predicted_prob_day_").notna().sum().sum()
            ),
            "unconditional_non_null_cells": int(
                clim_unc.filter(regex=r"^predicted_prob_day_").notna().sum().sum()
            ),
        }

    if build_combined:
        if not build_climatology:
            raise ValueError("build_combined requires build_climatology=True")

        from functools import reduce

        from python.prepare_data.combine_forecasts_utils import (
            format_forecast_family,
            read_and_format_climatology_wide,
            read_ground_truth_wide,
        )

        forecast_years_by_model = {
            model_name: manifest["forecasts"][model_name]["years"] for model_name in forecast_dirs
        }
        forecast_parts = {}
        for model_name, forecast_path in forecast_paths.items():
            years = forecast_years_by_model[model_name]
            if not years:
                raise ValueError(f"Forecast {model_name!r} has no years")
            years_spec = f"{min(years)}:{max(years)}"
            daily = [
                {
                    "col": "predicted_prob",
                    "out": "p_onset",
                    "add_plus": True,
                },
                {
                    "col": "predicted_prob_clim_mok_date",
                    "out": "p_onset_clim_mok_date",
                    "add_plus": True,
                },
                {
                    "col": "predicted_prob_mok",
                    "out": "p_onset_mok",
                    "add_plus": True,
                },
                {"col": "forecast_rain", "out": "rain_mean", "add_plus": False},
                {"col": "frac_raining", "out": "frac_raining", "add_plus": False},
            ]
            if manifest["forecasts"][model_name]["non_null_sd_cells"] > 0:
                daily.append(
                    {
                        "col": "forecast_rain_sd",
                        "out": "rain_sd",
                        "add_plus": False,
                    }
                )
            forecast_parts[model_name] = format_forecast_family(
                model_name,
                {
                    "max_day": int(max_day),
                    "sources": [{"file": str(forecast_path), "years": years_spec}],
                    "constants": [
                        {"col": "onset_thresh", "out": "onset_thresh"},
                        {"col": "mok_date", "out": "mok_date"},
                    ],
                    "daily": daily,
                },
            )

        daily_tables = [
            read_and_format_climatology_wide(
                str(output_dir / "climatology_issue.pkl"),
                out_prefix="clim_p_onset",
            ),
            read_and_format_climatology_wide(
                str(output_dir / "climatology_issue_unc.pkl"),
                out_prefix="clim_unc_p_onset",
            ),
        ]
        daily_tables.extend(part["daily"] for part in forecast_parts.values())
        join_how = "outer" if combine_join == "full" else "inner"
        daily_wide = reduce(
            lambda left, right: left.merge(
                right,
                on=["id", "time", "year"],
                how=join_how,
            ),
            daily_tables,
        )
        daily_wide["year"] = daily_wide["year"].astype(int)
        truth = read_ground_truth_wide(str(obs_wide_path))
        combined = daily_wide.merge(truth, on=["id", "year"], how="left")
        if trim_forecasts_after_true_onset:
            mask = combined["true_onset_date"].isna() | (
                pd.to_datetime(combined["time"]) <= pd.to_datetime(combined["true_onset_date"])
            )
            combined = combined.loc[mask].copy()

        constant_tables = [part["constants"] for part in forecast_parts.values()]
        if constant_tables:
            constants = reduce(
                lambda left, right: left.merge(
                    right,
                    on=["id", "time", "year"],
                    how="outer",
                ),
                constant_tables,
            )
            combined = combined.merge(
                constants,
                on=["id", "time", "year"],
                how="left",
            )

        combined_path = output_dir / "combined_wide.pkl"
        with combined_path.open("wb") as f:
            pickle.dump(combined, f)
        manifest["outputs"][combined_path.name] = {"bytes": combined_path.stat().st_size}
        manifest["combined"] = {
            "rows": int(len(combined)),
            "columns": int(len(combined.columns)),
            "join": combine_join,
            "trim_forecasts_after_true_onset": bool(trim_forecasts_after_true_onset),
            "years": sorted(int(year) for year in combined["year"].dropna().unique()),
            "sample_ids": combined["id"].head(5).tolist(),
            "first_columns": list(combined.columns[:60]),
        }

    if cache_dir:
        manifest["cache"] = {"hits": cache_hits, "misses": cache_misses}

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    manifest["outputs"][manifest_path.name] = {"bytes": manifest_path.stat().st_size}

    outputs_tar = None
    if return_outputs:
        include_names = set(manifest["outputs"])
        outputs_tar = _tar_directory(output_dir, include_names=include_names)

    return {"manifest": manifest, "outputs_tar": outputs_tar}


def _prepare_blend_workspace(
    combined_wide_pkl: bytes,
    model_names: list[str],
    cutoff_mode: str,
    day_max: int,
    days_per_week: int,
    n_weeks: int,
    rain_window: int,
):
    """Materialize the workspace both training and coef-apply need: the
    combined wide pickle on disk, the weekly connect output (the pipeline
    input every blending script reads), and the dissemination cells CSV.
    Returns (work_dir, combined, weekly, pipeline_input_path, dissemination_path)."""
    import pickle
    import sys

    import pandas as pd

    sys.path.insert(0, str(BLENDING_ROOT))
    from python.blending_process.connect_utils import make_cv_rds_from_daylevel

    if not model_names:
        raise ValueError("model_names must not be empty")

    work_dir = Path(tempfile.mkdtemp(prefix="blend-training-work-"))
    combined_path = work_dir / "combined_wide.pkl"
    pipeline_input_path = work_dir / "cv_data_clim_mok_date_new_pipeline.pkl"
    combined_path.write_bytes(combined_wide_pkl)

    with combined_path.open("rb") as f:
        combined = pickle.load(f)
    if not isinstance(combined, pd.DataFrame):
        combined = pd.DataFrame(combined)

    missing_model_cols = [
        f"{name}_onset_thresh"
        for name in model_names
        if f"{name}_onset_thresh" not in combined.columns
    ]
    if missing_model_cols:
        raise ValueError(
            "Combined wide file is missing model constant columns: " + ", ".join(missing_model_cols)
        )

    connect_spec = {
        "mode": cutoff_mode,
        "input_rds": str(combined_path),
        "output_rds": str(pipeline_input_path),
        "day_max": int(day_max),
        "days_per_week": int(days_per_week),
        "n_weeks": int(n_weeks),
        "climatology": {
            "base_prefix": "clim",
            "unconditional_prefix": "clim_unc",
            "window_tags": [],
        },
        "forecast_models": [
            {
                "name": name,
                "variants": ["clim_mok_date"],
                "rain_predictors": [{"agg": "diff", "window": int(rain_window)}],
            }
            for name in model_names
        ],
    }
    weekly = make_cv_rds_from_daylevel(connect_spec)

    dissemination_path = work_dir / "dissemination_cells.csv"
    pd.DataFrame({"adm3_name": sorted(weekly["id"].astype(str).unique())}).to_csv(
        dissemination_path,
        index=False,
    )
    return work_dir, combined, weekly, pipeline_input_path, dissemination_path


def _default_formula_text(model_names: list[str]) -> str:
    formula_terms = ["prob_clim_mr_qx"] + [f"diff_{name}_qx" for name in model_names]
    return "outcome ~ " + " * ".join(formula_terms)


def _build_blend_spec(
    model_names: list[str],
    training_years: list[int],
    cv_holdout_years: list[int],
    true_holdout_years: list[int] | None,
    cutoff_mode: str,
    formula_text: str,
    include_raw_forecasts: bool,
    include_calibrated_forecasts: bool,
    work_dir: Path,
    results_dir: Path,
    dissemination_path: Path,
) -> dict:
    forecast_extras = [
        {
            "name": name,
            "variant": "clim_mok_date",
            "raw": bool(include_raw_forecasts),
            "calibrated": bool(include_calibrated_forecasts),
            "fair_brier": False,
            "export_platt_weights": False,
        }
        for name in model_names
    ]
    return {
        "run": {
            "cutoff_mode": cutoff_mode,
            "MR": True,
            "training_years": [int(year) for year in training_years],
            "cv_holdout_years": [int(year) for year in cv_holdout_years],
            "true_holdout_years": [int(year) for year in (true_holdout_years or [])],
            "pipeline_input_dir": str(work_dir),
            "pipeline_output_dir": str(results_dir),
        },
        "cv": {"methods": ["global"]},
        "cell": {"dissemination": str(dissemination_path)},
        "models": {
            "formulas": {"blended_model": {"enabled": True, "text": formula_text}},
            "window_variants": {"enabled": False},
        },
        "mme": {"enabled": False, "variants": ["clim_mok_date"], "blend_models": []},
        "extras": {
            "clim_logits": [
                {
                    "name": "unc_clim_raw",
                    "base_col_prefix": "prob_clim_mr_unc",
                    "earlier_col": "prob_clim_mr_unc_earlier",
                    "earlier_is_logit": True,
                },
                {
                    "name": "clim_raw",
                    "base_col_prefix": "prob_clim_mr",
                    "window_start_years": [1900],
                    "window_end_year": max(training_years + cv_holdout_years),
                },
            ],
            "forecasts": forecast_extras,
            "forecast_variants": {"base": "", "clim_mok_date": "_clim_mok_date"},
        },
    }


@app.function(image=blending_image, cpu=4, memory=16384, timeout=3600)
def train_blending_model_bundle(
    combined_wide_pkl: bytes,
    model_names: list[str],
    training_years: list[int],
    cv_holdout_years: list[int],
    true_holdout_years: list[int] | None = None,
    cutoff_mode: str = "clim_mok_date",
    day_max: int = 28,
    days_per_week: int = 7,
    n_weeks: int = 4,
    rain_window: int = 3,
    formula_text: str | None = None,
    include_raw_forecasts: bool = True,
    include_calibrated_forecasts: bool = True,
    cores: int | None = None,
    return_outputs: bool = True,
) -> dict:
    """Train/evaluate weekly-bin blending models from a combined wide pickle."""
    import pickle
    import subprocess
    import sys
    import uuid

    import pandas as pd
    import yaml

    sys.path.insert(0, str(BLENDING_ROOT))
    from python.blending_process.blend_evaluation_utils import make_cutoff_tag

    if not training_years:
        raise ValueError("training_years must not be empty")
    if not cv_holdout_years:
        raise ValueError("cv_holdout_years must not be empty")

    work_dir, combined, weekly, pipeline_input_path, dissemination_path = _prepare_blend_workspace(
        combined_wide_pkl,
        model_names,
        cutoff_mode,
        day_max,
        days_per_week,
        n_weeks,
        rain_window,
    )
    results_dir = Path(tempfile.mkdtemp(prefix="blend-training-results-"))

    formula_text = formula_text or _default_formula_text(model_names)
    blend_spec = _build_blend_spec(
        model_names=model_names,
        training_years=training_years,
        cv_holdout_years=cv_holdout_years,
        true_holdout_years=true_holdout_years,
        cutoff_mode=cutoff_mode,
        formula_text=formula_text,
        include_raw_forecasts=include_raw_forecasts,
        include_calibrated_forecasts=include_calibrated_forecasts,
        work_dir=work_dir,
        results_dir=results_dir,
        dissemination_path=dissemination_path,
    )

    spec_id = f"almanac_training_{uuid.uuid4().hex}"
    spec_path = BLENDING_ROOT / "specs" / "2025_blend" / f"{spec_id}.yml"
    spec_path.write_text(yaml.dump(blend_spec, default_flow_style=False))
    try:
        command = [
            sys.executable,
            "python/pipelines/blending_process/1_blend_evaluation.py",
            "--spec_id",
            spec_id,
            "--work_dir",
            str(work_dir),
            "--results_dir",
            str(results_dir),
        ]
        if cores is not None:
            command.extend(["--cores", str(cores)])
        completed = subprocess.run(
            command,
            cwd=str(BLENDING_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        # Fit the production model on all training years (no holdout) so the
        # coef bundle (FINAL_COEF_FILENAME) ships with the training outputs —
        # live forecasts apply it directly instead of retraining (see
        # apply_blend_coefs_bundle / score_live_forecast).
        final_fit = None
        if completed.returncode == 0:
            final_fit = subprocess.run(
                [
                    sys.executable,
                    "predict/3_fit_final_model.py",
                    "--spec_id",
                    spec_id,
                    "--model",
                    "blended_model",
                    "--dissem_file",
                    str(work_dir / "dissemination_cells.csv"),
                    "--out_dir",
                    str(results_dir),
                ],
                cwd=str(BLENDING_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
    finally:
        with suppress(FileNotFoundError):
            spec_path.unlink()

    output_tag = f"{make_cutoff_tag(cutoff_mode)}"
    holdouts = sorted(set(int(year) for year in cv_holdout_years + (true_holdout_years or [])))
    if holdouts:
        output_tag += f"_{holdouts[0]}" if len(holdouts) == 1 else f"_{holdouts[0]}_{holdouts[-1]}"

    result_files = sorted(path.name for path in results_dir.iterdir() if path.is_file())
    summary_csv = results_dir / f"summary_models_pooled{output_tag}.csv"
    summary_rows = []
    if summary_csv.exists():
        summary_rows = pd.read_csv(summary_csv).head(20).to_dict(orient="records")

    manifest = {
        "ok": completed.returncode == 0 and (final_fit is None or final_fit.returncode == 0),
        "returncode": int(completed.returncode),
        "final_fit_returncode": int(final_fit.returncode) if final_fit is not None else None,
        "model_names": model_names,
        "training_years": sorted(int(year) for year in training_years),
        "cv_holdout_years": sorted(int(year) for year in cv_holdout_years),
        "true_holdout_years": sorted(int(year) for year in (true_holdout_years or [])),
        "formula_text": formula_text,
        "combined": {
            "rows": int(len(combined)),
            "columns": int(len(combined.columns)),
            "years": sorted(int(year) for year in combined["year"].dropna().unique()),
        },
        "weekly": {
            "rows": int(len(weekly)),
            "columns": int(len(weekly.columns)),
            "years": sorted(int(year) for year in weekly["year"].dropna().unique()),
            "outcome_counts": weekly["outcome"].value_counts(dropna=False).to_dict(),
            "first_columns": list(weekly.columns[:80]),
        },
        "outputs": {
            path.name: {"bytes": path.stat().st_size}
            for path in sorted(results_dir.iterdir())
            if path.is_file()
        },
        "result_files": result_files,
        "summary_rows": summary_rows,
        "stdout_tail": (
            completed.stdout.splitlines() + (final_fit.stdout.splitlines() if final_fit else [])
        )[-80:],
        "stderr_tail": (
            completed.stderr.splitlines() + (final_fit.stderr.splitlines() if final_fit else [])
        )[-80:],
    }

    outputs_tar = None
    if return_outputs:
        output_dir = Path(tempfile.mkdtemp(prefix="blend-training-artifacts-"))
        weekly_path = output_dir / "weekly_training_input.pkl"
        with weekly_path.open("wb") as f:
            pickle.dump(weekly, f)
        (output_dir / "training_spec.yml").write_text(
            yaml.dump(blend_spec, default_flow_style=False)
        )
        for path in sorted(results_dir.iterdir()):
            if path.is_file():
                (output_dir / path.name).write_bytes(path.read_bytes())
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
        outputs_tar = _tar_directory(output_dir)

    return {"manifest": manifest, "outputs_tar": outputs_tar}


@app.function(image=blending_image, cpu=4, memory=16384, timeout=3600)
def apply_blend_coefs_bundle(
    combined_wide_pkl: bytes,
    coef_pkl: bytes,
    model_names: list[str],
    training_years: list[int],
    cv_holdout_years: list[int],
    live_year: int,
    cutoff_mode: str = "clim_mok_date",
    day_max: int = 28,
    days_per_week: int = 7,
    n_weeks: int = 4,
    rain_window: int = 3,
    formula_text: str | None = None,
) -> bytes:
    """Score one live season by applying a trained blend's saved coef bundle
    (the FINAL_COEF_FILENAME pickle written by train_blending_model_bundle's
    final fit) via predict/apply_blend_model.py — the fast path that skips CV
    retraining entirely. Returns the live season's scored rows as CSV bytes."""
    import subprocess
    import sys
    import uuid

    import yaml

    if not training_years:
        raise ValueError("training_years must not be empty")
    if not cv_holdout_years:
        raise ValueError("cv_holdout_years must not be empty")

    work_dir, _, weekly, _, dissemination_path = _prepare_blend_workspace(
        combined_wide_pkl,
        model_names,
        cutoff_mode,
        day_max,
        days_per_week,
        n_weeks,
        rain_window,
    )
    results_dir = Path(tempfile.mkdtemp(prefix="blend-apply-results-"))

    # Same feature-NaN filter 1_blend_evaluation.py applies before predicting,
    # so this path scores the same rows the retrain path would have.
    feature_cols = [
        column
        for column in weekly.columns
        if column.startswith(("prob_clim_mr", "diff_", "min_", "max_"))
    ]
    live_rows = weekly[weekly["year"] == int(live_year)].dropna(subset=feature_cols)
    if live_rows.empty:
        raise RuntimeError(f"No scoreable rows for live season {live_year}")
    live_input_path = work_dir / f"live_input_{int(live_year)}.pkl"
    live_rows.to_pickle(live_input_path)

    coef_dir = work_dir / "coefs"
    coef_dir.mkdir()
    (coef_dir / FINAL_COEF_FILENAME).write_bytes(coef_pkl)

    blend_spec = _build_blend_spec(
        model_names=model_names,
        training_years=training_years,
        cv_holdout_years=cv_holdout_years,
        true_holdout_years=[int(live_year)],
        cutoff_mode=cutoff_mode,
        formula_text=formula_text or _default_formula_text(model_names),
        include_raw_forecasts=True,
        include_calibrated_forecasts=True,
        work_dir=work_dir,
        results_dir=results_dir,
        dissemination_path=dissemination_path,
    )
    spec_id = f"almanac_apply_{uuid.uuid4().hex}"
    spec_path = BLENDING_ROOT / "specs" / "2025_blend" / f"{spec_id}.yml"
    spec_path.write_text(yaml.dump(blend_spec, default_flow_style=False))
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "predict/apply_blend_model.py",
                "--spec_id",
                spec_id,
                "--model",
                "blended_model",
                "--year",
                str(int(live_year)),
                "--coef_tag",
                "final",
                "--input_path",
                str(live_input_path),
                "--coef_dir",
                str(coef_dir),
                "--out_dir",
                str(results_dir),
                "--dissem_file",
                str(dissemination_path),
            ],
            cwd=str(BLENDING_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        with suppress(FileNotFoundError):
            spec_path.unlink()

    preds_csv = results_dir / f"blended_model_global_year{int(live_year)}_preds.csv"
    if completed.returncode != 0 or not preds_csv.exists():
        tail = "\n".join(completed.stderr.splitlines()[-40:])
        raise RuntimeError(
            f"apply_blend_model.py failed (returncode {completed.returncode}):\n{tail}"
        )
    return preds_csv.read_bytes()


def _merge_forecast_bundle(historical_bundle: bytes, live_bundle: bytes) -> bytes:
    """Combine a model's historical `{year}.nc` bundle with a freshly-generated
    live-season bundle into one bundle, so build_lat_lon_intermediates_bundle
    processes the live season's forecast file alongside historical years
    exactly like any other year — no special-casing in that function needed.
    """
    merged_dir = Path(tempfile.mkdtemp(prefix="blend-live-merge-"))
    for bundle in (historical_bundle, live_bundle):
        source_dir = _extract_bundle(bundle)
        for path in sorted(source_dir.glob("*.nc")):
            target = merged_dir / path.name
            if target.exists():
                raise ValueError(
                    f"Live forecast year collides with an existing historical file: {path.name}"
                )
            target.write_bytes(path.read_bytes())
    return _bundle_files(sorted(merged_dir.glob("*.nc")))


def _find_result_file(result_files: list[str], prefix: str) -> str:
    matches = [name for name in result_files if name.startswith(prefix)]
    if not matches:
        raise FileNotFoundError(
            f"No training result file starting with {prefix!r}; found: {result_files}"
        )
    if len(matches) > 1:
        raise ValueError(f"Ambiguous training result files for prefix {prefix!r}: {matches}")
    return matches[0]


@app.function(image=blending_image, cpu=(4, 8), memory=(16384, 32768), timeout=21600)
def score_live_forecast(
    obs_bundle: bytes,
    forecast_bundles: dict[str, bytes],
    model_names: list[str],
    blend_params: dict,
    live_year: int,
    coef_pkl: bytes | None = None,
    cache_dir: str | None = None,
) -> bytes:
    """Score a live/in-progress season against an already-trained blend, given
    already-staged bundles (no GCS or local-file knowledge here — that's the
    caller's job, mirroring how run_blend stages before calling
    build_lat_lon_intermediates_bundle/train_blending_model_bundle).

    When coef_pkl (the blend's saved FINAL_COEF_FILENAME bundle) is provided,
    the trained weights are applied directly via apply_blend_coefs_bundle and
    no retraining happens. Without it, this falls back to the original
    retrain-based scoring, which reuses build_lat_lon_intermediates_bundle and
    train_blending_model_bundle
    completely unchanged: the live season is just one more forecast year with
    no matching obs year, added to the blend's own true_holdout_years so it
    is excluded from every training fold but still gets its own scoring pass
    (see plan §"Key finding" — compute_cv_global already predicts holdout
    years using only their feature columns, never their outcome).

    forecast_bundles: one tar.gz bundle per blend model name, each already
    merged (historical `{year}.nc` files + the live season's file) by the
    caller via _merge_forecast_bundle.

    Returns the live season's scored rows as CSV bytes
    (blended_forecast_probabilities.csv content).
    """
    import pickle
    import time

    print("==> Building blending intermediates (including live season)")
    t0 = time.perf_counter()
    prep_kwargs = {
        k: blend_params[k]
        for k in ("threshold_mm", "cutoff_month_day", "mok_month_day")
        if blend_params.get(k) is not None
    }
    if blend_params.get("region_id"):
        prep_kwargs["region_id"] = blend_params["region_id"]
    intermediates = build_lat_lon_intermediates_bundle.local(
        obs_bundle, forecast_bundles, return_outputs=True, cache_dir=cache_dir, **prep_kwargs
    )
    print(f"==> Intermediates built in {time.perf_counter() - t0:.1f}s")
    combined = _read_tar_member_bytes(intermediates["outputs_tar"], "combined_wide.pkl")

    if coef_pkl is not None:
        print(f"==> Applying trained blend coefficients to live season {live_year}")
        t0 = time.perf_counter()
        csv_bytes = apply_blend_coefs_bundle.local(
            combined,
            coef_pkl,
            model_names,
            training_years=_parse_years(blend_params.get("training_years") or "") or [],
            cv_holdout_years=_parse_years(blend_params.get("cv_holdout_years") or "") or [],
            live_year=live_year,
            formula_text=blend_params.get("formula_text") or None,
        )
        if _should_use_adm3_domain(blend_params.get("region_id"), None):
            csv_bytes = _attach_adm3_centroids_to_csv(csv_bytes)
        print(f"==> Coef apply finished in {time.perf_counter() - t0:.1f}s")
        return csv_bytes

    train_kwargs = {}
    if blend_params.get("formula_text"):
        train_kwargs["formula_text"] = blend_params["formula_text"]
    true_holdout_years = _parse_years(blend_params.get("true_holdout_years") or "") or []
    if live_year not in true_holdout_years:
        true_holdout_years = sorted({*true_holdout_years, live_year})
    print(f"==> Scoring live season {live_year} against trained blend")
    t0 = time.perf_counter()
    training = train_blending_model_bundle.local(
        combined,
        model_names=model_names,
        training_years=_parse_years(blend_params.get("training_years") or "") or [],
        cv_holdout_years=_parse_years(blend_params.get("cv_holdout_years") or "") or [],
        true_holdout_years=true_holdout_years,
        return_outputs=True,
        **train_kwargs,
    )
    print(f"==> Scoring finished in {time.perf_counter() - t0:.1f}s")
    if not training["manifest"].get("ok"):
        _print_manifest_tails(training["manifest"], "Blend scoring pipeline")
        raise RuntimeError("Blend scoring pipeline failed; stderr tail printed above (see run.log)")

    result_files = training["manifest"]["result_files"]
    cv_preds_name = _find_result_file(result_files, "cv_preds_blended_model_global")
    cv_preds_bytes = _read_tar_member_bytes(training["outputs_tar"], cv_preds_name)
    cv_preds = pickle.loads(cv_preds_bytes)
    live_rows = cv_preds[cv_preds["year"] == live_year].copy()
    if live_rows.empty:
        raise RuntimeError(f"Blend scoring produced no rows for live season {live_year}")
    csv_bytes = live_rows.to_csv(index=False).encode("utf-8")
    if _should_use_adm3_domain(blend_params.get("region_id"), None):
        csv_bytes = _attach_adm3_centroids_to_csv(csv_bytes)
    return csv_bytes


@app.function(
    image=blending_image, cpu=(4, 8), memory=(16384, 32768), timeout=21600, secrets=[gcp_secret]
)
def score_live_forecast_bundle(
    job_id: str,
    blend_config: dict,
    live_forecast_bundles: dict[str, bytes],
    live_year: int,
    outputs_bucket: str,
) -> None:
    """GCS-staging wrapper around score_live_forecast, mirroring run_blend's
    relationship to build_lat_lon_intermediates_bundle/train_blending_model_bundle:
    this function only stages inputs from/publishes outputs to GCS; all the
    actual scoring logic lives in score_live_forecast so it's reusable from a
    local (non-GCS) execution path too.
    """
    import sys
    import traceback
    from contextlib import redirect_stderr, redirect_stdout

    from google.cloud import storage as gcs

    log_buffer = io.StringIO()
    client = None
    failure: Exception | None = None

    with (
        redirect_stdout(_LogTee(sys.stdout, log_buffer)),
        redirect_stderr(_LogTee(sys.stderr, log_buffer)),
    ):
        try:
            _write_gcp_credentials_from_secret()
            client = gcs.Client()

            params = blend_config.get("blend_params") or {}
            params = {**params, "region_id": blend_config.get("region_id")}
            model_names = blend_config["model_names"]
            model_files = blend_config["model_files"]
            missing = [name for name in model_names if name not in live_forecast_bundles]
            if missing:
                raise ValueError(f"No live forecast bundle provided for models: {missing}")

            stage_root = Path(tempfile.mkdtemp(prefix="blend-live-"))
            obs_local = stage_root / "obs"
            obs_local.mkdir()
            print(f"==> Staging obs from {blend_config['obs_dir']}")
            _stage_gcs_prefix(client, blend_config["obs_dir"], obs_local, "obs")
            obs_bundle = _bundle_files(sorted(obs_local.glob("*.nc")))

            forecast_bundles: dict[str, bytes] = {}
            for name in model_names:
                model_local = stage_root / f"fc_{name}"
                model_local.mkdir()
                uris = model_files[name]
                print(f"==> Staging historical forecast {name}: {len(uris)} files")
                _stage_uris(client, uris, model_local, f"forecast {name}")
                historical_bundle = _bundle_files(sorted(model_local.glob("*.nc")))
                forecast_bundles[name] = _merge_forecast_bundle(
                    historical_bundle, live_forecast_bundles[name]
                )

            coef_pkl = None
            blend_output_uri = blend_config.get("blend_output_uri")
            if blend_output_uri:
                bucket_name, prefix = _split_gcs_uri(blend_output_uri, "blend_output_uri")
                coef_blob = client.bucket(bucket_name).blob(
                    f"{prefix.rstrip('/')}/{FINAL_COEF_FILENAME}"
                )
                if coef_blob.exists():
                    print("==> Staging trained blend coefficients (skipping CV retrain)")
                    coef_pkl = coef_blob.download_as_bytes()
                else:
                    print(
                        "==> Blend outputs have no final coef bundle; "
                        "falling back to retrain-based scoring"
                    )

            cache_bucket = (blend_config.get("gcs_cache_bucket") or "").strip()
            csv_bytes = score_live_forecast.local(
                obs_bundle,
                forecast_bundles,
                model_names,
                params,
                live_year,
                coef_pkl=coef_pkl,
                cache_dir=f"gs://{cache_bucket}/blend-intermediates" if cache_bucket else None,
            )

            out_local = stage_root / "output"
            out_local.mkdir()
            (out_local / "blended_forecast_probabilities.csv").write_bytes(csv_bytes)
            _upload_output_dir_to_gcs(client, outputs_bucket, job_id, out_local)
            print("==> Done.")
        except Exception as exc:  # noqa: BLE001 — surfaced via run.log + raise
            failure = exc
            traceback.print_exc()
        finally:
            if client is not None:
                try:
                    _upload_run_log_to_gcs(client, outputs_bucket, job_id, log_buffer.getvalue())
                except Exception:
                    traceback.print_exc()

    if failure is not None:
        raise RuntimeError(
            f"Live forecast scoring for job {job_id} failed; see run.log for details: {failure}"
        ) from failure


@app.local_entrypoint()
def inspect_local_netcdfs(
    input_dir: str = str(DEFAULT_LOCAL_DATA_DIR / "ethiopia" / "aifs"),
    year: int | None = 2024,
    max_files: int = 1,
) -> None:
    """Upload local NetCDF files to Modal and print their remote metadata."""
    files = _candidate_files(Path(input_dir).expanduser(), year, max_files)
    print("Uploading files:")
    for path in files:
        print(f"  {path}")
    result = inspect_netcdf_bundle.remote(_bundle_files(files))
    print(result)


@app.local_entrypoint()
def probe_blending_forecast_reader(
    input_dir: str = str(DEFAULT_LOCAL_DATA_DIR / "ethiopia" / "aifs"),
    year: int | None = 2024,
    max_files: int = 1,
    value_col: str = "tp",
    min_day: int = 1,
    max_day: int = 45,
) -> None:
    """Upload local NetCDF files and test the blending forecast reader."""
    files = _candidate_files(Path(input_dir).expanduser(), year, max_files)
    print("Uploading files:")
    for path in files:
        print(f"  {path}")
    result = probe_forecast_reader_bundle.remote(
        _bundle_files(files),
        value_col=value_col,
        min_day=min_day,
        max_day=max_day,
    )
    print(result)


@app.local_entrypoint()
def probe_lat_lon_onset_processing(
    input_dir: str = str(DEFAULT_LOCAL_DATA_DIR / "ethiopia" / "aifs"),
    year: int | None = 2024,
    max_files: int = 1,
    value_col: str = "tp",
    min_day: int = 1,
    max_day: int = 45,
    threshold_mm: float = 20.0,
    id_precision: int = 2,
    row_limit: int = 5000,
    mok_month_day: str | None = "06-01",
    sample_row_count: int = 0,
) -> None:
    """Upload local NetCDF files and test lat/lon id onset processing."""
    files = _candidate_files(Path(input_dir).expanduser(), year, max_files)
    print("Uploading files:")
    for path in files:
        print(f"  {path}")
    result = probe_lat_lon_onset_bundle.remote(
        _bundle_files(files),
        value_col=value_col,
        min_day=min_day,
        max_day=max_day,
        threshold_mm=threshold_mm,
        id_precision=id_precision,
        row_limit=row_limit,
        mok_month_day=mok_month_day,
        sample_row_count=sample_row_count,
    )
    print(result)


@app.local_entrypoint()
def probe_lat_lon_ground_truth_processing(
    input_dir: str = str(DEFAULT_LOCAL_DATA_DIR / "ethiopia" / "obs"),
    year: int | None = 2024,
    max_files: int = 1,
    value_col: str = "RAINFALL",
    threshold_mm: float = 20.0,
    id_precision: int = 2,
    cutoff_month_day: str = "05-01",
    mok_month_day: str | None = "06-01",
    row_limit: int = 0,
    sample_row_count: int = 0,
) -> None:
    """Upload local obs NetCDF files and test lat/lon ground-truth processing."""
    files = _candidate_files(Path(input_dir).expanduser(), year, max_files)
    print("Uploading files:")
    for path in files:
        print(f"  {path}")
    result = probe_lat_lon_ground_truth_bundle.remote(
        _bundle_files(files),
        value_col=value_col,
        threshold_mm=threshold_mm,
        id_precision=id_precision,
        cutoff_month_day=cutoff_month_day,
        mok_month_day=mok_month_day,
        row_limit=row_limit,
        sample_row_count=sample_row_count,
    )
    print(result)


@app.local_entrypoint()
def build_lat_lon_intermediates(
    obs_dir: str = str(DEFAULT_LOCAL_DATA_DIR / "ethiopia" / "obs"),
    forecast_inputs: str = ("aifs=" + str(DEFAULT_LOCAL_DATA_DIR / "ethiopia" / "aifs")),
    years: str = "2024",
    obs_years: str = "",
    forecast_years: str = "",
    max_files: int = 0,
    obs_value_col: str = "RAINFALL",
    forecast_value_col: str = "tp",
    min_day: int = 1,
    max_day: int = 45,
    threshold_mm: float = 20.0,
    id_precision: int = 2,
    cutoff_month_day: str = "05-01",
    mok_month_day: str | None = "06-01",
    include_long: bool = False,
    build_climatology: bool = True,
    build_combined: bool = True,
    climatology_train_year_min: int | None = None,
    climatology_train_year_max: int | None = None,
    climatology_test_year_min: int | None = None,
    climatology_test_year_max: int | None = None,
    min_onset_years: int = 10,
    forecast_window: int = 45,
    issue_end_month_day: str = "07-31",
    combine_join: str = "inner",
    trim_forecasts_after_true_onset: bool = True,
    output_dir: str = "./job_outputs/blending_intermediates",
) -> None:
    """Build intermediate pickle files and write returned artifacts locally."""
    selected_years = _parse_years(years)
    selected_obs_years = _parse_years(obs_years) or selected_years
    selected_forecast_years = _parse_years(forecast_years) or selected_years
    obs_files = _candidate_files_for_years(
        Path(obs_dir).expanduser(), selected_obs_years, max_files
    )
    forecast_dirs = _parse_forecast_inputs(forecast_inputs)
    forecast_files = {
        model_name: _candidate_files_for_years(path, selected_forecast_years, max_files)
        for model_name, path in forecast_dirs.items()
    }

    print("Uploading obs files:")
    for path in obs_files:
        print(f"  {path}")
    print("Uploading forecast files:")
    for model_name, files in forecast_files.items():
        print(f"  {model_name}:")
        for path in files:
            print(f"    {path}")

    result = build_lat_lon_intermediates_bundle.remote(
        _bundle_files(obs_files),
        {model_name: _bundle_files(files) for model_name, files in forecast_files.items()},
        obs_value_col=obs_value_col,
        forecast_value_col=forecast_value_col,
        min_day=min_day,
        max_day=max_day,
        threshold_mm=threshold_mm,
        id_precision=id_precision,
        cutoff_month_day=cutoff_month_day,
        mok_month_day=mok_month_day,
        include_long=include_long,
        build_climatology=build_climatology,
        build_combined=build_combined,
        climatology_train_year_min=climatology_train_year_min,
        climatology_train_year_max=climatology_train_year_max,
        climatology_test_year_min=climatology_test_year_min,
        climatology_test_year_max=climatology_test_year_max,
        min_onset_years=min_onset_years,
        forecast_window=forecast_window,
        issue_end_month_day=issue_end_month_day,
        combine_join=combine_join,
        trim_forecasts_after_true_onset=trim_forecasts_after_true_onset,
        return_outputs=True,
    )

    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    tar_bytes = result.get("outputs_tar")
    if tar_bytes:
        tar_path = out_dir / "intermediates.tar.gz"
        tar_path.write_bytes(tar_bytes)
        with tarfile.open(tar_path, mode="r:gz") as tar:
            tar.extractall(out_dir)
        print(f"Wrote artifacts to {out_dir}")
    print(json.dumps(result["manifest"], indent=2, default=str))


@app.local_entrypoint()
def train_blending_model(
    combined_wide_path: str = "./job_outputs/blending_full/combined_wide.pkl",
    model_names: str = "gencast,aifs",
    training_years: str = "2019:2024",
    cv_holdout_years: str = "2019:2024",
    true_holdout_years: str = "",
    day_max: int = 28,
    days_per_week: int = 7,
    n_weeks: int = 4,
    rain_window: int = 3,
    formula_text: str | None = None,
    include_raw_forecasts: bool = True,
    include_calibrated_forecasts: bool = True,
    cores: int | None = None,
    output_dir: str = "./job_outputs/blending_training",
) -> None:
    """Train/evaluate a blending model from a local combined_wide pickle."""
    path = Path(combined_wide_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"combined_wide_path is not a file: {path}")

    result = train_blending_model_bundle.remote(
        path.read_bytes(),
        model_names=_parse_model_names(model_names),
        training_years=_parse_years(training_years) or [],
        cv_holdout_years=_parse_years(cv_holdout_years) or [],
        true_holdout_years=_parse_years(true_holdout_years) or [],
        day_max=day_max,
        days_per_week=days_per_week,
        n_weeks=n_weeks,
        rain_window=rain_window,
        formula_text=formula_text,
        include_raw_forecasts=include_raw_forecasts,
        include_calibrated_forecasts=include_calibrated_forecasts,
        cores=cores,
        return_outputs=True,
    )

    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    tar_bytes = result.get("outputs_tar")
    if tar_bytes:
        tar_path = out_dir / "training_outputs.tar.gz"
        tar_path.write_bytes(tar_bytes)
        with tarfile.open(tar_path, mode="r:gz") as tar:
            tar.extractall(out_dir)
        print(f"Wrote training artifacts to {out_dir}")
    print(json.dumps(result["manifest"], indent=2, default=str))


@app.local_entrypoint()
def train_blending_model_from_artifacts(
    artifacts_tar_path: str = "./job_outputs/blending_full/intermediates.tar.gz",
    model_names: str = "gencast,aifs",
    training_years: str = "2019:2024",
    cv_holdout_years: str = "2019:2024",
    true_holdout_years: str = "",
    combined_member_name: str = "combined_wide.pkl",
    day_max: int = 28,
    days_per_week: int = 7,
    n_weeks: int = 4,
    rain_window: int = 3,
    formula_text: str | None = None,
    include_raw_forecasts: bool = True,
    include_calibrated_forecasts: bool = True,
    cores: int | None = None,
    output_dir: str = "./job_outputs/blending_training",
) -> None:
    """Train/evaluate a blending model from a prep artifact tarball."""
    path = Path(artifacts_tar_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"artifacts_tar_path is not a file: {path}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="blend-training-local-"))
    combined_path = tmp_dir / combined_member_name
    _copy_tar_member(path.read_bytes(), combined_member_name, combined_path)

    result = train_blending_model_bundle.remote(
        combined_path.read_bytes(),
        model_names=_parse_model_names(model_names),
        training_years=_parse_years(training_years) or [],
        cv_holdout_years=_parse_years(cv_holdout_years) or [],
        true_holdout_years=_parse_years(true_holdout_years) or [],
        day_max=day_max,
        days_per_week=days_per_week,
        n_weeks=n_weeks,
        rain_window=rain_window,
        formula_text=formula_text,
        include_raw_forecasts=include_raw_forecasts,
        include_calibrated_forecasts=include_calibrated_forecasts,
        cores=cores,
        return_outputs=True,
    )

    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    tar_bytes = result.get("outputs_tar")
    if tar_bytes:
        tar_path = out_dir / "training_outputs.tar.gz"
        tar_path.write_bytes(tar_bytes)
        with tarfile.open(tar_path, mode="r:gz") as tar:
            tar.extractall(out_dir)
        print(f"Wrote training artifacts to {out_dir}")
    print(json.dumps(result["manifest"], indent=2, default=str))
