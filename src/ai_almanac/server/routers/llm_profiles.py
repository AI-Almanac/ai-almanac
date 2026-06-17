from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import text

from ai_almanac.server.auth import AdminUser, CurrentUser
from ai_almanac.server.db import get_db
from ai_almanac.server.services.events import audit
from ai_almanac.server.services.llm_profiles import encrypt_api_key

router = APIRouter(prefix="/llm", tags=["llm"])


class ProviderIn(BaseModel):
    provider_type: str = Field(pattern="^(openai-compatible|pydantic-ai)$")
    display_name: str = Field(min_length=1)
    base_url: HttpUrl | None = None
    enabled: bool = True


class ProviderOut(BaseModel):
    id: str
    provider_type: str
    display_name: str
    base_url: str | None
    enabled: bool
    allow_shared: bool = False
    shared_model_name: str | None = None
    has_shared_key: bool = False


def _provider_out(row) -> ProviderOut:
    data = dict(row)
    return ProviderOut(
        id=data["id"],
        provider_type=data["provider_type"],
        display_name=data["display_name"],
        base_url=data.get("base_url"),
        enabled=bool(data["enabled"]),
        allow_shared=bool(data.get("allow_shared")),
        shared_model_name=data.get("shared_model_name"),
        has_shared_key=data.get("shared_key_ciphertext") is not None,
    )


class ProviderSharedIn(BaseModel):
    allow_shared: bool
    shared_model_name: str | None = None
    api_key: str | None = Field(default=None, min_length=1)


class PreferenceIn(BaseModel):
    preference: str = Field(pattern="^(auto|shared|own)$")


class ProfileIn(BaseModel):
    provider_id: str
    model_name: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    is_default: bool = False


class ProfileUpdate(BaseModel):
    model_name: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, min_length=1)


class ProfileOut(BaseModel):
    id: str
    provider_id: str
    provider_display_name: str
    model_name: str
    is_default: bool
    has_api_key: bool = True
    created_at: str
    updated_at: str


def _profile_out(row) -> ProfileOut:
    return ProfileOut(
        id=row["id"],
        provider_id=row["provider_id"],
        provider_display_name=row["provider_display_name"],
        model_name=row["model_name"],
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(_user: CurrentUser) -> list[ProviderOut]:
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text("SELECT * FROM llm_providers WHERE enabled = TRUE ORDER BY display_name")
                )
            )
            .mappings()
            .fetchall()
        )
    return [_provider_out(row) for row in rows]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderIn, admin: AdminUser) -> ProviderOut:
    provider_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO llm_providers "
                        "(id, provider_type, display_name, base_url, enabled, created_at, updated_at) "
                        "VALUES (:id, :type, :name, :url, :enabled, :now, :now) RETURNING *"
                    ),
                    {
                        "id": provider_id,
                        "type": body.provider_type,
                        "name": body.display_name,
                        "url": str(body.base_url) if body.base_url else None,
                        "enabled": body.enabled,
                        "now": now,
                    },
                )
            )
            .mappings()
            .fetchone()
        )
        await audit(
            conn,
            "llm_provider.created",
            user_id=admin.id,
            resource_type="llm_provider",
            resource_id=provider_id,
        )
    return _provider_out(row)


@router.put("/providers/{provider_id}/shared", response_model=ProviderOut)
async def set_provider_shared(
    provider_id: str, body: ProviderSharedIn, admin: AdminUser
) -> ProviderOut:
    """Set whether a provider offers a shared key to all users, and rotate it.

    The key is encrypted at rest like personal profiles. Omitting `api_key`
    keeps any existing shared key (so an admin can toggle availability without
    re-entering the secret)."""
    now = datetime.now(UTC).isoformat()
    assignments = [
        "allow_shared = :allow_shared",
        "shared_model_name = :model",
        "updated_at = :now",
    ]
    values: dict[str, object] = {
        "id": provider_id,
        "allow_shared": body.allow_shared,
        "model": body.shared_model_name,
        "now": now,
    }
    if body.api_key is not None:
        version, nonce, ciphertext = encrypt_api_key(body.api_key)
        assignments += [
            "shared_key_version = :version",
            "shared_key_nonce = :nonce",
            "shared_key_ciphertext = :ciphertext",
        ]
        values.update(version=version, nonce=nonce, ciphertext=ciphertext)
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"UPDATE llm_providers SET {', '.join(assignments)} "
                        "WHERE id = :id RETURNING *"
                    ),
                    values,
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            raise HTTPException(status_code=404, detail="LLM provider not found")
        await audit(
            conn,
            "llm_provider.shared_updated",
            user_id=admin.id,
            resource_type="llm_provider",
            resource_id=provider_id,
            metadata={"allow_shared": body.allow_shared, "key_rotated": body.api_key is not None},
        )
    return _provider_out(row)


@router.get("/status")
async def llm_status(user: CurrentUser) -> dict:
    """The current user's effective LLM source and preference, for the UI."""
    from ai_almanac.server.services.llm_profiles import describe_user_llm

    return await describe_user_llm(user.id)


@router.put("/preference")
async def set_preference(body: PreferenceIn, user: CurrentUser) -> dict:
    from ai_almanac.server.services.llm_profiles import (
        describe_user_llm,
        set_user_preference,
    )

    await set_user_preference(user.id, body.preference)
    return await describe_user_llm(user.id)


@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(user: CurrentUser) -> list[ProfileOut]:
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT u.*, p.display_name AS provider_display_name "
                        "FROM user_llm_profiles u JOIN llm_providers p ON p.id = u.provider_id "
                        "WHERE u.user_id = :uid ORDER BY u.is_default DESC, u.created_at"
                    ),
                    {"uid": user.id},
                )
            )
            .mappings()
            .fetchall()
        )
    return [_profile_out(row) for row in rows]


