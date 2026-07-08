"""Storage service — local filesystem or Google Cloud Storage.

Selected via the `storage_backend` setting (`STORAGE_BACKEND`):
  local  — artifacts live under `$AI_ALMANAC_DATA_DIR/` (default; resolved by
           `ai_almanac.paths`). `is_local` is True.
  gcs    — artifacts live in GCS buckets; result files and chat figures are
           served to the browser via short-lived signed URLs. `is_local` is
           False, which the routers use to redirect instead of streaming.

Both backends expose the same method surface so routers stay backend-agnostic.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

from ai_almanac.paths import datasets_dir, uploads_dir

# Probed in priority order when a chat figure's extension is unknown.
_CHAT_FIGURE_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bin")

# HDF5/NetCDF4 is not thread-safe. Serialize all dataset opens with this lock.
_nc_lock = threading.Lock()

_CHAT_FIGURE_FORMATS: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"RIFF", ".webp", "image/webp"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)


def detect_chat_figure_format(data: bytes) -> tuple[str, str]:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    for magic, ext, content_type in _CHAT_FIGURE_FORMATS:
        if data.startswith(magic):
            return ext, content_type
    return ".bin", "application/octet-stream"


def guess_chat_figure_media_type(path: Path) -> str:
    try:
        return detect_chat_figure_format(path.read_bytes())[1]
    except Exception:
        if path.suffix == ".png":
            return "image/png"
        if path.suffix in (".jpg", ".jpeg"):
            return "image/jpeg"
        if path.suffix == ".gif":
            return "image/gif"
        return "image/webp"


def _chat_figure_candidates(base: Path, figure_id: str) -> list[Path]:
    return [
        base / "chat-figures" / f"{figure_id}{ext}"
        for ext in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bin")
    ]


def _chat_figure_storage_keys(storage_key: str) -> list[str]:
    key = Path(storage_key).name
    if Path(key).suffix:
        return [f"chat-figures/{key}"]
    return [
        f"chat-figures/{key}{ext}"
        for ext in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bin")
    ]


class LocalStorage:
    """Local filesystem storage rooted under the ai-almanac data dir."""

    is_local: bool = True

    def __init__(self, upload_dir: Path, job_outputs_dir: Path, datasets_dir: Path):
        self._upload_dir = Path(upload_dir).resolve()
        self._outputs_dir = Path(job_outputs_dir).resolve()
        self._datasets_dir = Path(datasets_dir).resolve()
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        self._datasets_dir.mkdir(parents=True, exist_ok=True)

    def list_dataset_tree(self) -> list[str]:
        """Posix relpaths of every file under the dataset root (the catalog walk)."""
        root = self._datasets_dir
        return sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file()
        )

    def read_dataset_text(self, relkey: str) -> str | None:
        """Read a text file (e.g. a manifest) under the dataset root, or None."""
        try:
            path = self._contained(self._datasets_dir, relkey)
        except ValueError:
            return None
        return path.read_text() if path.is_file() else None

    def dataset_uri(self, prefix: str) -> str:
        """Absolute path of a dataset dir (``{kind}/{region}/{id}``) for staging."""
        return str(self._contained(self._datasets_dir, prefix))

    def resolve_obs_path(self, storage_key: str) -> str:
        key = Path(storage_key)
        if key.is_absolute():
            return str(key)
        return str(self._contained(self._upload_dir, storage_key).parent)

    def job_dir(self, job_id: str) -> Path:
        """Root directory holding a job's workspace (output/, figure/, run.log)."""
        return self._contained(self._outputs_dir, job_id)

    def job_output_uri(self, job_id: str) -> tuple[str, str]:
        output = self._outputs_dir / job_id / "output"
        figure = self._outputs_dir / job_id / "figure"
        output.mkdir(parents=True, exist_ok=True)
        figure.mkdir(parents=True, exist_ok=True)
        return str(output), str(figure)

    def generate_result_url(self, job_id: str, kind: str, filename: str) -> str:
        return f"/jobs/{job_id}/results/{kind}/{filename}"

    def result_file_path(self, job_id: str, kind: str, filename: str) -> Path | None:
        if kind not in {"output", "figure"} or Path(filename).name != filename:
            return None
        return self._contained(self._outputs_dir, f"{job_id}/{kind}/{filename}")

    def read_result_text(self, job_id: str, kind: str, filename: str) -> str | None:
        """Read a small text result file (e.g. a summary CSV), or None if absent."""
        path = self.result_file_path(job_id, kind, filename)
        if path is None or not path.is_file():
            return None
        return path.read_text()

    def result_file_uri(self, job_id: str, kind: str, filename: str) -> str:
        """Raw local path for range-read consumers (TiTiler/rio-tiler), as
        opposed to result_file_path (single-level names only) / open_result_stream
        (proxied bytes). Unlike result_file_path, filename may contain
        subdirectories (e.g. a forecast's ``{model_id}/rasters/{var}/{lead}.tif``)
        — safety against path traversal still comes from ``_contained``.
        """
        if kind not in {"output", "figure"}:
            raise ValueError(f"Unknown result kind: {kind!r}")
        return str(self._contained(self._outputs_dir, f"{job_id}/{kind}/{filename}"))

    @staticmethod
    def _contained(root: Path, relative: str) -> Path:
        path = (root / relative).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError("storage key escapes its configured root")
        return path

    def list_result_files(self, job_id: str) -> list[tuple[str, str]]:
        results = []
        job_dir = self._outputs_dir / job_id
        for kind in ("output", "figure"):
            d = job_dir / kind
            if d.exists():
                for f in sorted(d.iterdir()):
                    if f.is_file():
                        results.append((kind, f.name))
        return results

    def list_nc_output_files(self, job_id: str) -> list:
        output_dir = self._outputs_dir / job_id / "output"
        if not output_dir.exists():
            return []
        return sorted(
            output_dir.glob("spatial_metrics_*.nc"),
            key=lambda p: p.name,
        ) + sorted(
            output_dir.glob("e2s_spatial_metrics_*.nc"),
            key=lambda p: p.name,
        )

    def find_nc_output_file(self, job_id: str, model: str, window: str) -> str | None:
        output_dir = self._outputs_dir / job_id / "output"
        for prefix in ("spatial_metrics", "e2s_spatial_metrics"):
            for w in (window, window.replace("-", ",")):
                matches = list(output_dir.glob(f"{prefix}_{model}_{w}.nc"))
                if matches:
                    return str(matches[0])
        return None

    def open_nc_dataset(self, path):
        import xarray as xr

        with _nc_lock:
            return xr.load_dataset(path)

    def save_chat_figure(self, figure_id: str, data: bytes) -> None:
        ext, _ = detect_chat_figure_format(data)
        path = self._outputs_dir / "chat-figures" / f"{figure_id}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def chat_figure_local_path(self, figure_id: str) -> Path | None:
        for candidate in _chat_figure_candidates(self._outputs_dir, figure_id):
            if candidate.exists():
                return candidate
        return self._outputs_dir / "chat-figures" / f"{figure_id}.webp"

    def chat_figure_redirect_url(self, figure_id: str) -> str | None:
        return None

    def read_chat_figure(self, figure_id: str) -> tuple[bytes, str] | None:
        path = self.chat_figure_local_path(figure_id)
        if path is None or not path.exists():
            return None
        return path.read_bytes(), guess_chat_figure_media_type(path)

    def delete_chat_figure(self, storage_key: str) -> None:
        for candidate_key in _chat_figure_storage_keys(storage_key):
            path = self._outputs_dir / candidate_key
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    def log_path(self, job_id: str) -> Path:
        p = self._outputs_dir / job_id / "run.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def read_log(self, job_id: str) -> str:
        p = self._outputs_dir / job_id / "run.log"
        return p.read_text() if p.exists() else ""


