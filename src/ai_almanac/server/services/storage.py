"""Storage service — local filesystem.

Job outputs, chat figures, and run logs live under `$AI_ALMANAC_DATA_DIR/`.
Cloud deployments mount GCS buckets at these paths via Cloud Run GCS FUSE
volumes so the same code runs identically on both.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from ai_almanac.paths import uploads_dir

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
        f"chat-figures/{key}{ext}" for ext in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bin")
    ]


class LocalStorage:
    """Local filesystem storage rooted under the ai-almanac data dir."""

    is_local: bool = True

    def __init__(self, upload_dir: Path, job_outputs_dir: Path):
        self._upload_dir = Path(upload_dir).resolve()
        self._outputs_dir = Path(job_outputs_dir).resolve()
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._outputs_dir.mkdir(parents=True, exist_ok=True)

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
        if kind not in {"output", "figure"}:
            return None
        try:
            return self._contained(self._outputs_dir, f"{job_id}/{kind}/{filename}")
        except ValueError:
            return None

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
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        results.append((kind, f.relative_to(d).as_posix()))
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

    def delete_job(self, job_id: str) -> None:
        job_dir = self._outputs_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

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
# Factory
# ---------------------------------------------------------------------------

StorageBackend = LocalStorage

_instance: LocalStorage | None = None
_instance_key: tuple | None = None


def get_storage() -> LocalStorage:
    """Return the process-wide storage instance.

    Rebuilds when output_dir changes so the Settings UI and env overrides take
    effect on the next call.
    """
    global _instance, _instance_key
    from ai_almanac.settings import settings

    desired = settings.job_outputs_dir  # honors the user-configurable `output_dir`
    key = ("local", desired)
    if _instance is None or _instance_key != key:
        _instance = LocalStorage(
            upload_dir=uploads_dir(),
            job_outputs_dir=Path(desired),
        )
        _instance_key = key
    return _instance
