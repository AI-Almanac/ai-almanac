"""Filesystem artifact publication and dataset mount-root containment."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_almanac.server.services.artifact_store import FilesystemArtifactStore
from ai_almanac.server.services.dataset_resolver import (
    DatasetAccessError,
    DataSource,
    FilesystemDatasetResolver,
)
from ai_almanac.server.services.storage import LocalStorage
from ai_almanac.settings import settings


def _store(tmp_path: Path) -> FilesystemArtifactStore:
    storage = LocalStorage(
        upload_dir=tmp_path / "uploads",
        job_outputs_dir=tmp_path / "jobs",
    )
    return FilesystemArtifactStore(storage)


def _write_outputs(store: FilesystemArtifactStore, job_id: str) -> dict[str, bytes]:
    output, figure = store._storage.job_output_uri(job_id)
    payloads = {
        "output/metrics.nc": b"\x89netcdf-fake-bytes",
        "figure/portrait.png": b"\x89PNG\r\n\x1a\n-fake",
    }
    (Path(output) / "metrics.nc").write_bytes(payloads["output/metrics.nc"])
    (Path(figure) / "portrait.png").write_bytes(payloads["figure/portrait.png"])
    return payloads


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


def test_publish_indexes_outputs_with_hash_size_and_media_type(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    job_id = "job-1"
    store.create_workspace(job_id)
    payloads = _write_outputs(store, job_id)

    artifacts = store.publish(job_id, store._storage.job_dir(job_id))
    by_name = {a.filename: a for a in artifacts}

    assert set(by_name) == {"metrics.nc", "portrait.png"}
    nc = by_name["metrics.nc"]
    assert nc.kind == "output"
    assert nc.media_type == "application/x-netcdf"
    assert nc.size_bytes == len(payloads["output/metrics.nc"])
    assert nc.checksum == hashlib.sha256(payloads["output/metrics.nc"]).hexdigest()
    assert nc.storage_key == "job-1/output/metrics.nc"

    png = by_name["portrait.png"]
    assert png.kind == "figure"
    assert png.media_type == "image/png"


# ---------------------------------------------------------------------------
# DatasetResolver — mount-root containment
# ---------------------------------------------------------------------------


def _source(path: Path, origin: str = "mounted") -> DataSource:
    return DataSource(
        id="src",
        kind="obs",
        path=str(path),
        origin=origin,
        owner_id=None,
        visibility="shared",
    )


@pytest.mark.asyncio
async def test_resolve_mounted_within_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "mounts"
    data = root / "obs"
    data.mkdir(parents=True)
    monkeypatch.setattr(settings, "dataset_mount_roots", str(root))

    resolved = await FilesystemDatasetResolver().resolve(_source(data), tmp_path)
    assert resolved.path == data.resolve()


@pytest.mark.asyncio
async def test_resolve_mounted_outside_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "mounts"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setattr(settings, "dataset_mount_roots", str(root))

    with pytest.raises(DatasetAccessError):
        await FilesystemDatasetResolver().resolve(_source(outside), tmp_path)


@pytest.mark.asyncio
async def test_resolve_mounted_blocks_traversal_out_of_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "mounts"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    monkeypatch.setattr(settings, "dataset_mount_roots", str(root))

    # A path that traverses out of the root canonicalizes outside it.
    traversal = root / ".." / "secret"
    with pytest.raises(DatasetAccessError):
        await FilesystemDatasetResolver().resolve(_source(traversal), tmp_path)


@pytest.mark.asyncio
async def test_resolve_mounted_unrestricted_when_no_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dataset_mount_roots", "")
    data = tmp_path / "anywhere"
    data.mkdir()

    resolved = await FilesystemDatasetResolver().resolve(_source(data), tmp_path)
    assert resolved.path == data.resolve()


@pytest.mark.asyncio
async def test_resolve_missing_mounted_path_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dataset_mount_roots", "")
    with pytest.raises(DatasetAccessError):
        await FilesystemDatasetResolver().resolve(_source(tmp_path / "does-not-exist"), tmp_path)
