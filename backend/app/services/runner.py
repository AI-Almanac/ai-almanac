"""
Job runner — local Docker vs Cloud Batch.

Selected via JOB_RUNNER env var:
  docker       — runs ROMP in a local Docker container (default for dev)
  modal-local  — stages local files to Modal without GCS (dev)
  batch        — submits a Cloud Batch job (production)

Both call run_job(job_id, config) and return immediately; status updates
happen asynchronously as the job completes.
"""

from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import tarfile
import threading
import traceback
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


def _to_host_path(path: str) -> str:
    """
    Translate a container-internal path to a host path for Docker volume mounts.
    Uses the DOCKER_PATH_MAP setting when the backend itself runs in a container.
    No-op when the setting is empty (backend running directly on the host).
    """
    from ..config import settings

    for entry in settings.docker_path_map.split(","):
        entry = entry.strip()
        if "=" not in entry:
            continue
        container_prefix, host_prefix = entry.split("=", 1)
        if path.startswith(container_prefix):
            return host_prefix + path[len(container_prefix) :]
    return path


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


def _romp_entry_command(
    config_overrides: str, compute_e2s_metrics: bool = False
) -> list[str]:
    script = [
        "set -eu",
        'config_path="${ROMP_CONFIG_PATH:-/tmp/romp_job.in}"',
        'echo "==> Generating config from environment..."',
        "python3 /app/scripts/generate_config.py",
    ]
    if config_overrides:
        script.append(
            "cat >> \"$config_path\" <<'ALMANAC_ROMP_OVERRIDES'\n"
            "\n# Extended region parameters (appended by almanac runner)\n"
            f"{config_overrides}\n"
            "ALMANAC_ROMP_OVERRIDES"
        )
    script.extend(['echo "==> Starting ROMP..."', 'momp-run -p "$config_path"'])
    if compute_e2s_metrics:
        script.extend(
            [
                'echo "==> Starting Earth2Studio metrics..."',
                "python3 /almanac/e2s_metrics_runner.py || "
                'echo "WARNING: Earth2Studio metrics failed; ROMP outputs are still available."',
            ]
        )
    return ["-c", "\n".join(script)]


def _e2s_metrics_runner_host_path() -> str:
    return _to_host_path(str(Path(__file__).with_name("e2s_metrics_runner.py")))


class JobRunner(ABC):
    @abstractmethod
    def run_job(self, job_id: str, config: dict) -> None:
        """Fire and forget — start the job, return immediately."""


# ---------------------------------------------------------------------------
# Docker runner (local dev)
# ---------------------------------------------------------------------------


