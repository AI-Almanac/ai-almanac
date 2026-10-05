"""The bundled vLLM server behind the chat assistant (`llm_engine = "managed"`).

On unified-memory hosts (DGX Spark) the model and a forecast share one pool of
RAM, so the model steps aside while a forecast runs: the job supervisor parks
it (vLLM sleep level 2 discards weights and KV cache, keeping the process and
its CUDA graphs), chat turns refuse with `AssistantPaused` until the forecast
finishes, and the next turn afterwards wakes it. Sleep level 1 would offload
the weights to CPU RAM, which on the Spark is the same memory and frees
nothing.

Three processes touch the server: `serve` (start/stop, chat wakes, idle
sleep), and each job supervisor (parking). Every state change takes
`llm.lock` in the data dir so a chat-triggered wake cannot race a
supervisor-triggered sleep. All functions here are synchronous and block;
call them via `asyncio.to_thread` from async code.

vLLM's sleep/wake endpoints need VLLM_SERVER_DEV_MODE and are unauthenticated
(its --api-key only guards /v1), so the server only ever binds to loopback.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from ai_almanac.locking import file_lock, try_file_lock
from ai_almanac.paths import data_root
from ai_almanac.settings import settings

logger = logging.getLogger(__name__)

AssistantStatus = Literal["off", "loading", "ready", "sleeping", "paused"]

PAUSED_MESSAGE = (
    "The assistant is paused while a forecast runs. It'll be back when the forecast finishes."
)
LOADING_MESSAGE = "The assistant is starting up. Try again in a few minutes."

# Forecast jobs this far into their lifecycle may be holding GPU memory.
_GPU_JOB_STATUSES = ("starting", "running", "canceling")
_FORBIDDEN_SERVE_FLAGS = ("--host", "--port")
# Wake reloads the weights from the checkpoint: ~25 s for the default model on
# the Spark with eager safetensors loading, ~2 min with vLLM's mmap default.
# Generous so a slower disk or a larger model doesn't fail the wake.
_WAKE_TIMEOUT_SECONDS = 900.0
_STOP_GRACE_SECONDS = 30.0
_DRAIN_TIMEOUT_SECONDS = 120.0

# Last chat turn in this process (the `serve` process owns chat and the idle
# timer). Seeded by start() so a freshly loaded model idles out too.
_last_used: float = 0.0
# The Popen for a server this process launched, kept so its exit is reaped.
_proc: subprocess.Popen | None = None


class AssistantUnavailable(RuntimeError):
    """The managed model cannot answer right now; `str(exc)` is user-facing."""


class AssistantPaused(AssistantUnavailable):
    def __init__(self) -> None:
        super().__init__(PAUSED_MESSAGE)


class AssistantLoading(AssistantUnavailable):
    def __init__(self) -> None:
        super().__init__(LOADING_MESSAGE)


def is_managed() -> bool:
    return settings.llm_engine == "managed" and settings.deployment_mode == "personal"


def pid_path() -> Path:
    return data_root() / "llm.pid"


def log_path() -> Path:
    return data_root() / "llm.log"


def _lock_path() -> Path:
    return data_root() / "llm.lock"


def _lock():
    return file_lock(
        _lock_path(),
        message="waiting for another process to finish changing the local model's state…",
    )


def _base_url() -> str:
    return f"http://127.0.0.1:{settings.llm_port}"


def _client(timeout: float = 5.0) -> httpx.Client:
    return httpx.Client(base_url=_base_url(), timeout=timeout)


# ---------------------------------------------------------------------------
# Process bookkeeping
# ---------------------------------------------------------------------------


def _read_record() -> dict:
    try:
        data = json.loads(pid_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_pid() -> int | None:
    try:
        return int(_read_record()["pid"])
    except (KeyError, TypeError, ValueError):
        return None


def _launch_identity() -> dict:
    """What a running server must match to be reused rather than replaced."""
    from ai_almanac.paths import llm_env_dir

    return {"args": serve_args(), "env_dir": str(llm_env_dir())}


def _is_our_server(pid: int) -> bool:
    """True if `pid` is alive and is a vLLM server (guards against PID reuse)."""
    global _proc
    if _proc is not None and _proc.pid == pid and _proc.poll() is not None:
        _proc = None  # reap our own exited child
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"vllm" in cmdline and b"serve" in cmdline


def _live_pid() -> int | None:
    pid = _read_pid()
    return pid if pid is not None and _is_our_server(pid) else None


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        logger.warning("cannot signal local model process group %s", pid)


def _terminate(pid: int, grace: float = _STOP_GRACE_SECONDS) -> None:
    """SIGTERM the server's process group, escalating to SIGKILL."""
    _signal_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _is_our_server(pid):
            break
        time.sleep(0.5)
    else:
        _signal_group(pid, signal.SIGKILL)
    if _proc is not None and _proc.pid == pid:
        with suppress(subprocess.TimeoutExpired):
            _proc.wait(timeout=5)


