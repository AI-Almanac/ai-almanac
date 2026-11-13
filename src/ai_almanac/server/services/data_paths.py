"""Where a data source's files live, parsed once so every reader agrees on the bucket.

A gs:// path is accepted only as ``gs://<bucket>/<folder>``, with a single scheme and a
valid bucket name, so the bucket checked here is the bucket the storage client reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import PlainSerializer, PlainValidator, TypeAdapter, ValidationError, WithJsonSchema

from ai_almanac.server.services.romp import FilePattern

_GCS_SCHEME = "gs://"
_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]")
_GLOB_CHARACTERS = frozenset("*?[]")
_PATTERN_KEYS = ("file_pattern", "obs_file_pattern")
_FILE_PATTERN = TypeAdapter(FilePattern)


@dataclass(frozen=True)
class GcsPath:
    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return (
            f"{_GCS_SCHEME}{self.bucket}/{self.key}" if self.key else f"{_GCS_SCHEME}{self.bucket}"
        )

    @classmethod
    def parse(cls, raw: str) -> GcsPath:
        text = raw.strip()
        if not text.startswith(_GCS_SCHEME):
            raise ValueError(f"{raw!r} is not a gs:// URL")
        rest = text.removeprefix(_GCS_SCHEME)
        if "://" in rest:
            raise ValueError(f"{raw!r} has more than one scheme; use gs://<bucket>/<folder>")
        bucket, _, key = rest.partition("/")
        if not _BUCKET_NAME.fullmatch(bucket):
            raise ValueError(f"{raw!r} does not start with a valid bucket name after gs://")
        return cls(bucket=bucket, key=_parse_key(raw, key.rstrip("/")))


@dataclass(frozen=True)
class LocalPath:
    directory: Path

    @property
    def uri(self) -> str:
        return str(self.directory)


DataPath = GcsPath | LocalPath


def _parse_key(raw: str, key: str) -> str:
    segments = key.split("/") if key else []
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"{raw!r} has an empty, '.' or '..' folder")
    if _GLOB_CHARACTERS & set(key):
        raise ValueError(f"{raw!r} has wildcard characters; put them in the file pattern")
    return key


def parse_data_path(raw: str) -> DataPath:
    """A gs:// URL or a local directory, in the canonical form it is stored in."""
    text = raw.strip()
    if not text:
        raise ValueError("A data source needs a path")
    if text.startswith(_GCS_SCHEME):
        return GcsPath.parse(text)
    if "://" in text:
        raise ValueError(f"{raw!r} must be a gs:// URL or a local directory")
    return LocalPath(Path(text).expanduser().resolve())


def parse_file_pattern(raw: str) -> str:
    """A file pattern relative to the source's folder, e.g. 'chirps_{}.nc'."""
    try:
        return _FILE_PATTERN.validate_python(raw)
    except ValidationError as exc:
        raise ValueError(
            f"File pattern {raw!r} must be a relative name like 'chirps_{{}}.nc', "
            "without '..' folders or a leading '/'"
        ) from exc


def parse_pattern_metadata(metadata: dict) -> dict:
    """Source metadata with any file pattern it names parsed."""
    return {
        key: parse_file_pattern(str(value)) if key in _PATTERN_KEYS and value else value
        for key, value in metadata.items()
    }


def _parse_data_path_field(value: object) -> DataPath:
    if isinstance(value, GcsPath | LocalPath):
        return value
    if not isinstance(value, str):
        raise ValueError("A data source path must be text")
    return parse_data_path(value)


DataPathField = Annotated[
    DataPath,
    PlainValidator(_parse_data_path_field),
    PlainSerializer(lambda path: path.uri, return_type=str),
    WithJsonSchema({"type": "string", "minLength": 1}),
]
"""A request's data source path, parsed into the form every reader trusts."""