class DockerRunner(JobRunner):
    def __init__(self, romp_image: str, job_timeout_seconds: int, storage):
        self._image = romp_image
        self._timeout = job_timeout_seconds
        self._storage = storage

    def run_job(self, job_id: str, config: dict) -> None:
        loop = asyncio.get_event_loop()
        t = threading.Thread(target=self._run, args=(job_id, config, loop), daemon=True)
        t.start()

    def _run(self, job_id: str, config: dict, loop: asyncio.AbstractEventLoop) -> None:
        from .storage import LocalStorage
        from ..config import REMOTE_OBS_PROVIDERS

        assert isinstance(self._storage, LocalStorage), (
            "DockerRunner requires LocalStorage"
        )

        dataset_config = config.get("dataset_config", {})
        if dataset_config.get("provider") in REMOTE_OBS_PROVIDERS:
            _update_status(
                job_id,
                "failed",
                error="Remote observation datasets are not supported with the local Docker runner.",
                loop=loop,
            )
            logger.error("Job %s: remote obs dataset rejected by DockerRunner", job_id)
            return

        output_dir, figure_dir = self._storage.job_output_uri(job_id)
        log_path = self._storage.log_path(job_id)

        romp_params = dict(config.get("romp_params", {}))
        extra_mounts = []

        nc_mask_host = romp_params.get("nc_mask")
        if nc_mask_host:
            p = Path(nc_mask_host).resolve()
            extra_mounts += ["-v", f"{p.parent}:/data/masks:ro"]
            romp_params["nc_mask"] = f"/data/masks/{p.name}"

        ref_model_dir_host = romp_params.get("ref_model_dir")
        if ref_model_dir_host:
            p = Path(ref_model_dir_host).resolve()
            extra_mounts += ["-v", f"{p}:/data/ref_model:ro"]
            romp_params["ref_model_dir"] = "/data/ref_model"

        thresh_file_host = romp_params.get("thresh_file")
        if thresh_file_host:
            p = Path(thresh_file_host).resolve()
            extra_mounts += ["-v", f"{p.parent}:/data/thresh:ro"]
            romp_params["thresh_file"] = f"/data/thresh/{p.name}"

        env = {
            "ROMP_OBS_DIR": "/data/obs",
            "ROMP_MODEL_DIR": "/data/model",
            "ROMP_MODEL_NAME": config["model_name"],
            "ROMP_DIR_OUT": "/data/output",
            "ROMP_DIR_FIG": "/data/figure",
            **{
                f"ROMP_{k.upper()}": str(v)
                for k, v in romp_params.items()
                if v is not None
            },
        }

        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            f"romp-{job_id}",
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{_to_host_path(config['obs_dir'])}:/data/obs:ro",
            "-v",
            f"{_to_host_path(config['model_dir'])}:/data/model:ro",
            "-v",
            f"{_to_host_path(output_dir)}:/data/output",
            "-v",
            f"{_to_host_path(figure_dir)}:/data/figure",
            "-v",
            f"{_e2s_metrics_runner_host_path()}:/almanac/e2s_metrics_runner.py:ro",
            *extra_mounts,
        ]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd.append(self._image)
        cmd.extend(
            _romp_entry_command(
                _romp_config_override_lines(env),
                compute_e2s_metrics=bool(config.get("compute_e2s_metrics")),
            )
        )

        logger.info("Starting ROMP container for job %s", job_id)
        try:
            with log_path.open("w") as log_f:
                result = subprocess.run(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    timeout=self._timeout,
                )

            if result.returncode == 0:
                _update_status(job_id, "complete", loop=loop)
                logger.info("Job %s completed", job_id)
            elif result.returncode in (-11, 139):
                # SIGSEGV in a C extension after outputs are written — treat as success.
                if any(Path(output_dir).iterdir()):
                    _update_status(job_id, "complete", loop=loop)
                    logger.warning(
                        "Job %s segfaulted but has output — marking complete", job_id
                    )
                else:
                    _update_status(
                        job_id,
                        "failed",
                        error="Container segfaulted with no output",
                        loop=loop,
                    )
            else:
                _update_status(
                    job_id,
                    "failed",
                    error=f"Container exited with code {result.returncode}",
                    loop=loop,
                )

        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "stop", f"romp-{job_id}"], check=False)
            _update_status(job_id, "failed", error="Job exceeded timeout", loop=loop)
            logger.error("Job %s timed out", job_id)
        except Exception as exc:
            _update_status(job_id, "failed", error=str(exc), loop=loop)
            logger.exception("Job %s raised an unexpected error", job_id)


# ---------------------------------------------------------------------------
# Cloud Run Jobs runner (production)
# ---------------------------------------------------------------------------


