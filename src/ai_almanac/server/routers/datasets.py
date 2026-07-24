"""Read-only obs dataset listing for the benchmarks page.

Registration and management of datasets live in the data-sources router;
this is a thin obs-shaped view over the same `data_sources` rows.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.services import data_sources as data_source_service

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetOut(BaseModel):
    id: str
    name: str
    status: str
    region: str | None = None
    storage_key: str | None = None
    created_at: str
    ready_at: str | None = None
    error: str | None = None
    is_demo: bool = False
    provider: str | None = None
    obs_file_pattern: str | None = None
    obs_year_start: int | None = None
    obs_year_end: int | None = None


@router.get("", response_model=list[DatasetOut])
async def list_datasets(user: CurrentUser):
    sources = await data_source_service.list_sources(
        kind="obs", user_id=user.id, is_admin=user.is_admin
    )
    return [
        DatasetOut(
            id=source["id"],
            name=source["name"],
            status=source.get("status") or "invalid",
            region=source.get("region"),
            created_at=source["created_at"],
            ready_at=source.get("updated_at"),
            error=source.get("validation_error"),
            is_demo=False,
            provider=source["metadata"].get("provider") or "local",
            obs_file_pattern=source["metadata"].get("obs_file_pattern"),
            obs_year_start=source["metadata"].get("start_year"),
            obs_year_end=source["metadata"].get("end_year"),
        )
        for source in sources
        if source.get("status") == "ready"
    ]
