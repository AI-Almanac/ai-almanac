"""A stand-in for gcsfs that serves local files as GCS objects.

Reads go through fsspec's `AbstractBufferedFile`, the same ranged, block-cached
machinery `gcsfs.GCSFile` uses, so tests see the access pattern production
does. Every byte fetched is counted, which lets tests assert how much of an
object a read actually pulled.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from fsspec.spec import AbstractBufferedFile, AbstractFileSystem

from ai_almanac.server.services.storage import GCSStorage


class RangeReadFileSystem(AbstractFileSystem):
    cachable = False

    def __init__(self, objects: dict[str, Path]) -> None:
        super().__init__()
        self.objects = objects
        self.bytes_fetched = 0
        self.requests = 0

    def glob(self, path, **kwargs):
        return sorted(fnmatch.filter(self.objects, path))

    def size(self, path):
        return self.objects[path].stat().st_size

    def cat_file(self, path, start=None, end=None, **kwargs):
        return self.fetch(path, start or 0, end if end is not None else self.size(path))

    def fetch(self, path: str, start: int, end: int) -> bytes:
        with self.objects[path].open("rb") as file:
            file.seek(start)
            data = file.read(end - start)
        self.bytes_fetched += len(data)
        self.requests += 1
        return data

    def _open(self, path, mode="rb", block_size=None, autocommit=True, cache_options=None, **kw):
        return _RangeReadFile(
            self,
            path,
            mode,
            block_size=block_size or "default",
            cache_options=cache_options,
            size=self.size(path),
            **kw,
        )


class _RangeReadFile(AbstractBufferedFile):
    def _fetch_range(self, start, end):
        return self.fs.fetch(self.path, start, end)


def gcs_storage_over(local_dir: Path, gcs_prefix: str) -> tuple[GCSStorage, RangeReadFileSystem]:
    """A real `GCSStorage` whose bucket objects are the files in `local_dir`."""
    base = gcs_prefix.removeprefix("gs://").rstrip("/")
    fs = RangeReadFileSystem({f"{base}/{p.name}": p for p in local_dir.iterdir() if p.is_file()})
    storage = GCSStorage("uploads", "outputs", "data", client=object())
    storage._fs = lambda: fs
    return storage, fs
