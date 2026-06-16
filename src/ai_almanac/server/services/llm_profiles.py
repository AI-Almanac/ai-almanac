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
