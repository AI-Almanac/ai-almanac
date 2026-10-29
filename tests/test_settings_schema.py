from __future__ import annotations

import base64
import os

import pytest

from ai_almanac.server.routers.settings import _SCHEMA_FIELDS, get_schema, get_settings
from ai_almanac.settings import (
    LOCAL_ONLY_FIELDS,
    SENSITIVE_FIELDS,
    SHARED_ENV_ONLY_FIELDS,
    _seal_secret,
    _unseal_secret,
    reload_settings,
    settings,
)


def _fields(schema) -> dict[str, dict]:
    return {f["name"]: f for g in schema.groups for f in g["fields"]}


@pytest.mark.parametrize("mode", ["personal", "shared"])
def test_local_fields_and_env_locks_follow_the_deployment_mode(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", mode)
    fields = _fields(get_schema(_admin=None))
    personal = mode == "personal"

    local_fields = LOCAL_ONLY_FIELDS & _SCHEMA_FIELDS
    assert local_fields
    assert (local_fields <= set(fields)) is personal
    assert LOCAL_ONLY_FIELDS.isdisjoint(fields) is not personal
    env_locked = SHARED_ENV_ONLY_FIELDS & set(fields)
    assert env_locked
    assert all(fields[name]["editable"] is personal for name in env_locked)


def test_get_settings_reports_secrets_as_flags_not_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = next(iter(SENSITIVE_FIELDS & _SCHEMA_FIELDS))
    monkeypatch.setattr(settings, secret, "super-secret-value")
    out = get_settings(_admin=None)
    assert out.secrets[secret] is True
    assert secret not in out.values
    assert "super-secret-value" not in str(out.model_dump())


def test_get_settings_never_exposes_undeclared_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fields the UI never declares (DB password, admin emails, DB URL) must not
    # appear anywhere in the payload, in any form.
    monkeypatch.setattr(settings, "db_password", "pg-secret")
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "database_url", "postgresql://u:pw@host/db")
    payload = str(get_settings(_admin=None).model_dump())
    assert "pg-secret" not in payload
    assert "boss@example.com" not in payload
    assert "host/db" not in payload
    # database_url is declared but sensitive: present only as a configured flag.
    assert "database_url" not in get_settings(_admin=None).values


@pytest.mark.parametrize(
    ("legacy_flag", "audience"),
    [
        ("enable_assistant_comparisons", "assistant_comparisons_audience"),
        ("enable_data_management", "data_management_audience"),
    ],
)
def test_legacy_feature_flag_maps_to_audience_off(
    legacy_flag: str, audience: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ai_almanac.settings._load_config_yaml",
        lambda: {legacy_flag: False},
    )
    monkeypatch.setattr("ai_almanac.settings._load_db_overlay", dict)
    reload_settings()
    assert getattr(settings, audience) == "off"

    # An explicit audience wins over the deprecated flag.
    monkeypatch.setattr(
        "ai_almanac.settings._load_db_overlay",
        lambda: {audience: "everyone"},
    )
    reload_settings()
    assert getattr(settings, audience) == "everyone"


def test_db_overlay_beats_env_but_yaml_seed_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #179: a prod deploy sets LLM_MODEL via env; an admin's saved
    change (the DB overlay) must still take effect, while config.yaml stays a
    seed below env."""
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setattr(
        "ai_almanac.settings._load_config_yaml", lambda: {"llm_model": "yaml-model"}
    )
    monkeypatch.setattr("ai_almanac.settings._load_db_overlay", dict)
    reload_settings()
    assert settings.llm_model == "env-model"

    monkeypatch.setattr(
        "ai_almanac.settings._load_db_overlay", lambda: {"llm_model": "admin-model"}
    )
    reload_settings()
    assert settings.llm_model == "admin-model"


def test_shared_env_only_fields_resist_the_db_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "shared")
    monkeypatch.setattr("ai_almanac.settings._load_config_yaml", dict)
    monkeypatch.setattr(
        "ai_almanac.settings._load_db_overlay",
        lambda: {"admin_emails": "attacker@example.com"},
    )
    reload_settings()
    assert settings.admin_emails == ""


def test_secret_overlay_value_is_sealed_at_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )
    sealed = _seal_secret("sk-super-secret")
    # The on-disk envelope is opaque: plaintext appears nowhere in it.
    assert "sk-super-secret" not in str(sealed)
    # And it round-trips back for the in-memory settings singleton.
    assert _unseal_secret(sealed) == "sk-super-secret"
    # Plaintext/legacy values pass through unchanged.
    assert _unseal_secret("plain") == "plain"


MANAGED = {"field": "llm_engine", "one_of": ["managed"]}


def test_the_model_page_leads_with_the_model_and_tucks_the_rest_under_advanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    [model] = [g for g in get_schema(_admin=None).groups if g["name"] == "Model"]
    sections = [(f["name"], f["advanced_section"]) for f in model["fields"]]

    assert sections[:3] == [
        ("llm_model", None),
        ("llm_speculative_decoding", None),
        ("llm_gpu_memory_utilization", None),
    ]
    assert [name for name, section in sections if section == "Connection"] == [
        "llm_provider",
        "llm_base_url",
        "llm_engine",
        "llm_api_key",
    ]
    assert {section for _, section in sections[3:]} == {"Connection", "Local server", "Limits"}


def test_each_server_shows_only_the_settings_it_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    fields = _fields(get_schema(_admin=None))

    external = {"field": "llm_engine", "one_of": ["external"]}
    for name in ("llm_provider", "llm_base_url", "llm_api_key"):
        assert fields[name]["shown_when"] == [external]
    for name in ("llm_gpu_memory_utilization", "llm_port", "llm_serve_args"):
        assert fields[name]["shown_when"] == [MANAGED]
    assert fields["llm_model"]["shown_when"] is None
    assert fields["llm_engine"]["shown_when"] is None


def test_speculative_decoding_shows_only_for_a_model_with_a_tested_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    fields = _fields(get_schema(_admin=None))

    [managed, drafted] = fields["llm_speculative_decoding"]["shown_when"]
    assert managed == MANAGED
    assert drafted["field"] == "llm_model"
    assert "nvidia/Qwen3.8-27B-NVFP4" in drafted["one_of"]
    assert "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4" not in drafted["one_of"]
    for name in ("llm_draft_model", "llm_draft_tokens"):
        assert fields[name]["shown_when"] == [
            managed,
            drafted,
            {"field": "llm_speculative_decoding", "one_of": [True]},
        ]
    assert "z-lab/Qwen3.8-27B-DFlash2" in fields["llm_draft_model"]["suggestions"]


def test_model_field_offers_the_local_catalog_while_the_server_is_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    model = _fields(get_schema(_admin=None))["llm_model"]

    assert model["presets_when"] == MANAGED
    labels = {p["value"]: p["label"] for p in model["presets"]}
    assert labels["nvidia/Qwen3.8-27B-NVFP4"] == "Qwen3.8 27B (NVFP4) + DFlash 2 · 25.7 GB"
    assert labels["RedHatAI/Qwen3.6-35B-A3B-NVFP4"].endswith("· untested")


def test_shared_mode_offers_no_local_model_presets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    model = _fields(get_schema(_admin=None)).get("llm_model")

    assert model is None or model["presets"] is None
