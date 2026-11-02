"""Setup service — install state, bootstrap token, probes, and persistence.

Shared by the setup router (web wizard) and `ai-almanac init` (CLI) so the
two paths cannot drift from each other.
"""

from __future__ import annotations

import asyncio
import os
import platform
import secrets
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ai_almanac.paths import config_yaml_path, data_root
from ai_almanac.settings import settings

if TYPE_CHECKING:
    from ai_almanac.envs.manager import EnvProgressEvent


# ---------------------------------------------------------------------------
# Install state
# ---------------------------------------------------------------------------


def setup_required() -> bool:
    """Return True when personal mode and setup has not been completed."""
    return settings.deployment_mode == "personal" and not settings.setup_complete


def grandfather_existing_install() -> bool:
    """Mark setup complete for installs that predate the wizard.

    Rule: llm is configured OR DB overlay is non-empty OR config.yaml exists.
    Returns True when it wrote the completion flag (caller should re-reload).
    """
    if not setup_required():
        return False

    from ai_almanac.server.services.llm import llm_is_configured
    from ai_almanac.settings import _load_db_overlay, write_settings_overlay

    if llm_is_configured() or _load_db_overlay() or config_yaml_path().exists():
        write_settings_overlay({"setup_complete": True})
        return True
    return False


# ---------------------------------------------------------------------------
# Bootstrap token (file-backed so it survives uvicorn --reload workers)
# ---------------------------------------------------------------------------

_TOKEN_FILENAME = "setup_token"


def _token_path() -> Path:
    return data_root() / _TOKEN_FILENAME


def get_or_create_bootstrap_token() -> str:
    """Return the existing bootstrap token or generate and persist a new one."""
    p = _token_path()
    if p.exists():
        try:
            t = p.read_text().strip()
            if t:
                return t
        except OSError:
            pass
    t = secrets.token_urlsafe(32)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, t.encode())
    finally:
        os.close(fd)
    return t


def verify_bootstrap_token(candidate: str | None) -> bool:
    if not candidate:
        return False
    p = _token_path()
    if not p.exists():
        return False
    try:
        expected = p.read_text().strip()
    except OSError:
        return False
    if not expected:
        return False
    return secrets.compare_digest(candidate, expected)


def clear_bootstrap_token() -> None:
    with suppress(FileNotFoundError):
        _token_path().unlink()


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def detect_platform() -> dict:
    from ai_almanac.envs.pixi_bootstrap import _current_pixi_platform

    return {
        "platform": _current_pixi_platform(),
        "machine": platform.machine(),
    }


