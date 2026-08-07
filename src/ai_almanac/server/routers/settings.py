"""Settings API.

Surfaces the application settings as a typed JSON document plus a per-field
schema so the UI can render an opinionated form. Patches are persisted to the
`app_config` database overlay (so they survive redeploys) and applied to the
live `settings` singleton via `reload_settings()` without restarting the
server.

Sensitive fields (API keys, signing secrets) are reported only as a
configured/not flag and can never be revealed through the API. Their plaintext
is encrypted at rest in the `app_config` overlay (AES-GCM, keyed by
`credential_encryption_key`). To change a secret, overwrite it with a new value
or clear it.
"""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

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

# Field grouping + display metadata. Drives the Settings UI's section layout.
# Order matters — sections render in this order, fields within a section too.
# Tuples are (field_name, label, description); labels use product language, not
# the underlying setting names.
_FIELD_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Benchmark runner",
        [
            (
                "runner_mode",
                "Runner",
                "'stub' (synthetic outputs) or 'pixi' (real ROMP via the benchmark env)",
            ),
            (
                "max_local_jobs",
                "Concurrent benchmarks",
                "Maximum number of benchmarks to run at once",
            ),
            (
                "output_dir",
                "Output location",
                "Where benchmark outputs land. Empty = default (<data dir>/jobs/)",
            ),
        ],
    ),
    (
        "Weather data access",
        [
            ("cdsapi_url", "Copernicus CDS endpoint", "Copernicus CDS API endpoint"),
            (
                "cdsapi_key",
                "Copernicus CDS key",
                "Copernicus CDS API key (used by ARCO/CDS obs fetchers)",
            ),
        ],
    ),
    (
        "AI assistant",
        [
            (
                "llm_provider",
                "Provider type",
                "'openai-compatible' (vLLM, Ollama, OpenAI) or 'pydantic-ai' (provider-prefixed model strings)",
            ),
            ("llm_base_url", "Provider URL", "Base URL for OpenAI-compatible providers"),
            ("llm_model", "Model", "Model identifier"),
            (
                "llm_api_key",
                "API key",
                "API key (use 'placeholder' for local servers that don't check)",
            ),
            ("llm_timeout_seconds", "Request timeout (seconds)", "Per-request timeout"),
            (
                "llm_history_max_messages",
                "Chat history limit",
                "Max messages kept in chat history sent to the assistant",
            ),
            (
                "llm_tool_result_max_chars",
                "Tool output limit",
                "Max characters of tool output forwarded to the assistant",
            ),
        ],
    ),
    (
        # Thresholds the platform enforces. Shared by the submission chokepoint,
        # the validation display, and the assistant's prompt prose, so the number
        # shown here is the number that will be applied — see
        # services.guardrails.current().
        "Assistant guardrails",
        [
            (
                "guardrail_min_training_years",
                "Minimum training years",
                "Below this many blend training years, warn that the fitted weights will not generalize",
            ),
            (
                "guardrail_blend_member_warn",
                "Blend member warning threshold",
                "Warn about overfitting at this many blend members or more",
            ),
            (
                "guardrail_small_sample_years",
                "Small-sample threshold",
                "Below this many scored years, warn that differences are dominated by noise",
            ),
            (
                "guardrail_presatellite_end_year",
                "Pre-satellite era ends",
                "Last year for which ERA5 initial conditions are treated as less reliable",
            ),
        ],
    ),
    (
        "Assistant capabilities",
        [
            ("enable_run_code", "Allow code execution", "Let the assistant run code"),
            (
                "enable_run_code_sandbox",
                "Allow sandboxed code execution",
                "Let the assistant run code in a remote sandbox (requires a sandbox runner; off in local builds)",
            ),
        ],
    ),
    (
        "Shared-host quotas",
        [
            (
                "max_active_jobs_per_user",
                "Active jobs per user",
                "Maximum jobs a single user may run at once",
            ),
        ],
    ),
    (
        "Features",
        [
            (
                "enable_data_management",
                "Data management",
                "Let users create custom regions and register their own datasets",
            ),
            (
                "enable_forecasting",
                "Live forecasting",
                "Let users generate live AI weather forecasts and score them against a trained blend",
            ),
            (
                "assistant_comparisons_audience",
                "Assistant comparisons",
                "Who can compare two assistant rulesets side by side and vote; each comparison costs two model replies. 'admins' lets admins test before exposing it to everyone",
            ),
        ],
    ),
    (
        "Advanced",
        [
            (
                "submitted_by_header",
                "Job attribution header",
                "Reverse-proxy header to read for job attribution (default X-Forwarded-User)",
            ),
            (
                "frontend_url",
                "Allowed web origin",
                "Allowed origin for CORS (only relevant in Vite-dev workflow)",
            ),
            ("cors_allow_all", "Allow all origins", "Allow any CORS origin (dev only)"),
            (
                "chat_figure_signing_secret",
                "Chat figure signing secret",
                "HMAC secret for chat figure URLs",
            ),
            ("database_url", "Database URL", "SQLAlchemy URL. Empty = SQLite under the data dir."),
        ],
    ),
]

