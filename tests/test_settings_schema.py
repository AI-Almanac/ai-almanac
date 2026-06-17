from __future__ import annotations

import pytest

from ai_almanac.server.routers.settings import get_schema
from ai_almanac.settings import LOCAL_ONLY_FIELDS, SHARED_ENV_ONLY_FIELDS, settings


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