def _kill_recorded() -> None:
    pid = _live_pid()
    if pid is not None:
        _terminate(pid)
    pid_path().unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def serve_args() -> list[str]:
    extra = shlex.split(settings.llm_serve_args)
    for flag in extra:
        name = flag.split("=", 1)[0]
        if name in _FORBIDDEN_SERVE_FLAGS:
            raise ValueError(
                f"llm_serve_args must not set {name}: the local model server binds "
                "to loopback on llm_port"
            )
    return [
        "vllm",
        "serve",
        settings.llm_model,
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.llm_port),
        "--enable-sleep-mode",
        "--gpu-memory-utilization",
        str(settings.llm_gpu_memory_utilization),
        *extra,
    ]


def start() -> None:
    """Launch the managed server, or adopt one a previous `serve` left running.

    A cold start takes minutes (weight processing, warmup, CUDA graph
    capture), so a live server launched with the same flags — left asleep by
    a clean shutdown, or still running after a crash — is reused. One launched
    with different flags is replaced.

    Returns once the process is spawned; the model loads in the background
    and `status()` reports "loading" until it answers.
    """
    global _last_used
    from ai_almanac.envs.manager import llm_env_ready

    if not llm_env_ready():
        raise RuntimeError(
            "The local model environment is not installed; run `ai-almanac init` "
            "or `ai-almanac env prepare`."
        )
    identity = _launch_identity()
    with _lock():
        running = _live_pid()
        if running is not None:
            record = _read_record()
            if {key: record.get(key) for key in identity} == identity:
                logger.info("reusing running local model server (pid %s)", running)
                _last_used = time.monotonic()
                return
            logger.info("replacing local model server with changed settings (pid %s)", running)
            _terminate(running)
        _start_locked(identity)


def stop() -> None:
    """Shut the server down entirely (`ai-almanac llm stop`)."""
    with _lock():
        _kill_recorded()


def suspend() -> None:
    """On `serve` shutdown: sleep the model so its memory is free while nothing
    can use it, but keep the process for the next `serve` to adopt.

    Sleeping is fast and a later wake (~25 s) beats a cold start (~45 s). A
    server that is still loading is stopped instead — it cannot be slept, and
    left alone it would come up awake with no idle timer to put it back down.
    """
    with _lock():
        if _live_pid() is None:
            return
        try:
            if not _healthy():
                raise RuntimeError("server is still loading")
            if not is_sleeping():
                sleep()
            return
        except Exception as exc:  # noqa: BLE001 — memory safety wins; fall back to kill
            logger.info("stopping local model server instead of sleeping it (%s)", exc)
        _kill_recorded()


def _start_locked(identity: dict | None = None) -> None:
    global _proc, _last_used
    from ai_almanac.envs.manager import run_llm_server

    identity = identity or _launch_identity()
    env = {**os.environ, "VLLM_SERVER_DEV_MODE": "1"}
    _proc = run_llm_server(identity["args"], env=env, log_path=log_path())
    pid_path().write_text(json.dumps({"pid": _proc.pid, **identity}))
    _last_used = time.monotonic()
    logger.info("started local model server (pid %s), log: %s", _proc.pid, log_path())


# ---------------------------------------------------------------------------
# vLLM HTTP state
# ---------------------------------------------------------------------------


def _healthy() -> bool:
    try:
        with _client(timeout=2.0) as client:
            return client.get("/health").status_code == 200
    except httpx.HTTPError:
        return False


def is_sleeping() -> bool:
    with _client() as client:
        resp = client.get("/is_sleeping")
        resp.raise_for_status()
        return bool(resp.json().get("is_sleeping"))


def _requests_in_flight() -> float:
    """Running + waiting requests, from vLLM's Prometheus metrics.

    The metrics are per-server, so this sees every client's requests (including
    other processes' chat turns) without any bookkeeping on our side.
    """
    with _client() as client:
        resp = client.get("/metrics")
        resp.raise_for_status()
    total = 0.0
    for line in resp.text.splitlines():
        if line.startswith(("vllm:num_requests_running", "vllm:num_requests_waiting")):
            total += float(line.rsplit(" ", 1)[-1])
    return total


