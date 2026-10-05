"""Derived data built once from a completed job's outputs and stored beside them.

Job outputs never change once a job completes (a rerun is a new job), so
anything derived from them can be stored on first use and read back forever.
Bump the version in a cache name whenever its shape changes.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from .storage import StorageBackend


def read_or_build[Model: BaseModel](
    storage: StorageBackend,
    job_id: str,
    name: str,
    model: type[Model],
    build: Callable[[], Model],
) -> Model:
    stored = storage.read_job_cache(job_id, name)
    if stored is not None:
        return model.model_validate_json(stored)
    built = build()
    storage.write_job_cache(job_id, name, built.model_dump_json().encode())
    return built
