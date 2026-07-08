from __future__ import annotations

import base64

import httpx
import pytest
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import settings


@pytest.mark.asyncio
async def test_llm_profile_key_is_encrypted_and_not_returned(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
    )
    provider = await client.post(
        "/llm/providers",
        headers=auth_headers,
        json={
            "provider_type": "openai-compatible",
            "display_name": "Approved endpoint",
            "base_url": "https://llm.example.test/v1",
        },
    )
    assert provider.status_code == 201
    profile = await client.post(
        "/llm/profiles",
        headers=auth_headers,
        json={
            "provider_id": provider.json()["id"],
            "model_name": "test-model",
            "api_key": "plaintext-secret",
            "is_default": True,
        },
    )
    assert profile.status_code == 201
    assert "api_key" not in profile.json()

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT key_nonce, key_ciphertext FROM user_llm_profiles "
                        "WHERE id = :id"
                    ),
                    {"id": profile.json()["id"]},
                )
            )
            .mappings()
            .one()
        )
    assert b"plaintext-secret" not in bytes(row["key_ciphertext"])
    assert len(bytes(row["key_nonce"])) == 12