class CloudRunJobRunner(JobRunner):
    def __init__(
        self,
        romp_image: str,
        job_timeout_seconds: int,
        project: str,
        region: str,
        worker_sa: str,
        outputs_bucket: str,
        job_cpu: str = "4",
        job_memory: str = "16Gi",
        job_cpu_probabilistic: str = "8",
        job_memory_probabilistic: str = "32Gi",
    ):
        self._image = romp_image
        self._timeout = job_timeout_seconds
        self._project = project
        self._region = region
        self._worker_sa = worker_sa
        self._outputs_bucket = outputs_bucket
        self._job_cpu = job_cpu
        self._job_memory = job_memory
        self._job_cpu_prob = job_cpu_probabilistic
        self._job_memory_prob = job_memory_probabilistic

    def run_job(self, job_id: str, config: dict) -> None:
        loop = asyncio.get_event_loop()
        t = threading.Thread(
            target=self._submit, args=(job_id, config, loop), daemon=True
        )
        t.start()

    def _submit(
        self, job_id: str, config: dict, loop: asyncio.AbstractEventLoop
    ) -> None:
        from google.cloud import run_v2
        from google.protobuf import duration_pb2

        romp_params = config.get("romp_params", {})
        probabilistic = str(romp_params.get("probabilistic", "false")).lower() == "true"
        cpu = self._job_cpu_prob if probabilistic else self._job_cpu
        memory = self._job_memory_prob if probabilistic else self._job_memory

        # Build GCS volumes and derive container-local paths from gs:// URIs.
        # One volume per unique bucket, mounted at /mnt/{bucket-name}.
        volumes: list[run_v2.Volume] = []
        volume_mounts: list[run_v2.VolumeMount] = []

        def _local_path(uri: str, read_only: bool) -> str:
            bucket, _, prefix = uri.removeprefix("gs://").partition("/")
            if not any(v.name == bucket for v in volumes):
                volumes.append(
                    run_v2.Volume(
                        name=bucket,
                        gcs=run_v2.GCSVolumeSource(bucket=bucket, read_only=read_only),
                    )
                )
                volume_mounts.append(
                    run_v2.VolumeMount(
                        name=bucket,
                        mount_path=f"/mnt/{bucket}",
                    )
                )
            return f"/mnt/{bucket}/{prefix}".rstrip("/")

        obs_local = _local_path(config["obs_dir"], read_only=True)
        model_local = _local_path(config["model_dir"], read_only=True)
        _local_path(f"gs://{self._outputs_bucket}/", read_only=False)

        env_vars = [
            run_v2.EnvVar(name="ROMP_OBS_DIR", value=obs_local),
            run_v2.EnvVar(name="ROMP_MODEL_DIR", value=model_local),
            run_v2.EnvVar(name="ROMP_MODEL_NAME", value=config["model_name"]),
            run_v2.EnvVar(
                name="ROMP_DIR_OUT",
                value=f"/mnt/{self._outputs_bucket}/{job_id}/output",
            ),
            run_v2.EnvVar(
                name="ROMP_DIR_FIG",
                value=f"/mnt/{self._outputs_bucket}/{job_id}/figure",
            ),
            *[
                run_v2.EnvVar(name=f"ROMP_{k.upper()}", value=str(v))
                for k, v in romp_params.items()
                if v is not None
            ],
        ]

        job_name = f"romp-{job_id.replace('_', '-')}"[:49]
        parent = f"projects/{self._project}/locations/{self._region}"

        cloud_run_job = run_v2.Job(
            template=run_v2.ExecutionTemplate(
                template=run_v2.TaskTemplate(
                    containers=[
                        run_v2.Container(
                            image=self._image,
                            env=env_vars,
                            volume_mounts=volume_mounts,
                            resources=run_v2.ResourceRequirements(
                                limits={"cpu": cpu, "memory": memory},
                            ),
                        )
                    ],
                    volumes=volumes,
                    service_account=self._worker_sa,
                    max_retries=0,
                    timeout=duration_pb2.Duration(seconds=self._timeout),
                ),
            ),
        )

        jobs_client = run_v2.JobsClient()
        execution_name: str | None = None

        try:
            jobs_client.create_job(
                parent=parent,
                job=cloud_run_job,
                job_id=job_name,
            ).result(timeout=None)
            logger.info("Created Cloud Run Job %s for job_id %s", job_name, job_id)

            execution = jobs_client.run_job(
                name=f"{parent}/jobs/{job_name}",
            ).result(timeout=None)
            execution_name = execution.name
            logger.info("Started execution %s", execution_name)

            self._poll(job_id, execution_name, loop)

        except Exception as exc:
            _update_status(job_id, "failed", error=str(exc), loop=loop)
            logger.exception("Cloud Run Job failed for %s", job_id)
        finally:
            try:
                jobs_client.delete_job(name=f"{parent}/jobs/{job_name}").result()
            except Exception:
                pass

    def _fetch_execution_error(self, execution_name: str) -> str:
        from .logging import fetch_cloud_logs

        execution_id = execution_name.split("/")[-1]
        filter_expr = (
            f'resource.type="cloud_run_job" '
            f'AND labels."run.googleapis.com/execution_name"=~"{execution_id}"'
        )
        result = fetch_cloud_logs(filter_expr, max_entries=20, descending=True)
        return (
            result
            if result != "(no logs found)"
            else "Cloud Run Job task failed — check Cloud Logging"
        )

    def _poll(
        self, job_id: str, execution_name: str, loop: asyncio.AbstractEventLoop
    ) -> None:
        import time
        from google.cloud import run_v2

        client = run_v2.ExecutionsClient()
        while True:
            time.sleep(15)
            try:
                ex = client.get_execution(name=execution_name)
                if ex.succeeded_count > 0:
                    _update_status(job_id, "complete", loop=loop)
                    logger.info("Execution %s succeeded", execution_name)
                    return
                if ex.failed_count > 0:
                    error_msg = self._fetch_execution_error(execution_name)
                    _update_status(job_id, "failed", error=error_msg, loop=loop)
                    logger.error("Execution %s failed", execution_name)
                    return
            except Exception as exc:
                logger.exception("Error polling execution %s: %s", execution_name, exc)


