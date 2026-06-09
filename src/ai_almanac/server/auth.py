"""Request identity and authorization.

Parses an `AuthenticatedUser` from each request (or WebSocket handshake) and
exposes role-based FastAPI dependencies. Replaces the old attribution shim.

Two deployment modes (see `settings.deployment_mode` / `settings.auth_mode`):

- `none` (personal installs): single operator. The subject comes from the proxy
  header if present (so attribution still works behind a proxy), otherwise the
  implicit `local` user. The operator owns the box, so the role is always
  `admin`.
- `proxy` (shared deployments): identity is taken from trusted reverse-proxy
  headers set by oauth2-proxy. A missing subject header is rejected — this
  blocks direct-to-app access that bypasses the proxy. The role is `admin` iff
  the subject or email is in the configured admin allow-lists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, WebSocket, status
from starlette.datastructures import Headers

from ai_almanac.server.db import get_db, get_or_create_user
from ai_almanac.settings import settings

logger = logging.getLogger(__name__)

Role = Literal["admin", "user"]
_LOCAL_SUBJECT = "local"


@dataclass(frozen=True)
class AuthenticatedUser:
    """Parsed request identity. Trustworthy once constructed."""

    id: str  # internal users.id (uuid)
    subject: str  # stable OIDC `sub` / "local"
    email: str | None
    display_name: str | None
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class _MissingIdentity(Exception):
    """Raised in proxy mode when the trusted subject header is absent."""


def _split_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _is_admin(subject: str, email: str | None) -> bool:
    if subject in _split_csv(settings.admin_subjects):
        return True
    return bool(
        email and email.lower() in {e.lower() for e in _split_csv(settings.admin_emails)}
    )


async def _resolve_identity(headers: Headers) -> AuthenticatedUser:
    raw_subject = (headers.get(settings.submitted_by_header) or "").strip()
    email = (headers.get(settings.identity_email_header) or "").strip() or None
    display_name = (headers.get(settings.identity_name_header) or "").strip() or None

    if settings.auth_mode == "proxy":
        if not raw_subject:
            raise _MissingIdentity
        subject = raw_subject
        role: Role = "admin" if _is_admin(subject, email) else "user"
    else:
        # Personal install: header identity is optional (attribution only); the
        # operator owns the machine, so they are always admin.
        subject = raw_subject or _LOCAL_SUBJECT
        role = "admin"

    async with get_db() as conn:
        row = await get_or_create_user(conn, external_id=subject, email=email)
    return AuthenticatedUser(
        id=row["id"],
        subject=subject,
        email=email or row.get("email"),
        display_name=display_name,
        role=role,
    )


async def require_user(request: Request) -> AuthenticatedUser:
    try:
        return await _resolve_identity(request.headers)
    except _MissingIdentity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        ) from None


async def require_admin(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
) -> AuthenticatedUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]


async def authenticate_websocket(websocket: WebSocket) -> AuthenticatedUser | None:
    """Resolve identity for a WebSocket handshake.

    Returns the user, or closes the handshake (policy violation) and returns
    None when identity is required but absent. The caller must stop on None.
    """
    try:
        return await _resolve_identity(websocket.headers)
    except _MissingIdentity:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None


def enforce_deployment_invariants() -> None:
    """Validate and harden configuration for the active deployment mode.

    Called once on startup, after config.yaml has been layered onto settings.
    Personal mode is permissive; shared mode fails fast on unsafe config.
    """
    if settings.deployment_mode != "shared":
        return

    settings.auth_mode = "proxy"

    if settings.resolve_database_url().startswith("sqlite"):
        raise RuntimeError(
            "shared deployment requires PostgreSQL; set DATABASE_URL "
            "(SQLite is not allowed in shared mode)"
        )
    if not _split_csv(settings.admin_subjects) and not _split_csv(settings.admin_emails):
        raise RuntimeError(
            "shared deployment requires at least one admin; "
            "set ADMIN_SUBJECTS or ADMIN_EMAILS"
        )
    for field in ("enable_fs_browser", "enable_run_code"):
        if getattr(settings, field):
            logger.warning("shared deployment: forcing %s=false", field)
            setattr(settings, field, False)
