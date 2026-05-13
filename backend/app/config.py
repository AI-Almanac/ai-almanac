import os
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

_MODELS_YAML = Path(__file__).parent / "config" / "models.yaml"
_DATASETS_YAML = Path(__file__).parent / "config" / "datasets.yaml"
_ROMP_YAML = Path(__file__).parent / "config" / "romp.yaml"
_ENV_FILE_NAMES = (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env")


def _env_key(*parts: str) -> str:
    """Join parts into an uppercase env var name, e.g. ("india", "fuxi", "model_dir") → INDIA_FUXI_MODEL_DIR."""
    return "_".join(p for p in parts if p).upper().replace("-", "_")


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---------------------------------------------------------------------------
    # Database
    # Local dev: compose postgres (see docker-compose.yml).
    # Production: Cloud SQL PostgreSQL; DB_PASSWORD is injected separately from
    # Secret Manager and merged into the URL at engine creation time (database.py).
    # ---------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://almanac:almanac@localhost:5432/almanac"
    db_password: str = ""

    # ---------------------------------------------------------------------------
    # Storage backend
    # "local" — filesystem under upload_dir / job_outputs_dir (dev default)
    # "gcs"   — Google Cloud Storage (production)
    # ---------------------------------------------------------------------------
    storage_backend: str = "local"
    upload_dir: str = "./uploads"
    job_outputs_dir: str = "./job_outputs"

    # GCS bucket names — required when storage_backend=gcs
    gcs_data_bucket: str = ""
    gcs_uploads_bucket: str = ""
    gcs_outputs_bucket: str = ""

    # ---------------------------------------------------------------------------
    # Job runner
    # "docker" — local Docker container (dev default)
    # "batch"  — Google Cloud Batch (production)
    # ---------------------------------------------------------------------------
    # When the backend runs in a container and spawns ROMP as a sibling via the
    # host Docker socket, volume mounts must use host-side paths. This setting
    # maps container path prefixes to their host equivalents.
    # Format: comma-separated "container_prefix=host_prefix" pairs.
    # Example: "/app/job_outputs=/host/repo/backend/job_outputs,/testdata=/host/repo/testdata"
    # docker-compose.yml sets this automatically using ${PWD}.
    docker_path_map: str = ""

    job_runner: str = "docker"
    romp_image: str = "romp:latest"
    romp_wrapper_image: str = (
        ""  # if set, used instead of romp_image for Cloud Run jobs
    )
    job_timeout_seconds: int = 3600
    job_cpu: str = "4"
    job_memory: str = "16Gi"
    # Probabilistic models load all ensemble members simultaneously and need
    # more resources. CPU and memory must be scaled together on Cloud Run.
    job_cpu_probabilistic: str = "8"
    job_memory_probabilistic: str = "32Gi"

    # Cloud Run / Batch settings — required when job_runner=cloudrun or batch
    gcp_project: str = ""
    gcp_region: str = "us-central1"
    batch_worker_sa: str = ""

    # Modal settings — required when job_runner=modal
    # MODAL_TOKEN_ID and MODAL_TOKEN_SECRET are read directly by the Modal client
    # from env; these fields just make them available for validation/logging.
    modal_token_id: str = ""
    modal_token_secret: str = ""

    # ---------------------------------------------------------------------------
    # LLM.
    # llm_provider="openai-compatible" uses llm_base_url with an OpenAI chat-completions-compatible client.
    # llm_provider="pydantic-ai" treats llm_model as a provider-prefixed Pydantic AI model string.
    # Examples:
    #   Modal vLLM: llm_provider="openai-compatible", llm_base_url="https://xxx--almanac-llm.modal.run/v1", llm_model="Qwen/Qwen2.5-Coder-7B-Instruct"
    #   Ollama:     llm_provider="openai-compatible", llm_base_url="http://localhost:11434/v1", llm_model="qwen2.5-coder"
    #   Anthropic:  llm_provider="pydantic-ai", llm_model="anthropic:claude-sonnet-4-6"
    # ---------------------------------------------------------------------------
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

    # ---------------------------------------------------------------------------
    # Auth
    # ---------------------------------------------------------------------------
    frontend_url: str = "http://localhost:5173"
    cors_allow_all: bool = False
    globus_client_id: str = ""
    globus_client_secret: str = ""

    # ---------------------------------------------------------------------------
    # Model directories and demo dataset paths
    # Resolved dynamically from env vars derived from models.yaml / datasets.yaml.
    # See get_model_registry() and get_demo_datasets() below.
    # Pattern: {REGION}_{ID}_MODEL_DIR  /  {ID}_OBS_DIR
    # ---------------------------------------------------------------------------


settings = Settings()


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


def get_model_registry() -> list[dict]:
    """Load model definitions from models.yaml; resolve model_dir from env vars.

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
    """Return the default ROMP run parameters."""
    return get_romp_config()["defaults"]


def get_metric_definitions() -> list[dict]:
    """Return all metric definitions (deterministic + probabilistic)."""
    cfg = get_romp_config()["metrics"]
    return cfg["deterministic"] + cfg["probabilistic"]


def get_demo_datasets() -> list[dict]:
    """Load demo dataset definitions from datasets.yaml; resolve obs_dir from env vars.

    Env var pattern: {ID}_OBS_DIR (uppercased, hyphens → underscores).
    Datasets whose env var is unset or empty are excluded.
    """
    raw = yaml.safe_load(_DATASETS_YAML.read_text())
    result = []
    for entry in raw:
        env_key = _env_key(entry["id"], "obs_dir")
        obs_dir = _env_value(env_key)
        if not obs_dir:
            continue
        result.append(
            {
                "id": "demo:" + entry["id"],
                "name": entry["name"],
                "region": entry.get("region", ""),
                "obs_dir": obs_dir,
                "obs_file_pattern": entry.get("obs_file_pattern"),
            }
        )
    return result