# ---------------------------------------------------------------------------
# Modal runner (production alternative to Cloud Run Jobs)
# ---------------------------------------------------------------------------


class ModalRunner(JobRunner):
    def __init__(self, outputs_bucket: str, job_timeout_seconds: int):
        self._outputs_bucket = outputs_bucket
        self._timeout = job_timeout_seconds

    def run_job(self, job_id: str, config: dict) -> None:
        loop = asyncio.get_event_loop()
        t = threading.Thread(
            target=self._submit_and_poll, args=(job_id, config, loop), daemon=True
        )
        t.start()

    def _submit_and_poll(
        self, job_id: str, config: dict, loop: asyncio.AbstractEventLoop
    ) -> None:
        import time
        import modal

        preflight_error = self._preflight_error(config)
        if preflight_error:
            _update_status(job_id, "failed", error=preflight_error, loop=loop)
            logger.error(
                "Modal job %s rejected before spawn: %s", job_id, preflight_error
            )
            return

        try:
            run_romp = modal.Function.from_name("almanac-romp", "run_benchmark")
            handle = run_romp.spawn(job_id, config, self._outputs_bucket)
            logger.info("Spawned Modal function for job %s", job_id)
        except Exception as exc:
            _update_status(
                job_id,
                "failed",
                error=f"Failed to spawn Modal function: {exc}",
                loop=loop,
            )
            logger.exception("Failed to spawn Modal job %s", job_id)
            return

        deadline = time.time() + self._timeout
        while time.time() < deadline:
            time.sleep(15)
            try:
                handle.get(timeout=0)
                _update_status(job_id, "complete", loop=loop)
                logger.info("Modal job %s completed", job_id)
                return
            except TimeoutError:
                continue  # still running
            except Exception as exc:
                error = self._modal_failure_error(job_id, exc)
                _update_status(job_id, "failed", error=error, loop=loop)
                logger.error("Modal job %s failed: %s", job_id, error)
                return

        handle.cancel()
        _update_status(job_id, "failed", error="Job exceeded timeout", loop=loop)
        logger.error("Modal job %s timed out", job_id)

    def _modal_failure_error(self, job_id: str, exc: Exception) -> str:
        try:
            from google.cloud import storage as gcs

            blob = gcs.Client().bucket(self._outputs_bucket).blob(f"{job_id}/run.log")
            if not blob.exists():
                return str(exc)
            log_text = blob.download_as_text()
            tail = "\n".join(log_text.strip().splitlines()[-20:])
            if not tail:
                return str(exc)
            return f"{exc}\n\nLast run log lines:\n{tail}"
        except Exception:
            logger.exception("Could not fetch Modal run log for failed job %s", job_id)
            return str(exc)

    def _preflight_error(self, config: dict) -> str | None:
        from ..config import REMOTE_OBS_PROVIDERS

        if not self._outputs_bucket:
            return "JOB_RUNNER=modal requires GCS_OUTPUTS_BUCKET. Use JOB_RUNNER=modal-local for local filesystem outputs."

        dataset_config = config.get("dataset_config", {})
        if dataset_config.get("provider") not in REMOTE_OBS_PROVIDERS:
            obs_dir = config.get("obs_dir", "")
            if not str(obs_dir).startswith("gs://"):
                return (
                    f"JOB_RUNNER=modal requires obs_dir to be a gs:// URI; got {obs_dir!r}. "
                    "Use JOB_RUNNER=modal-local for local filesystem inputs."
                )

        model_dir = config.get("model_dir", "")
        if not str(model_dir).startswith("gs://"):
            return (
                f"JOB_RUNNER=modal requires model_dir to be a gs:// URI; got {model_dir!r}. "
                "Use JOB_RUNNER=modal-local for local filesystem inputs."
            )

        return None


# ---------------------------------------------------------------------------
# Modal local runner (dev, no GCS)
# ---------------------------------------------------------------------------


