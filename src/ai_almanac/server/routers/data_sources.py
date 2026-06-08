"""Data sources catalog — UI-driven registration of obs/model directories.

Replaces the env-var-driven YAML registry for runtime use. See
`services.data_sources` for the storage layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_almanac.server.services import data_sources as svc

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


class DataSourceIn(BaseModel):
    kind: Literal["obs", "model"]
    name: str
    path: str
    region: str | None = None
    metadata: dict = {}


class DataSourceOut(BaseModel):
    id: str
    kind: str
    name: str
    path: str
    region: str | None
    metadata: dict
    created_at: str
    exists: bool  # whether the path is reachable on disk right now


def _to_out(row: dict) -> DataSourceOut:
    import json as _json

    raw = row.get("metadata") or {}
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except Exception:
            raw = {}
    return DataSourceOut(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        path=row["path"],
        region=row.get("region"),
        metadata=raw,
        created_at=row["created_at"],
        exists=Path(row["path"]).expanduser().exists(),
    )


@router.get("", response_model=list[DataSourceOut])
async def list_data_sources(kind: Literal["obs", "model"] | None = None):
    rows = await svc.list_sources(kind=kind)
    return [_to_out(r) for r in rows]


@router.post("", response_model=DataSourceOut, status_code=201)
async def create_data_source(body: DataSourceIn):
    expanded = str(Path(body.path).expanduser().resolve())
    if body.kind == "model" and not body.region:
        raise HTTPException(
            status_code=400, detail="region is required for model data sources"
        )
    row = await svc.create_source(
        kind=body.kind,
        name=body.name,
        path=expanded,
        region=body.region,
        metadata=body.metadata,
    )
    return _to_out(row)


@router.delete("/{source_id}", status_code=204)
async def delete_data_source(source_id: str):
    ok = await svc.delete_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="data source not found")
