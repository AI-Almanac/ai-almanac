"""Settings API.

Surfaces the application settings as a typed JSON document plus a per-field
schema so the UI can render an opinionated form. Patches are persisted to the
`app_config` database overlay (so they survive redeploys) and applied to the
live `settings` singleton via `reload_settings()` without restarting the
server.

Sensitive fields (API keys, signing secrets) are masked in GET responses
unless `?reveal=true` is passed.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_almanac.paths import config_yaml_path
from ai_almanac.server.auth import AdminUser
from ai_almanac.settings import (
    LOCAL_ONLY_FIELDS,
    RESTART_REQUIRED_FIELDS,
    SENSITIVE_FIELDS,
    SHARED_ENV_ONLY_FIELDS,
    reload_settings,
    settings,
    write_settings_overlay,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_MASK = "***"

# Field grouping + display metadata. Drives the Settings UI's section layout.
# Order matters — sections render in this order, fields within a section too.
# Tuples are (field_name, label, description); labels use product language, not
# the underlying setting names.
_FIELD_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Benchmark runner",
        [
            ("runner_mode", "Runner", "'stub' (synthetic outputs) or 'pixi' (real ROMP via the benchmark env)"),
            ("max_local_jobs", "Concurrent benchmarks", "Maximum number of benchmarks to run at once"),
            ("output_dir", "Output location", "Where benchmark outputs land. Empty = default (<data dir>/jobs/)"),
        ],
    ),
    (
        "Weather data access",
        [
            ("cdsapi_url", "Copernicus CDS endpoint", "Copernicus CDS API endpoint"),
            ("cdsapi_key", "Copernicus CDS key", "Copernicus CDS API key (used by ARCO/CDS obs fetchers)"),
        ],
    ),
    (
        "AI assistant",
        [
            ("llm_provider", "Provider type", "'openai-compatible' (vLLM, Ollama, OpenAI) or 'pydantic-ai' (provider-prefixed model strings)"),
            ("llm_base_url", "Provider URL", "Base URL for OpenAI-compatible providers"),
            ("llm_model", "Model", "Model identifier"),
            ("llm_api_key", "API key", "API key (use 'placeholder' for local servers that don't check)"),
            ("llm_timeout_seconds", "Request timeout (seconds)", "Per-request timeout"),
            ("llm_history_max_messages", "Chat history limit", "Max messages kept in chat history sent to the assistant"),
            ("llm_tool_result_max_chars", "Tool output limit", "Max characters of tool output forwarded to the assistant"),
            ("llm_code_context_max_chars", "Code context limit", "Max characters of code context included per request"),
        ],
    ),
    (
        "Assistant capabilities",
        [
            ("enable_run_code", "Allow code execution", "Let the assistant run code"),
            ("enable_run_code_sandbox", "Allow sandboxed code execution", "Let the assistant run code in a remote sandbox (requires a sandbox runner; off in local builds)"),
        ],
    ),
    (
        "Features",
        [
            ("enable_data_management", "Data management", "Let users create custom regions and upload their own datasets"),
        ],
    ),
    (
        "Advanced",
        [
            ("submitted_by_header", "Job attribution header", "Reverse-proxy header to read for job attribution (default X-Forwarded-User)"),
            ("frontend_url", "Allowed web origin", "Allowed origin for CORS (only relevant in Vite-dev workflow)"),
            ("cors_allow_all", "Allow all origins", "Allow any CORS origin (dev only)"),
            ("chat_figure_signing_secret", "Chat figure signing secret", "HMAC secret for chat figure URLs"),
            ("database_url", "Database URL", "SQLAlchemy URL. Empty = SQLite under the data dir."),
        ],
    ),
]


class FieldSchema(BaseModel):
    name: str
    label: str
    description: str
    type: Literal["string", "int", "float", "bool"]
    default: Any
    sensitive: bool
    restart_required: bool
    editable: bool


class SettingsSchema(BaseModel):
    deployment_mode: str
    groups: list[dict[str, Any]]


class SettingsValues(BaseModel):
    values: dict[str, Any]


def _python_type_name(annotation) -> str:
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    return "string"


def _present_value(field_name: str, value: Any, reveal: bool) -> Any:
    if not reveal and field_name in SENSITIVE_FIELDS and value:
        return _MASK
    return value


@router.get("/schema", response_model=SettingsSchema)
def get_schema(_admin: AdminUser) -> SettingsSchema:
    """Return field metadata grouped into UI sections.

    In shared deployments, local-only fields (runner, storage, database) are
    omitted entirely, and environment-managed fields are returned read-only.
    """
    shared = settings.deployment_mode == "shared"
    model_fields = type(settings).model_fields
    groups: list[dict[str, Any]] = []
    for group_name, fields in _FIELD_GROUPS:
        rendered: list[FieldSchema] = []
        for field_name, label, description in fields:
            if field_name not in model_fields:
                continue
            if shared and field_name in LOCAL_ONLY_FIELDS:
                continue
            info = model_fields[field_name]
            rendered.append(
                FieldSchema(
                    name=field_name,
                    label=label,
                    description=description,
                    type=_python_type_name(info.annotation),
                    default=info.default,
                    sensitive=field_name in SENSITIVE_FIELDS,
                    restart_required=field_name in RESTART_REQUIRED_FIELDS,
                    editable=not (shared and field_name in SHARED_ENV_ONLY_FIELDS),
                )
            )
        if rendered:
            groups.append({"name": group_name, "fields": [f.model_dump() for f in rendered]})
    return SettingsSchema(deployment_mode=settings.deployment_mode, groups=groups)


@router.get("", response_model=SettingsValues)
def get_settings(_admin: AdminUser, reveal: bool = False) -> SettingsValues:
    """Return current effective settings. Sensitive fields are masked unless
    `?reveal=true` is passed (which the UI sends after the user explicitly
    clicks 'show' on a secret input)."""
    values: dict[str, Any] = {}
    for name in type(settings).model_fields:
        values[name] = _present_value(name, getattr(settings, name), reveal)
    return SettingsValues(values=values)


class SettingsPatch(BaseModel):
    values: dict[str, Any]


@router.patch("", response_model=SettingsValues)
def patch_settings(body: SettingsPatch, _admin: AdminUser) -> SettingsValues:
    """Persist a partial update to the database overlay and hot-reload the
    settings singleton. Returns the new effective settings (with secrets
    masked)."""
    cleaned: dict[str, Any] = {}
    for key, value in body.values.items():
        if key not in type(settings).model_fields:
            raise HTTPException(status_code=400, detail=f"unknown setting: {key}")
        if settings.deployment_mode == "shared" and key in SHARED_ENV_ONLY_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"{key} is environment-only in shared deployments",
            )
        # Treat masked-value submissions as no-ops so the UI can round-trip
        # the whole GET payload without accidentally writing "***" to a secret.
        if key in SENSITIVE_FIELDS and value == _MASK:
            continue
        cleaned[key] = value

    write_settings_overlay(cleaned)
    reload_settings()

    values: dict[str, Any] = {}
    for name in type(settings).model_fields:
        values[name] = _present_value(name, getattr(settings, name), reveal=False)
    return SettingsValues(values=values)


@router.get("/config-yaml-path")
def get_config_yaml_path(_admin: AdminUser) -> dict:
    """Expose the config.yaml location so the UI can tell the user where the
    file lives if they want to edit it directly."""
    return {"path": str(config_yaml_path())}
