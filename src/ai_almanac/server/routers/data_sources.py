"""Observation and model source catalog (local directories and gs:// pointers)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field

from ai_almanac.server.auth import CurrentUser, require_data_management
from ai_almanac.server.services import data_sources as svc
from ai_almanac.server.services import region_catalog
from ai_almanac.server.services.data_paths import DataPath, DataPathField, parse_pattern_metadata
from ai_almanac.server.services.forecast_models import (
    LiveForecastStatus,
    live_forecast_compatibility,
)

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


SourceMetadata = Annotated[dict, AfterValidator(parse_pattern_metadata)]


def _check_path_allowed(path: DataPath, *, admin_registered: bool) -> None:
    """A user's sources must live in cloud storage on shared deployments, and never in
    the platform's own buckets."""
    if error := svc.location_error(path, admin_registered=admin_registered):
        raise HTTPException(status_code=400, detail=error)


async def _owned_source_or_404(source_id: str, user) -> dict:
    source = await svc.get_source(source_id)
    if not source or not (user.is_admin or source.get("owner_id") == user.id):
        raise HTTPException(status_code=404, detail="data source not found")
    return source


class DataSourceIn(BaseModel):
    kind: Literal["obs", "model"]
    name: str = Field(min_length=1)
    path: DataPathField
    region: str | None = None
    metadata: SourceMetadata = Field(default_factory=dict)


class DataSourceUpdate(BaseModel):
    name: str = Field(min_length=1)
    path: DataPathField
    region: str | None = None
    metadata: SourceMetadata = Field(default_factory=dict)


class LiveForecastOut(BaseModel):
    status: LiveForecastStatus
    detail: str | None
    model_id: str | None


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
    live_forecast: LiveForecastOut | None
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


def _live_forecast_out(kind: str, metadata: dict) -> LiveForecastOut | None:
    if kind != "model":
        return None
    verdict = live_forecast_compatibility(
        metadata.get("forecast_model_id"), metadata.get("grid_step_deg")
    )
    return LiveForecastOut(status=verdict.status, detail=verdict.detail, model_id=verdict.model_id)


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
        live_forecast=_live_forecast_out(row["kind"], raw),
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
    _check_path_allowed(body.path, admin_registered=user.is_admin)
    region = await _parse_region(body.region)
    status, validation_error, metadata = await svc.validate_source(
        body.kind,
        body.path.uri,
        body.metadata,
        admin_registered=user.is_admin,
    )
    return DataSourceValidationOut(
        kind=body.kind,
        path=body.path.uri,
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
    _check_path_allowed(body.path, admin_registered=user.is_admin)
    region = await _parse_region(body.region)
    row = await svc.create_source(
        kind=body.kind,
        name=body.name,
        path=body.path.uri,
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
    source = await _owned_source_or_404(source_id, user)
    _check_path_allowed(body.path, admin_registered=svc.registered_by_admin(source))
    region = await _parse_region(body.region)
    row = await svc.update_source(
        source_id,
        name=body.name.strip(),
        path=body.path.uri,
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
