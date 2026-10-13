"""Identity parsing, role policies, and shared-mode startup invariants."""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.server.auth import enforce_deployment_invariants
from ai_almanac.settings import settings

# ---------------------------------------------------------------------------
# /auth/me — identity and capabilities


@pytest.mark.asyncio
async def test_runtime_config_exposes_auth_mode_and_feedback_flag(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setenv("FEEDBACK_GITHUB_TOKEN", "test-token")

    response = await client.get("/config.js")

    assert response.status_code == 200
    assert '"authMode": "proxy"' in response.text
    assert '"feedbackEnabled": true' in response.text


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
    body = (await client.get("/auth/me", headers={"X-Forwarded-User": "alice"})).json()
    assert body["subject"] == "alice"
    # Personal mode: the operator owns the box, so they are admin regardless.
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_proxy_non_admin_is_user(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    body = (await client.get("/auth/me", headers={"X-Forwarded-User": "bob"})).json()
    assert body["role"] == "user"
    assert body["capabilities"]["can_admin"] is False


@pytest.mark.parametrize(
    ("admin_subjects", "admin_emails", "headers"),
    [
        ("carol,dave", "", {"X-Forwarded-User": "carol"}),
        (
            "",
            "Boss@Example.com",
            {"X-Forwarded-User": "erin", "X-Forwarded-Email": "boss@example.com"},
        ),
    ],
    ids=["by-subject", "by-email-case-insensitive"],
)
@pytest.mark.asyncio
async def test_auth_me_proxy_admin_allow_list(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    admin_subjects: str,
    admin_emails: str,
    headers: dict[str, str],
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", admin_subjects)
    monkeypatch.setattr(settings, "admin_emails", admin_emails)
    body = (await client.get("/auth/me", headers=headers)).json()
    assert body["role"] == "admin"


# ---------------------------------------------------------------------------
# Shared-mode startup invariants
# ---------------------------------------------------------------------------


def _valid_shared_config(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    config = {
        "deployment_mode": "shared",
        "auth_mode": "globus",
        "database_url": "postgresql+psycopg://u@h/db",
        "admin_subjects": "admin",
        "admin_emails": "",
        "allowed_groups": "",
        "credential_encryption_key": "configured",
        "chat_figure_signing_secret": "prod-secret",
        "storage_backend": "local",
        "dataset_mount_roots": "/srv/data",
        "globus_client_id": "configured",
        "globus_client_secret": "configured",
        **overrides,
    }
    for name, value in config.items():
        monkeypatch.setattr(settings, name, value)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"database_url": ""}, "PostgreSQL"),
        ({"admin_subjects": ""}, "admin"),
        ({"dataset_mount_roots": ""}, "DATASET_MOUNT_ROOTS"),
        ({"globus_client_id": ""}, "GLOBUS_CLIENT_ID"),
        ({"globus_client_secret": ""}, "GLOBUS_CLIENT_SECRET"),
    ],
    ids=["sqlite", "no-admin", "no-mount-roots", "no-globus-id", "no-globus-secret"],
)
def test_shared_deployment_refuses_to_start_when_misconfigured(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], error: str
) -> None:
    _valid_shared_config(monkeypatch, **overrides)
    with pytest.raises(RuntimeError, match=error):
        enforce_deployment_invariants()


@pytest.mark.parametrize(
    "overrides",
    [{}, {"storage_backend": "gcs", "dataset_mount_roots": ""}],
    ids=["globus-without-groups", "gcs-without-mount-roots"],
)
def test_shared_globus_deployment_starts_and_keeps_its_auth_mode(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str]
) -> None:
    _valid_shared_config(monkeypatch, **overrides)
    enforce_deployment_invariants()
    assert settings.auth_mode == "globus"


def test_enforce_shared_hardens_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _valid_shared_config(monkeypatch, auth_mode="none", allowed_groups="users")
    monkeypatch.setattr(settings, "enable_fs_browser", True)
    monkeypatch.setattr(settings, "enable_run_code", True)

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
        await client.get("/auth/me", headers={**base, "X-Forwarded-Issuer": "https://issuer-a"})
    ).json()
    second = (
        await client.get("/auth/me", headers={**base, "X-Forwarded-Issuer": "https://issuer-b"})
    ).json()
    assert first["id"] != second["id"]


# ---------------------------------------------------------------------------
# Admin gating — settings, fs browser, and catalog mutation
# ---------------------------------------------------------------------------

_RANDO = {"X-Forwarded-User": "rando"}


@pytest.mark.parametrize(
    ("auth_mode", "method", "path", "headers", "status"),
    [
        (None, "GET", "/settings", {}, 200),
        ("proxy", "GET", "/settings", {}, 401),
        ("proxy", "GET", "/settings", _RANDO, 403),
        ("proxy", "GET", "/fs/quick-paths", _RANDO, 403),
        ("proxy", "GET", "/regions", _RANDO, 200),
        ("proxy", "DELETE", "/regions/anything", _RANDO, 403),
        # Unowned sources are a uniform 404 so other users' sources aren't
        # discoverable; ownership CRUD lives in test_data_sources.py.
        ("proxy", "DELETE", "/data-sources/anything", _RANDO, 404),
    ],
    ids=[
        "personal-settings",
        "settings-unidentified",
        "settings-non-admin",
        "fs-non-admin",
        "regions-list-non-admin",
        "region-delete-non-admin",
        "data-source-delete-non-admin",
    ],
)
@pytest.mark.asyncio
async def test_role_gates(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: str | None,
    method: str,
    path: str,
    headers: dict[str, str],
    status: int,
) -> None:
    if auth_mode is not None:
        monkeypatch.setattr(settings, "auth_mode", auth_mode)
        monkeypatch.setattr(settings, "admin_subjects", "")
        monkeypatch.setattr(settings, "admin_emails", "")
    response = await client.request(method, path, headers=headers)
    assert response.status_code == status