def probe_gpu() -> dict | None:
    """Run nvidia-smi to detect GPU; returns None on any failure."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            return None
        parts = lines[0].split(",", 1)
        name = parts[0].strip()
        memory_mib = int(parts[1].strip()) if len(parts) > 1 else None
        return {"name": name, "memory_mib": memory_mib, "count": len(lines)}
    except Exception:  # noqa: BLE001
        return None


def env_status() -> dict:
    """Return readiness status for each workload environment."""
    from ai_almanac.envs.manager import (
        _FORECAST_PLATFORMS,
        BLENDING_SOURCE_MARKER,
        FORECAST_ENVIRONMENTS,
    )
    from ai_almanac.envs.pixi_bootstrap import _current_pixi_platform
    from ai_almanac.paths import benchmark_env_dir, blending_env_dir, forecast_env_dir

    result: dict[str, str] = {}

    # Benchmark env
    benv = benchmark_env_dir()
    if (benv / "pixi.toml").exists() and (benv / ".pixi" / "envs" / "default").exists():
        result["benchmark"] = "ready"
    else:
        result["benchmark"] = "missing"

    # Blending env
    blenv = blending_env_dir()
    source_ok = (blenv / "onset-blending" / BLENDING_SOURCE_MARKER).is_file()
    if (
        (blenv / "pixi.toml").exists()
        and (blenv / ".pixi" / "envs" / "default").exists()
        and source_ok
    ):
        result["blending"] = "ready"
    else:
        result["blending"] = "missing"

    # Forecast env
    current = _current_pixi_platform()
    if current not in _FORECAST_PLATFORMS:
        result["forecast"] = "unsupported"
    else:
        fenv = forecast_env_dir()
        if not (fenv / "pixi.toml").exists():
            result["forecast"] = "missing"
        else:
            ready_envs = [
                e for e in FORECAST_ENVIRONMENTS if (fenv / ".pixi" / "envs" / e).exists()
            ]
            if len(ready_envs) == len(FORECAST_ENVIRONMENTS):
                result["forecast"] = "ready"
            elif ready_envs:
                result["forecast"] = "partial"
            else:
                result["forecast"] = "missing"

    return result


# ---------------------------------------------------------------------------
# LLM test
# ---------------------------------------------------------------------------


@dataclass
class LlmTestResult:
    ok: bool
    models_ok: bool
    completion_ok: bool
    models: list
    error: str | None


async def test_llm_connection(base_url: str, model: str, api_key: str | None) -> LlmTestResult:
    """Probe an OpenAI-compatible endpoint; never raises."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return LlmTestResult(
            ok=False,
            models_ok=False,
            completion_ok=False,
            models=[],
            error="openai package not installed",
        )

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or "placeholder",
        timeout=10,
    )
    models_ok = False
    completion_ok = False
    model_ids: list[str] = []
    error: str | None = None

    try:
        resp = await client.models.list()
        model_ids = [m.id for m in (resp.data or [])][:10]
        models_ok = True
    except Exception as e:  # noqa: BLE001
        error = f"models.list() failed: {e}"

    if models_ok:
        try:
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            completion_ok = True
        except Exception as e:  # noqa: BLE001
            error = f"chat.completions.create() failed: {e}"

    return LlmTestResult(
        ok=models_ok and completion_ok,
        models_ok=models_ok,
        completion_ok=completion_ok,
        models=model_ids,
        error=error,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_storage(output_dir: str | None, dataset_mount_roots: list[str] | None) -> None:
    from ai_almanac.settings import reload_settings, write_settings_overlay

    updates: dict = {}
    if output_dir is not None:
        updates["output_dir"] = output_dir
    if dataset_mount_roots is not None:
        updates["dataset_mount_roots"] = ",".join(dataset_mount_roots)
    if updates:
        write_settings_overlay(updates)
        reload_settings()


def save_llm(base_url: str, model: str, api_key: str | None) -> None:
    from ai_almanac.settings import reload_settings, write_settings_overlay

    updates: dict = {
        "llm_base_url": base_url,
        "llm_model": model,
        "llm_provider": "openai-compatible",
    }
    if api_key:
        updates["llm_api_key"] = api_key
    write_settings_overlay(updates)
    reload_settings()


def finish_setup() -> None:
    from ai_almanac.settings import reload_settings, write_settings_overlay

    write_settings_overlay({"setup_complete": True})
    reload_settings()
    clear_bootstrap_token()


# ---------------------------------------------------------------------------
# Env-prepare task singleton
# ---------------------------------------------------------------------------

_MAX_RING = 2000


class PrepareTask:
    def __init__(self) -> None:
        self.status: Literal["idle", "running", "done", "failed"] = "idle"
        self._events: deque[dict] = deque(maxlen=_MAX_RING)
        self._seq: int = 0
        self._waiters: list[asyncio.Event] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def _push(self, evt: dict) -> None:
        self._events.append(evt)
        for w in self._waiters:
            w.set()

    def _on_progress(self, env_event: EnvProgressEvent) -> None:
        data = {"type": "env", **asdict(env_event)}
        evt = {"seq": self._seq, **data}
        self._seq += 1
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._push, evt)

    def start(self, include_forecast: bool = True) -> bool:
        """Start preparation. Returns True if started, False if already running."""
        if self.status == "running":
            return False
        if self.status in ("done", "failed"):
            self._events.clear()
            self._seq = 0

        self.status = "running"
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()

        async def _run() -> None:
            try:
                from ai_almanac.envs.manager import ensure_env

                await asyncio.to_thread(ensure_env, self._on_progress, include_forecast)
                evt = {
                    "seq": self._seq,
                    "type": "done",
                    "ok": True,
                    "error": None,
                    "envs": env_status(),
                }
                self._seq += 1
                self._push(evt)
                self.status = "done"
            except Exception as exc:  # noqa: BLE001
                evt = {
                    "seq": self._seq,
                    "type": "done",
                    "ok": False,
                    "error": str(exc),
                    "envs": env_status(),
                }
                self._seq += 1
                self._push(evt)
                self.status = "failed"

        asyncio.create_task(_run())
        return True

    async def subscribe(self, after: int = -1) -> AsyncIterator[dict]:
        """Yield a state snapshot, buffered events (seq > after), then live events."""
        yield {"type": "state", "seq": -1, "status": self.status, "envs": env_status()}

        for evt in list(self._events):
            if evt["seq"] > after:
                yield evt
                after = evt["seq"]

        while self.status == "running":
            waiter = asyncio.Event()
            self._waiters.append(waiter)
            try:
                await asyncio.wait_for(waiter.wait(), timeout=15.0)
            except TimeoutError:
                yield {"type": "keepalive"}
                continue
            finally:
                with suppress(ValueError):
                    self._waiters.remove(waiter)

            for evt in list(self._events):
                if evt["seq"] > after:
                    yield evt
                    after = evt["seq"]


prepare_task = PrepareTask()
