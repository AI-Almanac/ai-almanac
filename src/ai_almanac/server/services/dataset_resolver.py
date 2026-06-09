"""Dataset resolution boundary.

Resolves a registered data source to a concrete location a runner can read,
enforcing that mounted sources stay within the configured allow-list of root
directories. This is the choke point that prevents a registered mounted path
from escaping the admin-configured dataset mounts (path traversal).

v1 is filesystem-only. A remote/object-store resolver can implement the same
Protocol later and stage inputs into the job workspace without changing the
contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_almanac.paths import uploads_dir
from ai_almanac.settings import settings


class DatasetAccessError(Exception):
    """A source could not be resolved or is outside the permitted roots."""


@dataclass(frozen=True)
class DataSource:
    """Parsed data-source row — trustworthy once constructed."""

    id: str
    kind: str  # obs | model
    path: str
    origin: str  # mounted | upload
    owner_id: str | None
    visibility: str  # private | shared

    @classmethod
    def from_row(cls, row: Mapping) -> DataSource:
        return cls(
            id=row["id"],
            kind=row["kind"],
            path=row["path"],
            origin=row.get("origin") or "mounted",
            owner_id=row.get("owner_id"),
            visibility=row.get("visibility") or "shared",
        )


@dataclass(frozen=True)
class ResolvedDataset:
    """A data source resolved to a concrete, readable location."""

    source_id: str
    kind: str
    path: Path


class DatasetResolver(Protocol):
    async def resolve(
        self, source: DataSource, workspace: Path
    ) -> ResolvedDataset: ...


def _mount_roots() -> list[Path]:
    return [
        Path(item.strip()).expanduser().resolve()
        for item in settings.dataset_mount_roots.split(",")
        if item.strip()
    ]


def _assert_within(path: Path, roots: list[Path]) -> None:
    if roots and not any(path == root or path.is_relative_to(root) for root in roots):
        raise DatasetAccessError(
            f"path {path} is outside the configured dataset mount roots"
        )


class FilesystemDatasetResolver:
    """Resolve sources to local paths, enforcing mount-root containment."""

    async def resolve(
        self, source: DataSource, workspace: Path
    ) -> ResolvedDataset:
        if source.origin == "upload":
            path = self._resolve_upload(source.path)
        else:
            path = self._resolve_mounted(source.path)
        return ResolvedDataset(source_id=source.id, kind=source.kind, path=path)

    def _resolve_mounted(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        _assert_within(path, _mount_roots())
        if not path.exists():
            raise DatasetAccessError(f"path does not exist: {path}")
        return path

    def _resolve_upload(self, raw_path: str) -> Path:
        root = uploads_dir().resolve()
        candidate = Path(raw_path).expanduser()
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        # Uploads are always contained within the uploads directory.
        _assert_within(path, [root])
        if not path.exists():
            raise DatasetAccessError(f"upload not found: {path}")
        return path


def get_dataset_resolver() -> FilesystemDatasetResolver:
    return FilesystemDatasetResolver()
