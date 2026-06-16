"""Per-request submitter attribution (replaces the old Globus auth module).

ai-almanac has no built-in authentication. For local installs the submitter is
always `"local"`. For public deployments behind a reverse proxy doing OIDC, the
proxy forwards the authenticated user's identity in a configurable header
(default `X-Forwarded-User`); the value is recorded on jobs and datasets for
attribution only — the app does not enforce anything.

This module exposes `CurrentUser` as a FastAPI dependency, preserving the
shape (`dict` with an `id` field) the existing routers and SQL queries expect.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ai_almanac.server.db import get_db, get_or_create_user
from ai_almanac.settings import settings

_LOCAL_USER = "local"


async def current_user(request: Request) -> dict:
    submitter = (
        request.headers.get(settings.submitted_by_header)
        or _LOCAL_USER
    ).strip() or _LOCAL_USER

    async with get_db() as conn:
        return await get_or_create_user(conn, external_id=submitter, email=None)


CurrentUser = Annotated[dict, Depends(current_user)]
