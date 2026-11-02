"""Tests for the setup API (gate middleware, bootstrap token, state/llm/prepare/finish)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import ai_almanac.server.services.setup as setup_svc
from ai_almanac.server.app import app
from ai_almanac.settings import reload_settings, settings


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETUP_COMPLETE", raising=False)
    monkeypatch.setattr(settings, "setup_complete", False)
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    # Reset the prepare singleton so tests don't bleed state
    setup_svc.prepare_task.__init__()
    # Apply migrations to the isolated DB so write_settings_overlay works
    from ai_almanac.paths import ensure_layout
    from ai_almanac.server.app import _apply_migrations

    ensure_layout()
    _apply_migrations()
    reload_settings()
    yield
    reload_settings()


@pytest.fixture()
def token(tmp_path: Path) -> str:
    """Bootstrap token written to the setup_token file."""
    return setup_svc.get_or_create_bootstrap_token()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def authed(token: str) -> dict[str, str]:
    return {"x-setup-token": token}


# ---------------------------------------------------------------------------
# Gate middleware
# ---------------------------------------------------------------------------


def test_gated_api_returns_403_with_code(client: TestClient, token: str) -> None:
    resp = client.get("/api/regions", headers={"accept": "application/json"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "setup_required"


def test_health_passes_gate(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


def test_ready_passes_gate(client: TestClient) -> None:
    resp = client.get("/ready")
    assert resp.status_code in (200, 503)  # depends on DB; not 403


def test_config_js_passes_gate(client: TestClient) -> None:
    resp = client.get("/config.js")
    assert resp.status_code == 200
    assert "setupRequired" in resp.text
    assert "true" in resp.text


def test_document_nav_redirects_to_setup(client: TestClient) -> None:
    resp = client.get(
        "/blends",
        headers={"sec-fetch-dest": "document", "accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "/setup"


def test_gate_lifts_after_finish(client: TestClient, authed: dict) -> None:
    # Finish setup
    resp = client.post("/api/setup/finish", headers=authed)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # Gate is lifted; /api/setup/state should now 404
    resp2 = client.get("/api/setup/state", headers=authed)
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Bootstrap token auth
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(client: TestClient) -> None:
    resp = client.get("/api/setup/state")
    assert resp.status_code == 401


def test_wrong_token_returns_401(client: TestClient) -> None:
    resp = client.get("/api/setup/state", headers={"x-setup-token": "wrong-token"})
    assert resp.status_code == 401


def test_correct_token_returns_200(client: TestClient, authed: dict) -> None:
    resp = client.get("/api/setup/state", headers=authed)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/setup/state
# ---------------------------------------------------------------------------


def test_state_shape(client: TestClient, authed: dict) -> None:
    resp = client.get("/api/setup/state", headers=authed)
    assert resp.status_code == 200
    body = resp.json()
    assert "platform" in body
    assert "platform" in body["platform"]
    assert "data_dir" in body
    assert "envs" in body
    assert set(body["envs"].keys()) >= {"benchmark", "blending", "forecast"}
    assert "prepare" in body
    assert body["prepare"]["status"] == "idle"


def test_state_gpu_monkeypatched(
    client: TestClient, authed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_svc, "probe_gpu", lambda: {"name": "TestGPU", "memory_mib": 8192, "count": 1}
    )
    resp = client.get("/api/setup/state", headers=authed)
    assert resp.json()["gpu"]["name"] == "TestGPU"


# ---------------------------------------------------------------------------
# POST /api/setup/storage
# ---------------------------------------------------------------------------


def test_storage_saves(client: TestClient, authed: dict, tmp_path: Path) -> None:
    resp = client.post(
        "/api/setup/storage",
        headers=authed,
        json={"output_dir": str(tmp_path / "jobs"), "dataset_mount_roots": ["/mnt/data"]},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /api/setup/llm
# ---------------------------------------------------------------------------


def _mock_openai_success():
    model_mock = MagicMock()
    model_mock.id = "gpt-4"
    models_resp = MagicMock()
    models_resp.data = [model_mock]

    client = AsyncMock()
    client.models.list = AsyncMock(return_value=models_resp)
    client.chat.completions.create = AsyncMock(return_value=MagicMock())
    return client


def _mock_openai_failure():
    client = AsyncMock()
    client.models.list = AsyncMock(side_effect=Exception("connection refused"))
    return client


def test_llm_test_only_returns_result_without_saving(
    client: TestClient, authed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_client = _mock_openai_success()
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        resp = client.post(
            "/api/setup/llm",
            headers=authed,
            json={"base_url": "http://localhost:11434/v1", "model": "llama3", "test_only": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["models_ok"] is True
    assert body["ok"] is True
    # test_only=True: settings not saved
    assert settings.llm_base_url != "http://localhost:11434/v1"


def test_llm_save_on_success(
    client: TestClient, authed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unset LLM_BASE_URL env var so the overlay value wins after reload
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    mock_client = _mock_openai_success()
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        resp = client.post(
            "/api/setup/llm",
            headers=authed,
            json={"base_url": "http://localhost:11434/v1", "model": "llama3", "test_only": False},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # After save, reload to see new settings (env var no longer overrides)
    reload_settings()
    assert settings.llm_base_url == "http://localhost:11434/v1"


def test_llm_failure_not_saved(
    client: TestClient, authed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_client = _mock_openai_failure()
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        resp = client.post(
            "/api/setup/llm",
            headers=authed,
            json={"base_url": "http://localhost:11434/v1", "model": "llama3", "test_only": False},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    reload_settings()
    assert settings.llm_base_url != "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# POST /api/setup/envs/prepare
# ---------------------------------------------------------------------------


def test_prepare_start_is_idempotent(client: TestClient, authed: dict) -> None:
    # Force the task into "running" state directly to avoid race with fast-completing task
    setup_svc.prepare_task.status = "running"

    resp = client.post("/api/setup/envs/prepare", headers=authed, json={"include_forecast": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] is False  # already running — no-op


# ---------------------------------------------------------------------------
# GET /api/setup/envs/events
# ---------------------------------------------------------------------------


def test_envs_events_state_snapshot(client: TestClient, authed: dict) -> None:
    with client.stream("GET", "/api/setup/envs/events?after=-1", headers=authed) as resp:
        assert resp.status_code == 200
        # Read only the first event (state snapshot)
        for line in resp.iter_lines():
            if line.startswith("data:"):
                evt = json.loads(line[len("data:") :])
                assert evt["type"] == "state"
                assert "envs" in evt
                break


# ---------------------------------------------------------------------------
# POST /api/setup/finish
# ---------------------------------------------------------------------------


def test_finish_writes_setup_complete(client: TestClient, authed: dict, tmp_path: Path) -> None:
    resp = client.post("/api/setup/finish", headers=authed)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # Token file should be removed
    assert not (tmp_path / "setup_token").exists()
    # Settings reloaded with setup_complete=True
    reload_settings()
    assert settings.setup_complete is True


# ---------------------------------------------------------------------------
# Grandfathering
# ---------------------------------------------------------------------------


def test_grandfather_marks_complete_when_llm_configured(tmp_path: Path) -> None:
    monkeypatch_settings = MagicMock()
    monkeypatch_settings.llm_base_url = "http://localhost"
    with (
        patch("ai_almanac.server.services.setup.setup_required", return_value=True),
        patch("ai_almanac.server.services.llm.llm_is_configured", return_value=True),
        patch("ai_almanac.settings._load_db_overlay", return_value={}),
        patch("ai_almanac.settings.write_settings_overlay") as mock_write,
    ):
        result = setup_svc.grandfather_existing_install()
    assert result is True
    mock_write.assert_called_once_with({"setup_complete": True})


def test_grandfather_noop_when_fresh_install(tmp_path: Path) -> None:
    with (
        patch("ai_almanac.server.services.setup.setup_required", return_value=True),
        patch("ai_almanac.server.services.llm.llm_is_configured", return_value=False),
        patch("ai_almanac.settings._load_db_overlay", return_value={}),
    ):
        result = setup_svc.grandfather_existing_install()
    assert result is False
