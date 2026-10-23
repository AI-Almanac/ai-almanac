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
import re
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
from ai_almanac.settings import Settings, local_llm_model, settings

logger = logging.getLogger(__name__)

AssistantStatus = Literal["off", "loading", "ready", "sleeping", "paused"]

PAUSED_MESSAGE = (
    "The assistant is paused while a forecast runs. It'll be back when the forecast finishes."
)
LOADING_MESSAGE = "The assistant is starting up. Try again in a few minutes."

# Forecast jobs this far into their lifecycle may be holding GPU memory.
_GPU_JOB_STATUSES = ("starting", "running", "canceling")
_FORBIDDEN_SERVE_FLAGS = ("--host", "--port")
# Flags for a model outside local_llm_models.yaml when llm_serve_args is blank:
# the Spark startup tuning only. Without a tool-call parser the assistant's
# tools fail, so a custom model needs its parser set in llm_serve_args.
_CUSTOM_MODEL_SERVE_ARGS = (
    "--max-model-len 32768 --max-cudagraph-capture-size 32 --safetensors-load-strategy eager"
)
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


class AssistantStartFailed(AssistantUnavailable):
    """The last launch exited before it ever answered; it is being retried."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"The assistant's local model failed to start ({reason}). Trying again now; "
            f"if this keeps happening, see {log_path()}."
        )


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


def _mark_ready() -> None:
    """Record that the current launch answered, so a later exit isn't a failed start."""
    record = _read_record()
    if record and record.get("ready") is False:
        pid_path().write_text(json.dumps({**record, "ready": True}))


# A Python exception line in vLLM's log, after any "(EngineCore pid=N) ERROR …]" prefix.
_EXCEPTION_LINE = re.compile(r"(?:\]\s*|^|\)\s+)((?:\w+\.)*\w+(?:Error|Exception): .+)$")


def _startup_error() -> str | None:
    """The root-cause exception from the end of the log, if one is there.

    vLLM ends a failed start with "Engine core initialization failed. See root
    cause above", so the most recent exception other than that one is the cause.
    """
    try:
        with log_path().open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 256 * 1024))
            tail = log.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        match = _EXCEPTION_LINE.search(line.strip())
        if match and "Engine core initialization failed" not in match.group(1):
            reason = match.group(1)
            return reason if len(reason) <= 240 else reason[:237] + "…"
    return None


_LAUNCH_MARKER = "==> ai-almanac: starting the local model server"

# Startup stages in the order vLLM goes through them, each with the log text
# that shows it has begun. The furthest one seen is the current stage.
STARTUP_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("starting", ()),
    ("loading_weights", ("Loading model from scratch", "Loading weights")),
    # Kernel compilation (torch.compile, FlashInfer JIT) and the warmup run;
    # minutes on a first start, seconds once the kernels are cached.
    ("compiling", ("Model loading took",)),
    ("capturing", ("Capturing CUDA graph", "Graph capturing")),
    ("tuning", ("Running FlashInfer autotune", "[AutoTuner]: Tuning")),
    ("finishing", ("Starting vLLM API server", "Available routes", "Application startup")),
)
# tqdm bars, e.g. "[AutoTuner]: Tuning fp8_gemm:  69%|######9   | 11/16"
# or "Capturing CUDA graphs (decode, FULL): 57%|#####7    | 4/7".
_PROGRESS_BAR = re.compile(
    r"(?:\[AutoTuner\]: Tuning (?P<op>[\w.]+)|(?P<capture>Capturing CUDA graphs?)[^:]*):"
    r"\s*\d+%\|[^|]*\|\s*(?P<done>\d+)/(?P<total>\d+)"
)


# The first draw of each tuning pass's bar ("…: Tuning fp8_gemm:   0%|…| 0/16").
_TUNING_PASS_START = re.compile(r"\[AutoTuner\]: Tuning ([\w.]+):\s+0%\|[^|]*\|\s*0/\d+")


@dataclass
class StartupProgress:
    stage: str
    # Seconds since this launch began, when the launch recorded it.
    elapsed_seconds: float | None = None
    # What the stage is working on, e.g. the kernel being tuned.
    detail: str | None = None
    # How far through `detail` it is: `done` of `total` when vLLM reports a
    # bounded count, or just `done` (passes so far) when no total is known.
    done: int | None = None
    total: int | None = None


