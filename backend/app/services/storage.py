"""
Storage service — local filesystem vs GCS.

Selected via STORAGE_BACKEND env var:
  local  — files live on disk under upload_dir / job_outputs_dir (default for dev)
  gcs    — files live in GCS; upload/download URLs are signed GCS URLs

Both implementations share the same interface so routers don't need to care.
"""

from __future__ import annotations

import datetime
import threading
from abc import ABC, abstractmethod
from pathlib import Path

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


class StorageBackend(ABC):
    @abstractmethod
    def generate_upload_url(
        self,
        storage_key: str,
        base_url: str,
    ) -> str:
        """Return a URL the client can PUT a file to."""

    @abstractmethod
    def confirm_upload(self, storage_key: str) -> bool:
        """Return True if the object exists (upload completed)."""

    @abstractmethod
    def resolve_obs_path(self, storage_key: str) -> str:
        """
        Resolve a dataset storage_key to a path the ROMP container can read.
        Local: absolute filesystem path.
        GCS: gs://bucket/key (Cloud Batch FUSE-mounts it).
        """

    @abstractmethod
    def job_output_uri(self, job_id: str) -> tuple[str, str]:
        """
        Return (output_uri, figure_uri) where ROMP should write results.
        Local: absolute paths. GCS: gs:// URIs.
        """

    @abstractmethod
    def generate_result_url(self, job_id: str, kind: str, filename: str) -> str:
        """Return a URL to serve a result file to the browser."""

    @abstractmethod
    def result_file_path(self, job_id: str, kind: str, filename: str) -> Path | None:
        """
        Return a local Path to stream via FileResponse, or None if the file
        should be served via redirect (GCS signed URL).
        """

    @abstractmethod
    def list_result_files(self, job_id: str) -> list[tuple[str, str]]:
        """Return [(kind, filename), ...] for all result files for a job."""

    @abstractmethod
    def list_nc_output_files(self, job_id: str) -> list:
        """Return list of ROMP and Earth2Studio metric NetCDF paths/URIs for a job."""

    @abstractmethod
    def find_nc_output_file(self, job_id: str, model: str, window: str) -> str | None:
        """
        Return the path/URI for the model/window metric NetCDF, or None if not found.
        Tries both the original window string and a hyphen→comma variant.
        """

    @abstractmethod
    def open_nc_dataset(self, path):
        """Open a NetCDF path/URI and return an xarray Dataset."""

    @abstractmethod
    def save_chat_figure(self, figure_id: str, data: bytes) -> None:
        """Persist a chat figure by ID."""

    @abstractmethod
    def chat_figure_local_path(self, figure_id: str) -> Path | None:
        """Return a local Path to serve via FileResponse, or None for GCS."""

    @abstractmethod
    def chat_figure_redirect_url(self, figure_id: str) -> str | None:
        """Return a signed GCS URL to redirect to, or None for local storage."""

    @abstractmethod
    def read_chat_figure(self, figure_id: str) -> tuple[bytes, str] | None:
        """Return (data, media_type) for a chat figure, or None if not found."""

    @abstractmethod
    def delete_chat_figure(self, storage_key: str) -> None:
        """Best-effort deletion of a stored chat figure by storage key or figure ID."""

    @property
    def is_local(self) -> bool:
        return isinstance(self, LocalStorage)


# ---------------------------------------------------------------------------
# Local implementation
# ---------------------------------------------------------------------------


class LocalStorage(StorageBackend):
    def __init__(self, upload_dir: str, job_outputs_dir: str):
        self._upload_dir = Path(upload_dir).resolve()
        self._outputs_dir = Path(job_outputs_dir).resolve()

    def generate_upload_url(self, storage_key: str, base_url: str) -> str:
        return base_url.rstrip("/") + f"/upload/{storage_key}"

    def confirm_upload(self, storage_key: str) -> bool:
        return (self._upload_dir / storage_key).exists()

    def resolve_obs_path(self, storage_key: str) -> str:
        key = Path(storage_key)
        if key.is_absolute():
            return str(key)
        return str((self._upload_dir / storage_key).parent)

    def job_output_uri(self, job_id: str) -> tuple[str, str]:
        output = self._outputs_dir / job_id / "output"
        figure = self._outputs_dir / job_id / "figure"
        output.mkdir(parents=True, exist_ok=True)
        figure.mkdir(parents=True, exist_ok=True)
        return str(output), str(figure)

    def generate_result_url(self, job_id: str, kind: str, filename: str) -> str:
        return f"/jobs/{job_id}/results/{kind}/{filename}"

    def result_file_path(self, job_id: str, kind: str, filename: str) -> Path | None:
        return self._outputs_dir / job_id / kind / filename

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


