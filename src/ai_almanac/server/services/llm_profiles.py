from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import settings


@dataclass(frozen=True)
class ResolvedLLMProfile:
    provider_type: str
    base_url: str | None
    model_name: str
    api_key: str


def _master_key() -> bytes:
    raw = settings.credential_encryption_key.strip()
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except ValueError as exc:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be URL-safe base64") from exc
    if len(key) != 32:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def encrypt_api_key(api_key: str) -> tuple[int, bytes, bytes]:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_master_key()).encrypt(nonce, api_key.encode(), None)
    return 1, nonce, ciphertext


def decrypt_api_key(version: int, nonce: bytes, ciphertext: bytes) -> str:
    if version != 1:
        raise RuntimeError(f"unsupported credential key version: {version}")
    return AESGCM(_master_key()).decrypt(nonce, ciphertext, None).decode()


async def resolve_default_profile(user_id: str) -> ResolvedLLMProfile:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT p.provider_type, p.base_url, u.model_name, u.key_version, "
                        "u.key_nonce, u.key_ciphertext "
                        "FROM user_llm_profiles u "
                        "JOIN llm_providers p ON p.id = u.provider_id "
                        "WHERE u.user_id = :uid AND u.is_default = TRUE AND p.enabled = TRUE"
                    ),
                    {"uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise RuntimeError("No default LLM profile is configured")
    return ResolvedLLMProfile(
        provider_type=row["provider_type"],
        base_url=row["base_url"],
        model_name=row["model_name"],
        api_key=decrypt_api_key(
            row["key_version"], bytes(row["key_nonce"]), bytes(row["key_ciphertext"])
        ),
    )


def _env_backed_profile() -> ResolvedLLMProfile | None:
    """The host's env-configured model as a shared profile, if usable.

    Lets keys already set on the host (the values `_build_model` uses in local
    mode) back the shared option before an admin stores one in the DB.
    """
    from ai_almanac.server.services.llm import llm_is_configured

    if not llm_is_configured():
        return None
    return ResolvedLLMProfile(
        provider_type=settings.llm_provider.lower(),
        base_url=settings.llm_base_url or None,
        model_name=settings.llm_model,
        api_key=settings.llm_api_key,
    )


async def resolve_shared_profile() -> ResolvedLLMProfile | None:
    """The admin-provided shared LLM, if any.

    Prefers a provider with a stored shared key; falls back to the env-configured
    model so the host's keys work before any DB config exists.
    """
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT provider_type, base_url, shared_model_name, "
                        "shared_key_version, shared_key_nonce, shared_key_ciphertext "
                        "FROM llm_providers "
                        "WHERE enabled = TRUE AND allow_shared = TRUE "
                        "AND shared_key_ciphertext IS NOT NULL "
                        "ORDER BY updated_at DESC"
                    )
                )
            )
            .mappings()
            .fetchone()
        )
    if row:
        return ResolvedLLMProfile(
            provider_type=row["provider_type"],
            base_url=row["base_url"],
            model_name=row["shared_model_name"] or "",
            api_key=decrypt_api_key(
                row["shared_key_version"],
                bytes(row["shared_key_nonce"]),
                bytes(row["shared_key_ciphertext"]),
            ),
        )
    return _env_backed_profile()


async def _user_preference(user_id: str) -> str:
    async with get_db() as conn:
        pref = (
            await conn.execute(
                text("SELECT llm_preference FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).scalar_one_or_none()
    return pref or "auto"


async def resolve_llm_for_user(user_id: str) -> ResolvedLLMProfile:
    """Pick the LLM for a user's chat turn: own > shared, honoring preference.

    `auto` uses the user's default profile when they have one, else the shared
    option. `own` insists on a personal profile. `shared` skips personal
    profiles entirely.
    """
    pref = await _user_preference(user_id)
    if pref != "shared":
        try:
            return await resolve_default_profile(user_id)
        except RuntimeError:
            if pref == "own":
                raise RuntimeError(
                    "You chose to use your own LLM key but have no default profile. "
                    "Add one in AI settings."
                ) from None
    shared = await resolve_shared_profile()
    if shared is not None:
        return shared
    raise RuntimeError(
        "No LLM is available — ask an admin to enable a shared model or add your "
        "own in AI settings."
    )


async def chat_available_for_user(user_id: str) -> bool:
    try:
        await resolve_llm_for_user(user_id)
        return True
    except RuntimeError:
        return False


async def describe_user_llm(user_id: str) -> dict:
    """Status for the AI-settings UI: preference and effective source."""
    pref = await _user_preference(user_id)
    shared_available = await resolve_shared_profile() is not None
    try:
        await resolve_default_profile(user_id)
        has_own_default = True
    except RuntimeError:
        has_own_default = False
    if pref != "shared" and has_own_default:
        effective: str | None = "own"
    elif shared_available:
        effective = "shared"
    else:
        effective = None
    return {
        "preference": pref,
        "shared_available": shared_available,
        "has_own_default": has_own_default,
        "effective_source": effective,
    }


async def set_user_preference(user_id: str, preference: str) -> None:
    if preference not in ("auto", "shared", "own"):
        raise ValueError(f"invalid preference: {preference!r}")
    async with get_db() as conn:
        await conn.execute(
            text("UPDATE users SET llm_preference = :pref WHERE id = :uid"),
            {"pref": preference, "uid": user_id},
        )


async def resolve_profile(profile_id: str, user_id: str) -> ResolvedLLMProfile:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT p.provider_type, p.base_url, u.model_name, u.key_version, "
                        "u.key_nonce, u.key_ciphertext "
                        "FROM user_llm_profiles u "
                        "JOIN llm_providers p ON p.id = u.provider_id "
                        "WHERE u.id = :id AND u.user_id = :uid AND p.enabled = TRUE"
                    ),
                    {"id": profile_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise RuntimeError("LLM profile not found")
    return ResolvedLLMProfile(
        provider_type=row["provider_type"],
        base_url=row["base_url"],
        model_name=row["model_name"],
        api_key=decrypt_api_key(
            row["key_version"], bytes(row["key_nonce"]), bytes(row["key_ciphertext"])
        ),
    )
