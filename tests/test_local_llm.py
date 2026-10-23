"""The managed local vLLM: parking for forecasts, waking for chat, idle sleep.

A fake vLLM (httpx MockTransport) stands in for the real server; process
liveness and termination are stubbed so no subprocess is ever launched.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services import local_llm
from ai_almanac.settings import DEFAULT_LOCAL_LLM_MODEL, reload_settings, settings

_FAKE_PID = 424242


class FakeVllm:
    """Just enough of vLLM's dev/metrics API to drive local_llm."""

    def __init__(self, *, running: list[float] | None = None, sleep_status: int = 200) -> None:
        # Successive num_requests_running readings; the last one repeats.
        self.running = list(running or [0.0])
        self.sleep_status = sleep_status
        self.sleeping = False
        self.calls: list[str] = []

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
        if path == "/collective_rpc":
            assert json.loads(request.content) == {"method": "reload_weights"}
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
    assert terminated == []
    assert local_llm.pid_path().exists()


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
