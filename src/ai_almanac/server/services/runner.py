"""In-process job runner.

ai-almanac runs benchmark jobs as subprocesses inside the pixi-managed
benchmark environment (see `ai_almanac.envs.manager`). There is no
Docker-in-Docker and no remote runner; jobs execute on the same machine that
serves the web UI, gated by an `asyncio.Semaphore` so the GPU isn't
oversubscribed.

This module exposes the `InProcessRunner` and a process-wide `get_runner()`
factory. The historical `JobRunner` ABC and the Docker/CloudRun/Modal
implementations have been removed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from ai_almanac.envs.manager import run as pixi_run
from ai_almanac.paths import benchmark_env_dir
from ai_almanac.server.db import get_db
from ai_almanac.server.services.job_events import JobEvent, get_broker
from ai_almanac.server.services.storage import get_storage
from ai_almanac.settings import REMOTE_OBS_PROVIDERS, settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ROMP config-file generation helpers (unchanged from the docker-runner era).
# ---------------------------------------------------------------------------


def _romp_config_override_lines(env: dict[str, str]) -> str:
    extra: list[str] = [
        "plot_spatial_far_mr_mae = False",
        "plot_heatmap_bss_auc = False",
        "plot_reliability = False",
        "plot_portrait = False",
        "plot_climatology_onset = False",
        "plot_panel_heatmap_error = False",
        "plot_panel_heatmap_skill = False",
        "plot_bar_bss_rpss_auc = False",
    ]

    for env_key, cfg_key in (
        ("ROMP_LAND_ONLY", "land_only"),
        ("ROMP_SHP_ONLY", "shp_only"),
    ):
        val = env.get(env_key)
        if val is not None:
            bool_val = "False" if val.lower() in ("false", "0", "no") else "True"
            extra.append(f"{cfg_key} = {bool_val}")

    for env_key, cfg_key in (
        ("ROMP_LAT_MIN", "lat_min"),
        ("ROMP_LAT_MAX", "lat_max"),
        ("ROMP_LON_MIN", "lon_min"),
        ("ROMP_LON_MAX", "lon_max"),
    ):
        val = env.get(env_key)
        if val is not None:
            extra.append(f"{cfg_key} = {val}")

    return "\n".join(extra)


def _romp_entry_script(config_overrides: str, compute_e2s_metrics: bool) -> str:
    """Build the shell script that runs ROMP and optionally e2s metrics inside the pixi env."""
    lines = [
        "set -eu",
        'config_path="${ROMP_CONFIG_PATH:-/tmp/romp_job.in}"',
        'echo "==> Generating config from environment..."',
        "python3 -m romp.scripts.generate_config",
    ]
    if config_overrides:
        lines.append(
            "cat >> \"$config_path\" <<'ALMANAC_ROMP_OVERRIDES'\n"
            "\n# Extended region parameters (appended by almanac runner)\n"
            f"{config_overrides}\n"
            "ALMANAC_ROMP_OVERRIDES"
        )
    lines.extend(['echo "==> Starting ROMP..."', 'momp-run -p "$config_path"'])
    if compute_e2s_metrics:
        lines.extend(
            [
                'echo "==> Starting Earth2Studio metrics..."',
                "python -m ai_almanac.server.services.e2s || "
                'echo "WARNING: Earth2Studio metrics failed; ROMP outputs are still available."',
            ]
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# In-process runner.
# ---------------------------------------------------------------------------


# Bounds concurrent benchmark jobs so the GPU isn't oversubscribed. Resolved
# lazily on first submission so settings/env-vars are fully loaded.
_job_semaphore: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _job_semaphore
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(settings.max_local_jobs)
    return _job_semaphore


class InProcessRunner:
    """Run benchmark jobs as subprocesses in the local pixi env."""

    def __init__(self, job_timeout_seconds: int, storage) -> None:
        self._timeout = job_timeout_seconds
        self._storage = storage

    def run_job(self, job_id: str, config: dict) -> None:
        """Fire-and-forget. Status flows through `_update_status()`."""
        loop = asyncio.get_event_loop()
        thread = threading.Thread(
            target=self._execute, args=(job_id, config, loop), daemon=True
        )
        thread.start()

    def _execute(
        self,
        job_id: str,
        config: dict,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        log_path = self._storage.log_path(job_id)
        output_dir, figure_dir = self._storage.job_output_uri(job_id)

        env = os.environ.copy()
        env.update(self._job_env(job_id, config, output_dir, figure_dir))

        dataset_config = config.get("dataset_config", {})
        compute_e2s = dataset_config.get("provider") in REMOTE_OBS_PROVIDERS

        script = _romp_entry_script(
            _romp_config_override_lines(env),
            compute_e2s_metrics=compute_e2s,
        )

        broker = get_broker()

        try:
            _update_status(job_id, "running", loop=loop)
            broker.publish_threadsafe(
                job_id, JobEvent(type="status", payload={"status": "running"}), loop
            )
            with log_path.open("w") as logf:
                proc = pixi_run(["bash", "-c", script], env=env)
                assert proc.stdout is not None
                for line in proc.stdout:
                    logf.write(line)
                    logf.flush()
                    broker.publish_threadsafe(
                        job_id,
                        JobEvent(type="log", payload={"line": line.rstrip("\n")}),
                        loop,
                    )
                rc = proc.wait(timeout=self._timeout)
            if rc != 0:
                _update_status(
                    job_id,
                    "failed",
                    error=f"benchmark exited with code {rc}; see {log_path}",
                    loop=loop,
                )
                broker.publish_threadsafe(
                    job_id,
                    JobEvent(
                        type="done",
                        payload={"status": "failed", "exit_code": rc},
                    ),
                    loop,
                )
                return
            _update_status(job_id, "complete", loop=loop)
            broker.publish_threadsafe(
                job_id,
                JobEvent(type="done", payload={"status": "complete"}),
                loop,
            )
        except FileNotFoundError as e:
            _update_status(
                job_id,
                "failed",
                error=(
                    f"benchmark environment not available: {e}. "
                    "Run `ai-almanac env prepare` to install it."
                ),
                loop=loop,
            )
        except subprocess.TimeoutExpired:
            _update_status(
                job_id,
                "failed",
                error=f"benchmark exceeded timeout ({self._timeout}s)",
                loop=loop,
            )
        except Exception as e:  # noqa: BLE001 — surface to user
            logger.exception("job %s failed", job_id)
            _update_status(
                job_id,
                "failed",
                error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                loop=loop,
            )

    @staticmethod
    def _job_env(
        job_id: str,
        config: dict,
        output_dir: str,
        figure_dir: str,
    ) -> dict[str, str]:
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


# ---------------------------------------------------------------------------
# Status helper — invoked from the runner thread, schedules onto the FastAPI
# event loop. Kept as a free function so other callers (e.g. websocket
# streaming) can reuse it.
# ---------------------------------------------------------------------------


def _update_status(
    job_id: str,
    status: str,
    error: str | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    async def _do() -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as conn:
            if status == "complete":
                await conn.execute(
                    text(
                        "UPDATE jobs SET status = :status, completed_at = :now "
                        "WHERE id = :id"
                    ),
                    {"status": status, "now": now, "id": job_id},
                )
            else:
                await conn.execute(
                    text(
                        "UPDATE jobs SET status = :status, completed_at = :now, "
                        "error = :error WHERE id = :id"
                    ),
                    {"status": status, "now": now, "error": error, "id": job_id},
                )

    if loop is None:
        loop = asyncio.get_event_loop()
    future = asyncio.run_coroutine_threadsafe(_do(), loop)
    future.result(timeout=30)


# ---------------------------------------------------------------------------
# Factory.
# ---------------------------------------------------------------------------

_instance: InProcessRunner | None = None


def get_runner() -> InProcessRunner:
    global _instance
    if _instance is None:
        _instance = InProcessRunner(
            job_timeout_seconds=3600,
            storage=get_storage(),
        )
    return _instance
