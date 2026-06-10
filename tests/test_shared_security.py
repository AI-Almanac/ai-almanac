from __future__ import annotations

import base64

import httpx
import pytest
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import settings


@pytest.mark.asyncio
async def test_upload_grant_is_single_use_and_publishes_private_source(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allowed_upload_extensions", ".nc")
    created = await client.post(
        "/uploads",
        headers=auth_headers,
        json={"name": "Observations", "filename": "observations.nc"},
    )
    assert created.status_code == 201
    session = created.json()

    uploaded = await client.put(session["upload_url"], content=b"netcdf-placeholder")
    assert uploaded.status_code == 200
    replay = await client.put(session["upload_url"], content=b"again")
    assert replay.status_code == 409

    confirmed = await client.post(
        f"/uploads/{session['id']}/confirm", headers=auth_headers
    )
    assert confirmed.status_code == 200
    async with get_db() as conn:
        source = (
            (
                await conn.execute(
                    text("SELECT * FROM data_sources WHERE id = :id"),
                    {"id": session["data_source_id"]},
                )
            )
            .mappings()
            .one()
        )
    assert source["origin"] == "upload"
    assert source["visibility"] == "private"


@pytest.mark.asyncio
async def test_upload_rejects_path_filename(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/uploads",
        headers=auth_headers,
        json={"name": "Traversal", "filename": "../escape.nc"},
    )
    assert response.status_code == 400


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
