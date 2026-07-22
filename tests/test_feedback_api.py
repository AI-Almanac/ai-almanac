"""Feedback endpoint — GitHub issue forwarding."""

from __future__ import annotations

import httpx
import pytest

class _FakeGitHubClient:
    """Stands in for the router's outbound httpx client."""

    def __init__(self, captured: dict, status: int) -> None:
        self._captured = captured
        self._status = status

    async def __aenter__(self) -> _FakeGitHubClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        self._captured.update({"url": url, "json": json, "headers": headers})
        request = httpx.Request("POST", url)
        if self._status == 201:
            return httpx.Response(
                201,
                json={"html_url": "https://github.com/acme/widgets/issues/7"},
                request=request,
            )
        return httpx.Response(self._status, text="bad", request=request)


_SUBMISSION = {
    "message": "The blend page crashed",
    "category": "bug",
    "page": "/blends",
    "snapshot": {"version": "0.1.0", "route": "/blends"},
    "breadcrumbs": [
        {"ts": 1, "type": "navigation", "message": "→ /blends"},
        {"ts": 2, "type": "api", "message": "GET /jobs 500 (12ms)"},
    ],
}


@pytest.mark.asyncio
async def test_feedback_503_when_unconfigured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FEEDBACK_GITHUB_TOKEN", raising=False)

    response = await client.post("/feedback", json=_SUBMISSION)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_feedback_creates_github_issue(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEEDBACK_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("FEEDBACK_GITHUB_REPO", "acme/widgets")

    captured: dict = {}
    monkeypatch.setattr(
        "ai_almanac.server.routers.feedback._github_client",
        lambda: _FakeGitHubClient(captured, status=201),
    )

    response = await client.post("/feedback", json=_SUBMISSION)

    assert response.status_code == 200
    assert response.json() == {"issue_url": "https://github.com/acme/widgets/issues/7"}
    assert captured["url"] == "https://api.github.com/repos/acme/widgets/issues"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["title"] == "[bug] The blend page crashed"
    assert "demo-feedback" in captured["json"]["labels"]
    assert "Breadcrumb trail (2 events)" in captured["json"]["body"]


@pytest.mark.asyncio
async def test_feedback_502_when_github_rejects(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEEDBACK_GITHUB_TOKEN", "test-token")

    monkeypatch.setattr(
        "ai_almanac.server.routers.feedback._github_client",
        lambda: _FakeGitHubClient({}, status=422),
    )

    response = await client.post("/feedback", json=_SUBMISSION)

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_config_js_reports_feedback_flag(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEEDBACK_GITHUB_TOKEN", "test-token")

    response = await client.get("/config.js")

    assert response.status_code == 200
    assert '"feedbackEnabled": true' in response.text
    assert '"version":' in response.text
