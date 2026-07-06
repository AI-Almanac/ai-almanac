"""FastAPI application factory.

The web server is single-process and single-port: it serves both the JSON API
and the bundled SvelteKit SPA from `/`. The SPA bundle is populated into
`ai_almanac/server/static/` at wheel-build time by the hatch `force-include`
directive in pyproject.toml.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_almanac.paths import ensure_layout
from ai_almanac.server.routers import (
    auth,
    blends,
    chat,
    config,
    data_sources,
    datasets,
    forecasts,
    fs,
    jobs,
    llm_profiles,
    regions,
    tiles,
    uploads,
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
_STATIC_DIR = _SOURCE_STATIC_DIR if _SOURCE_STATIC_DIR.exists() else _PACKAGED_STATIC_DIR


def _apply_migrations() -> None:
    """Run alembic to `head` so a local install never has to run it manually."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    command.upgrade(cfg, "head")


def _should_auto_migrate() -> bool:
    """Local/personal installs migrate on startup for zero-setup launch. Shared
    and opted-out deployments run migrations as a dedicated step (the `migrate`
    compose service / the Cloud Run migration job), so request-serving instances
    never migrate on cold start or race one another."""
    return settings.deployment_mode == "personal" and settings.auto_migrate


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_layout()
    _reload_user_config()
    _enforce_deployment()
    await _wait_for_database()
    if _should_auto_migrate():
        _apply_migrations()
    # Re-layer settings now that the database (the persistent `app_config`
    # overlay) is reachable; the call at line ~75 only saw config.yaml + env.
    _reload_user_config()
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
    """Layer config.yaml + the database overlay onto the settings singleton."""
    from ai_almanac.settings import reload_settings

    reload_settings()


async def _wait_for_database() -> None:
    """Fail startup (and exit nonzero) if the database never becomes reachable,
    so a process supervisor or Compose restart policy can retry."""
    from ai_almanac.server.db import wait_for_database

    await wait_for_database()


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


# Last failure message per background step, used to record each distinct
# failure (and the recovery) once in the audit log instead of every 5 seconds.
_background_failures: dict[str, str] = {}


async def _record_background_event(event_type: str, metadata: dict | None = None) -> None:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services.events import audit

    try:
        async with get_db() as conn:
            await audit(conn, event_type, metadata=metadata)
    except Exception:  # noqa: BLE001 — audit is best-effort
        pass


async def _run_background_step(name: str, step) -> None:
    """Run one maintenance step, surfacing failures where an operator sees them."""
    try:
        await step()
    except Exception as e:  # noqa: BLE001 — non-fatal: keep serving
        message = str(e)
        logger.warning("%s failed: %s", name, message)
        if _background_failures.get(name) != message:
            _background_failures[name] = message
            await _record_background_event(
                f"background.{name}.failed", metadata={"error": message}
            )
        return
    if _background_failures.pop(name, None) is not None:
        await _record_background_event(f"background.{name}.recovered")


async def _cleanup_uploads() -> None:
    from ai_almanac.server.routers.uploads import cleanup_expired_uploads

    cleaned = await cleanup_expired_uploads()
    if cleaned:
        logger.info("expired %d abandoned upload(s)", cleaned)


async def _reconcile_jobs() -> None:
    from ai_almanac.server.services.artifacts import publish_pending
    from ai_almanac.server.services.job_manager import reconcile_jobs

    await _run_background_step("job_reconciliation", reconcile_jobs)
    await _run_background_step("artifact_publication", publish_pending)
    await _run_background_step("upload_cleanup", _cleanup_uploads)


async def _job_reconciler_loop() -> None:
    while True:
        await asyncio.sleep(5)
        await _reconcile_jobs()


app = FastAPI(title="ai-almanac", lifespan=lifespan)

tiles.add_exception_handlers(app)


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


@app.middleware("http")
async def enforce_cookie_csrf(request: Request, call_next):
    if (
        settings.deployment_mode == "shared"
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.headers.get("cookie")
    ):
        allowed_origins = set(_cors_origins())
        origin = request.headers.get("origin")
        if not origin or origin not in allowed_origins:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
    return await call_next(request)


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


def _storage_ready() -> bool:
    if settings.storage_backend.lower() == "gcs":
        return all(
            (
                settings.gcs_data_bucket.strip(),
                settings.gcs_uploads_bucket.strip(),
                settings.gcs_outputs_bucket.strip(),
            )
        )
    data_dir = Path(settings.upload_dir)
    return data_dir.exists() and os.access(data_dir, os.W_OK)


def _auth_ready() -> bool:
    if settings.deployment_mode != "shared":
        return True
    if not settings.credential_encryption_key:
        return False
    if settings.auth_mode == "proxy":
        return bool(settings.allowed_groups)
    if settings.auth_mode == "globus":
        return bool(settings.globus_client_id)
    return False


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_allow_all else _cors_origins(),
    allow_origin_regex=None if settings.cors_allow_all else _LOOPBACK_ORIGIN_RE,
    allow_credentials=not settings.cors_allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Several SvelteKit pages share a name with an API router (/blends, /forecasts,
# /regions, /data-sources, /settings), so a hard refresh on one of those pages
# is a real GET to this process and would otherwise be dispatched straight to
# the API route, rendering its raw JSON instead of the SPA. A browser
# top-level navigation is distinguishable from the SPA's own same-origin
# fetch() calls (which never send `Sec-Fetch-Dest: document` and default to
# `Accept: */*`), so route those to the SPA shell before the API routers ever
# see them.
_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _is_page_navigation(request: Request) -> bool:
    if request.method != "GET" or request.url.path in _DOC_PATHS:
        return False
    if request.headers.get("sec-fetch-dest") == "document":
        return True
    return request.headers.get("accept", "").startswith("text/html")


@app.middleware("http")
async def _spa_navigation_fallback(request: Request, call_next):
    if _STATIC_DIR.exists() and _is_page_navigation(request):
        response = FileResponse(_STATIC_DIR / "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response
    return await call_next(request)


app.include_router(auth.router)
app.include_router(blends.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(config.root_router)
app.include_router(data_sources.router)
app.include_router(datasets.router)
app.include_router(forecasts.router)
app.include_router(fs.router)
app.include_router(jobs.router)
app.include_router(llm_profiles.router)
app.include_router(regions.router)
app.include_router(settings_router.router)
app.include_router(tiles.router, prefix="/cog", tags=["COG tiles"])
app.include_router(uploads.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    from sqlalchemy import text

    from ai_almanac.server.db import get_db
    from ai_almanac.server.services.runner_registry import get_job_runner

    checks: dict[str, bool] = {}
    try:
        async with get_db() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    checks["storage"] = _storage_ready()
    checks["runner"] = bool(get_job_runner().name)
    checks["auth"] = _auth_ready()
    return JSONResponse(
        {"status": "ready" if all(checks.values()) else "not_ready", "checks": checks},
        status_code=200 if all(checks.values()) else 503,
    )


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