# ---------------------------------------------------------------------------
# GCS implementation
# ---------------------------------------------------------------------------


class GCSStorage:
    """Storage backed by Google Cloud Storage buckets.

    Job outputs, chat figures, and run logs live in the outputs bucket; user
    uploads in the uploads bucket. Result files and chat figures are served to
    the browser via short-lived V4 signed URLs (the routers redirect there when
    `is_local` is False). Local-workspace methods (`job_dir`, `log_path`) do not
    apply: GCS deployments run jobs on a remote runner that writes results
    straight into the bucket.
    """

    is_local: bool = False
    _SIGNED_URL_EXPIRY = timedelta(minutes=15)

    def __init__(
        self,
        uploads_bucket: str,
        outputs_bucket: str,
        data_bucket: str,
        client=None,
    ) -> None:
        if client is None:
            from google.cloud import storage as gcs

            client = gcs.Client()
        self._client = client
        self._uploads_bucket = uploads_bucket
        self._outputs_bucket = outputs_bucket
        self._data_bucket = data_bucket
        self._signer_creds = None  # cloud-platform-scoped creds for IAM signing

    def _bucket(self, name: str):
        return self._client.bucket(name)

    def _signing_kwargs(self) -> dict:
        """Extra ``generate_signed_url`` args needed when the ambient credentials
        can't sign locally.

        On Cloud Run the credentials are compute-engine tokens with no private
        key, so V4 signing must go through the IAM ``signBlob`` API — which
        ``generate_signed_url`` does when handed the SA email and an access
        token. With a real SA key (local/dev) the credentials sign locally and
        these stay empty. Requires the runtime SA to hold
        ``roles/iam.serviceAccountTokenCreator`` on itself and the IAM Service
        Account Credentials API enabled.
        """
        from google.auth import credentials as ga_credentials

        if isinstance(self._client._credentials, ga_credentials.Signing):
            return {}
        # The storage client's token is devstorage-scoped; signBlob needs a
        # cloud-platform-scoped token, so sign with a separately-scoped cred.
        creds = self._signer_credentials()
        return {
            "service_account_email": creds.service_account_email,
            "access_token": creds.token,
        }

    def _signer_credentials(self):
        import google.auth
        from google.auth.transport import requests as ga_requests

        if self._signer_creds is None:
            self._signer_creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not self._signer_creds.valid:
            self._signer_creds.refresh(ga_requests.Request())
        return self._signer_creds

    @staticmethod
    def _fs():
        import gcsfs

        return gcsfs.GCSFileSystem()

    def resolve_obs_path(self, storage_key: str) -> str:
        if storage_key.startswith("gs://") or Path(storage_key).is_absolute():
            return storage_key
        prefix = "/".join(storage_key.split("/")[:-1])
        return f"gs://{self._uploads_bucket}/{prefix}"

    def job_dir(self, job_id: str) -> Path:
        raise NotImplementedError(
            "GCS storage has no local job workspace; jobs run on a remote runner"
        )

    def job_output_uri(self, job_id: str) -> tuple[str, str]:
        return (
            f"gs://{self._outputs_bucket}/{job_id}/output",
            f"gs://{self._outputs_bucket}/{job_id}/figure",
        )

    def generate_result_url(self, job_id: str, kind: str, filename: str) -> str:
        # Same backend path as local storage: the result-file route proxies the
        # bytes so the browser never reads the outputs bucket cross-origin.
        return f"/jobs/{job_id}/results/{kind}/{filename}"

    def result_file_path(self, job_id: str, kind: str, filename: str) -> Path | None:
        return None  # streamed via open_result_stream, not a local FileResponse

    def read_result_text(self, job_id: str, kind: str, filename: str) -> str | None:
        """Download a small text result object (e.g. a summary CSV), or None."""
        blob = self._bucket(self._outputs_bucket).blob(f"{job_id}/{kind}/{filename}")
        if not blob.exists():
            return None
        return blob.download_as_text()

    def result_file_uri(self, job_id: str, kind: str, filename: str) -> str:
        """Raw gs:// URI for range-read consumers (TiTiler/rio-tiler via GDAL's
        /vsigs/ driver with ambient credentials), as opposed to
        open_result_stream (proxied bytes) / generate_result_url (signed URLs
        for direct browser access).
        """
        if kind not in {"output", "figure"}:
            raise ValueError(f"Unknown result kind: {kind!r}")
        return f"gs://{self._outputs_bucket}/{job_id}/{kind}/{filename}"

    def open_result_stream(
        self, job_id: str, kind: str, filename: str
    ) -> tuple[Iterator[bytes], str, int] | None:
        """Stream a result object's bytes for the backend download proxy.

        Returns ``(chunk iterator, media type, size)`` or ``None`` if the object
        is absent, so the browser fetches result files from this origin instead
        of the outputs bucket directly.
        """
        blob = self._bucket(self._outputs_bucket).blob(f"{job_id}/{kind}/{filename}")
        if not blob.exists():
            return None
        blob.reload()

        def chunks() -> Iterator[bytes]:
            with blob.open("rb") as handle:
                while data := handle.read(1 << 20):
                    yield data

        return chunks(), (blob.content_type or "application/octet-stream"), int(blob.size or 0)

    def list_result_files(self, job_id: str) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for kind in ("output", "figure"):
            prefix = f"{job_id}/{kind}/"
            blobs = self._client.list_blobs(self._outputs_bucket, prefix=prefix)
            for blob in sorted(blobs, key=lambda b: b.name):
                filename = blob.name.removeprefix(prefix)
                if filename:
                    results.append((kind, filename))
        return results

    def stat_result_file(self, job_id: str, kind: str, filename: str) -> tuple[int, str]:
        """Return (size_bytes, content checksum) for a result object.

        The checksum is the object's GCS MD5 hash (no download required); local
        storage uses SHA-256, so artifact checksums differ by backend.
        """
        blob = self._bucket(self._outputs_bucket).blob(f"{job_id}/{kind}/{filename}")
        blob.reload()
        return int(blob.size or 0), (blob.md5_hash or "")

    def delete_job(self, job_id: str) -> None:
        for blob in self._client.list_blobs(self._outputs_bucket, prefix=f"{job_id}/"):
            blob.delete()

    def list_nc_output_files(self, job_id: str) -> list:
        fs = self._fs()
        base = f"{self._outputs_bucket}/{job_id}/output"
        romp = [f"gs://{f}" for f in sorted(fs.glob(f"{base}/spatial_metrics_*.nc"))]
        e2s = [f"gs://{f}" for f in sorted(fs.glob(f"{base}/e2s_spatial_metrics_*.nc"))]
        return romp + e2s

    def find_nc_output_file(self, job_id: str, model: str, window: str) -> str | None:
        fs = self._fs()
        base = f"{self._outputs_bucket}/{job_id}/output"
        for prefix in ("spatial_metrics", "e2s_spatial_metrics"):
            for w in (window, window.replace("-", ",")):
                matches = fs.glob(f"{base}/{prefix}_{model}_{w}.nc")
                if matches:
                    return f"gs://{matches[0]}"
        return None

    def list_dataset_files(self, path: str, glob: str) -> list[str]:
        """List object URIs under a `gs://` source path whose name matches `glob`.

        Used to validate a registered GCS data source the same way the local
        backend globs a directory.
        """
        fs = self._fs()
        base = str(path).removeprefix("gs://").rstrip("/")
        return [f"gs://{match}" for match in sorted(fs.glob(f"{base}/{glob}"))]

    def _datasets_base(self) -> str:
        return f"{self._data_bucket}/datasets"

    def list_dataset_tree(self) -> list[str]:
        """Posix relpaths of every object under the dataset prefix (the catalog walk)."""
        fs = self._fs()
        base = self._datasets_base()
        return sorted(
            match.removeprefix(f"{base}/")
            for match in fs.glob(f"{base}/**")
            if not fs.isdir(match)
        )

    def read_dataset_text(self, relkey: str) -> str | None:
        """Read a text object (e.g. a manifest) under the dataset prefix, or None."""
        fs = self._fs()
        key = f"{self._datasets_base()}/{relkey}"
        if not fs.exists(key):
            return None
        with fs.open(key, "rt") as handle:
            return handle.read()

    def dataset_uri(self, prefix: str) -> str:
        """``gs://`` URI of a dataset dir (``{kind}/{region}/{id}``) for staging."""
        return f"gs://{self._datasets_base()}/{prefix}"

    def open_nc_dataset(self, path):
        import xarray as xr

        fs = self._fs()
        with _nc_lock, fs.open(str(path).removeprefix("gs://"), "rb") as handle:
            return xr.load_dataset(handle, engine="h5netcdf")

    def save_chat_figure(self, figure_id: str, data: bytes) -> None:
        ext, content_type = detect_chat_figure_format(data)
        self._bucket(self._outputs_bucket).blob(
            f"chat-figures/{figure_id}{ext}"
        ).upload_from_string(data, content_type=content_type)

    def chat_figure_local_path(self, figure_id: str) -> Path | None:
        return None

    def chat_figure_redirect_url(self, figure_id: str) -> str | None:
        for ext in _CHAT_FIGURE_EXTS:
            blob = self._bucket(self._outputs_bucket).blob(
                f"chat-figures/{figure_id}{ext}"
            )
            if blob.exists():
                return blob.generate_signed_url(
                    version="v4",
                    expiration=self._SIGNED_URL_EXPIRY,
                    method="GET",
                    **self._signing_kwargs(),
                )
        return None

    def read_chat_figure(self, figure_id: str) -> tuple[bytes, str] | None:
        for ext in _CHAT_FIGURE_EXTS:
            blob = self._bucket(self._outputs_bucket).blob(
                f"chat-figures/{figure_id}{ext}"
            )
            if blob.exists():
                data = blob.download_as_bytes()
                return data, (blob.content_type or detect_chat_figure_format(data)[1])
        return None

    def delete_chat_figure(self, storage_key: str) -> None:
        for candidate_key in _chat_figure_storage_keys(storage_key):
            try:
                self._bucket(self._outputs_bucket).blob(candidate_key).delete()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                continue

    def log_path(self, job_id: str) -> Path:
        raise NotImplementedError(
            "GCS storage has no local log file; read_log fetches it from the bucket"
        )

    def read_log(self, job_id: str) -> str:
        blob = self._bucket(self._outputs_bucket).blob(f"{job_id}/run.log")
        return blob.download_as_text() if blob.exists() else ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

