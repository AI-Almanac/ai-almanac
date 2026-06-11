"""ai-almanac settings and configuration registries.

This module centralizes runtime configuration. The default values are tuned for
local single-user installs (`ai-almanac serve`). Most settings can be overridden
via environment variables or `.env` files for development / public deployment.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_almanac.paths import (
    config_yaml_path,
    database_path,
    ensure_layout,
    jobs_dir,
    uploads_dir,
)

# ---------------------------------------------------------------------------
# YAML registries (packaged as `ai_almanac.server.config`).
# ---------------------------------------------------------------------------

_CONFIG_PKG = files("ai_almanac.server").joinpath("config")
_MODELS_YAML = Path(str(_CONFIG_PKG.joinpath("models.yaml")))
_DATASETS_YAML = Path(str(_CONFIG_PKG.joinpath("datasets.yaml")))
_ROMP_YAML = Path(str(_CONFIG_PKG.joinpath("romp.yaml")))
_REGIONS_YAML = Path(str(_CONFIG_PKG.joinpath("regions.yaml")))


# ---------------------------------------------------------------------------
# Env-var lookup with .env fallback.
# Searched paths: CWD/.env, then the repo-root .env when running from source.
# ---------------------------------------------------------------------------

_ENV_FILE_NAMES = (
    Path.cwd() / ".env",
    Path(__file__).resolve().parents[2] / ".env",
)
_env_file_cache: dict[str, str] | None = None


def _env_file_values() -> dict[str, str]:
    global _env_file_cache
    if _env_file_cache is not None:
        return _env_file_cache

    values: dict[str, str] = {}
    for env_file in dict.fromkeys(_ENV_FILE_NAMES):
        if not env_file.exists():
            continue
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                values.setdefault(key, value)

    _env_file_cache = values
    return values


def _env_value(key: str) -> str:
    return os.environ.get(key, _env_file_values().get(key, ""))


def _env_key(*parts: str) -> str:
    """Join parts into an uppercase env var name."""
    return "_".join(p for p in parts if p).upper().replace("-", "_")


# ---------------------------------------------------------------------------
# Settings.
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database — SQLite under the data dir by default. Override with
    # `DATABASE_URL` for non-default backends (e.g., Postgres for public deploys).
    database_url: str = ""  # resolved below if empty

    # Deployment mode. `personal` (default) is the zero-config single-operator
    # install: SQLite, no auth, local filesystem. `shared` is the multi-user
    # public deployment: PostgreSQL + reverse-proxy OIDC, validated at startup
    # by `auth.enforce_deployment_invariants()`.
    deployment_mode: str = "personal"  # personal | shared

    # Authentication mode. `none` trusts the local operator (personal installs).
    # `proxy` parses identity from trusted reverse-proxy headers (oauth2-proxy).
    # Shared mode forces `proxy` at startup.
    auth_mode: str = "none"  # none | proxy

    # Admin allow-lists for shared mode. Comma-separated OIDC subjects / emails;
    # a request whose identity matches either list is granted the `admin` role.
    admin_subjects: str = ""
    admin_emails: str = ""
    allowed_groups: str = ""
    admin_groups: str = ""

    # Allow-list of root directories that mounted data sources may resolve
    # within (comma-separated). Empty = unrestricted (personal installs, where
    # the operator owns the box). In shared mode, admins set this so a
    # registered source path cannot escape the configured dataset mounts.
    dataset_mount_roots: str = ""

    # Concurrency — gates simultaneous benchmark jobs so the GPU isn't oversubscribed.
    max_local_jobs: int = 1

    # Runner selection. Pixi executes real ROMP benchmarks. Stub mode remains
    # available for tests and UI development without the benchmark environment.
    runner_mode: str = "pixi"

    # Where workflow outputs live. Defaults to `<AI_ALMANAC_DATA_DIR>/jobs/`.
    # Set this to a bulk-storage path on hosts with separate fast/bulk disks.
    # Empty string = use the default under the data dir.
    output_dir: str = ""

    # Server-side filesystem browser (backs the UI directory picker).
    # Safe for local installs — the server IS the user. For public deploys
    # behind a reverse proxy, this exposes the host's filesystem to anyone
    # the proxy admits; set to false unless that's intentional.
    enable_fs_browser: bool = True

    # Attribution header. When ai-almanac runs behind a reverse proxy that has
    # authenticated the user, the proxy can forward the user's identity in this
    # header. The value is the stable subject (OIDC `sub`) parsed by
    # `auth._resolve_identity`: attribution-only in personal mode, the trusted
    # authenticated subject in `proxy` mode.
    submitted_by_header: str = "X-Forwarded-User"

    # Additional identity headers parsed in `proxy` auth mode. The subject is
    # read from `submitted_by_header` above.
    identity_email_header: str = "X-Forwarded-Email"
    identity_name_header: str = "X-Forwarded-Preferred-Username"
    identity_issuer_header: str = "X-Forwarded-Issuer"
    identity_groups_header: str = "X-Forwarded-Groups"
    logout_url: str = "/oauth2/sign_out"

    # CORS — only relevant for the frontend dev server proxying to the API.
    frontend_url: str = "http://localhost:5173"
    cors_allow_all: bool = False

    # Earth2Studio / CDS API credentials. Read by the in-process benchmark runner
    # when an ARCO-ERA5 / CDS dataset is selected.
    cdsapi_url: str = "https://cds.climate.copernicus.eu/api"
    cdsapi_key: str = ""

    # LLM — for the chat UI.
    # llm_provider="openai-compatible" uses llm_base_url with an OpenAI client.
    # llm_provider="pydantic-ai" treats llm_model as a provider-prefixed model string.
    llm_provider: str = "openai-compatible"
    llm_base_url: str = ""
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = "placeholder"
    llm_timeout_seconds: float = 60.0
    llm_history_max_messages: int = 80
    llm_tool_result_max_chars: int = 12000
    llm_code_context_max_chars: int = 6000
    enable_run_code: bool = True
    enable_run_code_sandbox: bool = True
    chat_figure_signing_secret: str = "dev-chat-figure-secret"
    credential_encryption_key: str = ""

    # Shared-host quotas and upload policy.
    max_active_jobs_per_user: int = 2
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_stored_upload_bytes_per_user: int = 10 * 1024 * 1024 * 1024
    max_concurrent_llm_requests_per_user: int = 2
    max_llm_requests_per_minute: int = 30
    upload_grant_ttl_seconds: int = 900
    allowed_upload_extensions: str = ".nc,.zip,.tar,.gz,.tgz"

    # Model directories and demo dataset paths are resolved dynamically from
    # env vars derived from models.yaml / datasets.yaml.
    # Pattern: {REGION}_{ID}_MODEL_DIR  /  {ID}_OBS_DIR
    # See get_model_registry() and get_demo_datasets() below.

    def resolve_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        ensure_layout()
        return f"sqlite+aiosqlite:///{database_path()}"

    @property
    def upload_dir(self) -> str:
        return str(uploads_dir())

    @property
    def job_outputs_dir(self) -> str:
        return self.output_dir or str(jobs_dir())


settings = Settings()


# ---------------------------------------------------------------------------
# config.yaml layering + hot reload.
# ---------------------------------------------------------------------------
#
# Resolution order, lowest to highest precedence:
#   1. Code defaults (the Settings field defaults above)
#   2. config.yaml at $AI_ALMANAC_DATA_DIR/config.yaml (edited via the UI)
#   3. Environment variables / .env files
#
# config.yaml is the user-editable surface. Env vars still win so headless /
# CI deployments can override anything via env without touching the file.


# Fields that need a server restart to fully take effect (e.g. baked into the
# DB engine, CORS middleware, etc.). Mutating these at runtime updates the
# `settings` value but the system component that consumed it earlier doesn't
# re-read. UI surfaces a warning when these are edited.
RESTART_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "database_url",
        "deployment_mode",
        "auth_mode",
        "admin_subjects",
        "admin_emails",
        "allowed_groups",
        "admin_groups",
        "frontend_url",
        "cors_allow_all",
        "submitted_by_header",
        "identity_email_header",
        "identity_name_header",
        "identity_issuer_header",
        "identity_groups_header",
        "credential_encryption_key",
    }
)

# Fields whose values must not be returned in plaintext from GET /settings
# (they're masked with "***" unless an explicit `?reveal=true` is requested).
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "cdsapi_key",
        "llm_api_key",
        "chat_figure_signing_secret",
        "credential_encryption_key",
    }
)

SHARED_ENV_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "database_url",
        "deployment_mode",
        "auth_mode",
        "admin_subjects",
        "admin_emails",
        "allowed_groups",
        "admin_groups",
        "submitted_by_header",
        "identity_email_header",
        "identity_name_header",
        "identity_issuer_header",
        "identity_groups_header",
        "credential_encryption_key",
        "chat_figure_signing_secret",
    }
)


def _load_config_yaml() -> dict:
    path = config_yaml_path()
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text())
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _apply_yaml_overrides() -> None:
    """Overlay config.yaml values onto the `settings` singleton, but never
    overwrite a field whose corresponding environment variable is set.
    """
    data = _load_config_yaml()
    for key, value in data.items():
        if key not in settings.model_fields:
            continue
        if key.upper() in os.environ:
            continue  # env wins
        if settings.deployment_mode == "shared" and key in SHARED_ENV_ONLY_FIELDS:
            continue
        try:
            setattr(settings, key, value)
        except Exception:
            # Bad value type — skip silently; the schema validator surfaces it
            # on the next save.
            continue


def reload_settings() -> Settings:
    """Re-read code defaults + env + config.yaml; mutate the singleton in place.

    Called by the Settings PATCH endpoint after writing changes, and once on
    server startup. Mutates `settings` so existing imports stay valid.
    """
    fresh = Settings()  # defaults + env (no YAML)
    for name in fresh.model_fields:
        setattr(settings, name, getattr(fresh, name))
    _apply_yaml_overrides()
    return settings


def write_config_yaml(updates: dict) -> dict:
    """Persist a partial settings update to config.yaml.

    Merges into the existing file (does not clobber unrelated keys). Returns
    the full new config.yaml contents as a dict.
    """
    path = config_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _load_config_yaml()
    merged = {**current, **updates}
    # Drop keys whose value is `None` so users can revert to default by
    # clearing a field in the UI.
    merged = {k: v for k, v in merged.items() if v is not None}
    path.write_text(yaml.safe_dump(merged, sort_keys=True, default_flow_style=False))
    return merged


# ---------------------------------------------------------------------------
# Registries — models, datasets, regions, ROMP metric defs.
# ---------------------------------------------------------------------------


# Cached synchronous engine for the registry read path (see `_sync_db_query`).
_sync_engine = None
_sync_engine_url: str | None = None


def _sync_read_url(async_url: str) -> str:
    """Map the app's async DB URL to an equivalent synchronous-driver URL."""
    from ai_almanac.server.database_urls import sync_database_url

    return sync_database_url(async_url)


