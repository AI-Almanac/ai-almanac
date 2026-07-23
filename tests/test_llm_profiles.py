"""Resolution order and shared-key handling for LLM profiles."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.llm_profiles import (
    encrypt_api_key,
    resolve_llm_for_user,
    resolve_shared_profile,
    set_user_preference,
)


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        base64.urlsafe_b64encode(b"x" * 32).decode(),
    )


@pytest_asyncio.fixture(autouse=True)
async def _clean_llm_tables():
    # The engine is session-scoped, so wipe LLM state between tests.
    yield
    async with get_db() as conn:
        await conn.execute(text("DELETE FROM user_llm_profiles"))
        await conn.execute(text("DELETE FROM llm_providers"))
        await conn.execute(text("UPDATE users SET llm_preference = 'auto'"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _insert_shared_provider(api_key: str, model: str) -> str:
    provider_id = str(uuid.uuid4())
    version, nonce, ciphertext = encrypt_api_key(api_key)
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO llm_providers (id, provider_type, display_name, base_url, "
                "enabled, allow_shared, shared_model_name, shared_key_version, "
                "shared_key_nonce, shared_key_ciphertext, created_at, updated_at) "
                "VALUES (:id, 'openai-compatible', 'Shared', 'http://shared.local', "
                "TRUE, TRUE, :model, :v, :n, :c, :now, :now)"
            ),
            {
                "id": provider_id,
                "model": model,
                "v": version,
                "n": nonce,
                "c": ciphertext,
                "now": _now(),
            },
        )
    return provider_id


async def _insert_own_default(user_id: str, provider_id: str, api_key: str, model: str) -> None:
    version, nonce, ciphertext = encrypt_api_key(api_key)
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO user_llm_profiles (id, user_id, provider_id, model_name, "
                "key_version, key_nonce, key_ciphertext, is_default, created_at, updated_at) "
                "VALUES (:id, :uid, :pid, :model, :v, :n, :c, TRUE, :now, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "pid": provider_id,
                "model": model,
                "v": version,
                "n": nonce,
                "c": ciphertext,
                "now": _now(),
            },
        )


@pytest.mark.asyncio
async def test_shared_profile_decrypts_db_key_over_env(user_id: str) -> None:
    await _insert_shared_provider("sk-shared-123", "shared-model")
    shared = await resolve_shared_profile()
    assert shared is not None
    assert shared.api_key == "sk-shared-123"
    assert shared.model_name == "shared-model"


@pytest.mark.asyncio
async def test_env_backs_shared_when_no_db_key(monkeypatch, user_id: str) -> None:
    # The session fixture sets llm_base_url, so the env fallback is configured.
    shared = await resolve_shared_profile()
    assert shared is not None
    assert shared.base_url == "http://test-llm.local"


@pytest.mark.asyncio
async def test_resolution_order_own_over_shared(monkeypatch, user_id: str) -> None:
    # Disable the env fallback so "shared" means only an explicit DB provider.
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "llm_base_url", "")

    # No own profile, no shared → error.
    with pytest.raises(RuntimeError):
        await resolve_llm_for_user(user_id)

    provider_id = await _insert_shared_provider("sk-shared", "shared-model")
    await _insert_own_default(user_id, provider_id, "sk-own", "own-model")

    # auto prefers the user's own default.
    await set_user_preference(user_id, "auto")
    assert (await resolve_llm_for_user(user_id)).api_key == "sk-own"

    # shared forces the shared key even when an own default exists.
    await set_user_preference(user_id, "shared")
    assert (await resolve_llm_for_user(user_id)).api_key == "sk-shared"

    # own uses the personal profile.
    await set_user_preference(user_id, "own")
    assert (await resolve_llm_for_user(user_id)).api_key == "sk-own"
