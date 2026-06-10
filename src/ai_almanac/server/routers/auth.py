"""Auth router — identity and capability discovery for the frontend."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

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
        "issuer": user.issuer,
        "email": user.email,
        "display_name": user.display_name,
        "groups": list(user.groups),
        "role": user.role,
        "deployment_mode": settings.deployment_mode,
        "capabilities": {
            "can_admin": user.is_admin,
            "can_browse_fs": settings.enable_fs_browser and user.is_admin,
            "can_run_code": settings.enable_run_code,
            "max_active_jobs": settings.max_active_jobs_per_user,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_stored_upload_bytes": settings.max_stored_upload_bytes_per_user,
            "max_concurrent_llm_requests": settings.max_concurrent_llm_requests_per_user,
            "max_llm_requests_per_minute": settings.max_llm_requests_per_minute,
        },
    }


@router.post("/auth/logout")
async def logout(_user: CurrentUser):
    return RedirectResponse(settings.logout_url, status_code=303)
