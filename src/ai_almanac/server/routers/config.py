"""Config endpoints — metric defs, ROMP defaults, runtime SPA config."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response

from ai_almanac.settings import get_metric_definitions, get_romp_defaults, settings

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/metrics")
def list_metrics() -> list[dict]:
    return get_metric_definitions()


@router.get("/romp-defaults")
def romp_defaults() -> dict:
    return get_romp_defaults()


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
        "submittedByEnabled": bool(settings.submitted_by_header),
        "submittedByHeader": settings.submitted_by_header,
    }
    body = f"window.__ALMANAC_CONFIG__ = {json.dumps(payload)};\n"
    return Response(
        content=body,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )
