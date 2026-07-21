"""Phase 2 — identity parsing and role policies."""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.server.auth import enforce_deployment_invariants
from ai_almanac.settings import settings

# ---------------------------------------------------------------------------
# /auth/me — identity and capabilities


@pytest.mark.asyncio
async def test_runtime_config_exposes_auth_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")

    response = await client.get("/config.js")

    assert response.status_code == 200
    assert '"authMode": "proxy"' in response.text


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_me_personal_defaults_to_local_admin(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/auth/me")).json()
    assert body["subject"] == "local"
    assert body["role"] == "admin"
    assert body["deployment_mode"] == "personal"
    assert body["capabilities"]["can_admin"] is True


@pytest.mark.asyncio
async def test_auth_me_personal_honors_proxy_header(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.get("/auth/me", headers={"X-Forwarded-User": "alice"})
    ).json()
    assert body["subject"] == "alice"
    # Personal mode: the operator owns the box, so they are admin regardless.
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_proxy_rejects_missing_identity(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_proxy_non_admin_is_user(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    body = (
        await client.get("/auth/me", headers={"X-Forwarded-User": "bob"})
    ).json()
    assert body["role"] == "user"
    assert body["capabilities"]["can_admin"] is False


@pytest.mark.asyncio
async def test_auth_me_proxy_admin_by_subject(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "carol,dave")
    body = (
        await client.get("/auth/me", headers={"X-Forwarded-User": "carol"})
    ).json()
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_proxy_admin_by_email_case_insensitive(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_emails", "Boss@Example.com")
    body = (
        await client.get(
            "/auth/me",
            headers={
                "X-Forwarded-User": "erin",
                "X-Forwarded-Email": "boss@example.com",
            },
        )
    ).json()
    assert body["role"] == "admin"


# ---------------------------------------------------------------------------
# Shared-mode startup invariants
# ---------------------------------------------------------------------------


def test_enforce_personal_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    enforce_deployment_invariants()  # must not raise


def test_enforce_shared_rejects_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "none")  # enforce mutates this
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "database_url", "")  # resolves to SQLite
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        enforce_deployment_invariants()


def test_enforce_shared_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "none")  # enforce mutates this
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    with pytest.raises(RuntimeError, match="admin"):
        enforce_deployment_invariants()


def test_enforce_shared_requires_mount_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "allowed_groups", "users")
    monkeypatch.setattr(settings, "credential_encryption_key", "configured")
    monkeypatch.setattr(settings, "chat_figure_signing_secret", "configured")
    monkeypatch.setattr(settings, "dataset_mount_roots", "")
    with pytest.raises(RuntimeError, match="DATASET_MOUNT_ROOTS"):
        enforce_deployment_invariants()


def test_enforce_shared_globus_requires_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "credential_encryption_key", "configured")
    monkeypatch.setattr(settings, "chat_figure_signing_secret", "configured")
    monkeypatch.setattr(settings, "dataset_mount_roots", "/srv/data")
    monkeypatch.setattr(settings, "globus_client_id", "")
    with pytest.raises(RuntimeError, match="GLOBUS_CLIENT_ID"):
        enforce_deployment_invariants()


def test_enforce_shared_hardens_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "enable_fs_browser", True)
    monkeypatch.setattr(settings, "enable_run_code", True)
    monkeypatch.setattr(settings, "allowed_groups", "users")
    monkeypatch.setattr(settings, "credential_encryption_key", "configured")
    monkeypatch.setattr(settings, "chat_figure_signing_secret", "configured")
    monkeypatch.setattr(settings, "dataset_mount_roots", "/srv/data")

    enforce_deployment_invariants()

    assert settings.auth_mode == "proxy"
    assert settings.enable_fs_browser is False
    assert settings.enable_run_code is False


