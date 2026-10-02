"""Blend runs stage per-year files from GCS, then process only the files the
intermediates cache doesn't already hold."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_APP_PATH = Path(__file__).parents[1] / "modal" / "blending_app.py"


def _load_blending_app():
    spec = importlib.util.spec_from_file_location("almanac_blending_app", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Blob:
    def __init__(self, store: dict[str, bytes], bucket: str, name: str) -> None:
        self._store, self._key, self.name = store, f"{bucket}/{name}", name

    def exists(self) -> bool:
        return self._key in self._store

    def download_to_filename(self, path: str) -> None:
        Path(path).write_bytes(self._store[self._key])


class _Client:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def bucket(self, bucket: str):
        store = self._store

        class _Bucket:
            def blob(self, name: str) -> _Blob:
                return _Blob(store, bucket, name)

        return _Bucket()


def test_staging_downloads_every_year_file_under_its_own_name(tmp_path: Path) -> None:
    app = _load_blending_app()
    store = {f"data/models/india/aifs/{year}.nc": str(year).encode() for year in range(1990, 2010)}
    uris = [f"gs://{key}" for key in store]

    count = app._stage_uris(_Client(store), uris, tmp_path, "forecast aifs")

    assert count == len(uris)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == {
        Path(key).name: content for key, content in store.items()
    }


def test_staging_fails_loudly_when_a_year_file_is_missing(tmp_path: Path) -> None:
    app = _load_blending_app()
    store = {"data/aifs/2000.nc": b"2000"}
    uris = ["gs://data/aifs/2000.nc", "gs://data/aifs/2001.nc"]

    with pytest.raises(FileNotFoundError, match="2001.nc"):
        app._stage_uris(_Client(store), uris, tmp_path, "forecast aifs")


def test_cached_parts_only_compute_cache_misses_and_keep_file_order(tmp_path: Path) -> None:
    app = _load_blending_app()
    cache_dir = str(tmp_path / "cache")
    paths = [tmp_path / f"{year}.nc" for year in (2001, 2002, 2003)]
    keys = [{"file": path.name} for path in paths]
    app._cached_pickle(cache_dir, "obs", keys[1], lambda: "cached 2002")
    computed: list[str] = []

    def process(path: Path, context: dict) -> str:
        computed.append(path.name)
        return f"{context['label']} {path.stem}"

    results = app._cached_parts(cache_dir, "obs", keys, paths, process, {"label": "fresh"}, 1)

    assert results == [("fresh 2001", False), ("cached 2002", True), ("fresh 2003", False)]
    assert computed == ["2001.nc", "2003.nc"]
    rerun = app._cached_parts(cache_dir, "obs", keys, paths, process, {"label": "fresh"}, 1)
    assert all(was_cached for _, was_cached in rerun)
    assert computed == ["2001.nc", "2003.nc"]
