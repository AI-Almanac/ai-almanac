"""Auth router — identity and capability discovery for the frontend."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from ai_almanac.server.auth import CurrentUser, OptionalCurrentUser
from ai_almanac.settings import settings

router = APIRouter(tags=["auth"])


@router.get("/auth/me")
async def me(user: OptionalCurrentUser) -> dict:
    """Return the current identity plus capability flags the UI uses to gate
    admin-only navigation and feature affordances. Anonymous visitors get a
    null identity with read-only capabilities so the public pages render."""
    if user is None:
        return {
            "anonymous": True,
            "id": None,
            "subject": None,
            "issuer": None,
            "email": None,
            "display_name": None,
            "groups": [],
            "role": "user",
            "deployment_mode": settings.deployment_mode,
            "capabilities": {
                "can_admin": False,
                "can_browse_fs": False,
                "can_manage_data": False,
                # Real flag, not False: the /forecasts page redirects away when
                # forecasting is off, and anonymous visitors may view examples.
                "can_use_forecasting": settings.enable_forecasting,
                "can_run_code": False,
                "max_active_jobs": 0,
                "max_concurrent_llm_requests": 0,
                "max_llm_requests_per_minute": 0,
            },
        }
    return {
        "anonymous": False,
        "id": user.id,
        "subject": user.subject,
        "issuer": user.issuer,
        "email": user.email,
        "display_name": user.display_name,
        "groups": list(user.groups),
        "role": user.role,
        "deployment_mode": settings.deployment_mode,
        "capabilities": {
            "can_admin": user.is_admin,
            "can_browse_fs": settings.enable_fs_browser and user.is_admin,
            "can_manage_data": settings.enable_data_management,
            "can_use_forecasting": settings.enable_forecasting,
            "can_run_code": settings.enable_run_code,
            "max_active_jobs": settings.max_active_jobs_per_user,
            "max_concurrent_llm_requests": settings.max_concurrent_llm_requests_per_user,
            "max_llm_requests_per_minute": settings.max_llm_requests_per_minute,
        },
    }


@router.post("/auth/logout")
async def logout(_user: CurrentUser):
    return RedirectResponse(settings.logout_url, status_code=303)
