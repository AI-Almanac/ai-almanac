import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from ai_almanac.server.attribution import CurrentUser
from ai_almanac.settings import REMOTE_OBS_PROVIDERS, get_demo_datasets
from ai_almanac.server.db import get_db
from ..services.storage import get_storage

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _obs_year_range(obs_dir: str) -> tuple[int | None, int | None]:
    """Scan obs_dir for {year}.nc files and return (min_year, max_year)."""
    from pathlib import Path

    p = Path(obs_dir)
    if not p.is_dir():
        return None, None
    years = []
    for f in p.iterdir():
        if f.suffix == ".nc" and f.stem.isdigit():
            years.append(int(f.stem))
    if not years:
        return None, None
    return min(years), max(years)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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


class UploadUrlRequest(BaseModel):
    name: str
    filename: str


class UploadUrlResponse(BaseModel):
    dataset_id: str
    upload_url: str
    storage_key: str


class DatasetFromPathRequest(BaseModel):
    name: str
    obs_dir: str  # absolute path on the server — local dev only


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/upload-url", response_model=UploadUrlResponse)
async def request_upload_url(
    body: UploadUrlRequest, user: CurrentUser, request: Request
):
    dataset_id = str(uuid.uuid4())
    storage_key = f"{user['id']}/{dataset_id}/{body.filename}"

    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO datasets (id, user_id, name, status, storage_key, created_at) "
                "VALUES (:id, :uid, :name, 'pending', :key, :now)"
            ),
            {
                "id": dataset_id,
                "uid": user["id"],
                "name": body.name,
                "key": storage_key,
                "now": datetime.now(timezone.utc).isoformat(),
            },
        )

    storage = get_storage()
    upload_url = storage.generate_upload_url(storage_key, str(request.base_url))
    return UploadUrlResponse(
        dataset_id=dataset_id, upload_url=upload_url, storage_key=storage_key
    )


@router.post("/{dataset_id}/confirm", response_model=DatasetOut)
async def confirm_upload(dataset_id: str, user: CurrentUser):
    storage = get_storage()

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM datasets WHERE id = :id AND user_id = :uid"),
                    {"id": dataset_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found"
            )
        row = dict(row)
        if row["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Dataset is already {row['status']}",
            )

        exists = await asyncio.to_thread(storage.confirm_upload, row["storage_key"])
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Upload not found in storage — did the upload complete?",
            )

        result = await conn.execute(
            text(
                "UPDATE datasets SET status = 'ready', ready_at = :now WHERE id = :id RETURNING *"
            ),
            {"now": datetime.now(timezone.utc).isoformat(), "id": dataset_id},
        )
        return DatasetOut(**dict(result.mappings().fetchone()))


@router.get("", response_model=list[DatasetOut])
async def list_datasets(user: CurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM datasets WHERE user_id = :uid ORDER BY created_at DESC"
                    ),
                    {"uid": user["id"]},
                )
            )
            .mappings()
            .fetchall()
        )
    user_datasets = [DatasetOut(**dict(r)) for r in rows]

    demo_datasets = []
    for d in get_demo_datasets():
        yr_start, yr_end = (None, None)
        if d.get("provider") not in REMOTE_OBS_PROVIDERS and d.get("obs_dir"):
            yr_start, yr_end = _obs_year_range(d["obs_dir"])
        demo_datasets.append(
            DatasetOut(
                id=d["id"],
                name=d["name"],
                status="ready",
                region=d.get("region"),
                created_at="",
                is_demo=True,
                provider=d.get("provider"),
                obs_file_pattern=d.get("obs_file_pattern"),
                obs_year_start=yr_start,
                obs_year_end=yr_end,
            )
        )

    return demo_datasets + user_datasets


@router.post(
    "/from-path", response_model=DatasetOut, status_code=status.HTTP_201_CREATED
)
async def dataset_from_path(body: DatasetFromPathRequest, user: CurrentUser):
    """
    Register a local directory as a ready dataset without an upload step.
    Only available when STORAGE_BACKEND=local (local development).
    """
    storage = get_storage()
    if not storage.is_local:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="from-path registration is only available in local development mode",
        )

    from pathlib import Path

    if not Path(body.obs_dir).is_dir():
        raise HTTPException(
            status_code=400, detail=f"obs_dir does not exist: {body.obs_dir}"
        )

    now = datetime.now(timezone.utc).isoformat()
    dataset_id = str(uuid.uuid4())

    async with get_db() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO datasets (id, user_id, name, status, storage_key, created_at, ready_at) "
                "VALUES (:id, :uid, :name, 'ready', :key, :now, :now) RETURNING *"
            ),
            {
                "id": dataset_id,
                "uid": user["id"],
                "name": body.name,
                "key": body.obs_dir,
                "now": now,
            },
        )
        return DatasetOut(**dict(result.mappings().fetchone()))


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset(dataset_id: str, user: CurrentUser):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM datasets WHERE id = :id AND user_id = :uid"),
                    {"id": dataset_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found"
        )
    return DatasetOut(**dict(row))
