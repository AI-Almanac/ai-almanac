"""Auth router — identity and capability discovery for the frontend."""

from __future__ import annotations

from fastapi import APIRouter

from ai_almanac.server.auth import CurrentUser
from ai_almanac.settings import settings

router = APIRouter(tags=["auth"])


@router.get("/auth/me")
async def me(user: CurrentUser) -> dict:
    """Return the current identity plus capability flags the UI uses to gate
    admin-only navigation and feature affordances."""
    return {
        "id": user.id,
        "subject": user.subject,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "deployment_mode": settings.deployment_mode,
        "capabilities": {
            "can_admin": user.is_admin,
            "can_browse_fs": settings.enable_fs_browser and user.is_admin,
            "can_run_code": settings.enable_run_code,
        },
    }