@router.post("/profiles", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(body: ProfileIn, user: CurrentUser) -> ProfileOut:
    profile_id = str(uuid.uuid4())
    version, nonce, ciphertext = encrypt_api_key(body.api_key)
    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        provider = (
            await conn.execute(
                text("SELECT id FROM llm_providers WHERE id = :id AND enabled = TRUE"),
                {"id": body.provider_id},
            )
        ).fetchone()
        if not provider:
            raise HTTPException(status_code=404, detail="LLM provider not found")
        if body.is_default:
            await conn.execute(
                text("UPDATE user_llm_profiles SET is_default = FALSE WHERE user_id = :uid"),
                {"uid": user.id},
            )
        row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO user_llm_profiles "
                        "(id, user_id, provider_id, model_name, key_version, key_nonce, "
                        "key_ciphertext, is_default, created_at, updated_at) "
                        "VALUES (:id, :uid, :provider, :model, :version, :nonce, "
                        ":ciphertext, :default, :now, :now) RETURNING *"
                    ),
                    {
                        "id": profile_id,
                        "uid": user.id,
                        "provider": body.provider_id,
                        "model": body.model_name,
                        "version": version,
                        "nonce": nonce,
                        "ciphertext": ciphertext,
                        "default": body.is_default,
                        "now": now,
                    },
                )
            )
            .mappings()
            .fetchone()
        )
        provider_name = (
            await conn.execute(
                text("SELECT display_name FROM llm_providers WHERE id = :id"),
                {"id": body.provider_id},
            )
        ).scalar_one()
        await audit(
            conn,
            "llm_profile.created",
            user_id=user.id,
            resource_type="llm_profile",
            resource_id=profile_id,
            metadata={"provider_id": body.provider_id, "model_name": body.model_name},
        )
    return _profile_out({**dict(row), "provider_display_name": provider_name})


@router.post("/profiles/{profile_id}/default", response_model=ProfileOut)
async def set_default_profile(profile_id: str, user: CurrentUser) -> ProfileOut:
    async with get_db() as conn:
        exists = (
            await conn.execute(
                text("SELECT id FROM user_llm_profiles WHERE id = :id AND user_id = :uid"),
                {"id": profile_id, "uid": user.id},
            )
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="LLM profile not found")
        await conn.execute(
            text("UPDATE user_llm_profiles SET is_default = FALSE WHERE user_id = :uid"),
            {"uid": user.id},
        )
        row = (
            (
                await conn.execute(
                    text(
                        "UPDATE user_llm_profiles SET is_default = TRUE, updated_at = :now "
                        "WHERE id = :id RETURNING *"
                    ),
                    {"id": profile_id, "now": datetime.now(UTC).isoformat()},
                )
            )
            .mappings()
            .fetchone()
        )
        provider_name = (
            await conn.execute(
                text("SELECT display_name FROM llm_providers WHERE id = :id"),
                {"id": row["provider_id"]},
            )
        ).scalar_one()
        await audit(
            conn,
            "llm_profile.default_changed",
            user_id=user.id,
            resource_type="llm_profile",
            resource_id=profile_id,
        )
    return _profile_out({**dict(row), "provider_display_name": provider_name})


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
async def update_profile(profile_id: str, body: ProfileUpdate, user: CurrentUser) -> ProfileOut:
    values: dict[str, object] = {
        "id": profile_id,
        "uid": user.id,
        "now": datetime.now(UTC).isoformat(),
    }
    assignments = ["updated_at = :now"]
    if body.model_name is not None:
        assignments.append("model_name = :model")
        values["model"] = body.model_name
    if body.api_key is not None:
        version, nonce, ciphertext = encrypt_api_key(body.api_key)
        assignments.extend(
            [
                "key_version = :version",
                "key_nonce = :nonce",
                "key_ciphertext = :ciphertext",
            ]
        )
        values.update(version=version, nonce=nonce, ciphertext=ciphertext)
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"UPDATE user_llm_profiles SET {', '.join(assignments)} "
                        "WHERE id = :id AND user_id = :uid RETURNING *"
                    ),
                    values,
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            raise HTTPException(status_code=404, detail="LLM profile not found")
        provider_name = (
            await conn.execute(
                text("SELECT display_name FROM llm_providers WHERE id = :id"),
                {"id": row["provider_id"]},
            )
        ).scalar_one()
        await audit(
            conn,
            "llm_profile.updated",
            user_id=user.id,
            resource_type="llm_profile",
            resource_id=profile_id,
            metadata={
                "model_changed": body.model_name is not None,
                "credential_changed": body.api_key is not None,
            },
        )
    return _profile_out({**dict(row), "provider_display_name": provider_name})


@router.post("/profiles/{profile_id}/test")
async def test_profile(profile_id: str, user: CurrentUser) -> dict:
    from ai_almanac.server.services.llm_profiles import resolve_profile

    profile = await resolve_profile(profile_id, user.id)
    if profile.provider_type != "openai-compatible" or not profile.base_url:
        return {"status": "configured"}

    import httpx

    started = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                profile.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {profile.api_key}"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Provider connectivity test failed") from exc
    return {
        "status": "ok",
        "latency_ms": int((datetime.now(UTC) - started).total_seconds() * 1000),
    }


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, user: CurrentUser) -> None:
    async with get_db() as conn:
        result = await conn.execute(
            text("DELETE FROM user_llm_profiles WHERE id = :id AND user_id = :uid"),
            {"id": profile_id, "uid": user.id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="LLM profile not found")
        await audit(
            conn,
            "llm_profile.deleted",
            user_id=user.id,
            resource_type="llm_profile",
            resource_id=profile_id,
        )
