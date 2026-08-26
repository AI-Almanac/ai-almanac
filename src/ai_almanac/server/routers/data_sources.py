"""Observation and model source catalog (local directories and gs:// pointers)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ai_almanac.server.auth import CurrentUser, require_data_management
from ai_almanac.server.services import data_sources as svc
from ai_almanac.server.services import region_catalog
from ai_almanac.server.services.dataset_resolver import mount_roots
from ai_almanac.settings import settings

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


def _normalized_path(raw: str) -> str:
    return str(Path(raw.strip()).expanduser().resolve())


def _check_path_allowed(user, path: str) -> None:
    """Non-admin sources in shared deployments must be within the dataset mount roots."""
    if user.is_admin or settings.deployment_mode != "shared":
        return
    from pathlib import Path as _Path

    from ai_almanac.server.services.dataset_resolver import is_within

    resolved = _Path(path)
    roots = mount_roots()
    if roots and not is_within(resolved, roots):
        raise HTTPException(
            status_code=400,
            detail="user datasets must be under the configured dataset mount roots",
        )


async def _owned_source_or_404(source_id: str, user) -> dict:
    source = await svc.get_source(source_id)
    if not source or not (user.is_admin or source.get("owner_id") == user.id):
        raise HTTPException(status_code=404, detail="data source not found")
    return source


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
    kind: Literal["obs", "model"]
    name: str
    path: str
    region: str | None
    metadata: dict
    location_type: Literal["local_directory", "gcs"]
    status: Literal["ready", "invalid"]
    validation_error: str | None
    visibility: Literal["private", "shared"]
    is_owner: bool
    created_at: str
    updated_at: str | None


class DataSourceValidationOut(BaseModel):
    kind: Literal["obs", "model"]
    path: str
    region: str
    metadata: dict
    status: Literal["ready", "invalid"]
    validation_error: str | None


def _to_out(row: dict, user) -> DataSourceOut:
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
        visibility=row.get("visibility") or "shared",
        is_owner=row.get("owner_id") == user.id,
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
async def list_data_sources(user: CurrentUser, kind: Literal["obs", "model"] | None = None):
    rows = await svc.list_sources(kind=kind, user_id=user.id, is_admin=user.is_admin)
    return [_to_out(r, user) for r in rows]


@router.post(
    "/validate",
    response_model=DataSourceValidationOut,
    dependencies=[Depends(require_data_management)],
)
async def validate_data_source(body: DataSourceIn, user: CurrentUser):
    normalized_path = _normalized_path(body.path)
    _check_path_allowed(user, normalized_path)
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


@router.post(
    "",
    response_model=DataSourceOut,
    status_code=201,
    dependencies=[Depends(require_data_management)],
)
async def create_data_source(body: DataSourceIn, user: CurrentUser):
    normalized = _normalized_path(body.path)
    _check_path_allowed(user, normalized)
    region = await _parse_region(body.region)
    row = await svc.create_source(
        kind=body.kind,
        name=body.name,
        path=normalized,
        region=region,
        metadata=body.metadata,
        owner_id=None if user.is_admin else user.id,
        visibility="shared" if user.is_admin else "private",
    )
    return _to_out(row, user)


@router.put(
    "/{source_id}",
    response_model=DataSourceOut,
    dependencies=[Depends(require_data_management)],
)
async def update_data_source(source_id: str, body: DataSourceUpdate, user: CurrentUser):
    await _owned_source_or_404(source_id, user)
    normalized = _normalized_path(body.path)
    _check_path_allowed(user, normalized)
    region = await _parse_region(body.region)
    row = await svc.update_source(
        source_id,
        name=body.name.strip(),
        path=normalized,
        region=region,
        metadata=body.metadata,
    )
    return _to_out(row, user)


@router.post(
    "/{source_id}/revalidate",
    response_model=DataSourceOut,
    dependencies=[Depends(require_data_management)],
)
async def revalidate_data_source(source_id: str, user: CurrentUser):
    await _owned_source_or_404(source_id, user)
    row = await svc.revalidate_source(source_id)
    if not row:
        raise HTTPException(status_code=404, detail="data source not found")
    return _to_out(row, user)


@router.delete(
    "/{source_id}",
    status_code=204,
    dependencies=[Depends(require_data_management)],
)
async def delete_data_source(source_id: str, user: CurrentUser):
    await _owned_source_or_404(source_id, user)
    ok = await svc.delete_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="data source not found")
