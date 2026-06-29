from __future__ import annotations

import pytest

from ai_almanac.server.routers.settings import _SCHEMA_FIELDS, get_schema, get_settings
from ai_almanac.settings import (
    LOCAL_ONLY_FIELDS,
    SENSITIVE_FIELDS,
    SHARED_ENV_ONLY_FIELDS,
    settings,
)


def _fields(schema) -> dict[str, dict]:
    return {f["name"]: f for g in schema.groups for f in g["fields"]}


def test_personal_mode_exposes_local_fields_as_editable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    fields = _fields(get_schema(_admin=None))
    assert "runner_mode" in fields
    assert all(f["editable"] for f in fields.values())
    # Labels are product language, not raw field names.
    assert fields["runner_mode"]["label"] != "runner_mode"


def test_shared_mode_hides_local_fields_and_locks_env_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "shared")
    fields = _fields(get_schema(_admin=None))
    assert LOCAL_ONLY_FIELDS.isdisjoint(fields)
    for name in SHARED_ENV_ONLY_FIELDS & set(fields):
        assert fields[name]["editable"] is False


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