class ModalLocalRunner(JobRunner):
    def __init__(self, job_timeout_seconds: int, storage):
        from .storage import LocalStorage

        assert isinstance(storage, LocalStorage), (
            "ModalLocalRunner requires STORAGE_BACKEND=local"
        )
        self._timeout = job_timeout_seconds
        self._storage = storage

    def run_job(self, job_id: str, config: dict) -> None:
        loop = asyncio.get_event_loop()
        t = threading.Thread(
            target=self._submit_and_poll, args=(job_id, config, loop), daemon=True
        )
        t.start()

    def _submit_and_poll(
        self, job_id: str, config: dict, loop: asyncio.AbstractEventLoop
    ) -> None:
        import time
        import modal

        try:
            bundle = _build_modal_local_bundle(config)
            runtime_env = _modal_local_runtime_env(config)
            run_benchmark = modal.Function.from_name(
                "almanac-romp", "run_benchmark_local"
            )
            handle = run_benchmark.spawn(job_id, config, bundle, runtime_env)
            logger.info("Spawned local Modal function for job %s", job_id)
        except Exception as exc:
            self._write_failure_log(
                job_id,
                "Failed to spawn local Modal function",
                exc,
            )
            _update_status(
                job_id,
                "failed",
                error=f"Failed to spawn local Modal function: {exc}",
                loop=loop,
            )
            logger.exception("Failed to spawn local Modal job %s", job_id)
            return

        deadline = time.time() + self._timeout
        while time.time() < deadline:
            time.sleep(15)
            try:
                result = handle.get(timeout=0)
                if not result.get("ok", False):
                    self._persist_result(job_id, result)
                    error = result.get("error") or "Local Modal benchmark failed"
                    _update_status(job_id, "failed", error=error, loop=loop)
                    logger.error("Local Modal job %s failed: %s", job_id, error)
                    return
                self._persist_result(job_id, result)
                _update_status(job_id, "complete", loop=loop)
                logger.info("Local Modal job %s completed", job_id)
                return
            except TimeoutError:
                continue
            except Exception as exc:
                self._write_failure_log(
                    job_id,
                    "Local Modal function failed before returning logs",
                    exc,
                )
                _update_status(job_id, "failed", error=str(exc), loop=loop)
                logger.error("Local Modal job %s failed: %s", job_id, exc)
                return

        handle.cancel()
        log_path = self._storage.log_path(job_id)
        log_path.write_text("Local Modal benchmark exceeded timeout.\n")
        _update_status(job_id, "failed", error="Job exceeded timeout", loop=loop)
        logger.error("Local Modal job %s timed out", job_id)

    def _persist_result(self, job_id: str, result: dict) -> None:
        output_dir, figure_dir = self._storage.job_output_uri(job_id)
        dirs = {"output": Path(output_dir), "figure": Path(figure_dir)}

        for file_info in result.get("files", []):
            kind = file_info.get("kind")
            filename = file_info.get("filename")
            data = file_info.get("data")
            if kind not in dirs or not filename or not isinstance(data, bytes):
                continue
            safe_name = Path(filename).name
            (dirs[kind] / safe_name).write_bytes(data)

        log_path = self._storage.log_path(job_id)
        log_text = result.get("log") or ""
        if not log_text and result.get("error"):
            log_text = f"Local Modal benchmark failed: {result['error']}\n"
        log_path.write_text(log_text)

    def _write_failure_log(self, job_id: str, summary: str, exc: Exception) -> None:
        log_path = self._storage.log_path(job_id)
        log_path.write_text(
            f"{summary}: {exc}\n\n"
            f"{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
        )


