"""Fetching geoBoundaries data: upstream quirks become BoundaryUnavailable, not crashes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from aiohttp import web

from ai_almanac.server.services import boundaries


@pytest_asyncio.fixture
async def upstream() -> AsyncIterator[str]:
    async def big_headers(request: web.Request) -> web.Response:
        # github.com's raw-file redirect sends a CSP header over 9 KB.
        return web.json_response({"ok": True}, headers={"Content-Security-Policy": "x" * 9300})

    async def broken(request: web.Request) -> web.StreamResponse:
        assert request.transport is not None
        request.transport.close()
        return web.Response()

    app = web.Application()
    app.router.add_get("/big-headers", big_headers)
    app.router.add_get("/broken", broken)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.mark.asyncio
async def test_responses_with_oversized_headers_still_load(upstream: str) -> None:
    async with boundaries._client_session() as session:
        assert await boundaries._fetch_json(session, f"{upstream}/big-headers") == {"ok": True}


@pytest.mark.asyncio
async def test_a_dropped_connection_is_reported_as_unavailable(upstream: str) -> None:
    async with boundaries._client_session() as session:
        with pytest.raises(boundaries.BoundaryUnavailable):
            await boundaries._fetch_json(session, f"{upstream}/broken")
