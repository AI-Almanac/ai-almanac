"""Artifact storage boundary.

Defines how a job's workspace is created and how its outputs are published,
opened, and deleted, without exposing filesystem assumptions to routers or
runners. `storage_key` is opaque: callers persist and pass it back, but never
interpret it as a path.

The filesystem implementation wraps `LocalStorage`. v1 ships filesystem-only;
an S3-backed store can implement the same Protocol later without changing the
public contract. Publication here computes artifact records (hash, size, media
type) but does not write database rows — indexing and download authorization
are wired into the job lifecycle separately.
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from ai_almanac.server.services.storage import LocalStorage, get_storage

_READ_CHUNK = 1 << 20  # 1 MiB
_MEDIA_TYPE_OVERRIDES = {".nc": "application/x-netcdf"}
_DEFAULT_MEDIA_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class JobArtifact:
    """A single published output of a job. Mirrors the `job_artifacts` row."""

    id: str
    job_id: str
    kind: str  # output | figure | log
    filename: str
    media_type: str
    size_bytes: int
    checksum: str  # sha256 hex
    storage_key: str  # opaque
    created_at: str


class ArtifactStore(Protocol):
    def create_workspace(self, job_id: str) -> Path: ...

    def publish(self, job_id: str, workspace: Path) -> list[JobArtifact]: ...

    def open(self, artifact: JobArtifact) -> BinaryIO: ...

    def delete_job(self, job_id: str) -> None: ...


def _media_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in _MEDIA_TYPE_OVERRIDES:
        return _MEDIA_TYPE_OVERRIDES[suffix]
    return mimetypes.guess_type(filename)[0] or _DEFAULT_MEDIA_TYPE


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class FilesystemArtifactStore:
    """Artifact store rooted in the local job outputs directory."""

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def create_workspace(self, job_id: str) -> Path:
        # Ensures output/ and figure/ exist and returns the job root.
        self._storage.job_output_uri(job_id)
        return self._storage.job_dir(job_id)

    def publish(self, job_id: str, workspace: Path) -> list[JobArtifact]:
        """Index every output/figure file in the workspace into artifact
        records. Pure: computes records, performs no database writes."""
        now = datetime.now(UTC).isoformat()
        artifacts: list[JobArtifact] = []
        for kind, filename in self._storage.list_result_files(job_id):
            path = self._storage.result_file_path(job_id, kind, filename)
            if path is None or not path.is_file():
                continue
            checksum, size = _sha256(path)
            artifacts.append(
                JobArtifact(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    kind=kind,
                    filename=filename,
                    media_type=_media_type(filename),
                    size_bytes=size,
                    checksum=checksum,
                    storage_key=f"{job_id}/{kind}/{filename}",
                    created_at=now,
                )
            )
        return artifacts

    def open(self, artifact: JobArtifact) -> BinaryIO:
        path = self._storage.result_file_path(
            artifact.job_id, artifact.kind, artifact.filename
        )
        if path is None or not path.is_file():
            raise FileNotFoundError(artifact.storage_key)
        return path.open("rb")

    def delete_job(self, job_id: str) -> None:
        job_dir = self._storage.job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)


def get_artifact_store() -> FilesystemArtifactStore:
    # Thin wrapper over the (separately cached) storage singleton; rebuilt each
    # call so an `output_dir` change is always reflected.
    return FilesystemArtifactStore(get_storage())
