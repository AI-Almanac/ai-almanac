"""FastAPI application factory.

The web server is single-process and single-port: it serves both the JSON API
and the bundled SvelteKit SPA from `/`. The SPA bundle is populated into
`ai_almanac/server/static/` at wheel-build time by the hatch `force-include`
directive in pyproject.toml.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_almanac.paths import ensure_layout, uploads_dir
from ai_almanac.server.routers import (
    auth,
    chat,
    config,
    data_sources,
    datasets,
    fs,
    jobs,
    regions,
)
from ai_almanac.server.routers import (
    settings as settings_router,
)
from ai_almanac.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_PACKAGED_STATIC_DIR = Path(__file__).parent / "static"
_SOURCE_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "build"
_STATIC_DIR = (
    _SOURCE_STATIC_DIR if _SOURCE_STATIC_DIR.exists() else _PACKAGED_STATIC_DIR
)


def _apply_migrations() -> None:
    """Run alembic to `head` on startup so users never need to run it manually."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).parent / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parent / "alembic")
    )
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_layout()
    _apply_migrations()
    _reload_user_config()
    _enforce_deployment()
    await _seed_regions()
    await _seed_data_sources()
    await _reconcile_jobs()
    reconciler = asyncio.create_task(_job_reconciler_loop())
    try:
        yield
    finally:
        reconciler.cancel()
        with suppress(asyncio.CancelledError):
            await reconciler


def _reload_user_config() -> None:
    """Layer `$AI_ALMANAC_DATA_DIR/config.yaml` onto the settings singleton."""
    from ai_almanac.settings import reload_settings

    reload_settings()


def _enforce_deployment() -> None:
    """Validate/harden config for the active deployment mode; fail fast if the
    shared deployment is misconfigured (SQLite, no admins, etc.)."""
    from ai_almanac.server.auth import enforce_deployment_invariants

    enforce_deployment_invariants()


async def _seed_data_sources() -> None:
    """Populate the data_sources table from the packaged YAMLs on first launch
    so existing testdata setups (`*_OBS_DIR`, `*_MODEL_DIR` env vars) work
    without the user needing to register anything manually."""
    from ai_almanac.server.services.data_sources import seed_from_yaml_if_empty

    try:
        count = await seed_from_yaml_if_empty()
        if count:
            logger.info("seeded %d data source(s) from packaged YAMLs", count)
    except Exception as e:  # noqa: BLE001 — non-fatal: keep serving
        logger.warning("data source seeding failed: %s", e)


async def _seed_regions() -> None:
    from ai_almanac.server.services.region_catalog import seed_packaged_regions

    try:
        count = await seed_packaged_regions()
        if count:
            logger.info("seeded %d packaged region(s)", count)
    except Exception as e:  # noqa: BLE001
        logger.warning("region seeding failed: %s", e)


async def _reconcile_jobs() -> None:
    from ai_almanac.server.services.artifacts import publish_pending
    from ai_almanac.server.services.job_manager import reconcile_jobs

    try:
        await reconcile_jobs()
    except Exception as e:  # noqa: BLE001
        logger.warning("job reconciliation failed: %s", e)
    try:
        await publish_pending()
    except Exception as e:  # noqa: BLE001
        logger.warning("artifact publication failed: %s", e)


async def _job_reconciler_loop() -> None:
    while True:
        await asyncio.sleep(5)
        await _reconcile_jobs()


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
#
# `frontend_url` may be a comma-separated list of allowed origins. Any
# loopback origin (localhost / 127.0.0.1, any port) is also allowed so the dev
# server works whether you open it as localhost or 127.0.0.1 and regardless of
# the port Vite lands on. `cors_allow_all` opens it to any origin (dev only;
# credentials are disabled, as the spec requires with a wildcard origin).
_LOOPBACK_ORIGIN_RE = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"


def _cors_origins() -> list[str]:
    return [origin.strip() for origin in settings.frontend_url.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_allow_all else _cors_origins(),
    allow_origin_regex=None if settings.cors_allow_all else _LOOPBACK_ORIGIN_RE,
    allow_credentials=not settings.cors_allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(config.root_router)
app.include_router(data_sources.router)
app.include_router(datasets.router)
app.include_router(fs.router)
app.include_router(jobs.router)
app.include_router(regions.router)
app.include_router(settings_router.router)


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
# from `web/build/`. When the directory is absent (e.g. in Pixi dev mode),
# the mount is skipped and the Vite dev server on :5173 serves the UI instead.
class _SPAStaticFiles(StaticFiles):
    """Serve a SvelteKit static build with SPA-style fallback to index.html."""

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                # SPA client-side routing: serve index.html for unknown paths.
                response = FileResponse(self.directory / "index.html")
            else:
                raise
        if path in ("", "index.html") or response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-store"
        return response


if _STATIC_DIR.exists():
    app.mount("/", _SPAStaticFiles(directory=_STATIC_DIR, html=True), name="spa")
else:
    logger.info(
        "static SPA bundle not found at %s; serve the frontend separately "
        "(e.g. `pixi run frontend`)",
        _STATIC_DIR,
    )