def _get_sync_engine():
    """Process-wide read-only engine for the synchronous registry resolvers.

    The async engine in `server/db.py` owns writes; this engine lets sync
    callers (the YAML-derived registries below) read DB rows the user added via
    the UI without async-ifying the entire registry surface. It is
    driver-agnostic so it works on both SQLite (personal installs) and
    PostgreSQL (shared deployments). `NullPool` opens a fresh connection per
    query, matching the previous raw-sqlite behavior and keeping the path safe
    to call from worker threads.
    """
    global _sync_engine, _sync_engine_url
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    url = _sync_read_url(settings.resolve_database_url())
    if _sync_engine is None or _sync_engine_url != url:
        if _sync_engine is not None:
            _sync_engine.dispose()
        _sync_engine = create_engine(url, poolclass=NullPool)
        _sync_engine_url = url
    return _sync_engine


def _sync_db_query(sql: str) -> list[dict]:
    """Run a read-only registry query against the app database (sync path).

    Works on SQLite and PostgreSQL. Returns an empty list if the table does not
    exist yet (first launch, pre-migration).
    """
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    try:
        with _get_sync_engine().connect() as conn:
            return [dict(row) for row in conn.execute(sql_text(sql)).mappings().all()]
    except (OperationalError, ProgrammingError):
        return []


