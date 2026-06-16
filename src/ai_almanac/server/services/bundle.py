"""Execution bundle compilation.

Turns a validated job config into what a runner needs to execute it: the process
environment (ROMP_* variables, credentials, data dir). ROMP config-file
generation lives in the `romp` adapter; keeping both out of the runner means
ROMP-specific concerns never leak into execution or supervision.
"""

from __future__ import annotations

from ai_almanac.paths import benchmark_env_dir
from ai_almanac.settings import settings


def build_job_env(config: dict, output_dir: str, figure_dir: str) -> dict[str, str]:
    """Build the environment variables the benchmark workload runs with."""
    env: dict[str, str] = {
        "ROMP_DIR_OUT": output_dir,
        "ROMP_DIR_FIG": figure_dir,
        "ROMP_MODEL_NAME": config.get("model_name", ""),
    }
    for key, value in (config.get("env") or {}).items():
        if value is not None:
            env[key] = str(value)
    if obs_dir := config.get("obs_dir"):
        env["ROMP_OBS_DIR"] = str(obs_dir)
    if model_dir := config.get("model_dir"):
        env["ROMP_MODEL_DIR"] = str(model_dir)
    if settings.cdsapi_key:
        env["CDSAPI_KEY"] = settings.cdsapi_key
        env["CDSAPI_URL"] = settings.cdsapi_url
    # Benchmark env reads its data from the app data dir for caching.
    env["AI_ALMANAC_DATA_DIR"] = str(benchmark_env_dir().parent)
    return env
