from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ai_almanac.paths import uploads_dir
from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.db import get_db, lock_for_update
from ai_almanac.server.services.events import audit, usage
from ai_almanac.settings import settings

router = APIRouter(prefix="/uploads", tags=["uploads"])
_CHUNK_SIZE = 1024 * 1024


class UploadSessionCreate(BaseModel):
    name: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    region: str | None = None
    expected_size_bytes: int | None = Field(default=None, ge=0)


class UploadSessionOut(BaseModel):
    id: str
    data_source_id: str
    filename: str
    status: str
    expires_at: str
    max_size_bytes: int
    upload_url: str | None = None


def _safe_filename(raw: str) -> str:
    filename = Path(raw).name
    if filename != raw or filename in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="filename must not contain a path")
    allowed = {item.strip().lower() for item in settings.allowed_upload_extensions.split(",")}
    if not any(filename.lower().endswith(ext) for ext in allowed if ext):
        raise HTTPException(status_code=400, detail="file type is not allowed")
    return filename


def _grant_hash(grant: str) -> str:
    return hashlib.sha256(grant.encode()).hexdigest()


def _contained_upload_path(storage_key: str) -> Path:
    root = uploads_dir().resolve()
    path = (root / storage_key).resolve()
    if path == root or not path.is_relative_to(root):
        raise HTTPException(status_code=400, detail="invalid upload storage key")
    return path


@router.post("", response_model=UploadSessionOut, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
    body: UploadSessionCreate, user: CurrentUser, request: Request
) -> UploadSessionOut:
    filename = _safe_filename(body.filename)
    if body.expected_size_bytes and body.expected_size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="upload exceeds the configured size limit")

    session_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    grant = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.upload_grant_ttl_seconds)
    storage_key = f"{user.id}/{source_id}/{filename}"
    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        stored = (
            await conn.execute(
                text(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM upload_sessions "
                    "WHERE owner_id = :uid AND status = 'complete'"
                ),
                {"uid": user.id},
            )
        ).scalar_one()
        if stored >= settings.max_stored_upload_bytes_per_user:
            raise HTTPException(status_code=429, detail="stored upload quota exceeded")
        await conn.execute(
            text(
                "INSERT INTO upload_sessions "
                "(id, owner_id, data_source_id, expected_filename, status, expires_at, "
                "max_size_bytes, storage_key, grant_hash, created_at) "
                "VALUES (:id, :uid, :source_id, :filename, 'pending', :expires, "
                ":max_size, :storage_key, :grant_hash, :now)"
            ),
            {
                "id": session_id,
                "uid": user.id,
                "source_id": source_id,
                "filename": filename,
                "expires": expires_at.isoformat(),
                "max_size": settings.max_upload_bytes,
                "storage_key": storage_key,
                "grant_hash": _grant_hash(grant),
                "now": now,
            },
        )
        await audit(
            conn,
            "upload_session.created",
            user_id=user.id,
            resource_type="upload_session",
            resource_id=session_id,
            metadata={"filename": filename, "name": body.name, "region": body.region},
        )
    upload_url = str(request.base_url).rstrip("/") + f"/uploads/{session_id}/content?grant={grant}"
    return UploadSessionOut(
        id=session_id,
        data_source_id=source_id,
        filename=filename,
        status="pending",
        expires_at=expires_at.isoformat(),
        max_size_bytes=settings.max_upload_bytes,
        upload_url=upload_url,
    )