def _ds_metadata(row: dict) -> dict:
    """SQLite stores JSON as text; deserialize on read."""
    import json as _json

    raw = row.get("metadata")
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except Exception:
            return {}
    return raw or {}


def get_model_registry() -> list[dict]:
    """Return the registered model entries.

    Reads from the `data_sources` table (the runtime source of truth, fed by
    the UI). The YAML registry is used as a one-time seed on first launch via
    `services.data_sources.seed_from_yaml_if_empty()` — it's not re-read here.
    """
    rows = _sync_db_query(
        "SELECT * FROM data_sources "
        "WHERE kind = 'model' AND status = 'ready' ORDER BY region, name"
    )
    result = []
    for row in rows:
        meta = _ds_metadata(row)
        result.append(
            {
                "id": row["id"],
                "display_name": row["name"],
                "region": row["region"],
                "model_dir": row["path"],
                **meta,
            }
        )
    return result


_romp_config_cache: dict | None = None


def get_romp_config() -> dict:
    """Load metric definitions and ROMP parameter defaults from romp.yaml."""
    global _romp_config_cache
    if _romp_config_cache is None:
        _romp_config_cache = yaml.safe_load(_ROMP_YAML.read_text())
    return _romp_config_cache


def get_romp_defaults() -> dict:
    return get_romp_config()["defaults"]