class GCSStorage(StorageBackend):
    _SIGNED_URL_EXPIRY = datetime.timedelta(minutes=15)

    def __init__(self, uploads_bucket: str, outputs_bucket: str, data_bucket: str):
        from google.cloud import storage as gcs

        self._client = gcs.Client()
        self._uploads_bucket = uploads_bucket
        self._outputs_bucket = outputs_bucket
        self._data_bucket = data_bucket

    def _bucket(self, name: str):
        return self._client.bucket(name)

    def generate_upload_url(self, storage_key: str, base_url: str) -> str:
        blob = self._bucket(self._uploads_bucket).blob(storage_key)
        return blob.generate_signed_url(
            version="v4",
            expiration=self._SIGNED_URL_EXPIRY,
            method="PUT",
            content_type="application/octet-stream",
        )

    def confirm_upload(self, storage_key: str) -> bool:
        return self._bucket(self._uploads_bucket).blob(storage_key).exists()

    def resolve_obs_path(self, storage_key: str) -> str:
        # If already an absolute path (legacy from-path datasets), return as-is.
        if storage_key.startswith("gs://") or Path(storage_key).is_absolute():
            return storage_key
        # User upload: storage_key is "{user_id}/{dataset_id}/{filename}",
        # resolve to the parent prefix (the obs directory).
        prefix = "/".join(storage_key.split("/")[:-1])
        return f"gs://{self._uploads_bucket}/{prefix}"

    def job_output_uri(self, job_id: str) -> tuple[str, str]:
        return (
            f"gs://{self._outputs_bucket}/{job_id}/output",
            f"gs://{self._outputs_bucket}/{job_id}/figure",
        )

    def generate_result_url(self, job_id: str, kind: str, filename: str) -> str:
        blob = self._bucket(self._outputs_bucket).blob(f"{job_id}/{kind}/{filename}")
        return blob.generate_signed_url(
            version="v4",
            expiration=self._SIGNED_URL_EXPIRY,
            method="GET",
        )

    def result_file_path(self, job_id: str, kind: str, filename: str) -> Path | None:
        # GCS results are served via signed URL redirect, not local FileResponse.
        return None

    def list_result_files(self, job_id: str) -> list[tuple[str, str]]:
        results = []
        for kind in ("output", "figure"):
            prefix = f"{job_id}/{kind}/"
            blobs = self._client.list_blobs(self._outputs_bucket, prefix=prefix)
            for blob in sorted(blobs, key=lambda b: b.name):
                filename = blob.name.removeprefix(prefix)
                if filename:
                    results.append((kind, filename))
        return results

    def list_nc_output_files(self, job_id: str) -> list:
        import gcsfs

        fs = gcsfs.GCSFileSystem()
        base = f"{self._outputs_bucket}/{job_id}/output"
        romp = [f"gs://{f}" for f in sorted(fs.glob(f"{base}/spatial_metrics_*.nc"))]
        e2s = [f"gs://{f}" for f in sorted(fs.glob(f"{base}/e2s_spatial_metrics_*.nc"))]
        return romp + e2s

    def find_nc_output_file(self, job_id: str, model: str, window: str) -> str | None:
        import gcsfs

        fs = gcsfs.GCSFileSystem()
        base = f"{self._outputs_bucket}/{job_id}/output"
        for prefix in ("spatial_metrics", "e2s_spatial_metrics"):
            for w in (window, window.replace("-", ",")):
                matches = fs.glob(f"{base}/{prefix}_{model}_{w}.nc")
                if matches:
                    return f"gs://{matches[0]}"
        return None

    def open_nc_dataset(self, path):
        import xarray as xr
        import gcsfs

        fs = gcsfs.GCSFileSystem()
        with fs.open(str(path).removeprefix("gs://"), "rb") as f:
            return xr.load_dataset(f, engine="h5netcdf")

    def save_chat_figure(self, figure_id: str, data: bytes) -> None:
        ext, content_type = detect_chat_figure_format(data)
        blob = self._bucket(self._outputs_bucket).blob(f"chat-figures/{figure_id}{ext}")
        blob.upload_from_string(data, content_type=content_type)

    def chat_figure_local_path(self, figure_id: str) -> Path | None:
        return None

    def chat_figure_redirect_url(self, figure_id: str) -> str | None:
        for ext in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bin"):
            blob = self._bucket(self._outputs_bucket).blob(
                f"chat-figures/{figure_id}{ext}"
            )
            if blob.exists():
                return blob.generate_signed_url(
                    version="v4",
                    expiration=self._SIGNED_URL_EXPIRY,
                    method="GET",
                )
        return None

    def read_chat_figure(self, figure_id: str) -> tuple[bytes, str] | None:
        for ext in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bin"):
            blob = self._bucket(self._outputs_bucket).blob(
                f"chat-figures/{figure_id}{ext}"
            )
            if blob.exists():
                data = blob.download_as_bytes()
                media_type = blob.content_type or detect_chat_figure_format(data)[1]
                return data, media_type
        return None

    def delete_chat_figure(self, storage_key: str) -> None:
        for candidate_key in _chat_figure_storage_keys(storage_key):
            try:
                self._bucket(self._outputs_bucket).blob(candidate_key).delete()
            except Exception:
                continue

    def read_log(self, job_id: str) -> str:
        blob = self._bucket(self._outputs_bucket).blob(f"{job_id}/run.log")
        return blob.download_as_text() if blob.exists() else ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_instance: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _instance
    if _instance is None:
        _instance = _make_storage()
    return _instance


def _make_storage() -> StorageBackend:
    from ..config import settings

    backend = settings.storage_backend.lower()
    if backend == "gcs":
        return GCSStorage(
            uploads_bucket=settings.gcs_uploads_bucket,
            outputs_bucket=settings.gcs_outputs_bucket,
            data_bucket=settings.gcs_data_bucket,
        )
    # Default: local
    return LocalStorage(
        upload_dir=settings.upload_dir,
        job_outputs_dir=settings.job_outputs_dir,
    )
