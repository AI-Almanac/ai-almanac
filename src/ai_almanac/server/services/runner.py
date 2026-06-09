"""In-process job runner.

Two implementations live here:

- `StubRunner` (default) writes synthetic but valid-shaped ROMP outputs so the
  full UI flow (submit → status → metrics map → figures) works end-to-end
  without pixi or ROMP installed. The output schema matches what ROMP itself
  produces, so the metrics/map/figure rendering code doesn't care.
- `InProcessRunner` shells out to `pixi run momp-run` inside the pixi-managed
  benchmark env for real benchmarks. Enabled by `RUNNER_MODE=pixi` (or
  whenever pixi + ROMP are set up).

`get_runner()` picks based on the `runner_mode` setting; the default is
`stub` so a fresh `ai-almanac serve` install is usable immediately.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import traceback
from datetime import UTC, datetime
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
        now = datetime.now(UTC).isoformat()
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
# Stub runner — synthetic outputs for POC / dev without ROMP installed.
# ---------------------------------------------------------------------------


class StubRunner:
    """Produce synthetic-but-valid ROMP-shaped outputs without invoking ROMP.

    Lets the full UI flow (submit → live status → metrics → spatial map →
    figures) work end-to-end before the real pixi-backed runner is wired up.
    Outputs land in the same paths ROMP would write to, with the same NetCDF
    variable schema, so the downstream metrics / map / figure code doesn't
    distinguish stub from real.
    """

    # The four forecast windows ROMP normally emits. Stub mirrors them so the
    # frontend window picker has real options to render.
    WINDOWS = ("1-7", "8-14", "15-21", "22-30")

    # Metric variables the frontend knows how to render. Keeping this in the
    # stub keeps the contract obvious; align with `romp.yaml` if it grows.
    METRIC_VARS = (
        "false_alarm_rate",
        "miss_rate",
        "mae",
        "rmse",
        "bias",
    )

    def __init__(self, storage) -> None:
        self._storage = storage

    def run_job(self, job_id: str, config: dict) -> None:
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
        import time

        broker = get_broker()
        log_path = self._storage.log_path(job_id)
        output_dir_str, figure_dir_str = self._storage.job_output_uri(job_id)
        output_dir = Path(output_dir_str)
        figure_dir = Path(figure_dir_str)
        model_name = config.get("model_name", "model")

        def log(line: str) -> None:
            with log_path.open("a") as f:
                f.write(line + "\n")
            broker.publish_threadsafe(
                job_id, JobEvent(type="log", payload={"line": line}), loop
            )

        try:
            _update_status(job_id, "running", loop=loop)
            broker.publish_threadsafe(
                job_id, JobEvent(type="status", payload={"status": "running"}), loop
            )
            log("==> [STUB RUNNER] producing synthetic ROMP-shaped outputs")
            log(f"    model:     {model_name}")
            log(f"    obs_dir:   {config.get('obs_dir')}")
            log(f"    model_dir: {config.get('model_dir')}")
            log(f"    output:    {output_dir}")

            lat, lon = self._resolve_grid(config)
            log(f"    grid:      lat={len(lat)} lon={len(lon)}")

            for window in self.WINDOWS:
                time.sleep(0.4)  # simulate work; gives the WS stream time to render live
                out_path = output_dir / f"spatial_metrics_{model_name}_{window}.nc"
                self._write_metric_nc(out_path, lat, lon, model_name, window)
                log(f"    wrote {out_path.name}")

            for figure_name in ("portrait", "panel_heatmap_skill"):
                fig_path = figure_dir / f"{figure_name}_{model_name}.png"
                self._write_placeholder_figure(fig_path, model_name, figure_name)
                log(f"    wrote figure {fig_path.name}")

            log("==> [STUB RUNNER] complete")
            _update_status(job_id, "complete", loop=loop)
            broker.publish_threadsafe(
                job_id,
                JobEvent(type="done", payload={"status": "complete"}),
                loop,
            )
        except Exception as e:  # noqa: BLE001 — surface to user
            logger.exception("stub job %s failed", job_id)
            _update_status(
                job_id,
                "failed",
                error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                loop=loop,
            )
            broker.publish_threadsafe(
                job_id,
                JobEvent(type="done", payload={"status": "failed"}),
                loop,
            )

    @staticmethod
    def _resolve_grid(config: dict) -> tuple[list[float], list[float]]:
        """Try to read the input obs to match its grid; otherwise fall back to a 10x10 stub."""
        import numpy as np

        obs_dir = config.get("obs_dir")
        if obs_dir:
            try:
                from glob import glob

                ncs = sorted(glob(str(Path(obs_dir) / "*.nc")))
                if ncs:
                    import xarray as xr

                    with xr.open_dataset(ncs[0]) as ds:
                        # Look for lat/lon (or aliases) in either coords or data_vars.
                        for lat_name in ("lat", "latitude", "LAT", "LATITUDE"):
                            if lat_name in ds.coords or lat_name in ds.dims:
                                lat_vals = ds[lat_name].values.tolist()
                                break
                        else:
                            lat_vals = list(np.linspace(-10, 10, 10))
                        for lon_name in ("lon", "longitude", "LON", "LONGITUDE"):
                            if lon_name in ds.coords or lon_name in ds.dims:
                                lon_vals = ds[lon_name].values.tolist()
                                break
                        else:
                            lon_vals = list(np.linspace(30, 50, 10))
                        return lat_vals, lon_vals
            except Exception:
                pass
        return (
            list(np.linspace(-10, 10, 10)),
            list(np.linspace(30, 50, 10)),
        )

    def _write_metric_nc(
        self,
        out_path: Path,
        lat: list[float],
        lon: list[float],
        model_name: str,
        window: str,
    ) -> None:
        import numpy as np
        import xarray as xr

        out_path.parent.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(hash((model_name, window)) & 0xFFFFFFFF)
        shape = (len(lat), len(lon))

        # Plausible bounded values per metric so the map renders meaningfully.
        values = {
            "false_alarm_rate": rng.uniform(0.0, 0.6, size=shape),
            "miss_rate": rng.uniform(0.0, 0.5, size=shape),
            "mae": rng.uniform(0.5, 5.0, size=shape),
            "rmse": rng.uniform(0.5, 6.0, size=shape),
            "bias": rng.uniform(-2.0, 2.0, size=shape),
        }
        ds = xr.Dataset(
            {name: (("lat", "lon"), arr) for name, arr in values.items()},
            coords={"lat": lat, "lon": lon},
            attrs={
                "model": model_name,
                "verification_window": window,
                "source": "ai-almanac StubRunner — synthetic values, not real metrics",
            },
        )
        ds.to_netcdf(out_path)

    def _write_placeholder_figure(self, path: Path, model_name: str, figure_name: str) -> None:
        """Render a tiny matplotlib image so the figure viewer has something to show."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np

            fig, ax = plt.subplots(figsize=(6, 4))
            ax.imshow(
                np.random.default_rng(hash(figure_name) & 0xFFFFFFFF).random((20, 30)),
                cmap="viridis",
            )
            ax.set_title(f"[STUB] {figure_name} — {model_name}")
            ax.axis("off")
            fig.tight_layout()
            fig.savefig(path, dpi=80)
            plt.close(fig)
        except Exception:
            # If matplotlib isn't installed, drop a tiny PNG so the link works.
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
                b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9c"
                b"c\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )


# ---------------------------------------------------------------------------
# Factory.
# ---------------------------------------------------------------------------

_instance: InProcessRunner | StubRunner | None = None


def get_runner() -> InProcessRunner | StubRunner:
    """Return the process-wide runner instance.

    Selection: `settings.runner_mode` ('stub' by default, 'pixi' for real
    ROMP execution once the benchmark env is prepared).
    """
    global _instance
    if _instance is None:
        if settings.runner_mode == "pixi":
            _instance = InProcessRunner(
                job_timeout_seconds=3600,
                storage=get_storage(),
            )
        else:
            _instance = StubRunner(storage=get_storage())
    return _instance


def reset_runner() -> None:
    """Force re-selection of the runner on next call. For tests / config reloads."""
    global _instance
    _instance = None
