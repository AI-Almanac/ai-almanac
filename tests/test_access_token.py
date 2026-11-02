"""Tests for the enforce_access_token middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ai_almanac.server.app import app
from ai_almanac.settings import settings

TOKEN = "test-token-abc123"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "serve_access_token", TOKEN)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def open_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with no token configured — all requests pass through."""
    monkeypatch.setattr(settings, "serve_access_token", "")
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Token present: token absent → 401
# ---------------------------------------------------------------------------


def test_api_without_token_returns_401_json(client: TestClient) -> None:
    resp = client.get("/api/regions", headers={"accept": "application/json"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Access token required"


def test_page_navigation_without_token_returns_401_html(client: TestClient) -> None:
    resp = client.get(
        "/blends",
        headers={"sec-fetch-dest": "document", "accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Access token required" in resp.text
    # Token must not appear in the body (basic check)
    assert TOKEN not in resp.text


def test_health_is_exempt(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Token provided correctly
# ---------------------------------------------------------------------------


def test_bearer_token_grants_access(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    # Test a real gated endpoint with Bearer
    resp = client.get(
        "/api/regions",
        headers={"authorization": f"Bearer {TOKEN}", "accept": "application/json"},
    )
    assert resp.status_code != 401


def test_cookie_grants_access(client: TestClient) -> None:
    resp = client.get(
        "/api/regions",
        headers={"accept": "application/json"},
        cookies={"almanac_token": TOKEN},
    )
    assert resp.status_code != 401


def test_query_token_redirects_and_sets_cookie(client: TestClient) -> None:
    resp = client.get(f"/api/regions?token={TOKEN}", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    assert "token=" not in location
    cookie = resp.headers.get("set-cookie", "")
    assert "almanac_token=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


def test_query_token_not_accepted_on_post(client: TestClient) -> None:
    resp = client.post(f"/api/regions?token={TOKEN}", json={})
    assert resp.status_code == 401


def test_wrong_token_returns_401(client: TestClient) -> None:
    resp = client.get(
        "/api/regions",
        headers={"authorization": "Bearer wrong-token", "accept": "application/json"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Empty setting: everything open
# ---------------------------------------------------------------------------


def test_empty_token_setting_allows_all(open_client: TestClient) -> None:
    resp = open_client.get("/health")
    assert resp.status_code == 200
    resp = open_client.get("/api/regions", headers={"accept": "application/json"})
    # Should not 401 (may still get other errors like 503 if DB not ready, but not 401)
    assert resp.status_code != 401


def test_non_ascii_token_is_rejected_not_an_error(client: TestClient) -> None:
    resp = client.get("/api/regions?token=%C3%A9", headers={"accept": "application/json"})
    assert resp.status_code == 401

    resp = client.get("/api/regions", headers={"authorization": b"Bearer \xe9"})
    assert resp.status_code == 401


@pytest.fixture()
def setup_pending(monkeypatch: pytest.MonkeyPatch) -> str:
    from ai_almanac.server.services.setup import _token_path, get_or_create_bootstrap_token

    monkeypatch.setattr(settings, "deployment_mode", "personal")
    monkeypatch.setattr(settings, "setup_complete", False)
    yield get_or_create_bootstrap_token()
    _token_path().unlink(missing_ok=True)


def test_wizard_loads_and_hands_over_the_cookie_while_setup_is_pending(
    client: TestClient, setup_pending: str
) -> None:
    assert client.get("/config.js").status_code != 401

    resp = client.get(f"/setup?token={setup_pending}", follow_redirects=False)
    assert resp.status_code != 401
    assert resp.cookies.get("almanac_token") == TOKEN


def test_wrong_bootstrap_token_gets_no_access_cookie(
    client: TestClient, setup_pending: str
) -> None:
    resp = client.get("/setup?token=not-the-token", follow_redirects=False)
    assert "almanac_token" not in resp.cookies


def test_non_ascii_bootstrap_token_is_rejected(setup_pending: str) -> None:
    from ai_almanac.server.services.setup import verify_bootstrap_token

    assert verify_bootstrap_token("é") is False
    assert verify_bootstrap_token(setup_pending) is True
