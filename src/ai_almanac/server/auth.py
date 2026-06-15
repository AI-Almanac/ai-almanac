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

import asyncio
import logging
import time
from dataclasses import dataclass
from threading import Lock
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
    issuer: str
    email: str | None
    display_name: str | None
    groups: tuple[str, ...]
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class _MissingIdentity(Exception):
    """Raised in proxy mode when the trusted subject header is absent."""


def _split_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _is_admin(subject: str, email: str | None, groups: set[str]) -> bool:
    if subject in _split_csv(settings.admin_subjects):
        return True
    if groups & _split_csv(settings.admin_groups):
        return True
    return bool(
        email and email.lower() in {e.lower() for e in _split_csv(settings.admin_emails)}
    )


_globus_cache: dict[str, tuple[dict, float]] = {}
_globus_cache_lock = Lock()
_GLOBUS_CACHE_TTL = 60.0  # seconds


def _bearer_token(headers: Headers) -> str | None:
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _introspect_globus_token(token: str) -> dict:
    """Validate a Globus access token via introspection, cached briefly.

    With no client id configured, runs in stub mode (the token is treated as the
    subject) so local development works without Globus credentials.
    """
    now = time.monotonic()
    with _globus_cache_lock:
        cached = _globus_cache.get(token)
        if cached and now < cached[1]:
            return cached[0]

    if not settings.globus_client_id:
        result: dict = {"active": True, "sub": token, "email": None}
    else:
        import globus_sdk

        client = globus_sdk.ConfidentialAppAuthClient(
            settings.globus_client_id, settings.globus_client_secret
        )
        result = dict(
            client.oauth2_token_introspect(token, include="identity_set").data
        )

    with _globus_cache_lock:
        _globus_cache[token] = (result, time.monotonic() + _GLOBUS_CACHE_TTL)
    return result


async def _resolve_globus_identity(headers: Headers) -> AuthenticatedUser:
    token = _bearer_token(headers)
    if not token:
        raise _MissingIdentity

    introspection = await asyncio.to_thread(_introspect_globus_token, token)
    subject = introspection.get("sub")
    if not introspection.get("active") or not subject:
        raise _MissingIdentity

    email = introspection.get("email")
    display_name = introspection.get("name") or introspection.get("username")
    # Globus introspection carries identities, not application groups; admit any
    # valid identity and authorize via the admin allow-lists.
    role: Role = "admin" if _is_admin(subject, email, set()) else "user"

    async with get_db() as conn:
        row = await get_or_create_user(
            conn,
            external_id=f"globus\x1f{subject}",
            issuer="globus",
            subject=subject,
            email=email,
            display_name=display_name,
            groups=[],
        )
    return AuthenticatedUser(
        id=row["id"],
        subject=subject,
        issuer="globus",
        email=email or row.get("email"),
        display_name=display_name,
        groups=(),
        role=role,
    )


async def _resolve_identity(headers: Headers) -> AuthenticatedUser:
    if settings.auth_mode == "globus":
        return await _resolve_globus_identity(headers)

    raw_subject = (headers.get(settings.submitted_by_header) or "").strip()
    email = (headers.get(settings.identity_email_header) or "").strip() or None
    display_name = (headers.get(settings.identity_name_header) or "").strip() or None
    issuer = (headers.get(settings.identity_issuer_header) or "").strip()
    groups = _split_csv(headers.get(settings.identity_groups_header) or "")

    if settings.auth_mode == "proxy":
        if not raw_subject:
            raise _MissingIdentity
        if not issuer:
            if settings.deployment_mode == "shared":
                raise _MissingIdentity
            issuer = "legacy-proxy"
        allowed_groups = _split_csv(settings.allowed_groups)
        if allowed_groups and not groups & allowed_groups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your identity is not admitted to this deployment",
            )
        subject = raw_subject
        external_id = (
            f"{issuer}\x1f{subject}"
            if settings.deployment_mode == "shared"
            or headers.get(settings.identity_issuer_header)
            else subject
        )
        role: Role = "admin" if _is_admin(subject, email, groups) else "user"
    else:
        # Personal install: header identity is optional (attribution only); the
        # operator owns the machine, so they are always admin.
        subject = raw_subject or _LOCAL_SUBJECT
        issuer = "personal"
        external_id = subject
        role = "admin"

    async with get_db() as conn:
        row = await get_or_create_user(
            conn,
            external_id=external_id,
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            groups=sorted(groups),
        )
    return AuthenticatedUser(
        id=row["id"],
        subject=subject,
        issuer=issuer,
        email=email or row.get("email"),
        display_name=display_name,
        groups=tuple(sorted(groups)),
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
        if settings.deployment_mode == "shared":
            origin = websocket.headers.get("origin")
            allowed = _split_csv(settings.frontend_url)
            if not origin or origin not in allowed:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return None
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

    # Shared mode must authenticate. Keep an explicit `globus`, otherwise default
    # to proxy-header auth.
    if settings.auth_mode not in ("proxy", "globus"):
        settings.auth_mode = "proxy"

    if settings.resolve_database_url().startswith("sqlite"):
        raise RuntimeError(
            "shared deployment requires PostgreSQL; set DATABASE_URL "
            "(SQLite is not allowed in shared mode)"
        )
    if not any(
        (
            _split_csv(settings.admin_subjects),
            _split_csv(settings.admin_emails),
            _split_csv(settings.admin_groups),
        )
    ):
        raise RuntimeError(
            "shared deployment requires at least one admin; "
            "set ADMIN_SUBJECTS, ADMIN_EMAILS, or ADMIN_GROUPS"
        )
    # Proxy mode gates admission by group; globus mode admits any valid identity
    # and authorizes via the admin allow-lists, so it needs no group list.
    if settings.auth_mode == "proxy" and not _split_csv(settings.allowed_groups):
        raise RuntimeError("shared proxy deployment requires ALLOWED_GROUPS")
    if not settings.credential_encryption_key:
        raise RuntimeError("shared deployment requires CREDENTIAL_ENCRYPTION_KEY")
    if settings.chat_figure_signing_secret == "dev-chat-figure-secret":
        raise RuntimeError("shared deployment rejects the development signing secret")
    # Mount roots only constrain the local filesystem resolver; GCS sources are
    # gs:// URIs validated against the bucket, so the list is irrelevant there.
    if settings.storage_backend == "local" and not settings.dataset_mount_roots.strip():
        raise RuntimeError(
            "shared deployment requires DATASET_MOUNT_ROOTS; without it admins "
            "could register data sources anywhere on the host filesystem"
        )
    for field in ("enable_fs_browser", "enable_run_code"):
        if getattr(settings, field):
            logger.warning("shared deployment: forcing %s=false", field)
            setattr(settings, field, False)
