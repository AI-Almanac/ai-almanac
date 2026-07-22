"""Config endpoints — metric defs, ROMP defaults, runtime SPA config."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.routers.feedback import feedback_enabled
from ai_almanac.settings import get_metric_definitions, get_romp_defaults, settings

router = APIRouter(prefix="/config", tags=["config"])


def _app_version() -> str:
    """Package version, suffixed with a build SHA when the build provides one
    (`ALMANAC_BUILD_SHA`), so feedback reports can pinpoint the exact build."""
    import os

    from ai_almanac import __version__

    sha = os.environ.get("ALMANAC_BUILD_SHA", "").strip()
    return f"{__version__}+{sha[:12]}" if sha else __version__


@router.get("/metrics")
def list_metrics() -> list[dict]:
    return get_metric_definitions()


@router.get("/romp-defaults")
def romp_defaults() -> dict:
    return get_romp_defaults()


@router.get("/capabilities")
async def capabilities(user: CurrentUser) -> dict[str, bool]:
    from ai_almanac.server.services.llm import llm_is_configured
    from ai_almanac.server.services.llm_profiles import chat_available_for_user

    if settings.deployment_mode == "shared":
        chat = await chat_available_for_user(user.id)
    else:
        chat = llm_is_configured()
    return {"chat": chat}


# Mounted at the root path (not under /config/) so the SPA's `<script
# src="/config.js">` tag in `app.html` resolves without prefix juggling.
root_router = APIRouter(tags=["config"])


@root_router.get("/config.js", include_in_schema=False)
def runtime_spa_config() -> Response:
    """Runtime config the SvelteKit SPA reads at boot.

    Exposed as JS so it loads synchronously before the app bundle. A `JSON.parse`
    payload would technically be valid but a global assignment is the simplest
    contract for the frontend.
    """
    payload = {
        "apiUrl": "",  # same-origin by default
        "authMode": settings.auth_mode,
        "submittedByEnabled": bool(settings.submitted_by_header),
        "submittedByHeader": settings.submitted_by_header,
        "version": _app_version(),
        "feedbackEnabled": feedback_enabled(),
    }
    body = f"window.__ALMANAC_CONFIG__ = {json.dumps(payload)};\n"
    return Response(
        content=body,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )
