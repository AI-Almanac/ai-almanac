"""ai-almanac settings and configuration registries.

This module centralizes runtime configuration. The default values are tuned for
local single-user installs (`ai-almanac serve`). Most settings can be overridden
via environment variables or `.env` files for development / public deployment.
"""

from __future__ import annotations

import base64
import os
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

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
_ROMP_YAML = Path(str(_CONFIG_PKG.joinpath("romp.yaml")))
_REGIONS_YAML = Path(str(_CONFIG_PKG.joinpath("regions.yaml")))
_FORECAST_MODELS_YAML = Path(str(_CONFIG_PKG.joinpath("forecast_models.yaml")))


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

    # Password spliced into `database_url` when the URL omits one. Lets managed
    # deployments keep the secret out of the URL itself (Cloud Run injects it
    # from Secret Manager as `DB_PASSWORD` alongside a password-less URL).
    db_password: str = ""

    # Deployment mode. `personal` (default) is the zero-config single-operator
    # install: SQLite, no auth, local filesystem. `shared` is the multi-user
    # public deployment: PostgreSQL + reverse-proxy OIDC, validated at startup
    # by `auth.enforce_deployment_invariants()`.
    deployment_mode: str = "personal"  # personal | shared

    # Authentication mode. `none` trusts the local operator (personal installs).
    # `proxy` parses identity from trusted reverse-proxy headers (oauth2-proxy).
    # `globus` validates a Globus bearer token per request (introspection), so
    # the backend can be public with no proxy in front. Shared mode requires
    # `proxy` or `globus`.
    auth_mode: str = "none"  # none | proxy | globus

    # Globus Auth confidential-client credentials, used by `auth_mode=globus` to
    # introspect bearer tokens. Empty client id = stub mode (the raw token is
    # treated as the subject) for local development without Globus.
    globus_client_id: str = ""
    globus_client_secret: str = ""

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

    # Job execution backend. `local` runs jobs as detached local processes
    # (default, local-first). `modal` submits the benchmark to a deployed Modal
    # app and reconciles status from Modal (used by Cloud Run deployments).
    job_runner: str = "local"
    modal_app_name: str = "almanac-romp"
    modal_function_name: str = "run_benchmark"
    # Blend training runs in a separate Modal app (different image/dependencies);
    # the job config carries this app name so the shared runner can target it.
    modal_blending_app_name: str = "almanac-blending"
    # Live forecast generation (GPU earth2studio inference) runs in its own
    # Modal app; same targeting mechanism as modal_blending_app_name.
    modal_forecast_app_name: str = "almanac-forecasts"
    modal_forecast_function_name: str = "run_forecast"

    # Where workflow outputs live. Defaults to `<AI_ALMANAC_DATA_DIR>/jobs/`.
    # Set this to a bulk-storage path on hosts with separate fast/bulk disks.
    # Empty string = use the default under the data dir.
    output_dir: str = ""

    # Storage backend. `local` keeps artifacts on disk (default). `gcs` stores
    # uploads, job outputs, chat figures, and logs in the buckets below and
    # serves downloads via signed URLs (used by Cloud Run deployments).
    storage_backend: str = "local"
    gcs_uploads_bucket: str = ""
    gcs_outputs_bucket: str = ""
    gcs_data_bucket: str = ""

    # Whether the serving process applies database migrations on startup.
    # Local/personal installs default to True for zero-setup launch. Managed
    # deployments set this False and run migrations as a dedicated step (the
    # `migrate` compose service / the Cloud Run migration job) so request-
    # serving instances don't migrate on every cold start or race each other.
    auto_migrate: bool = True

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

    # Overrides the built-in chat system prompt (services/llm.py:SYSTEM_PROMPT)
    # when non-empty. Edited from the admin Settings page.
    chat_system_prompt: str = ""

    # Feature flags. Boolean gates for features still in development. Default True
    # for zero-setup local installs; managed deployments set them False in
    # config.yaml/env until the feature is ready, then flip them at runtime from
    # the admin Settings page. Name new flags `enable_<feature>` and surface them
    # in the "Features" group of routers/settings.py.
    enable_data_management: bool = True
    # Defaults to disabled (unlike the other feature flags above) — still
    # under active development and requires GPU/Modal infra most installs
    # won't have configured; opt in explicitly once it's ready to ship.
    enable_forecasting: bool = False
    chat_figure_signing_secret: str = "dev-chat-figure-secret"
    credential_encryption_key: str = ""

    # Shared-host quotas.
    max_active_jobs_per_user: int = 10
    max_concurrent_llm_requests_per_user: int = 2
    max_llm_requests_per_minute: int = 30

    def resolve_database_url(self) -> str:
        if self.database_url:
            if self.db_password:
                url = make_url(self.database_url)
                if not url.password:
                    return url.set(password=self.db_password).render_as_string(
                        hide_password=False
                    )
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
# settings overlay layering + hot reload.
# ---------------------------------------------------------------------------
#
# Resolution order, lowest to highest precedence:
#   1. Code defaults (the Settings field defaults above)
#   2. config.yaml at $AI_ALMANAC_DATA_DIR/config.yaml (hand-editable seed)
#   3. The `app_config` database overlay (written by the admin Settings UI)
#   4. Environment variables / .env files
#
# The database overlay is where UI edits land. It lives in the application
# database, which is the persistent store in every deployment (the SQLite file
# in the data dir for personal installs, PostgreSQL for shared deployments), so
# admin changes survive redeploys — unlike config.yaml, which sits on the
# container's ephemeral filesystem in managed deployments.
#
# config.yaml remains a hand-editable seed/override for local installs. Env vars
# still win so headless / CI deployments can override anything via env.


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

