"""The managed local vLLM: parking for forecasts, waking for chat, idle sleep.

A fake vLLM (httpx MockTransport) stands in for the real server; process
liveness and termination are stubbed so no subprocess is ever launched.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services import local_llm
from ai_almanac.settings import DEFAULT_LOCAL_LLM_MODEL, reload_settings, settings

_FAKE_PID = 424242
_REAL_TERMINATE = local_llm._terminate


class FakeVllm:
    """Just enough of vLLM's dev/metrics API to drive local_llm."""

    def __init__(self, *, running: list[float] | None = None, sleep_status: int = 200) -> None:
        # Successive num_requests_running readings; the last one repeats.
        self.running = list(running or [0.0])
        self.sleep_status = sleep_status
        self.sleeping = False
        self.calls: list[str] = []
        self.rpcs: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = request.url.query.decode()
        self.calls.append(f"{request.method} {path}" + (f"?{query}" if query else ""))
        if path == "/health":
            return httpx.Response(200)
        if path == "/metrics":
            value = self.running.pop(0) if len(self.running) > 1 else self.running[0]
            return httpx.Response(
                200,
                text=(
                    "# HELP vllm:num_requests_running Number of requests running.\n"
                    f'vllm:num_requests_running{{engine="0",model_name="local"}} {value}\n'
                    'vllm:num_requests_waiting{engine="0",model_name="local"} 0.0\n'
                ),
            )
        if path == "/is_sleeping":
            return httpx.Response(200, json={"is_sleeping": self.sleeping})
        if path == "/sleep":
            if self.sleep_status == 200:
                self.sleeping = True
            return httpx.Response(self.sleep_status)
        if path == "/wake_up":
            if request.url.params.get("tags") == "kv_cache":
                self.sleeping = False
            return httpx.Response(200)
        if path == "/abort_requests":
            assert json.loads(request.content) == {}, "an empty body aborts everything"
            self.running = [0.0]
            return httpx.Response(200, json={})
        if path == "/collective_rpc":
            method = json.loads(request.content)["method"]
            assert method in ("reload_weights", "reload_draft_weights")
            self.rpcs.append(method)
            return httpx.Response(200, json={"results": [None]})
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [call for call in self.calls if not call.endswith(("/health", "/metrics"))]


@pytest.fixture
def managed(monkeypatch: pytest.MonkeyPatch):
    """Managed engine with a live fake server; records terminations."""
    monkeypatch.setattr(settings, "llm_engine", "managed")
    monkeypatch.setattr(settings, "deployment_mode", "personal")
    monkeypatch.setattr(local_llm.time, "sleep", lambda _s: None)
    local_llm.pid_path().write_text(json.dumps({"pid": _FAKE_PID, **local_llm._launch_identity()}))
    alive = {"value": True}
    terminated: list[int] = []

    def fake_terminate(pid: int, grace: float = 0) -> None:
        terminated.append(pid)
        alive["value"] = False

    monkeypatch.setattr(local_llm, "_is_our_server", lambda pid: alive["value"])
    monkeypatch.setattr(local_llm, "_terminate", fake_terminate)

    def use(fake: FakeVllm) -> FakeVllm:
        transport = httpx.MockTransport(fake.handler)
        monkeypatch.setattr(
            local_llm,
            "_client",
            lambda timeout=5.0: httpx.Client(
                base_url="http://127.0.0.1:8766", transport=transport, timeout=timeout
            ),
        )
        return fake

    yield use, terminated
    local_llm.pid_path().unlink(missing_ok=True)


