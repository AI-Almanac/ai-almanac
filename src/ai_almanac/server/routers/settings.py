"""Settings API.

Surfaces the application settings as a typed JSON document plus a per-field
schema so the UI can render an opinionated form. Patches are persisted to
`$AI_ALMANAC_DATA_DIR/config.yaml` and applied to the live `settings`
singleton via `reload_settings()` without restarting the server.

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
    RESTART_REQUIRED_FIELDS,
    SENSITIVE_FIELDS,
    SHARED_ENV_ONLY_FIELDS,
    reload_settings,
    settings,
    write_config_yaml,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_MASK = "***"

# Field grouping + display metadata. Drives the Settings UI's section layout.
# Order matters — sections render in this order, fields within a section too.
_FIELD_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Runner & jobs",
        [
            ("runner_mode", "Benchmark runner — 'stub' (synthetic outputs) or 'pixi' (real ROMP via the benchmark env)"),
            ("max_local_jobs", "Maximum number of benchmarks to run concurrently"),
            ("output_dir", "Where workflow outputs land. Empty = default (<data dir>/jobs/)"),
        ],
    ),
    (
        "Remote data sources",
        [
            ("cdsapi_url", "Copernicus CDS API endpoint"),
            ("cdsapi_key", "Copernicus CDS API key (used by ARCO/CDS obs fetchers)"),
        ],
    ),
    (
        "LLM",
        [
            ("llm_provider", "'openai-compatible' (vLLM, Ollama, OpenAI) or 'pydantic-ai' (provider-prefixed model strings)"),
            ("llm_base_url", "Base URL for OpenAI-compatible providers"),
            ("llm_model", "Model identifier"),
            ("llm_api_key", "API key (use 'placeholder' for local servers that don't check)"),
            ("llm_timeout_seconds", "Per-request timeout"),
            ("llm_history_max_messages", "Max messages kept in chat history sent to the LLM"),
            ("llm_tool_result_max_chars", "Max characters of tool output forwarded to the LLM"),
            ("llm_code_context_max_chars", "Max characters of code context included per request"),
        ],
    ),
    (
        "Feature flags",
        [
            ("enable_run_code", "Allow the LLM to run code"),
            ("enable_run_code_sandbox", "Allow the LLM to run code in a remote sandbox (requires a sandbox runner; off in local builds)"),
        ],
    ),
    (
        "Advanced",
        [
            ("submitted_by_header", "Reverse-proxy header to read for job attribution (default X-Forwarded-User)"),
            ("frontend_url", "Allowed origin for CORS (only relevant in Vite-dev workflow)"),
            ("cors_allow_all", "Allow any CORS origin (dev only)"),
            ("chat_figure_signing_secret", "HMAC secret for chat figure URLs"),
            ("database_url", "SQLAlchemy URL. Empty = SQLite under the data dir."),
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


class SettingsSchema(BaseModel):
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
    """Return field metadata grouped into UI sections."""
    model_fields = settings.model_fields
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group_name, fields in _FIELD_GROUPS:
        rendered: list[FieldSchema] = []
        for field_name, description in fields:
            if field_name not in model_fields:
                continue
            seen.add(field_name)
            info = model_fields[field_name]
            rendered.append(
                FieldSchema(
                    name=field_name,
                    label=field_name.replace("_", " "),
                    description=description,
                    type=_python_type_name(info.annotation),
                    default=info.default,
                    sensitive=field_name in SENSITIVE_FIELDS,
                    restart_required=field_name in RESTART_REQUIRED_FIELDS,
                )
            )
        if rendered:
            groups.append({"name": group_name, "fields": [f.model_dump() for f in rendered]})
    return SettingsSchema(groups=groups)


@router.get("", response_model=SettingsValues)
def get_settings(_admin: AdminUser, reveal: bool = False) -> SettingsValues:
    """Return current effective settings. Sensitive fields are masked unless
    `?reveal=true` is passed (which the UI sends after the user explicitly
    clicks 'show' on a secret input)."""
    values: dict[str, Any] = {}
    for name in settings.model_fields:
        values[name] = _present_value(name, getattr(settings, name), reveal)
    return SettingsValues(values=values)


class SettingsPatch(BaseModel):
    values: dict[str, Any]


@router.patch("", response_model=SettingsValues)
def patch_settings(body: SettingsPatch, _admin: AdminUser) -> SettingsValues:
    """Persist a partial update to config.yaml and hot-reload the settings
    singleton. Returns the new effective settings (with secrets masked)."""
    cleaned: dict[str, Any] = {}
    for key, value in body.values.items():
        if key not in settings.model_fields:
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

    write_config_yaml(cleaned)
    reload_settings()

    values: dict[str, Any] = {}
    for name in settings.model_fields:
        values[name] = _present_value(name, getattr(settings, name), reveal=False)
    return SettingsValues(values=values)


@router.get("/config-yaml-path")
def get_config_yaml_path(_admin: AdminUser) -> dict:
    """Expose the config.yaml location so the UI can tell the user where the
    file lives if they want to edit it directly."""
    return {"path": str(config_yaml_path())}
