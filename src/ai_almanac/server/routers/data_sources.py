"""Local observation and model source catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_almanac.server.services import data_sources as svc

router = APIRouter(prefix="/data-sources", tags=["data-sources"])

class DataSourceIn(BaseModel):
    kind: Literal["obs", "model"]
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    region: str | None = None
    metadata: dict = Field(default_factory=dict)


class DataSourceUpdate(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    region: str | None = None
    metadata: dict = Field(default_factory=dict)


class DataSourceOut(BaseModel):
    id: str
    kind: str
    name: str
    path: str
    region: str | None
    metadata: dict
    location_type: Literal["local_directory"]
    status: Literal["ready", "invalid"]
    validation_error: str | None
    created_at: str
    updated_at: str | None


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
        location_type=row.get("location_type") or "local_directory",
        status=row.get("status") or "invalid",
        validation_error=row.get("validation_error"),
        created_at=row["created_at"],
        updated_at=row.get("updated_at"),
    )


@router.get("", response_model=list[DataSourceOut])
async def list_data_sources(kind: Literal["obs", "model"] | None = None):
    rows = await svc.list_sources(kind=kind)
    return [_to_out(r) for r in rows]


@router.post("", response_model=DataSourceOut, status_code=201)
async def create_data_source(body: DataSourceIn):
    normalized = str(Path(body.path).expanduser().resolve())
    if body.kind == "model" and not body.region:
        raise HTTPException(
            status_code=400, detail="region is required for model data sources"
        )
    row = await svc.create_source(
        kind=body.kind,
        name=body.name,
        path=normalized,
        region=body.region,
        metadata=body.metadata,
    )
    return _to_out(row)


@router.put("/{source_id}", response_model=DataSourceOut)
async def update_data_source(source_id: str, body: DataSourceUpdate):
    existing = await svc.get_source(source_id)
    if not existing:
        raise HTTPException(status_code=404, detail="data source not found")
    if existing["kind"] == "model" and not body.region:
        raise HTTPException(status_code=400, detail="region is required for model data sources")
    row = await svc.update_source(
        source_id,
        name=body.name.strip(),
        path=str(Path(body.path).expanduser().resolve()),
        region=body.region,
        metadata=body.metadata,
    )
    return _to_out(row)


@router.post("/{source_id}/revalidate", response_model=DataSourceOut)
async def revalidate_data_source(source_id: str):
    row = await svc.revalidate_source(source_id)
    if not row:
        raise HTTPException(status_code=404, detail="data source not found")
    return _to_out(row)


@router.delete("/{source_id}", status_code=204)
async def delete_data_source(source_id: str):
    ok = await svc.delete_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="data source not found")