@pytest.mark.asyncio
async def test_proxy_rejects_user_outside_allowed_groups(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "allowed_groups", "researchers")
    response = await client.get(
        "/auth/me",
        headers={
            "X-Forwarded-User": "outside",
            "X-Forwarded-Groups": "other",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_proxy_uses_issuer_and_subject_as_stable_identity(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "allowed_groups", "researchers")
    base = {
        "X-Forwarded-User": "same-subject",
        "X-Forwarded-Groups": "researchers",
    }
    first = (
        await client.get(
            "/auth/me", headers={**base, "X-Forwarded-Issuer": "https://issuer-a"}
        )
    ).json()
    second = (
        await client.get(
            "/auth/me", headers={**base, "X-Forwarded-Issuer": "https://issuer-b"}
        )
    ).json()
    assert first["id"] != second["id"]


# ---------------------------------------------------------------------------
# Admin gating — settings, fs browser, and catalog mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_open_to_admin_in_personal(client: httpx.AsyncClient) -> None:
    # Personal mode: the local operator is admin, so settings stay accessible.
    assert (await client.get("/settings")).status_code == 200


@pytest.mark.asyncio
async def test_settings_rejects_missing_identity_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    assert (await client.get("/settings")).status_code == 401


@pytest.mark.asyncio
async def test_settings_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    resp = await client.get("/settings", headers={"X-Forwarded-User": "rando"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_fs_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.get("/fs/quick-paths", headers={"X-Forwarded-User": "rando"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_region_list_allowed_for_non_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.get("/regions", headers={"X-Forwarded-User": "rando"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_region_delete_requires_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.delete(
        "/regions/anything", headers={"X-Forwarded-User": "rando"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_data_source_delete_hides_unowned_sources(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-admins can only delete sources they own; anything else is a uniform
    404 so other users' sources aren't discoverable. Ownership CRUD semantics
    are covered in test_data_sources.py."""
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    resp = await client.delete(
        "/data-sources/anything", headers={"X-Forwarded-User": "rando"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CORS — local dev cross-origin (Vite dev server → API)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",  # opened via 127.0.0.1 instead of localhost
        "http://localhost:5174",  # Vite fell back to another port
    ],
)
@pytest.mark.asyncio
async def test_cors_allows_loopback_origins(
    client: httpx.AsyncClient, origin: str
) -> None:
    resp = await client.get("/health", headers={"Origin": origin})
    assert resp.headers.get("access-control-allow-origin") == origin


@pytest.mark.asyncio
async def test_cors_rejects_foreign_origin(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.headers.get("access-control-allow-origin") is None


# ---------------------------------------------------------------------------
# Globus auth mode — bearer-token validation (stub introspection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_me_globus_uses_bearer_subject(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No client id configured -> stub introspection treats the token as the sub.
    monkeypatch.setattr(settings, "auth_mode", "globus")
    body = (
        await client.get("/auth/me", headers={"Authorization": "Bearer alice-token"})
    ).json()
    assert body["subject"] == "alice-token"
    assert body["role"] == "user"


@pytest.mark.asyncio
async def test_auth_me_globus_rejects_missing_bearer(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "globus")
    assert (await client.get("/auth/me")).status_code == 401
    # A non-bearer Authorization header is also rejected.
    resp = await client.get("/auth/me", headers={"Authorization": "Basic abc"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_globus_admin_by_subject(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "admin_subjects", "admin-sub")
    body = (
        await client.get("/auth/me", headers={"Authorization": "Bearer admin-sub"})
    ).json()
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_globus_admin_by_email_from_introspection(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server import auth as auth_module

    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")
    monkeypatch.setattr(
        auth_module,
        "_introspect_globus_token",
        lambda token: {"active": True, "sub": "u1", "email": "boss@example.com"},
    )
    body = (
        await client.get("/auth/me", headers={"Authorization": "Bearer tok"})
    ).json()
    assert body["role"] == "admin"
    assert body["email"] == "boss@example.com"


@pytest.mark.asyncio
async def test_auth_me_globus_rejects_inactive_token(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server import auth as auth_module

    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(
        auth_module,
        "_introspect_globus_token",
        lambda token: {"active": False},
    )
    assert (
        await client.get("/auth/me", headers={"Authorization": "Bearer dead"})
    ).status_code == 401


def test_enforce_shared_globus_needs_no_groups_and_keeps_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "allowed_groups", "")  # not required for globus
    monkeypatch.setattr(settings, "credential_encryption_key", "k")
    monkeypatch.setattr(settings, "chat_figure_signing_secret", "prod-secret")
    monkeypatch.setattr(settings, "dataset_mount_roots", "/data")
    monkeypatch.setattr(settings, "globus_client_id", "configured")
    enforce_deployment_invariants()  # must not raise
    assert settings.auth_mode == "globus"  # not forced to proxy


def test_enforce_shared_gcs_skips_mount_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr(settings, "admin_subjects", "admin")
    monkeypatch.setattr(settings, "credential_encryption_key", "k")
    monkeypatch.setattr(settings, "chat_figure_signing_secret", "prod-secret")
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    monkeypatch.setattr(settings, "dataset_mount_roots", "")  # irrelevant for gcs
    monkeypatch.setattr(settings, "globus_client_id", "configured")
    enforce_deployment_invariants()  # must not raise


def test_ready_auth_accepts_globus_shared_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.server.app import _auth_ready

    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "credential_encryption_key", "configured")
    monkeypatch.setattr(settings, "globus_client_id", "configured")

    assert _auth_ready() is True


def test_ready_storage_accepts_gcs_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.server.app import _storage_ready

    monkeypatch.setattr(settings, "storage_backend", "gcs")
    monkeypatch.setattr(settings, "gcs_data_bucket", "data")
    monkeypatch.setattr(settings, "gcs_uploads_bucket", "uploads")
    monkeypatch.setattr(settings, "gcs_outputs_bucket", "outputs")

    assert _storage_ready() is True