@router.put("/{session_id}/content", status_code=status.HTTP_200_OK)
async def upload_content(
    session_id: str, request: Request, grant: str = Query(min_length=20)
) -> dict:
    async with get_db() as conn:
        lock_clause = await lock_for_update(conn)
        row = (
            (
                await conn.execute(
                    text(f"SELECT * FROM upload_sessions WHERE id = :id{lock_clause}"),
                    {"id": session_id},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row or not secrets.compare_digest(row["grant_hash"], _grant_hash(grant)):
            raise HTTPException(status_code=404, detail="upload grant not found")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="upload grant has already been used")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            await conn.execute(
                text("UPDATE upload_sessions SET status = 'expired' WHERE id = :id"),
                {"id": session_id},
            )
            raise HTTPException(status_code=410, detail="upload grant has expired")
        await conn.execute(
            text("UPDATE upload_sessions SET status = 'uploading' WHERE id = :id"),
            {"id": session_id},
        )
        upload = dict(row)

    destination = _contained_upload_path(upload["storage_key"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > upload["max_size_bytes"]:
                    raise HTTPException(
                        status_code=413, detail="upload exceeds the configured size limit"
                    )
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        async with get_db() as conn:
            await conn.execute(
                text("UPDATE upload_sessions SET status = 'pending' WHERE id = :id"),
                {"id": session_id},
            )
        raise

    async with get_db() as conn:
        await conn.execute(
            text(
                "UPDATE upload_sessions SET status = 'complete', size_bytes = :size, "
                "checksum = :checksum, completed_at = :now WHERE id = :id"
            ),
            {
                "id": session_id,
                "size": size,
                "checksum": digest.hexdigest(),
                "now": datetime.now(UTC).isoformat(),
            },
        )
        await usage(
            conn,
            "upload.bytes",
            user_id=upload["owner_id"],
            resource_type="upload_session",
            resource_id=session_id,
            quantity=size,
        )
        await audit(
            conn,
            "upload.completed",
            user_id=upload["owner_id"],
            resource_type="upload_session",
            resource_id=session_id,
            metadata={"checksum": digest.hexdigest()},
        )
    return {"size_bytes": size, "checksum": digest.hexdigest()}


@router.post("/{session_id}/confirm", response_model=UploadSessionOut)
async def confirm_upload(session_id: str, user: CurrentUser) -> UploadSessionOut:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM upload_sessions WHERE id = :id AND owner_id = :uid"),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            raise HTTPException(status_code=404, detail="upload session not found")
        if row["status"] != "complete":
            raise HTTPException(status_code=409, detail="upload is not complete")
        metadata = {"obs_file_pattern": row["expected_filename"]}
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, metadata, location_type, status, validation_error, "
                "owner_id, visibility, origin, created_at, updated_at) "
                "VALUES (:id, 'obs', :name, :path, :metadata, 'local_directory', 'ready', NULL, "
                ":uid, 'private', 'upload', :now, :now) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": row["data_source_id"],
                "name": row["expected_filename"],
                "path": str(_contained_upload_path(row["storage_key"]).parent),
                "metadata": json.dumps(metadata),
                "uid": user.id,
                "now": datetime.now(UTC).isoformat(),
            },
        )
        await audit(
            conn,
            "upload.confirmed",
            user_id=user.id,
            resource_type="data_source",
            resource_id=row["data_source_id"],
        )
    return UploadSessionOut(
        id=row["id"],
        data_source_id=row["data_source_id"],
        filename=row["expected_filename"],
        status="complete",
        expires_at=row["expires_at"],
        max_size_bytes=row["max_size_bytes"],
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_upload(session_id: str, user: CurrentUser) -> None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT storage_key FROM upload_sessions WHERE id = :id AND owner_id = :uid"
                    ),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            raise HTTPException(status_code=404, detail="upload session not found")
        await conn.execute(
            text("UPDATE upload_sessions SET status = 'canceled' WHERE id = :id"),
            {"id": session_id},
        )
        await audit(
            conn,
            "upload.canceled",
            user_id=user.id,
            resource_type="upload_session",
            resource_id=session_id,
        )
    _contained_upload_path(row["storage_key"]).unlink(missing_ok=True)


async def cleanup_expired_uploads() -> int:
    now = datetime.now(UTC)
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, storage_key FROM upload_sessions "
                        "WHERE status IN ('pending', 'uploading') AND expires_at <= :now"
                    ),
                    {"now": now.isoformat()},
                )
            )
            .mappings()
            .fetchall()
        )
        if rows:
            await conn.execute(
                text(
                    "UPDATE upload_sessions SET status = 'expired' "
                    "WHERE status IN ('pending', 'uploading') AND expires_at <= :now"
                ),
                {"now": now.isoformat()},
            )
    for row in rows:
        destination = _contained_upload_path(row["storage_key"])
        destination.unlink(missing_ok=True)
        for partial in destination.parent.glob(f".{destination.name}.*.part"):
            partial.unlink(missing_ok=True)
    return len(rows)