# The only fields GET/PATCH /settings will ever read or write. Derived from the
# UI's declared groups so the endpoint can never expose a model field the UI
# doesn't surface (e.g. db_password, globus_client_secret, admin_emails).
_SCHEMA_FIELDS: frozenset[str] = frozenset(
    field_name for _, fields in _FIELD_GROUPS for field_name, _, _ in fields
)

# Long free-text fields the UI renders as a textarea (in a collapsible section)
# instead of a single-line input.
MULTILINE_FIELDS: frozenset[str] = frozenset()

# Field name -> effective value to show when the override is unset. The assistant
# prompt used to live here as one big textarea; it is now edited section by
# section as an assistant ruleset (services/rulesets.py).
_LIVE_DEFAULTS: dict[str, Any] = {}


class FieldSchema(BaseModel):
    name: str
    label: str
    description: str
    type: Literal["string", "int", "float", "bool"]
    default: Any
    sensitive: bool
    restart_required: bool
    editable: bool
    multiline: bool
    # Set for enum-valued fields (Literal annotations); the UI renders a select.
    choices: list[str] | None = None


class SettingsSchema(BaseModel):
    deployment_mode: str
    groups: list[dict[str, Any]]


class SettingsValues(BaseModel):
    # Non-sensitive declared fields, by name -> value.
    values: dict[str, Any]
    # Sensitive declared fields, by name -> whether a value is configured. The
    # plaintext is never included.
    secrets: dict[str, bool]


def _python_type_name(annotation) -> str:
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    return "string"


def _choices(annotation) -> list[str] | None:
    if get_origin(annotation) is Literal:
        values = get_args(annotation)
        if all(isinstance(value, str) for value in values):
            return list(values)
    return None


def _read_settings() -> SettingsValues:
    """Project the live settings into the client payload: declared fields only,
    with sensitive ones reduced to a configured/not flag so no secret value can
    leave the server."""
    model_fields = type(settings).model_fields
    values: dict[str, Any] = {}
    secrets: dict[str, bool] = {}
    for name in _SCHEMA_FIELDS:
        if name not in model_fields:
            continue
        value = getattr(settings, name)
        if name in SENSITIVE_FIELDS:
            secrets[name] = bool(value)
        else:
            values[name] = value
    return SettingsValues(values=values, secrets=secrets)


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
                    default=_LIVE_DEFAULTS.get(field_name, info.default),
                    sensitive=field_name in SENSITIVE_FIELDS,
                    restart_required=field_name in RESTART_REQUIRED_FIELDS,
                    editable=not (shared and field_name in SHARED_ENV_ONLY_FIELDS),
                    multiline=field_name in MULTILINE_FIELDS,
                    choices=_choices(info.annotation),
                )
            )
        if rendered:
            groups.append({"name": group_name, "fields": [f.model_dump() for f in rendered]})
    return SettingsSchema(deployment_mode=settings.deployment_mode, groups=groups)


@router.get("", response_model=SettingsValues)
def get_settings(_admin: AdminUser) -> SettingsValues:
    """Return current effective settings for the UI's declared fields. Secret
    fields report only whether they are configured; plaintext never leaves the
    server, and undeclared model fields are not exposed at all."""
    return _read_settings()


class SettingsPatch(BaseModel):
    values: dict[str, Any]


@router.patch("", response_model=SettingsValues)
def patch_settings(body: SettingsPatch, _admin: AdminUser) -> SettingsValues:
    """Persist a partial update to the database overlay and hot-reload the
    settings singleton. Only declared fields may be written; sending an empty
    string clears a secret. Returns the new effective settings, with secrets
    reduced to configured/not flags."""
    cleaned: dict[str, Any] = {}
    for key, value in body.values.items():
        if key not in _SCHEMA_FIELDS:
            raise HTTPException(status_code=400, detail=f"unknown setting: {key}")
        if settings.deployment_mode == "shared" and key in SHARED_ENV_ONLY_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"{key} is environment-only in shared deployments",
            )
        cleaned[key] = value

    try:
        write_settings_overlay(cleaned)
    except RuntimeError as exc:
        # Sealing a secret requires a configured credential_encryption_key.
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot store a secret: set credential_encryption_key "
                f"(via environment or config.yaml) first. ({exc})"
            ),
        ) from exc
    reload_settings()
    return _read_settings()


@router.get("/config-yaml-path")
def get_config_yaml_path(_admin: AdminUser) -> dict:
    """Expose the config.yaml location so the UI can tell the user where the
    file lives if they want to edit it directly."""
    return {"path": str(config_yaml_path())}