# Fields whose plaintext must never leave the server. GET /settings reports only
# whether one of these is configured (a boolean), never its value. Membership
# here is a backstop: the endpoint already restricts its payload to the fields
# the UI declares, but any sensitive field that ever reaches the UI must be
# listed so it is reported as a status flag rather than a value.
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "cdsapi_key",
        "llm_api_key",
        "chat_figure_signing_secret",
        "credential_encryption_key",
        "database_url",
        "db_password",
        "globus_client_secret",
    }
)

# Fields that only make sense for a single-operator install (local filesystem,
# direct process runner, SQLite). They're hidden from the Settings UI in shared
# deployments, where the runner, storage, and database are environment-managed.
LOCAL_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "runner_mode",
        "max_local_jobs",
        "output_dir",
        "database_url",
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
        # Set from the deployment env (Cloud Run); the UI toggle would be a no-op
        # since env wins over config.yaml, so surface them read-only instead.
        "enable_run_code",
        "enable_run_code_sandbox",
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


# Sensitive overlay values are encrypted at rest in `app_config` with the same
# AES-GCM master key used for stored LLM credentials. On disk they are an opaque
# envelope, never plaintext. `credential_encryption_key` is the one secret we
# can't seal (it would need itself); it is environment/config-managed and never
# written through the UI, so it is left as-is.
_SEALED_TAG = "__sealed__"


def _seal_secret(value: str) -> dict:
    from ai_almanac.server.services.llm_profiles import encrypt_api_key

    version, nonce, ciphertext = encrypt_api_key(value)
    return {
        _SEALED_TAG: version,
        "n": base64.b64encode(nonce).decode(),
        "c": base64.b64encode(ciphertext).decode(),
    }


def _unseal_secret(value):
    if not (isinstance(value, dict) and _SEALED_TAG in value):
        return value  # plaintext legacy value or a normal setting
    from ai_almanac.server.services.llm_profiles import decrypt_api_key

    return decrypt_api_key(
        value[_SEALED_TAG],
        base64.b64decode(value["n"]),
        base64.b64decode(value["c"]),
    )


