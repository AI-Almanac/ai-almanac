"""FastAPI application factory.

The web server is single-process and single-port: it serves both the JSON API
and the bundled SvelteKit SPA from `/`. The SPA bundle is populated into
`ai_almanac/server/static/` at wheel-build time by the hatch `force-include`
directive in pyproject.toml.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_almanac.paths import ensure_layout
from ai_almanac.server.routers import (
    assistant,
    auth,
    blends,
    chat,
    config,
    data_sources,
    datasets,
    feedback,
    forecasts,
    fs,
    jobs,
    llm_profiles,
    regions,
    tiles,
)
from ai_almanac.server.routers import (
    settings as settings_router,
)
from ai_almanac.server.routers import (
    setup as setup_router,
)
from ai_almanac.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_PACKAGED_STATIC_DIR = Path(__file__).parent / "static"
_SOURCE_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "build"


def _has_spa_bundle(directory: Path) -> bool:
    """Probe for the entrypoint, not just the directory.

    `web/build/` is tracked (via .gitkeep) so that hatchling's force-include can
    resolve on a fresh clone, which means the directory exists before anything has
    been built. Testing the directory alone would treat an empty one as a valid
    bundle: it would shadow the packaged static dir in a wheel install, and make
    the navigation fallback below raise on a missing index.html.
    """
    return (directory / "index.html").is_file()


_STATIC_DIR = _SOURCE_STATIC_DIR if _has_spa_bundle(_SOURCE_STATIC_DIR) else _PACKAGED_STATIC_DIR
_STATIC_READY = _has_spa_bundle(_STATIC_DIR)


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


def _bootstrap_local_secrets() -> None:
    """Generate missing secrets for personal-mode installs; reload if written."""
    if settings.deployment_mode != "personal":
        return
    from ai_almanac.secrets_bootstrap import ensure_local_secrets
    from ai_almanac.settings import reload_settings

    if ensure_local_secrets():
        reload_settings()


def _grandfather_install() -> bool:
    """Grandfather pre-wizard installs; returns True when settings need reload."""
    from ai_almanac.server.services.setup import grandfather_existing_install

    return grandfather_existing_install()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_layout()
    _reload_user_config()
    _bootstrap_local_secrets()
    _enforce_deployment()
    await _wait_for_database()
    if _should_auto_migrate():
        _apply_migrations()
    # Re-layer settings now that the database (the persistent `app_config`
    # overlay) is reachable; the call at line ~75 only saw config.yaml + env.
    _reload_user_config()
    # Mark existing installs that predate the wizard as already complete.
    if _grandfather_install():
        _reload_user_config()
    await _seed_regions()
    await _seed_assistant_rulesets()
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


async def _seed_regions() -> None:
    from ai_almanac.server.services.region_catalog import seed_packaged_regions

    try:
        count = await seed_packaged_regions()
        if count:
            logger.info("seeded %d packaged region(s)", count)
    except Exception as e:  # noqa: BLE001
        logger.warning("region seeding failed: %s", e)


async def _seed_assistant_rulesets() -> None:
    """Refresh the packaged assistant rulesets and pick an active one.

    Non-fatal: ``rulesets.active_ruleset`` falls back to the packaged built-in,
    so a seeding failure degrades to the behaviour the deployment shipped with
    rather than taking chat down.
    """
    from ai_almanac.server.services.rulesets import seed_packaged_rulesets

    try:
        await seed_packaged_rulesets()
    except Exception as e:  # noqa: BLE001
        logger.warning("assistant ruleset seeding failed: %s", e)


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
            await _record_background_event(f"background.{name}.failed", metadata={"error": message})
        return
    if _background_failures.pop(name, None) is not None:
        await _record_background_event(f"background.{name}.recovered")


async def _reconcile_jobs() -> None:
    from ai_almanac.server.services.artifacts import publish_pending
    from ai_almanac.server.services.job_manager import reconcile_jobs

    await _run_background_step("job_reconciliation", reconcile_jobs)
    await _run_background_step("artifact_publication", publish_pending)


async def _job_reconciler_loop() -> None:
    while True:
        await asyncio.sleep(5)
        await _reconcile_jobs()


app = FastAPI(title="ai-almanac", lifespan=lifespan)

tiles.add_exception_handlers(app)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Per-request correlation ID: echoed to the client (recorded in the SPA's
    # feedback breadcrumbs) and logged here, so a feedback report's API trail
    # can be matched to server logs.
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s %d %.1fms rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
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
    upload_dir = Path(settings.upload_dir)
    outputs_dir = Path(settings.job_outputs_dir)
    return (
        upload_dir.exists()
        and os.access(upload_dir, os.W_OK)
        and outputs_dir.exists()
        and os.access(outputs_dir, os.W_OK)
    )


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
    # Let the cross-origin dev SPA read the correlation ID for breadcrumbs.
    expose_headers=["X-Request-ID"],
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
    if _STATIC_READY and _is_page_navigation(request):
        response = FileResponse(_STATIC_DIR / "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response
    return await call_next(request)


_TOKEN_COOKIE = "almanac_token"
_TOKEN_MAX_AGE = 2592000  # 30 days


@app.middleware("http")
async def enforce_access_token(request: Request, call_next):
    """Bearer-token gate for personal installs on shared hosts.

    No-op when serve_access_token is empty. When set:
    - /health is exempt (liveness probes must not require a token).
    - Accepts cookie almanac_token, Authorization: Bearer <t>, or ?token= on GETs.
    - ?token= on a GET → 303 redirect stripping the param + Set-Cookie (HttpOnly,
      SameSite=Lax). The token never appears in logs (log_requests logs path only).
    - All comparisons use hmac.compare_digest to prevent timing attacks.

    DNS-rebinding: loopback binding + SameSite=Lax is the protection boundary;
    full Host-header checking (Jupyter-style) is out of scope for this feature.
    """
    token = settings.serve_access_token
    if not token:
        return await call_next(request)

    if request.url.path == "/health":
        return await call_next(request)

    # During setup, the /setup page and /api/setup/* use their own bootstrap-token
    # auth; don't apply the serve-token gate so they remain reachable.
    if request.url.path == "/setup" or request.url.path.startswith("/api/setup/"):
        from ai_almanac.server.services.setup import setup_required as _setup_required

        if _setup_required():
            return await call_next(request)

    def _valid(candidate: str | None) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate, token)

    # Cookie
    if _valid(request.cookies.get(_TOKEN_COOKIE)):
        return await call_next(request)

    # Authorization: Bearer
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and _valid(auth_header[7:]):
        return await call_next(request)

    # ?token= on GET only
    if request.method == "GET":
        query_token = request.query_params.get("token")
        if _valid(query_token):
            redirect_url = str(request.url.remove_query_params("token"))
            response = RedirectResponse(redirect_url, status_code=303)
            response.set_cookie(
                _TOKEN_COOKIE,
                token,
                httponly=True,
                samesite="lax",
                path="/",
                max_age=_TOKEN_MAX_AGE,
            )
            return response

    # Reject
    if _is_page_navigation(request):
        return HTMLResponse(
            "<html><body><p>Access token required — open the URL printed "
            "in the terminal where <code>ai-almanac serve</code> is running.</p></body></html>",
            status_code=401,
        )
    return JSONResponse({"detail": "Access token required"}, status_code=401)


_SETUP_ALLOWLIST_PREFIXES = ("/api/setup/", "/config.js", "/ready", "/health")
_SETUP_EXACT = {"/setup"}


@app.middleware("http")
async def _setup_gate(request: Request, call_next):
    """Redirect all non-setup traffic to the wizard while setup is pending."""
    from ai_almanac.server.services.setup import setup_required

    if not setup_required():
        return await call_next(request)

    path = request.url.path

    # Exact allowlist
    if path in _SETUP_EXACT:
        return await call_next(request)

    # Prefix allowlist
    if any(path.startswith(p) for p in _SETUP_ALLOWLIST_PREFIXES):
        return await call_next(request)

    # Static assets — containment check prevents path traversal
    if request.method == "GET" and _STATIC_READY:
        rel = path.lstrip("/") or "index.html"
        candidate = (_STATIC_DIR / rel).resolve()
        if candidate.is_file() and candidate.is_relative_to(_STATIC_DIR.resolve()):
            return await call_next(request)

    # Navigation: redirect to /setup
    if _is_page_navigation(request):
        return RedirectResponse("/setup", status_code=307)

    return JSONResponse(
        {"detail": "Setup required", "code": "setup_required"},
        status_code=403,
    )


app.include_router(setup_router.router)
app.include_router(assistant.router)
app.include_router(auth.router)
app.include_router(blends.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(config.root_router)
app.include_router(data_sources.router)
app.include_router(datasets.router)
app.include_router(feedback.router)
app.include_router(forecasts.router)
app.include_router(fs.router)
app.include_router(jobs.router)
app.include_router(llm_profiles.router)
app.include_router(regions.router)
app.include_router(settings_router.router)
app.include_router(tiles.router, prefix="/cog", tags=["COG tiles"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    from sqlalchemy import text

    from ai_almanac.server.db import get_db
    from ai_almanac.server.services.runner_registry import get_job_runner
    from ai_almanac.server.services.setup import env_status, setup_required

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
        {
            "status": "ready" if all(checks.values()) else "not_ready",
            "checks": checks,
            "setup_complete": not setup_required(),
            "envs": env_status(),
        },
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


if _STATIC_READY:
    app.mount("/", _SPAStaticFiles(directory=_STATIC_DIR, html=True), name="spa")
else:
    logger.info(
        "static SPA bundle not found at %s; serve the frontend separately "
        "(e.g. `pixi run frontend`)",
        _STATIC_DIR,
    )