def _insert_job(status: str, job_type: str = "forecast", runner: str = "local") -> str:
    job_id = f"llm-{uuid.uuid4().hex[:8]}"
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, external_id, created_at) "
            "VALUES ('llm-u', 'llm-u', '2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO jobs (id, user_id, dataset_id, status, config_json, runner, "
            "created_at, heartbeat_at) VALUES (?, 'llm-u', 'ds', ?, ?, ?, ?, ?)",
            (
                job_id,
                status,
                json.dumps({"job_type": job_type}),
                runner,
                "2026-01-01T00:00:00",
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    return job_id


@pytest.fixture
def clean_jobs():
    yield
    with sqlite3.connect(database_path()) as conn:
        conn.execute("DELETE FROM jobs WHERE user_id = 'llm-u'")
        conn.commit()


def test_park_drains_then_sleeps(managed) -> None:
    use, terminated = managed
    fake = use(FakeVllm(running=[1.0, 1.0, 0.0]))

    result = local_llm.park_for_gpu_job()

    assert result.ok, result.detail
    assert fake.running == [0.0], "drain polled until no request was running"
    metrics_before_sleep = fake.calls.index("POST /sleep?level=2&mode=wait")
    assert fake.calls[:metrics_before_sleep].count("GET /metrics") == 3
    assert fake.paths()[-1] == "GET /is_sleeping"
    assert fake.sleeping
    assert terminated == []


def test_park_kills_server_when_sleep_fails(managed) -> None:
    use, terminated = managed
    use(FakeVllm(sleep_status=500))

    result = local_llm.park_for_gpu_job()

    assert not result.ok
    assert terminated == [_FAKE_PID]
    assert not local_llm.pid_path().exists()


def test_park_is_noop_without_a_running_server(managed, monkeypatch) -> None:
    use, terminated = managed
    fake = use(FakeVllm())
    monkeypatch.setattr(local_llm, "_is_our_server", lambda pid: False)

    assert local_llm.park_for_gpu_job().ok
    assert fake.calls == []
    assert terminated == []


def test_ensure_awake_pauses_while_forecast_runs(managed, clean_jobs) -> None:
    use, _ = managed
    fake = use(FakeVllm())
    fake.sleeping = True
    _insert_job("running")

    with pytest.raises(local_llm.AssistantPaused):
        local_llm.ensure_awake()
    assert "POST /wake_up?tags=weights" not in fake.calls


def test_ensure_awake_ignores_non_forecast_and_modal_jobs(managed, clean_jobs) -> None:
    use, _ = managed
    use(FakeVllm())
    _insert_job("running", job_type="blend")
    _insert_job("running", runner="modal")

    local_llm.ensure_awake()


def test_ensure_awake_wakes_sleeping_model(managed, clean_jobs) -> None:
    use, _ = managed
    fake = use(FakeVllm())
    fake.sleeping = True

    local_llm.ensure_awake()

    assert fake.paths() == [
        "GET /is_sleeping",
        "POST /wake_up?tags=weights",
        "POST /collective_rpc",
        "POST /wake_up?tags=kv_cache",
    ]
    assert not fake.sleeping


def test_a_wake_refills_the_draft_model_after_the_target(managed, monkeypatch, clean_jobs) -> None:
    # Level 2 drops the draft's weights too, and vLLM's reload_weights only
    # re-reads the target's; without the second reload the draft guesses from
    # garbage and speculative decoding quietly stops paying off.
    use, _ = managed
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    local_llm.pid_path().write_text(json.dumps({"pid": _FAKE_PID, **local_llm._launch_identity()}))
    fake = use(FakeVllm())
    fake.sleeping = True

    local_llm.ensure_awake()

    assert fake.rpcs == ["reload_weights", "reload_draft_weights"]
    assert fake.paths()[-1] == "POST /wake_up?tags=kv_cache"


def test_a_wake_without_a_draft_model_reloads_only_the_target(managed, clean_jobs) -> None:
    use, _ = managed
    fake = use(FakeVllm())
    fake.sleeping = True

    local_llm.ensure_awake()

    assert fake.rpcs == ["reload_weights"]


def test_ensure_awake_restarts_a_stopped_server(managed, monkeypatch, clean_jobs) -> None:
    use, _ = managed
    use(FakeVllm())
    monkeypatch.setattr(local_llm, "_is_our_server", lambda pid: False)
    started: list[bool] = []
    monkeypatch.setattr(local_llm, "_start_locked", lambda: started.append(True))

    with pytest.raises(local_llm.AssistantLoading):
        local_llm.ensure_awake()
    assert started == [True]


_CRASHED_START_LOG = """\
(EngineCore pid=7) INFO 10-23 18:34:33 [model_runner.py:428] Model loading took 17.82 GiB
(EngineCore pid=7) ERROR 10-23 18:34:37 [core.py:1366]     raise RuntimeError(
(EngineCore pid=7) torch._inductor.exc.InductorError: RuntimeError: Failed to find C compiler.
(APIServer pid=6)   File "/env/vllm/v1/engine/utils.py", line 1320, in wait_for_engine_startup
(APIServer pid=6) RuntimeError: Engine core initialization failed. See root cause above.
"""


def test_a_launch_that_died_while_loading_is_reported_and_retried(
    managed, monkeypatch, clean_jobs
) -> None:
    use, _ = managed
    use(FakeVllm())
    record = {"pid": _FAKE_PID, **local_llm._launch_identity(), "ready": False}
    local_llm.pid_path().write_text(json.dumps(record))
    local_llm.log_path().write_text(_CRASHED_START_LOG)
    monkeypatch.setattr(local_llm, "_is_our_server", lambda pid: False)
    started: list[bool] = []
    monkeypatch.setattr(local_llm, "_start_locked", lambda: started.append(True))

    with pytest.raises(local_llm.AssistantStartFailed) as failed:
        local_llm.ensure_awake()

    assert started == [True], "a failed start is still retried"
    assert "Failed to find C compiler" in str(failed.value)
    assert str(local_llm.log_path()) in str(failed.value)


def test_a_server_that_answered_before_exiting_is_just_restarted(
    managed, monkeypatch, clean_jobs
) -> None:
    use, _ = managed
    use(FakeVllm())
    record = {"pid": _FAKE_PID, **local_llm._launch_identity(), "ready": True}
    local_llm.pid_path().write_text(json.dumps(record))
    local_llm.log_path().write_text(_CRASHED_START_LOG)
    monkeypatch.setattr(local_llm, "_is_our_server", lambda pid: False)
    monkeypatch.setattr(local_llm, "_start_locked", lambda: None)

    with pytest.raises(local_llm.AssistantLoading):
        local_llm.ensure_awake()


def test_a_launch_is_marked_ready_once_it_answers(launchable, clean_jobs) -> None:
    use, _, launched = launchable
    use(FakeVllm())
    local_llm.pid_path().unlink()

    local_llm.start()
    assert json.loads(local_llm.pid_path().read_text())["ready"] is False
    local_llm.ensure_awake()

    assert launched, "start() launched a server"
    assert json.loads(local_llm.pid_path().read_text())["ready"] is True


_EARLIER_LAUNCH_LOG = (
    "(APIServer pid=5) INFO 10-23 18:00:00 [api_server.py:1] Available routes are:\n"
    "(APIServer pid=5) INFO 10-23 18:00:01 [launcher.py:1] Application startup complete.\n"
)


def _launch_log(*lines: str) -> str:
    """An earlier, finished launch followed by this launch's marker and `lines`."""
    return (
        _EARLIER_LAUNCH_LOG
        + f"{local_llm._LAUNCH_MARKER} 2026-10-23T19:01:22\n"
        + "".join(f"{line}\n" for line in lines)
    )


@pytest.mark.parametrize(
    ("lines", "stage"),
    [
        ((), "starting"),
        (
            ("(EngineCore pid=7) INFO [model_runner.py:396] Loading model from scratch...",),
            "loading_weights",
        ),
        (
            ("(EngineCore pid=7) INFO [model_runner.py:428] Model loading took 17.82 GiB memory",),
            "compiling",
        ),
        (
            (
                "(EngineCore pid=7) INFO [kernel_warmup.py:359] Running FlashInfer autotune with 2048 tokens",
            ),
            "tuning",
        ),
    ],
    ids=["just-launched", "weights", "compiling", "tuning"],
)
def test_startup_progress_reports_only_this_launchs_furthest_stage(managed, lines, stage) -> None:
    local_llm.log_path().write_text(_launch_log(*lines))

    assert local_llm.startup_progress().stage == stage


def _tuning_pass(op: str, buckets: int = 16) -> str:
    """One autotune pass as tqdm draws it: an opening 0% frame, then the finish."""
    return (
        f"[AutoTuner]: Tuning {op}:   0%|          | 0/{buckets} [00:00<?]\r"
        f"[AutoTuner]: Tuning {op}: 100%|##########| {buckets}/{buckets} [00:06<00:00]\r"
    )


def test_tuning_progress_shows_the_pass_bar_and_counts_passes(managed) -> None:
    local_llm.log_path().write_text(
        _launch_log(
            "(EngineCore pid=7) INFO [model_runner.py:428] Model loading took 17.82 GiB memory",
            "(EngineCore pid=7) INFO [kernel_warmup.py:359] Running FlashInfer autotune",
        )
        + _tuning_pass("fp8_gemm") * 2
        + "[AutoTuner]: Tuning fp8_gemm:   0%|          | 0/16 [00:00<?]\r"
        + "[AutoTuner]: Tuning fp8_gemm:  69%|######9   | 11/16 [00:03<00:01]\r"
    )
    record = json.loads(local_llm.pid_path().read_text())
    local_llm.pid_path().write_text(json.dumps({**record, "started_at": time.time() - 90}))

    progress = local_llm.startup_progress()

    # The current pass's own bar, plus how many passes have run.
    assert (progress.stage, progress.detail, progress.done, progress.total) == (
        "tuning",
        "fp8_gemm",
        11,
        16,
    )
    assert progress.passes_done == 3
    assert progress.passes_expected is None, "no earlier start on record to compare with"
    assert progress.elapsed_seconds is not None and 89 <= progress.elapsed_seconds < 100
    assert progress.overall == pytest.approx(4 / 6), "a rough count of stages passed"
    assert progress.expected_seconds is None


def test_graph_capture_progress_reads_its_bar(managed) -> None:
    local_llm.log_path().write_text(
        _launch_log("(EngineCore pid=7) INFO [model_runner.py:428] Model loading took 17.82 GiB")
        + "Capturing CUDA graphs (decode, FULL):  57%|#####7    | 4/7 [00:01<00:00]\r"
    )

    progress = local_llm.startup_progress()

    assert (progress.stage, progress.detail, progress.done, progress.total) == (
        "capturing",
        "CUDA graphs",
        4,
        7,
    )


def _timed_launch_log(launched: datetime, *, passes: int = 4, finished: bool = True) -> str:
    """This launch's stages, each on a vLLM line stamped with its time."""

    def at(seconds: int, message: str) -> str:
        stamp = (launched + timedelta(seconds=seconds)).strftime("%m-%d %H:%M:%S")
        return f"(EngineCore pid=7) INFO {stamp} [x.py:1] {message}"

    return (
        _launch_log(
            at(20, "Loading model from scratch..."),
            at(45, "Model loading took 17.82 GiB memory"),
            at(50, "Graph capturing finished in 7 secs"),
            at(52, "Running FlashInfer autotune with 2048 tokens"),
        )
        + _tuning_pass("fp8_gemm") * passes
        + (at(300, "Available routes are:") + "\n" if finished else "")
    )


def test_a_launch_records_its_timeline_when_it_first_answers(managed) -> None:
    started = time.time() - 600
    launched = datetime.fromtimestamp(started).replace(microsecond=0)
    started = launched.timestamp()
    record = {"pid": _FAKE_PID, **local_llm._launch_identity(), "ready": False}
    local_llm.pid_path().write_text(json.dumps({**record, "started_at": started}))
    local_llm.log_path().write_text(_timed_launch_log(launched))

    local_llm._mark_ready()

    profile = json.loads(local_llm._startup_profile_path().read_text())
    assert profile["args"] == record["args"]
    # Ready when the final stage began, not when it happened to be noticed.
    assert profile["ready_after_seconds"] == pytest.approx(300)
    assert profile["stage_offsets"] == {
        "loading_weights": 20,
        "compiling": 45,
        "capturing": 50,
        "tuning": 52,
        "finishing": 300,
    }
    assert profile["tuning_passes"] == 4


def test_overall_progress_follows_the_last_starts_timeline(managed) -> None:
    identity = local_llm._launch_identity()
    local_llm._startup_profile_path().write_text(
        json.dumps(
            {
                "args": identity["args"],
                "ready_after_seconds": 300,
                "stage_offsets": {"loading_weights": 20, "compiling": 45, "tuning": 100},
                "tuning_passes": 40,
            }
        )
    )
    launched = datetime.fromtimestamp(time.time() - 150).replace(microsecond=0)
    record = {"pid": _FAKE_PID, **identity, "ready": False, "started_at": launched.timestamp()}
    local_llm.pid_path().write_text(json.dumps(record))
    # Ten passes into tuning, not yet finished.
    local_llm.log_path().write_text(_timed_launch_log(launched, passes=10, finished=False))

    progress = local_llm.startup_progress()

    assert (progress.passes_done, progress.passes_expected) == (10, 40)
    # Tuning spans 100-300 s of the last start; a quarter of its passes is 150 s.
    assert progress.overall == pytest.approx(150 / 300)
    assert progress.expected_seconds == 300


def test_a_timeline_recorded_with_other_flags_is_not_used(managed) -> None:
    local_llm._startup_profile_path().write_text(
        json.dumps({"args": ["vllm", "serve", "other"], "ready_after_seconds": 60})
    )
    local_llm.log_path().write_text(_launch_log())

    progress = local_llm.startup_progress()

    assert progress.expected_seconds is None
    assert progress.overall == 0


async def _collect(stream) -> list[dict]:
    return [json.loads(event) async for event in stream]


@pytest.fixture
def startup_steps(monkeypatch):
    """Script ensure_awake's outcomes in order; an exception class is raised."""
    from ai_almanac.server.services import llm

    monkeypatch.setattr(llm, "_STARTUP_POLL_SECONDS", 0)
    monkeypatch.setattr(
        local_llm, "startup_progress", lambda: local_llm.StartupProgress(stage="compiling")
    )

    def script(*steps):
        queue = list(steps)

        def ensure_awake() -> None:
            step = queue.pop(0)
            if step is not None:
                raise step

        monkeypatch.setattr(local_llm, "ensure_awake", ensure_awake)
        return llm._wait_for_local_model()

    return script


@pytest.mark.asyncio
async def test_a_turn_waits_for_a_starting_model_and_reports_progress(startup_steps) -> None:
    loading = local_llm.AssistantLoading()

    events = await _collect(startup_steps(loading, loading, None))

    assert [(e["status"], e["stage"]) for e in events] == [("loading", "compiling")] * 2
    assert events[0]["previous_failure"] is None


@pytest.mark.asyncio
async def test_a_failure_left_by_an_earlier_launch_is_retried_and_waited_on(
    startup_steps,
) -> None:
    earlier = local_llm.AssistantStartFailed("RuntimeError: no C compiler")

    events = await _collect(startup_steps(earlier, local_llm.AssistantLoading(), None))

    assert [e["previous_failure"] for e in events] == ["RuntimeError: no C compiler"] * 2


@pytest.mark.asyncio
async def test_the_launch_a_turn_waited_on_failing_ends_the_turn(startup_steps) -> None:
    failed = local_llm.AssistantStartFailed("RuntimeError: out of memory")

    with pytest.raises(local_llm.AssistantStartFailed):
        await _collect(startup_steps(local_llm.AssistantLoading(), failed))


@pytest.mark.asyncio
async def test_a_turn_stops_waiting_at_the_cap(startup_steps, monkeypatch) -> None:
    from ai_almanac.server.services import llm

    monkeypatch.setattr(llm, "_STARTUP_WAIT_SECONDS", 0)

    with pytest.raises(local_llm.AssistantLoading):
        await _collect(startup_steps(local_llm.AssistantLoading()))


def test_idle_sleep_waits_for_timeout(managed, monkeypatch) -> None:
    use, _ = managed
    fake = use(FakeVllm())
    monkeypatch.setattr(settings, "llm_idle_sleep_minutes", 10)

    monkeypatch.setattr(local_llm, "_last_used", local_llm.time.monotonic())
    local_llm.idle_sleep()
    assert not fake.sleeping

    monkeypatch.setattr(local_llm, "_last_used", local_llm.time.monotonic() - 11 * 60)
    local_llm.idle_sleep()
    assert fake.sleeping


def test_serve_args_bind_loopback_and_reject_host_overrides(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_serve_args", "--max-model-len 4096")
    args = local_llm.serve_args()
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "--enable-sleep-mode" in args
    # A stop lets the engine finish its step instead of killing it mid-kernel,
    # and within the grace _terminate gives before it kills the group anyway.
    timeout = int(args[args.index("--shutdown-timeout") + 1])
    assert 0 < timeout < local_llm._STOP_GRACE_SECONDS

    monkeypatch.setattr(settings, "llm_serve_args", "--host=0.0.0.0")
    with pytest.raises(ValueError, match="--host"):
        local_llm.serve_args()


def test_serve_args_follow_the_catalog_model(monkeypatch) -> None:
    super_id = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4"
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", super_id)
    args = local_llm.serve_args()
    assert args[2] == super_id
    assert args[args.index("--reasoning-parser") + 1] == "nemotron_v3"
    # The 80 GB model brings its own memory share while the setting is default.
    assert args[args.index("--gpu-memory-utilization") + 1] == "0.8"

    monkeypatch.setattr(settings, "llm_model", "RedHatAI/Qwen3.6-35B-A3B-NVFP4")
    args = local_llm.serve_args()
    assert args[args.index("--reasoning-parser") + 1] == "qwen3"
    assert args[args.index("--gpu-memory-utilization") + 1] == "0.45"


def test_serve_settings_override_the_catalog(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_model", "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4")
    monkeypatch.setattr(settings, "llm_serve_args", "--max-model-len 4096")
    monkeypatch.setattr(settings, "llm_gpu_memory_utilization", 0.7)
    args = local_llm.serve_args()
    assert args[args.index("--gpu-memory-utilization") + 1] == "0.7"
    assert args[-2:] == ["--max-model-len", "4096"]
    assert "--reasoning-parser" not in args


def test_a_catalog_draft_model_is_speculated_with_and_reloadable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    args = local_llm.serve_args()

    config = json.loads(args[args.index("--speculative-config") + 1])
    assert config == {
        "method": "dflash",
        "model": "z-lab/Qwen3.8-27B-DFlash2",
        "num_speculative_tokens": 7,
    }
    assert args[args.index("--worker-extension-cls") + 1] == "almanac_vllm_worker.DraftReload"
    # Measured faster loads and wakes for this entry; the draft reloads through it too.
    assert args[args.index("--load-format") + 1] == "instanttensor"
    # Still one shell-quoted string the context-length lookup can parse.
    assert local_llm.model_serve_args().count("--max-model-len 65536") == 1


def test_turning_speculative_decoding_off_serves_the_model_alone(monkeypatch) -> None:
    from ai_almanac.settings import local_llm_weight_repos

    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    on = local_llm.serve_args()
    monkeypatch.setattr(settings, "llm_speculative_decoding", False)
    off = local_llm.serve_args()

    assert "--speculative-config" not in off
    assert "--worker-extension-cls" not in off
    # Everything else is the model's tested flags, unchanged.
    assert off == on[: on.index("--speculative-config")]
    # Nor is the draft fetched, or needed for the model to count as downloaded.
    assert local_llm_weight_repos("nvidia/Qwen3.8-27B-NVFP4") == ["nvidia/Qwen3.8-27B-NVFP4"]


def test_toggling_speculative_decoding_restarts_the_server(
    managed, monkeypatch, clean_jobs
) -> None:
    use, terminated = managed
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    local_llm.pid_path().write_text(json.dumps({"pid": _FAKE_PID, **local_llm._launch_identity()}))
    use(FakeVllm())
    started: list[dict] = []
    monkeypatch.setattr(local_llm, "_start_locked", lambda identity=None: started.append(identity))
    monkeypatch.setattr(settings, "llm_speculative_decoding", False)

    with pytest.raises(local_llm.AssistantLoading):
        local_llm.ensure_awake()
    assert terminated == [_FAKE_PID]
    assert "--speculative-config" not in started[0]["args"]


def _speculative(args: list[str]) -> dict:
    return json.loads(args[args.index("--speculative-config") + 1])


def test_a_chosen_draft_model_replaces_the_catalogs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")

    # Named alone, vLLM tells the method from the draft; the length has a default.
    monkeypatch.setattr(settings, "llm_draft_model", "someone/qwen3.8-eagle3")
    assert _speculative(local_llm.serve_args()) == {
        "model": "someone/qwen3.8-eagle3",
        "num_speculative_tokens": 3,
    }

    # A DFlash draft on disk says what it is and how far it drafts.
    draft = tmp_path / "my-draft"
    draft.mkdir()
    (draft / "config.json").write_text(
        json.dumps({"architectures": ["DFlash2DraftModel"], "dflash_config": {"block_size": 16}})
    )
    monkeypatch.setattr(settings, "llm_draft_model", str(draft))
    assert _speculative(local_llm.serve_args()) == {
        "model": str(draft),
        "method": "dflash",
        "num_speculative_tokens": 15,
    }

    # The catalog's own draft keeps its tested settings; a length setting wins.
    monkeypatch.setattr(settings, "llm_draft_model", "z-lab/Qwen3.8-27B-DFlash2")
    assert _speculative(local_llm.serve_args())["method"] == "dflash"
    monkeypatch.setattr(settings, "llm_draft_tokens", 4)
    assert _speculative(local_llm.serve_args())["num_speculative_tokens"] == 4


def test_a_model_outside_the_catalog_can_speculate_too(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "someone/custom-model")
    assert "--speculative-config" not in local_llm.serve_args()

    monkeypatch.setattr(settings, "llm_draft_model", "someone/custom-model-eagle3")
    args = local_llm.serve_args()
    assert _speculative(args)["model"] == "someone/custom-model-eagle3"
    assert "--safetensors-load-strategy" in args
    assert args[args.index("--worker-extension-cls") + 1] == "almanac_vllm_worker.DraftReload"


def test_a_chosen_draft_is_fetched_for_the_configured_model_only(monkeypatch, tmp_path) -> None:
    from ai_almanac.settings import local_llm_weight_repos

    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    monkeypatch.setattr(settings, "llm_draft_model", "someone/qwen3.8-eagle3")
    assert local_llm_weight_repos("nvidia/Qwen3.8-27B-NVFP4") == [
        "nvidia/Qwen3.8-27B-NVFP4",
        "someone/qwen3.8-eagle3",
    ]
    # Other catalog models (listed in setup with a "downloaded" flag) keep their own.
    assert local_llm_weight_repos(DEFAULT_LOCAL_LLM_MODEL) == [DEFAULT_LOCAL_LLM_MODEL]
    # A draft on disk has nothing to download.
    monkeypatch.setattr(settings, "llm_draft_model", str(tmp_path))
    assert local_llm_weight_repos("nvidia/Qwen3.8-27B-NVFP4") == ["nvidia/Qwen3.8-27B-NVFP4"]


def test_speculating_in_llm_serve_args_also_gets_the_draft_reload(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    monkeypatch.setattr(settings, "llm_serve_args", "--max-model-len 4096")
    assert "--speculative-config" not in local_llm.serve_args()
    assert "--worker-extension-cls" not in local_llm.serve_args()

    monkeypatch.setattr(
        settings,
        "llm_serve_args",
        """--speculative-config='{"method":"mtp","num_speculative_tokens":2}'""",
    )
    args = local_llm.serve_args()
    assert args[args.index("--worker-extension-cls") + 1] == "almanac_vllm_worker.DraftReload"


def test_models_without_a_draft_launch_as_before(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", DEFAULT_LOCAL_LLM_MODEL)
    args = local_llm.serve_args()
    assert "--speculative-config" not in args
    assert "--worker-extension-cls" not in args


def test_the_draft_reload_extension_is_importable_by_the_server(launchable, monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "nvidia/Qwen3.8-27B-NVFP4")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    envs: list[dict] = []

    class FakeProc:
        pid = _FAKE_PID + 1

    def fake_run(args, env=None, log_path=None):
        envs.append(env)
        return FakeProc()

    monkeypatch.setattr("ai_almanac.envs.manager.run_llm_server", fake_run)
    local_llm.pid_path().unlink(missing_ok=True)

    local_llm.start()

    first, rest = envs[0]["PYTHONPATH"].split(os.pathsep)
    assert (Path(first) / "almanac_vllm_worker.py").is_file()
    assert rest == "/elsewhere"


def _host(monkeypatch, *, cores: int, total_gib: float, available_gib: float | None) -> None:
    pages = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": int(total_gib * 2**30 / 4096)}
    monkeypatch.setattr(local_llm.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(local_llm.os, "sysconf", lambda name: pages[name])
    monkeypatch.setattr(local_llm, "_mem_available_gb", lambda: available_gib)


def test_kernel_builds_run_only_as_many_at_once_as_fit_beside_the_model(monkeypatch) -> None:
    # The Spark: 20 cores, 128 GiB, 0.45 of it claimed by the default model.
    # Unbounded, ninja ran 17 ~6.5 GB compiles at once and the start was OOM-killed.
    monkeypatch.setattr(settings, "llm_gpu_memory_utilization", 0.45)
    _host(monkeypatch, cores=20, total_gib=128, available_gib=113)
    assert local_llm._kernel_build_jobs() == 6

    _host(monkeypatch, cores=20, total_gib=128, available_gib=40)
    assert local_llm._kernel_build_jobs() == 1

    _host(monkeypatch, cores=4, total_gib=512, available_gib=500)
    assert local_llm._kernel_build_jobs() == 4

    _host(monkeypatch, cores=20, total_gib=128, available_gib=None)
    assert local_llm._kernel_build_jobs() == 20


def test_a_max_jobs_already_set_is_left_alone(launchable, monkeypatch) -> None:
    monkeypatch.setenv("MAX_JOBS", "3")
    envs: list[dict] = []

    class FakeProc:
        pid = _FAKE_PID + 1

    monkeypatch.setattr(
        "ai_almanac.envs.manager.run_llm_server",
        lambda args, env=None, log_path=None: envs.append(env) or FakeProc(),
    )
    local_llm.pid_path().unlink(missing_ok=True)

    local_llm.start()

    assert envs[0]["MAX_JOBS"] == "3"


def test_custom_model_gets_only_the_startup_tuning(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_serve_args", "")
    monkeypatch.setattr(settings, "llm_model", "someone/custom-model")
    args = local_llm.serve_args()
    assert "--safetensors-load-strategy" in args
    assert "--tool-call-parser" not in args


def test_ensure_awake_restarts_when_the_model_changed(managed, monkeypatch, clean_jobs) -> None:
    use, terminated = managed
    use(FakeVllm())
    started: list[dict] = []
    monkeypatch.setattr(local_llm, "_start_locked", lambda identity=None: started.append(identity))
    monkeypatch.setattr(settings, "llm_model", "RedHatAI/Qwen3.6-35B-A3B-NVFP4")

    with pytest.raises(local_llm.AssistantLoading):
        local_llm.ensure_awake()
    assert terminated == [_FAKE_PID]
    assert started[0]["args"][2] == "RedHatAI/Qwen3.6-35B-A3B-NVFP4"


def test_llm_model_cached_needs_every_shard(monkeypatch, tmp_path) -> None:
    from ai_almanac.envs.manager import llm_model_cached

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snapshot = tmp_path / "models--org--m" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    assert not llm_model_cached("org/m")
    shards = {"a": "model-1.safetensors", "b": "model-2.safetensors"}
    (snapshot / "model.safetensors.index.json").write_text(json.dumps({"weight_map": shards}))
    (snapshot / "model-1.safetensors").write_bytes(b"")
    assert not llm_model_cached("org/m")
    (snapshot / "model-2.safetensors").write_bytes(b"")
    assert llm_model_cached("org/m")


def test_a_model_with_a_draft_is_cached_only_with_its_draft(monkeypatch, tmp_path) -> None:
    from ai_almanac.envs.manager import llm_model_cached

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    for repo in ("nvidia--Qwen3.8-27B-NVFP4", "z-lab--Qwen3.8-27B-DFlash2"):
        (tmp_path / f"models--{repo}" / "snapshots" / "abc").mkdir(parents=True)
    (tmp_path / "models--nvidia--Qwen3.8-27B-NVFP4/snapshots/abc/model.safetensors").write_bytes(
        b""
    )
    assert not llm_model_cached("nvidia/Qwen3.8-27B-NVFP4")

    (tmp_path / "models--z-lab--Qwen3.8-27B-DFlash2/snapshots/abc/model.safetensors").write_bytes(
        b""
    )
    assert llm_model_cached("nvidia/Qwen3.8-27B-NVFP4")


def test_downloading_a_model_fetches_its_draft_too(monkeypatch) -> None:
    from ai_almanac.envs import manager

    fetched: list[str] = []
    monkeypatch.setattr(manager, "_ensure_pixi", lambda progress=None: "pixi")
    monkeypatch.setattr(manager, "_run_streaming", lambda cmd, **_: fetched.append(cmd[-1]))

    manager.download_llm_model("nvidia/Qwen3.8-27B-NVFP4")
    manager.download_llm_model(DEFAULT_LOCAL_LLM_MODEL)

    assert fetched == [
        "nvidia/Qwen3.8-27B-NVFP4",
        "z-lab/Qwen3.8-27B-DFlash2",
        DEFAULT_LOCAL_LLM_MODEL,
    ]


def test_managed_engine_derives_base_url(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENGINE", "managed")
    monkeypatch.setenv("LLM_PORT", "9123")
    try:
        reload_settings()
        assert settings.llm_base_url == "http://127.0.0.1:9123/v1"
        # The hosted-provider default model is not something vLLM can serve.
        assert settings.llm_model == DEFAULT_LOCAL_LLM_MODEL

        monkeypatch.setenv("DEPLOYMENT_MODE", "shared")
        reload_settings()
        assert settings.llm_engine == "external"
    finally:
        monkeypatch.undo()
        reload_settings()


def test_supervisor_heartbeats_while_parking(managed, monkeypatch, tmp_path) -> None:
    """A park that waits on a draining reply must not let the job's heartbeat
    go stale, or the reconciler would fail the forecast as crashed."""
    import threading

    from ai_almanac.server.services import job_manager

    beats: list[str] = []
    released = threading.Event()

    def fake_heartbeat(engine, job_id, worker_id) -> None:
        beats.append(job_id)
        if len(beats) >= 2:
            released.set()

    def slow_park() -> local_llm.ParkResult:
        assert released.wait(timeout=10)
        return local_llm.ParkResult(True, "local model asleep; memory released")

    monkeypatch.setattr(job_manager, "_heartbeat", fake_heartbeat)
    monkeypatch.setattr(local_llm, "park_for_gpu_job", slow_park)
    log_path = tmp_path / "job.log"
    log_path.write_text("")

    job_manager._park_local_llm(object(), "job-1", "worker-1", log_path)

    assert beats[:2] == ["job-1", "job-1"]
    assert "memory released" in log_path.read_text()


def test_idle_sleep_skips_while_lock_is_held(managed, monkeypatch) -> None:
    """The idle step runs in the reconciler loop; it must not block behind a
    minutes-long wake or park."""
    use, _ = managed
    fake = use(FakeVllm())
    monkeypatch.setattr(settings, "llm_idle_sleep_minutes", 10)
    monkeypatch.setattr(local_llm, "_last_used", local_llm.time.monotonic() - 11 * 60)

    with local_llm._lock():
        local_llm.idle_sleep()
    assert not fake.sleeping


@pytest.fixture
def launchable(managed, monkeypatch):
    """start() prerequisites stubbed; records launches instead of spawning."""
    use, terminated = managed
    launched: list[list[str]] = []

    class FakeProc:
        pid = _FAKE_PID + 1

    def fake_run(args, env=None, log_path=None):
        launched.append(args)
        return FakeProc()

    monkeypatch.setattr("ai_almanac.envs.manager.llm_env_ready", lambda: True)
    monkeypatch.setattr("ai_almanac.envs.manager.run_llm_server", fake_run)
    return use, terminated, launched


def test_start_reuses_a_server_launched_with_the_same_flags(launchable) -> None:
    use, terminated, launched = launchable
    use(FakeVllm())
    local_llm.pid_path().write_text(json.dumps({"pid": _FAKE_PID, **local_llm._launch_identity()}))

    local_llm.start()

    assert launched == []
    assert terminated == []


def test_start_replaces_a_server_with_changed_flags(launchable, monkeypatch) -> None:
    use, terminated, launched = launchable
    use(FakeVllm())
    local_llm.pid_path().write_text(json.dumps({"pid": _FAKE_PID, **local_llm._launch_identity()}))
    monkeypatch.setattr(settings, "llm_serve_args", "--max-model-len 4096")

    local_llm.start()

    assert terminated == [_FAKE_PID]
    assert launched and "4096" in launched[0]
    record = json.loads(local_llm.pid_path().read_text())
    assert record["pid"] == _FAKE_PID + 1
    assert record["args"] == launched[0]


def test_start_replaces_a_server_from_before_identities_were_recorded(launchable) -> None:
    use, terminated, launched = launchable
    use(FakeVllm())
    local_llm.pid_path().write_text(json.dumps({"pid": _FAKE_PID}))

    local_llm.start()

    assert terminated == [_FAKE_PID]
    assert len(launched) == 1


def test_suspend_sleeps_rather_than_stopping(managed) -> None:
    use, terminated = managed
    fake = use(FakeVllm())

    local_llm.suspend()

    assert fake.sleeping
    # Shutdown has a fixed grace period; a reply still generating is dropped
    # rather than waited on, so the server is never SIGKILLed mid-kernel.
    assert "POST /sleep?level=2&mode=abort" in fake.calls
    assert terminated == []
    assert local_llm.pid_path().exists()


def test_suspend_in_a_container_stops_the_server_instead_of_sleeping_it(
    managed, monkeypatch
) -> None:
    # The container's end SIGKILLs whatever is left in it, a sleeping server too.
    use, terminated = managed
    fake = use(FakeVllm())
    monkeypatch.setenv("AI_ALMANAC_CONTAINER", "1")

    local_llm.suspend()

    assert terminated == [_FAKE_PID]
    assert not fake.sleeping
    assert not local_llm.pid_path().exists()


@pytest.fixture
def signals(managed, monkeypatch):
    """The real _terminate, with process signals recorded instead of sent."""
    sent: list[signal.Signals] = []
    alive = {"value": True}

    def record(pid: int, sig: signal.Signals) -> None:
        sent.append(sig)
        alive["value"] = False  # the server exits on SIGTERM

    monkeypatch.setattr(local_llm, "_terminate", _REAL_TERMINATE)
    monkeypatch.setattr(local_llm, "_signal_group", record)
    monkeypatch.setattr(local_llm, "_is_our_server", lambda pid: alive["value"])
    return managed[0], sent


def test_a_stop_aborts_running_replies_before_signalling_the_server(signals) -> None:
    # Signalled mid-reply, vLLM keeps generating until --shutdown-timeout and
    # then kills its engine: a DFlash engine killed so on the Spark left the GPU
    # spinning with no process, until a reboot. Idle, the engine exits itself.
    use, sent = signals
    fake = use(FakeVllm(running=[1.0]))

    local_llm.stop()

    abort = fake.calls.index("POST /abort_requests")
    assert fake.calls[abort + 1 :].count("GET /metrics") >= 1, "waited until idle"
    assert sent == [signal.SIGTERM]


def test_a_stop_goes_ahead_when_the_server_cannot_be_reached(signals, monkeypatch) -> None:
    _, sent = signals

    def unreachable(timeout=5.0):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(local_llm, "_client", unreachable)

    local_llm.stop()

    assert sent == [signal.SIGTERM]


def test_a_server_that_wont_go_idle_is_stopped_anyway(signals, monkeypatch) -> None:
    use, sent = signals
    fake = use(FakeVllm(running=[1.0]))
    # An abort the server doesn't act on: requests stay running.
    handler = fake.handler
    fake.handler = lambda request: (
        httpx.Response(200, json={}) if request.url.path == "/abort_requests" else handler(request)
    )
    use(fake)
    monkeypatch.setattr(local_llm, "_QUIESCE_TIMEOUT_SECONDS", 0.0)

    local_llm.stop()

    assert sent == [signal.SIGTERM]


def test_suspend_stops_a_server_that_is_still_loading(managed) -> None:
    use, terminated = managed
    fake = FakeVllm()
    handler = fake.handler
    fake.handler = lambda request: (
        httpx.Response(503) if request.url.path == "/health" else handler(request)
    )
    use(fake)

    local_llm.suspend()

    assert terminated == [_FAKE_PID]
    assert not local_llm.pid_path().exists()
