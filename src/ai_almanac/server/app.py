"""FastAPI application factory.

The web server is single-process and single-port: it serves both the JSON API
and the bundled SvelteKit SPA from `/`. The SPA bundle is populated into
`ai_almanac/server/static/` at wheel-build time by the hatch `force-include`
directive in pyproject.toml.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ai_almanac.paths import ensure_layout, uploads_dir
from ai_almanac.server.routers import chat, config, datasets, jobs, regions
from ai_almanac.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_layout()
    yield


app = FastAPI(title="ai-almanac", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s %d %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# CORS is only relevant when the SvelteKit Vite dev server (on a different port)
# is talking to this API in dev. In production the SPA is served from the same
# origin and CORS doesn't apply.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_allow_all else [settings.frontend_url],
    allow_credentials=not settings.cors_allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(config.router)
app.include_router(datasets.router)
app.include_router(jobs.router)
app.include_router(regions.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.put("/upload/{storage_key:path}", status_code=status.HTTP_200_OK)
async def local_upload(storage_key: str, request: Request):
    """Receive a user-uploaded obs dataset into the local uploads directory."""
    dest = uploads_dir() / storage_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"stored": str(dest)}


# Bundled SvelteKit SPA. The static directory is populated at wheel-build time
# from `web/build/`. When the directory is absent (e.g. in `uv sync` dev mode),
# the mount is skipped and the Vite dev server on :5173 serves the UI instead.
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="spa")
else:
    logger.info(
        "static SPA bundle not found at %s; serve the frontend separately "
        "(e.g. `npm run dev` in web/)",
        _STATIC_DIR,
    )
