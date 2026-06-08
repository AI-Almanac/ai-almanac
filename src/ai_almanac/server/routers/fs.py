"""Filesystem browser endpoint — backs the UI's directory picker.

In a local install the server's filesystem is the user's filesystem, so this
endpoint is exactly what the UI needs to let users point at an obs/model
directory or pick a workflow output path. Read-only, no writes.

For public deployments (behind a reverse proxy) this exposes the host's
filesystem to anyone the proxy lets through, which is rarely desirable. Gate
via `enable_fs_browser` setting (default true for the local-first build).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_almanac.paths import data_root
from ai_almanac.settings import settings

router = APIRouter(prefix="/fs", tags=["fs"])


class FsEntry(BaseModel):
    name: str
    kind: Literal["file", "dir"]
    size: int | None
    is_hidden: bool


class FsListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FsEntry]


class QuickPath(BaseModel):
    label: str
    path: str


def _ensure_enabled() -> None:
    if not getattr(settings, "enable_fs_browser", True):
        raise HTTPException(
            status_code=403,
            detail="filesystem browser is disabled in this deployment",
        )


@router.get("/list", response_model=FsListing)
def list_directory(path: str = "", include_hidden: bool = False) -> FsListing:
    _ensure_enabled()
    p = (Path(path).expanduser() if path else Path.home()).resolve()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")

    entries: list[FsEntry] = []
    try:
        children = list(p.iterdir())
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"permission denied: {p}") from None

    # Dirs before files; case-insensitive name within each.
    children.sort(key=lambda c: (not c.is_dir(), c.name.lower()))

    for child in children:
        try:
            is_dir = child.is_dir()
            stat = child.stat() if not is_dir else None
            entries.append(
                FsEntry(
                    name=child.name,
                    kind="dir" if is_dir else "file",
                    size=stat.st_size if stat else None,
                    is_hidden=child.name.startswith("."),
                )
            )
        except (PermissionError, OSError, FileNotFoundError):
            # Skip entries we can't stat (broken symlinks, perms).
            continue

    if not include_hidden:
        entries = [e for e in entries if not e.is_hidden]

    parent = str(p.parent) if p.parent != p else None
    return FsListing(path=str(p), parent=parent, entries=entries)


@router.get("/quick-paths", response_model=list[QuickPath])
def quick_paths() -> list[QuickPath]:
    """Common starting points the UI can show as one-click shortcuts."""
    _ensure_enabled()
    home = Path.home()
    items: list[QuickPath] = [QuickPath(label="Home", path=str(home))]
    for name in ("Desktop", "Documents", "Downloads", "Data"):
        candidate = home / name
        if candidate.exists() and candidate.is_dir():
            items.append(QuickPath(label=name, path=str(candidate)))
    items.append(QuickPath(label="ai-almanac data", path=str(data_root())))
    items.append(QuickPath(label="Root", path="/"))
    # Deduplicate (handles platforms where Home itself contains 'Data').
    seen: set[str] = set()
    return [i for i in items if not (i.path in seen or seen.add(i.path))]