def drain(timeout: float = _DRAIN_TIMEOUT_SECONDS) -> bool:
    """Wait for in-flight requests to finish. Returns False on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        if _requests_in_flight() == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def sleep() -> None:
    # mode=wait: anything that slipped in after drain() still finishes rather
    # than being aborted mid-reply.
    with _client(timeout=_DRAIN_TIMEOUT_SECONDS) as client:
        client.post("/sleep", params={"level": 2, "mode": "wait"}).raise_for_status()


def wake() -> None:
    """Undo a level-2 sleep: re-allocate weights, reload them, then KV cache.

    Level 2 discarded the weights, so they are re-read from the checkpoint
    between the two wake stages; waking the KV cache last keeps peak memory at
    weights-only while the reload runs.
    """
    with _client(timeout=_WAKE_TIMEOUT_SECONDS) as client:
        client.post("/wake_up", params={"tags": "weights"}).raise_for_status()
        client.post("/collective_rpc", json={"method": "reload_weights"}).raise_for_status()
        client.post("/wake_up", params={"tags": "kv_cache"}).raise_for_status()


# ---------------------------------------------------------------------------
# Forecast coordination
# ---------------------------------------------------------------------------


def gpu_job_active() -> bool:
    """True while a local forecast job may be using the GPU.

    Only jobs with a live supervisor count: a crashed job must not pause the
    assistant until reconciliation catches up.
    """
    import sqlalchemy as sa

    from ai_almanac.server.services.job_manager import _heartbeat_is_fresh
    from ai_almanac.server.sync_db import sync_engine
    from ai_almanac.server.tables import jobs

    with sync_engine().connect() as conn:
        rows = conn.execute(
            sa.select(jobs.c.config_json, jobs.c.runner, jobs.c.heartbeat_at).where(
                jobs.c.status.in_(_GPU_JOB_STATUSES)
            )
        ).fetchall()
    for config_json, runner, heartbeat_at in rows:
        if runner == "modal" or not _heartbeat_is_fresh(heartbeat_at):
            continue
        try:
            config = json.loads(config_json or "{}")
        except json.JSONDecodeError:
            continue
        if config.get("job_type") == "forecast":
            return True
    return False


@dataclass(frozen=True, slots=True)
class ParkResult:
    ok: bool
    detail: str


def park_for_gpu_job() -> ParkResult:
    """Free the model's memory before a forecast starts. Never raises.

    Drains in-flight requests, sleeps at level 2, and confirms via
    /is_sleeping. If any step fails the server is killed instead: memory
    safety wins over wake latency. The next chat turn after the forecast
    restarts it.
    """
    with _lock():
        if _live_pid() is None:
            return ParkResult(True, "local model is not running")
        try:
            if is_sleeping():
                return ParkResult(True, "local model already asleep")
            if not drain():
                raise TimeoutError("in-flight requests did not finish")
            sleep()
            if not is_sleeping():
                raise RuntimeError("server did not report sleeping")
            return ParkResult(True, "local model asleep; memory released")
        except Exception as exc:  # noqa: BLE001 — any failure falls back to kill
            logger.warning("parking local model failed (%s); killing it", exc)
            _kill_recorded()
            return ParkResult(False, f"could not sleep the local model ({exc}); stopped it")


def needs_wake() -> bool:
    """Cheap check so chat can tell the user a wake (tens of seconds) is coming."""
    try:
        return _live_pid() is not None and _healthy() and is_sleeping()
    except httpx.HTTPError:
        return False


def ensure_awake() -> None:
    """Make the model ready for a chat turn, or raise AssistantUnavailable."""
    global _last_used
    with _lock():
        if gpu_job_active():
            raise AssistantPaused()
        if _live_pid() is None:
            # Stopped by a failed park (or crashed). Bring it back; the load
            # takes minutes, so this turn cannot wait for it.
            _start_locked()
            raise AssistantLoading()
        if not _healthy():
            raise AssistantLoading()
        if is_sleeping():
            started = time.monotonic()
            wake()
            logger.info("woke local model in %.1fs", time.monotonic() - started)
        _last_used = time.monotonic()


def idle_sleep() -> None:
    """Sleep the model once it has gone unused for llm_idle_sleep_minutes."""
    if not is_managed() or settings.llm_idle_sleep_minutes <= 0:
        return
    idle_for = time.monotonic() - _last_used
    if idle_for < settings.llm_idle_sleep_minutes * 60:
        return
    if _live_pid() is None or not _healthy():
        return
    # Never wait: this runs in the server's reconciler loop, and a wake or a
    # park can hold the lock for minutes. Whoever holds it is changing the
    # state anyway; the next tick re-checks.
    with try_file_lock(_lock_path()) as acquired:
        if not acquired or is_sleeping() or _requests_in_flight() > 0:
            return
        sleep()
        logger.info("local model idle for %.0f min; asleep", idle_for / 60)


def status() -> AssistantStatus:
    if not is_managed() or _live_pid() is None:
        return "off"
    if not _healthy():
        return "loading"
    try:
        if gpu_job_active():
            return "paused"
        return "sleeping" if is_sleeping() else "ready"
    except httpx.HTTPError:
        return "loading"
