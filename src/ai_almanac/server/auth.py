"""Request identity and authorization.

Parses an `AuthenticatedUser` from each request and exposes role-based FastAPI
dependencies. Replaces the old attribution shim.

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
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
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
    return bool(email and email.lower() in {e.lower() for e in _split_csv(settings.admin_emails)})


class _TTLCache[T]:
    """A bounded token-keyed cache: per-entry TTL, LRU eviction at capacity.

    The bound matters because keys are bearer tokens: a long-running shared
    deployment sees a new token on every refresh, so an unbounded dict grows
    with login volume forever. 1024 entries comfortably covers the tokens
    active within any TTL window.
    """

    def __init__(self, ttl: float, max_entries: int = 1024) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires = entry
            if time.monotonic() >= expires:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._entries[key] = (value, time.monotonic() + self._ttl)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


_globus_cache: _TTLCache[dict] = _TTLCache(ttl=60.0)


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
    cached = _globus_cache.get(token)
    if cached is not None:
        return cached

    if not settings.globus_client_id:
        result: dict = {"active": True, "sub": token, "email": None}
    else:
        import globus_sdk

        client = globus_sdk.ConfidentialAppAuthClient(
            settings.globus_client_id, settings.globus_client_secret
        )
        result = dict(client.oauth2_token_introspect(token, include="identity_set").data)

    _globus_cache.set(token, result)
    return result


# Memberships change rarely; the TTL also spaces out retries on failure.
_globus_groups_cache: _TTLCache[set[str]] = _TTLCache(ttl=300.0)


def _globus_user_groups(token: str) -> set[str]:
    """Resolve the Globus group ids for the token's user, cached.

    Exchanges the API token for a dependent Groups API token and lists the
    user's memberships. Requires the Groups view scope registered as a
    dependent of the API scope in the Globus app registration. Skipped
    entirely unless ADMIN_GROUPS is configured. Failures resolve to no groups
    (cached like a success so a Groups outage isn't re-queried per request),
    degrading to the subject/email allow-lists rather than failing auth.
    """
    if not _split_csv(settings.admin_groups) or not settings.globus_client_id:
        return set()

    cached = _globus_groups_cache.get(token)
    if cached is not None:
        return cached

    try:
        import globus_sdk

        client = globus_sdk.ConfidentialAppAuthClient(
            settings.globus_client_id, settings.globus_client_secret
        )
        dependent = client.oauth2_get_dependent_tokens(token).by_resource_server
        groups_client = globus_sdk.GroupsClient(
            authorizer=globus_sdk.AccessTokenAuthorizer(
                dependent["groups.api.globus.org"]["access_token"]
            )
        )
        # Active memberships only — an invited/pending member of the admin group
        # must not become admin, and the API default should not decide that.
        groups = {group["id"] for group in groups_client.get_my_groups(statuses="active")}
    except Exception:
        logger.warning("Globus group lookup failed; admin group checks skipped", exc_info=True)
        groups = set()

    _globus_groups_cache.set(token, groups)
    return groups


async def _resolve_globus_token(token: str | None) -> AuthenticatedUser:
    if not token:
        raise _MissingIdentity

    introspection = await asyncio.to_thread(_introspect_globus_token, token)
    subject = introspection.get("sub")
    if not introspection.get("active") or not subject:
        raise _MissingIdentity

    email = introspection.get("email")
    display_name = introspection.get("name") or introspection.get("username")
    # Globus introspection carries identities, not group memberships; those come
    # from a dependent-token Groups API call when admin groups are configured.
    groups = await asyncio.to_thread(_globus_user_groups, token)
    role: Role = "admin" if _is_admin(subject, email, groups) else "user"

    async with get_db() as conn:
        row = await get_or_create_user(
            conn,
            external_id=f"globus\x1f{subject}",
            issuer="globus",
            subject=subject,
            email=email,
            display_name=display_name,
            groups=sorted(groups),
        )
    return AuthenticatedUser(
        id=row["id"],
        subject=subject,
        issuer="globus",
        email=email or row.get("email"),
        display_name=display_name,
        groups=tuple(sorted(groups)),
        role=role,
    )


async def _resolve_globus_identity(headers: Headers) -> AuthenticatedUser:
    return await _resolve_globus_token(_bearer_token(headers))


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
            if settings.deployment_mode == "shared" or headers.get(settings.identity_issuer_header)
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


async def require_data_management() -> None:
    """Gate routes behind the data-management feature flag.

    Used as a route-level dependency so disabled mutations 404 (hiding the
    in-development feature) while their read counterparts stay available.
    """
    if not settings.enable_data_management:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def require_forecasting() -> None:
    """Gate the forecasting feature behind its flag. On by default; admins can
    turn it off (e.g. an install with no GPU/Modal infra) to hide the feature
    from all users."""
    if not settings.enable_forecasting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def require_assistant_comparisons(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
) -> None:
    """Gate ruleset comparisons on the audience setting. "everyone" by default;
    "admins" lets operators test the surface before exposing it, and "off"
    hides it from everyone, so nothing can spend two model replies on a
    comparison an operator has switched off."""
    if not settings.comparisons_allowed(user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]


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
    # Without a client id the introspection stub treats the bearer token as its
    # own subject, so any caller could present an admin subject as their token.
    if settings.auth_mode == "globus" and not settings.globus_client_id:
        raise RuntimeError("shared globus deployment requires GLOBUS_CLIENT_ID")
    if not settings.credential_encryption_key:
        raise RuntimeError("shared deployment requires CREDENTIAL_ENCRYPTION_KEY")
    if settings.chat_figure_signing_secret == "dev-chat-figure-secret":
        raise RuntimeError("shared deployment rejects the development signing secret")
    if not settings.dataset_mount_roots.strip():
        raise RuntimeError(
            "shared deployment requires DATASET_MOUNT_ROOTS; without it admins "
            "could register data sources anywhere on the host filesystem"
        )
    if settings.job_runner == "modal":
        from ai_almanac.server.services.bucket_mounts import outputs_bucket_name

        if not outputs_bucket_name():
            raise RuntimeError(
                "job_runner=modal requires job_outputs_dir to be mapped in "
                "BUCKET_MOUNTS as a bare-bucket gs:// URI (no key prefix)"
            )
    for field in ("enable_fs_browser", "enable_run_code"):
        if getattr(settings, field):
            logger.warning("shared deployment: forcing %s=false", field)
            setattr(settings, field, False)