def _current_launch_log() -> str:
    """The log written since this launch's marker (or the tail, before markers existed)."""
    try:
        with log_path().open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 4 * 2**20))
            text = log.read().decode(errors="replace")
    except OSError:
        return ""
    start = text.rfind(_LAUNCH_MARKER)
    if start < 0:
        # A launch from before the marker: vLLM's own first line starts it.
        start = text.rfind("non-default args")
    return text[start:] if start >= 0 else text


def startup_progress() -> StartupProgress:
    """Which startup stage the managed server is in, read from its log."""
    text = _current_launch_log()
    stage = "starting"
    for name, markers in STARTUP_STAGES:
        if any(marker in text for marker in markers):
            stage = name
    progress = StartupProgress(stage=stage)
    started_at = _read_record().get("started_at")
    if isinstance(started_at, int | float):
        progress.elapsed_seconds = max(0.0, time.time() - started_at)
    if stage == "capturing":
        # The newest bar is the work in hand; tqdm redraws it with "\r".
        bars = [bar for bar in _PROGRESS_BAR.finditer(text[-64 * 1024 :]) if bar.group("capture")]
        if bars:
            progress.detail = "CUDA graphs"
            progress.done, progress.total = (
                int(bars[-1].group("done")),
                int(bars[-1].group("total")),
            )
    elif stage == "tuning":
        # One pass per kernel shape, each with its own bar, and no total known
        # up front: a per-pass bar would keep resetting, so count passes instead.
        passes = _TUNING_PASS_START.findall(text)
        if passes:
            progress.detail, progress.done = passes[-1], len(passes)
    return progress


def _failed_start() -> str | None:
    """Why the recorded launch is gone without ever answering, or None.

    Launches record `ready: false` until they first answer; records from before
    that field existed don't say, so they aren't reported as failures.
    """
    if _read_record().get("ready") is not False:
        return None
    return _startup_error() or "it exited while loading"


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


def gpu_memory_utilization() -> float:
    """The configured share, or the catalog model's own while it is unset."""
    default = Settings.model_fields["llm_gpu_memory_utilization"].default
    entry = local_llm_model(settings.llm_model)
    if settings.llm_gpu_memory_utilization == default and entry is not None:
        return float(entry["gpu_memory_utilization"])
    return settings.llm_gpu_memory_utilization


def model_serve_args() -> str:
    """Model-specific `vllm serve` flags: llm_serve_args, else the catalog's."""
    if settings.llm_serve_args.strip():
        return settings.llm_serve_args
    entry = local_llm_model(settings.llm_model)
    return entry["serve_args"] if entry is not None else _CUSTOM_MODEL_SERVE_ARGS


def serve_args() -> list[str]:
    extra = shlex.split(model_serve_args())
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
        str(gpu_memory_utilization()),
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
    # Progress is read from the log after this line, so earlier launches'
    # stages don't count toward this one.
    with log_path().open("a") as log:
        log.write(f"{_LAUNCH_MARKER} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    _proc = run_llm_server(identity["args"], env=env, log_path=log_path())
    pid_path().write_text(
        json.dumps({"pid": _proc.pid, **identity, "ready": False, "started_at": time.time()})
    )
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
        running = _live_pid()
        if running is None:
            # Stopped by a failed park (or crashed). Bring it back; the load
            # takes minutes, so this turn cannot wait for it. A launch that
            # died before ever answering is retried too, but said so: reporting
            # "starting up" again would hide a server that can't start.
            failure = _failed_start()
            _start_locked()
            if failure:
                raise AssistantStartFailed(failure)
            raise AssistantLoading()
        identity = _launch_identity()
        if {key: _read_record().get(key) for key in identity} != identity:
            # The model or its flags changed in Settings since launch.
            logger.info("restarting local model server with changed settings (pid %s)", running)
            _terminate(running)
            _start_locked(identity)
            raise AssistantLoading()
        if not _healthy():
            raise AssistantLoading()
        _mark_ready()
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
    _mark_ready()
    try:
        if gpu_job_active():
            return "paused"
        return "sleeping" if is_sleeping() else "ready"
    except httpx.HTTPError:
        return "loading"
