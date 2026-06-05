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

from ai_almanac.paths import database_path, ensure_layout, jobs_dir, uploads_dir

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

    # Concurrency — gates simultaneous benchmark jobs so the GPU isn't oversubscribed.
    max_local_jobs: int = 1

    # Attribution header. When ai-almanac runs behind a reverse proxy that has
    # authenticated the user, the proxy can forward the user's identity in this
    # header. The value is recorded on jobs/datasets as `submitted_by`. No
    # enforcement happens in the app — this is for attribution only.
    submitted_by_header: str = "X-Forwarded-User"

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
        return str(jobs_dir())


settings = Settings()


# ---------------------------------------------------------------------------
# Registries — models, datasets, regions, ROMP metric defs.
# ---------------------------------------------------------------------------


def get_model_registry() -> list[dict]:
    """Load model definitions; resolve model_dir from env vars.

    Env var pattern: {REGION}_{ID}_MODEL_DIR (uppercased, hyphens → underscores).
    Models whose env var is unset or empty are excluded.
    """
    raw = yaml.safe_load(_MODELS_YAML.read_text())
    result = []
    for entry in raw:
        env_key = _env_key(entry["region"], entry["id"], "model_dir")
        model_dir = _env_value(env_key)
        if not model_dir:
            continue
        m = dict(entry)
        m["model_dir"] = model_dir
        result.append(m)
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


def get_regions() -> list[dict]:
    return yaml.safe_load(_REGIONS_YAML.read_text())


def get_region(region_id: str) -> dict | None:
    for r in get_regions():
        if r["id"].lower() == region_id.lower():
            return r
    return None


def get_demo_datasets() -> list[dict]:
    raw = yaml.safe_load(_DATASETS_YAML.read_text())
    result = []
    for entry in raw:
        provider = entry.get("provider", "local")

        if provider in REMOTE_OBS_PROVIDERS:
            required_env = entry.get("required_env")
            if required_env and not _env_value(required_env):
                continue
            result.append(
                {
                    "id": "demo:" + entry["id"],
                    "name": entry["name"],
                    "region": entry.get("region", ""),
                    "provider": provider,
                    "e2s_class": entry.get("e2s_class"),
                    "arco_url": entry.get("arco_url"),
                    "precip_var": entry.get("precip_var", "tp"),
                    "unit_cvt": entry.get("unit_cvt", 1.0),
                    "lat_bounds": entry.get("lat_bounds"),
                    "lon_bounds": entry.get("lon_bounds"),
                    "obs_file_pattern": entry.get("obs_file_pattern", "{}.nc"),
                    "obs_dir": None,
                }
            )
        else:
            env_key = _env_key(entry["id"], "obs_dir")
            obs_dir = _env_value(env_key)
            if not obs_dir:
                continue
            result.append(
                {
                    "id": "demo:" + entry["id"],
                    "name": entry["name"],
                    "region": entry.get("region", ""),
                    "provider": "local",
                    "obs_dir": obs_dir,
                    "obs_file_pattern": entry.get("obs_file_pattern"),
                }
            )
    return result