def _load_db_overlay() -> dict:
    """Read the persistent settings overlay from the `app_config` table.

    Returns ``{}`` if the database is unreachable or the table is absent (e.g.
    before migrations on first launch), so settings resolution never hard-
    depends on the database being ready. Sealed secret values are decrypted back
    to plaintext for the in-memory settings singleton.
    """
    try:
        from sqlalchemy import select

        from ai_almanac.server.sync_db import sync_engine
        from ai_almanac.server.tables import app_config

        with sync_engine().connect() as conn:
            rows = conn.execute(
                select(app_config.c.key, app_config.c.value)
            ).all()
        return {key: _unseal_secret(value) for key, value in rows}
    except Exception:
        return {}


def _apply_overlay(target: Settings, data: dict) -> None:
    """Overlay `data` onto `target`, but never overwrite a field whose
    corresponding environment variable is set."""
    for key, value in data.items():
        if key not in type(target).model_fields:
            continue
        if key.upper() in os.environ:
            continue  # env wins
        if target.deployment_mode == "shared" and key in SHARED_ENV_ONLY_FIELDS:
            continue
        try:
            setattr(target, key, value)
        except Exception:
            # Bad value type — skip silently; the schema validator surfaces it
            # on the next save.
            continue


def reload_settings() -> Settings:
    """Re-resolve defaults + env + config.yaml + DB overlay; mutate the
    singleton in place.

    Called by the Settings PATCH endpoint after writing changes, and on server
    startup. Mutates `settings` so existing imports stay valid. The new values
    are fully resolved on a scratch instance first, so the in-place update is a
    plain field copy with no window where overlays have been reverted to
    env/defaults.
    """
    fresh = Settings()  # defaults + env (no overlay)
    _apply_overlay(fresh, _load_config_yaml())  # config.yaml seed
    _apply_overlay(fresh, _load_db_overlay())  # DB overlay wins over the seed
    for name in type(fresh).model_fields:
        setattr(settings, name, getattr(fresh, name))
    return settings


def write_settings_overlay(updates: dict) -> dict:
    """Persist a partial settings update to the `app_config` database overlay.

    Upserts each key (does not clobber unrelated keys). A value of `None` clears
    the override so the field reverts to its config.yaml/env/default. Returns
    the full merged overlay. Lives in the database so it survives redeploys.
    """
    from sqlalchemy import delete, insert

    from ai_almanac.server.sync_db import sync_engine
    from ai_almanac.server.tables import app_config

    with sync_engine().begin() as conn:
        for key, value in updates.items():
            conn.execute(delete(app_config).where(app_config.c.key == key))
            if value is None:
                continue
            if (
                key in SENSITIVE_FIELDS
                and key != "credential_encryption_key"
                and isinstance(value, str)
                and value != ""
            ):
                value = _seal_secret(value)
            conn.execute(insert(app_config).values(key=key, value=value))
    return _load_db_overlay()


# ---------------------------------------------------------------------------
# Packaged registries — regions and ROMP metric defs.
# ---------------------------------------------------------------------------
#
# Runtime catalog data (models, datasets, regions added via the UI) lives in
# the application database and is read through the async
# `server.services.registry` module. Only the packaged YAML defaults are
# resolved here.


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


def get_packaged_regions() -> list[dict]:
    return yaml.safe_load(_REGIONS_YAML.read_text())


_forecast_models_cache: list[dict] | None = None


def get_packaged_forecast_models() -> list[dict]:
    """Load the AI weather model registry used for live forecast generation.

    Single source of truth for model id/variables/lead-hour defaults, shared
    by the API's model list endpoint and the Modal forecast app (bundled into
    its image), so the two never drift out of sync.
    """
    global _forecast_models_cache
    if _forecast_models_cache is None:
        _forecast_models_cache = yaml.safe_load(_FORECAST_MODELS_YAML.read_text())
    return _forecast_models_cache