# ---------------------------------------------------------------------------
# CORS — local dev cross-origin (Vite dev server → API)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://localhost:5173", True),
        ("http://127.0.0.1:5173", True),  # opened via 127.0.0.1 instead of localhost
        ("http://localhost:5174", True),  # Vite fell back to another port
        ("https://evil.example", False),
    ],
)
@pytest.mark.asyncio
async def test_cors_allows_only_loopback_origins(
    client: httpx.AsyncClient, origin: str, allowed: bool
) -> None:
    resp = await client.get("/health", headers={"Origin": origin})
    assert resp.headers.get("access-control-allow-origin") == (origin if allowed else None)


# ---------------------------------------------------------------------------
# Globus auth mode — bearer-token validation (stub introspection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_me_globus_uses_bearer_subject(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No client id configured -> stub introspection treats the token as the sub.
    monkeypatch.setattr(settings, "auth_mode", "globus")
    body = (await client.get("/auth/me", headers={"Authorization": "Bearer alice-token"})).json()
    assert body["subject"] == "alice-token"
    assert body["role"] == "user"


@pytest.mark.asyncio
async def test_auth_me_globus_missing_bearer_is_anonymous(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_mode", "globus")
    resp = await client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["anonymous"] is True
    # A non-bearer Authorization header carries no identity we accept either.
    resp = await client.get("/auth/me", headers={"Authorization": "Basic abc"})
    assert resp.status_code == 200
    assert resp.json()["anonymous"] is True


@pytest.mark.parametrize(
    ("allow_list", "groups"),
    [
        ({"admin_subjects": "u1"}, set()),
        ({"admin_emails": "boss@example.com"}, set()),
        ({"admin_groups": "grp-admins"}, {"grp-admins", "grp-x"}),
    ],
    ids=["by-subject", "by-email", "by-group"],
)
@pytest.mark.asyncio
async def test_auth_me_globus_admin_allow_list(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    allow_list: dict[str, str],
    groups: set[str],
) -> None:
    from ai_almanac.server import auth as auth_module

    monkeypatch.setattr(settings, "auth_mode", "globus")
    for name, value in allow_list.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(
        auth_module,
        "_introspect_globus_token",
        lambda token: {"active": True, "sub": "u1", "email": "boss@example.com"},
    )
    monkeypatch.setattr(auth_module, "_globus_user_groups", lambda token: groups)

    body = (await client.get("/auth/me", headers={"Authorization": "Bearer tok"})).json()

    assert body["role"] == "admin"
    assert body["email"] == "boss@example.com"
    assert body["groups"] == sorted(groups)


def test_globus_pending_membership_does_not_count_as_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import globus_sdk

    from ai_almanac.server import auth as auth_module

    monkeypatch.setattr(settings, "admin_groups", "grp-admins")
    monkeypatch.setattr(settings, "globus_client_id", "cid")
    memberships = [
        {"id": "grp-admins", "status": "invited"},
        {"id": "grp-x", "status": "active"},
    ]

    class FakeAuthClient:
        def __init__(self, *args): ...

        def oauth2_get_dependent_tokens(self, token):
            class Resp:
                by_resource_server = {"groups.api.globus.org": {"access_token": "gt"}}

            return Resp()

    class FakeGroupsClient:
        def __init__(self, authorizer): ...

        def get_my_groups(self, statuses):
            wanted = set(statuses.split(","))
            return [m for m in memberships if m["status"] in wanted]

    monkeypatch.setattr(globus_sdk, "ConfidentialAppAuthClient", FakeAuthClient)
    monkeypatch.setattr(globus_sdk, "GroupsClient", FakeGroupsClient)
    assert auth_module._globus_user_groups("tok") == {"grp-x"}


def test_globus_user_groups_failure_degrades_to_no_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import globus_sdk

    from ai_almanac.server import auth as auth_module

    monkeypatch.setattr(settings, "admin_groups", "grp-admins")
    monkeypatch.setattr(settings, "globus_client_id", "cid")

    def boom(*args):
        raise RuntimeError("groups api down")

    monkeypatch.setattr(globus_sdk, "ConfidentialAppAuthClient", boom)
    assert auth_module._globus_user_groups("tok") == set()


def test_ttl_cache_evicts_lru_at_capacity_and_expires_entries() -> None:
    from ai_almanac.server.auth import _TTLCache

    cache: _TTLCache[str] = _TTLCache(ttl=300.0, max_entries=2)
    cache.set("a", "1")
    cache.set("b", "2")
    assert cache.get("a") == "1"  # refreshes a's recency
    cache.set("c", "3")  # over capacity: b (least recent) is evicted
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"
    assert len(cache) == 2

    expired: _TTLCache[str] = _TTLCache(ttl=-1.0)  # already expired on write
    expired.set("a", "1")
    assert expired.get("a") is None
    assert len(expired) == 0


@pytest.mark.parametrize(("secret", "ready"), [("configured", True), ("", False)])
@pytest.mark.asyncio
async def test_readiness_reports_globus_shared_auth(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, secret: str, ready: bool
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    monkeypatch.setattr(settings, "auth_mode", "globus")
    monkeypatch.setattr(settings, "credential_encryption_key", "configured")
    monkeypatch.setattr(settings, "globus_client_id", "configured")
    monkeypatch.setattr(settings, "globus_client_secret", secret)

    response = await client.get("/ready")

    assert response.json()["checks"]["auth"] is ready