def _build_modal_local_bundle(config: dict) -> bytes:
    from ..config import REMOTE_OBS_PROVIDERS

    dataset_config = config.get("dataset_config", {})
    uses_remote_obs = dataset_config.get("provider") in REMOTE_OBS_PROVIDERS

    obs_dir = config.get("obs_dir")
    model_dir = config.get("model_dir")
    if not model_dir:
        raise ValueError("modal-local requires local model_dir")
    if not obs_dir and not uses_remote_obs:
        raise ValueError("modal-local requires local obs_dir for local datasets")

    model_path = Path(model_dir).resolve()
    if not model_path.is_dir():
        raise ValueError(f"model_dir is not a directory: {model_path}")
    obs_path = Path(obs_dir).resolve() if obs_dir else None
    if obs_path is not None and not obs_path.is_dir():
        raise ValueError(f"obs_dir is not a directory: {obs_path}")

    romp_params = config.get("romp_params", {})
    start_year = int((romp_params.get("start_date") or "1990-01-01")[:4])
    end_year = int((romp_params.get("end_date") or "2024-01-01")[:4])
    year_files = {f"{year}.nc" for year in range(start_year, end_year + 1)}
    missing_model_files = sorted(
        name for name in year_files if not (model_path / name).is_file()
    )
    if missing_model_files:
        preview = ", ".join(missing_model_files[:5])
        suffix = (
            ""
            if len(missing_model_files) <= 5
            else f", ... ({len(missing_model_files)} missing)"
        )
        raise ValueError(
            f"model_dir is missing required year files for modal-local: {preview}{suffix}. "
            f"model_dir={model_path}"
        )

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if obs_path is not None:
            _add_directory_children(tar, obs_path, "obs")
        _add_directory_children(tar, model_path, "model", include_names=year_files)
    return buffer.getvalue()


def _modal_local_runtime_env(config: dict) -> dict[str, str]:
    dataset_config = config.get("dataset_config", {})
    if dataset_config.get("provider") != "earth2studio":
        return {}

    from ..config import settings

    env: dict[str, str] = {}
    if settings.cdsapi_url:
        env["CDSAPI_URL"] = settings.cdsapi_url
    if settings.cdsapi_key:
        env["CDSAPI_KEY"] = settings.cdsapi_key

    if not env.get("CDSAPI_KEY"):
        raise ValueError(
            "modal-local Earth2Studio datasets require CDSAPI_KEY in backend/.env "
            "or the backend process environment."
        )
    return env


def _add_directory_children(
    tar: tarfile.TarFile,
    source_dir: Path,
    arc_dir: str,
    include_names: set[str] | None = None,
) -> None:
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        if include_names is not None and path.name not in include_names:
            continue
        tar.add(path, arcname=f"{arc_dir}/{path.name}")


# ---------------------------------------------------------------------------
# Shared status helper
# ---------------------------------------------------------------------------


def _update_status(
    job_id: str,
    status: str,
    error: str | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Write a job status update from a background thread onto the main event loop."""
    from datetime import datetime, timezone
    from ..database import get_db
    from sqlalchemy import text

    async def _do() -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as conn:
            if status == "complete":
                await conn.execute(
                    text(
                        "UPDATE jobs SET status = :status, completed_at = :now WHERE id = :id"
                    ),
                    {"status": status, "now": now, "id": job_id},
                )
            else:
                await conn.execute(
                    text(
                        "UPDATE jobs SET status = :status, completed_at = :now, error = :error WHERE id = :id"
                    ),
                    {"status": status, "now": now, "error": error, "id": job_id},
                )

    if loop is None:
        loop = asyncio.get_event_loop()
    future = asyncio.run_coroutine_threadsafe(_do(), loop)
    future.result(timeout=30)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_instance: JobRunner | None = None


def get_runner() -> JobRunner:
    global _instance
    if _instance is None:
        _instance = _make_runner()
    return _instance


def _make_runner() -> JobRunner:
    from ..config import settings
    from .storage import get_storage

    runner = settings.job_runner.lower()
    if runner in ("modal-local", "modal_local", "modaldev", "modal-dev"):
        return ModalLocalRunner(
            job_timeout_seconds=settings.job_timeout_seconds,
            storage=get_storage(),
        )
    if runner == "modal":
        return ModalRunner(
            outputs_bucket=settings.gcs_outputs_bucket,
            job_timeout_seconds=settings.job_timeout_seconds,
        )
    if runner in ("cloudrun", "batch"):
        image = settings.romp_wrapper_image or settings.romp_image
        return CloudRunJobRunner(
            romp_image=image,
            job_timeout_seconds=settings.job_timeout_seconds,
            project=settings.gcp_project,
            region=settings.gcp_region,
            worker_sa=settings.batch_worker_sa,
            outputs_bucket=settings.gcs_outputs_bucket,
            job_cpu=settings.job_cpu,
            job_memory=settings.job_memory,
            job_cpu_probabilistic=settings.job_cpu_probabilistic,
            job_memory_probabilistic=settings.job_memory_probabilistic,
        )
    # Default: docker
    return DockerRunner(
        romp_image=settings.romp_wrapper_image or settings.romp_image,
        job_timeout_seconds=settings.job_timeout_seconds,
        storage=get_storage(),
    )