def get_metric_definitions() -> list[dict]:
    cfg = get_romp_config()
    romp_metrics = cfg["metrics"]["deterministic"] + cfg["metrics"]["probabilistic"]
    e2s_metrics = cfg.get("e2s_metrics", [])
    return romp_metrics + e2s_metrics


REMOTE_OBS_PROVIDERS = {"earth2studio", "era5_arco"}


def get_packaged_regions() -> list[dict]:
    return yaml.safe_load(_REGIONS_YAML.read_text())


def get_regions() -> list[dict]:
    packaged = get_packaged_regions()
    packaged_ids = {region["id"] for region in packaged}
    rows = _sync_db_query(
        "SELECT * FROM regions ORDER BY is_builtin DESC, display_name"
    )
    by_id = {region["id"]: region for region in packaged}
    by_id.update({region["id"]: region for region in rows})
    return sorted(
        by_id.values(),
        key=lambda region: (
            not bool(region.get("is_builtin", region["id"] in packaged_ids)),
            region["display_name"].lower(),
        ),
    )


def get_region(region_id: str) -> dict | None:
    for r in get_regions():
        if r["id"].lower() == region_id.lower():
            return r
    return None


def get_demo_datasets() -> list[dict]:
    """Compatibility adapter for registered, executable observation sources."""
    rows = _sync_db_query(
        "SELECT * FROM data_sources "
        "WHERE kind = 'obs' AND status = 'ready' ORDER BY name"
    )
    result = []
    for row in rows:
        meta = _ds_metadata(row)
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "region": row.get("region", "") or "",
                "provider": "local",
                "obs_dir": row["path"],
                "obs_file_pattern": meta.get("obs_file_pattern"),
                "obs_var": meta.get("obs_var", "RAINFALL"),
                "start_year": meta.get("start_year"),
                "end_year": meta.get("end_year"),
            }
        )
    return result