StorageBackend = LocalStorage | GCSStorage

_instance: StorageBackend | None = None
_instance_key: tuple | None = None


def get_storage() -> StorageBackend:
    """Return the process-wide storage instance.

    Rebuilds when the relevant settings change — the local `output_dir`, or the
    GCS backend/bucket selection — so the Settings UI and env overrides take
    effect on the next call.
    """
    global _instance, _instance_key
    from ai_almanac.settings import settings

    if settings.storage_backend.lower() == "gcs":
        key = (
            "gcs",
            settings.gcs_uploads_bucket,
            settings.gcs_outputs_bucket,
            settings.gcs_data_bucket,
        )
        if _instance is None or _instance_key != key:
            _instance = GCSStorage(
                uploads_bucket=settings.gcs_uploads_bucket,
                outputs_bucket=settings.gcs_outputs_bucket,
                data_bucket=settings.gcs_data_bucket,
            )
            _instance_key = key
        return _instance

    desired = settings.job_outputs_dir  # honors the user-configurable `output_dir`
    key = ("local", desired)
    if _instance is None or _instance_key != key:
        _instance = LocalStorage(
            upload_dir=uploads_dir(),
            job_outputs_dir=Path(desired),
            datasets_dir=datasets_dir(),
        )
        _instance_key = key
    return _instance
