from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_capabilities_report_chat_unavailable_without_llm(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_almanac.server.services.llm.llm_is_configured",
        lambda: False,
    )

    response = await client.get("/config/capabilities")

    assert response.status_code == 200
    assert response.json() == {"chat": False}


@pytest.mark.asyncio
async def test_spa_entrypoint_is_not_cached(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
