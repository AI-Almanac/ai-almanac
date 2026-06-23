"""Local observation and model source catalog."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_almanac.server.auth import AdminUser, CurrentUser
from ai_almanac.server.services import data_sources as svc
from ai_almanac.server.services import region_catalog
from ai_almanac.server.services.data_catalog import discover_datasets

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


class DataSourceValidationOut(BaseModel):
    kind: Literal["obs", "model"]
    path: str
    region: str
    metadata: dict
    status: Literal["ready", "invalid"]
    validation_error: str | None


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


async def _parse_region(region: str | None) -> str:
    normalized = region.strip().lower() if region else ""
    if not normalized:
        raise HTTPException(status_code=400, detail="region is required")
    if await region_catalog.get_region(normalized) is None:
        raise HTTPException(
            status_code=400,
            detail=f"region {normalized!r} is not configured",
        )
    return normalized


@router.get("", response_model=list[DataSourceOut])
async def list_data_sources(
    user: CurrentUser, kind: Literal["obs", "model"] | None = None
):
    rows = await svc.list_sources(kind=kind, user_id=user.id, is_admin=user.is_admin)
    return [_to_out(r) for r in rows]


class DiscoveredDatasetOut(BaseModel):
    kind: str
    region: str
    id: str
    years: list[int]
    manifest: dict | None


@router.get("/catalog", response_model=list[DiscoveredDatasetOut])
async def discover_catalog(
    _user: CurrentUser, kind: Literal["obs", "forecasts"] | None = None
):
    """Datasets discovered by walking the active backend's dataset tree.

    Read-only and database-free: the uniform layout is the source of truth for
    *what data exists*, so this reflects the mirrored tree on local/GCS/volume
    storage without any seeding.
    """
    datasets = await asyncio.to_thread(discover_datasets)
    return [
        DiscoveredDatasetOut(
            kind=dataset.ref.kind,
            region=dataset.ref.region,
            id=dataset.ref.id,
            years=list(dataset.years),
            manifest=(
                dataset.manifest.model_dump(exclude_none=True)
                if dataset.manifest
                else None
            ),
        )
        for dataset in datasets
        if kind is None or dataset.ref.kind == kind
    ]


@router.post("/validate", response_model=DataSourceValidationOut)
async def validate_data_source(body: DataSourceIn, _admin: AdminUser):
    normalized_path = str(Path(body.path).expanduser().resolve())
    region = await _parse_region(body.region)
    status, validation_error, metadata = await svc.validate_source(
        body.kind,
        normalized_path,
        body.metadata,
    )
    return DataSourceValidationOut(
        kind=body.kind,
        path=normalized_path,
        region=region,
        metadata=metadata,
        status=status,
        validation_error=validation_error,
    )


@router.post("", response_model=DataSourceOut, status_code=201)
async def create_data_source(body: DataSourceIn, _admin: AdminUser):
    normalized = str(Path(body.path).expanduser().resolve())
    region = await _parse_region(body.region)
    row = await svc.create_source(
        kind=body.kind,
        name=body.name,
        path=normalized,
        region=region,
        metadata=body.metadata,
    )
    return _to_out(row)


@router.put("/{source_id}", response_model=DataSourceOut)
async def update_data_source(
    source_id: str, body: DataSourceUpdate, _admin: AdminUser
):
    existing = await svc.get_source(source_id)
    if not existing:
        raise HTTPException(status_code=404, detail="data source not found")
    region = await _parse_region(body.region)
    row = await svc.update_source(
        source_id,
        name=body.name.strip(),
        path=str(Path(body.path).expanduser().resolve()),
        region=region,
        metadata=body.metadata,
    )
    return _to_out(row)


@router.post("/{source_id}/revalidate", response_model=DataSourceOut)
async def revalidate_data_source(source_id: str, _admin: AdminUser):
    row = await svc.revalidate_source(source_id)
    if not row:
        raise HTTPException(status_code=404, detail="data source not found")
    return _to_out(row)


@router.delete("/{source_id}", status_code=204)
async def delete_data_source(source_id: str, _admin: AdminUser):
    ok = await svc.delete_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="data source not found")
