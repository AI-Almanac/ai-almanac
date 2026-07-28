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


def _spa_is_built() -> bool:
    from ai_almanac.server.app import _STATIC_READY

    return _STATIC_READY


@pytest.mark.skipif(
    not _spa_is_built(),
    reason=(
        "no built SPA bundle; run `pixi run build-web` first. The static mount is "
        "resolved at import time, so this cannot be faked with a fixture. CI builds "
        "the frontend before pytest, so coverage is preserved there."
    ),
)
@pytest.mark.asyncio
async def test_spa_entrypoint_is_not_cached(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_api_works_without_a_built_spa(client: httpx.AsyncClient) -> None:
    """The API must not depend on the frontend having been built.

    `pixi run dev` serves the UI from Vite and never touches web/build, and
    web/build/ now exists-but-empty on a fresh clone. Probing for index.html
    rather than the directory keeps that case from either shadowing the packaged
    bundle or raising on a missing file.
    """
    response = await client.get("/config/capabilities")

    assert response.status_code == 200
