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
    resp = client.get("/health")  # health is exempt; use /ready
    # Actually test a gated route:
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
    assert "SameSite=lax" in cookie.lower()


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
