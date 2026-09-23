# Bundle a local vLLM that steps aside for forecasts

## Context
The platform already runs end to end on a single DGX Spark, but the chat assistant still needs someone to run an OpenAI-compatible LLM server by hand. We want `ai-almanac init` / `serve` to install, start, and manage vLLM. The Spark's 128 GB is **unified memory**, so a medium or large model plus an AIWP forecast can OOM the whole machine, not just the GPU.

Decisions so far: we use **vLLM**, installed as a **managed pixi env**. **While a forecast runs, chat pauses with a message.**

### Why sleep level 2 (and not level 1)
- Level 1 offloads the weights to CPU RAM. On the Spark that is the same physical memory, so level 1 frees nothing. Level 2 discards the weights and KV cache. Wake then reloads the weights from disk, mostly from page cache, which is also reclaimable.
- Level 2 keeps the process, the CUDA context, and the captured graphs, so waking is much cheaper than a cold start.
- Fallback: if a sleep can't be confirmed, kill the vLLM process. Memory safety wins over wake latency.

## Design

### 1. Managed env: `src/ai_almanac/envs/llm.pixi.toml`
- Targets `linux-64` and `linux-aarch64`, with `cuda = "13"` and pypi `vllm`, plus `huggingface_hub` for downloads.
- **Step 0 spike (do first, on the Spark):**
  1. Confirm the vLLM wheels resolve and run on GB10 (sm_121).
  2. Confirm `--enable-sleep-mode` works on unified memory, i.e. the CuMem allocator.
  3. Confirm the exact level-2 wake sequence for the pinned version:
     `POST /sleep?level=2` → `POST /wake_up?tags=weights` → `POST /collective_rpc {"method":"reload_weights"}` → `POST /wake_up?tags=kv_cache`, and check `GET /is_sleeping`.
  4. Pick the default model and tool-call parser by running `evals/assistant` against it.
  5. Record the measured wake and cold-start times.
- `envs/manager.py` changes:
  - Add `_ensure_llm_env()` next to `_ensure_forecast_env`. It reuses `_install` and the same platform gate.
  - Add `download_llm_model(model_id, progress)`, which runs `hf download` in the env and streams through the existing `EnvProgressEvent`.
  - Add `run_llm_server(args)`, modeled on `run_forecast`.
  - `ensure_env(..., include_llm=...)` gains the new flag.

### 2. Settings (`src/ai_almanac/settings.py`)
- New fields:
  - `llm_engine: Literal["external", "managed"] = "external"`
  - `llm_port = 8766`
  - `llm_gpu_memory_utilization = 0.45`: required on the Spark. vLLM's default of 0.9 would claim ~115 GB of unified memory for KV cache.
  - `llm_idle_sleep_minutes = 10`
  - `llm_serve_args: str`: extra vLLM flags such as `--enable-auto-tool-choice --tool-call-parser <x> --max-model-len`, with the default taken from the spike.
- When the engine is managed, `llm_base_url` is derived as `http://127.0.0.1:{llm_port}/v1`.
- Managed mode is allowed only when `deployment_mode == "personal"`.

### 3. `src/ai_almanac/server/services/local_llm.py` (new)
This file owns the vLLM process and its state changes. Its functions:
- `start()` / `stop()`:
  - Launch through `run_llm_server` with `--host 127.0.0.1 --enable-sleep-mode` and `VLLM_SERVER_DEV_MODE=1`. The dev endpoints are unauthenticated, so the server must bind to localhost only.
  - Write the PID to `data_root()/llm.pid`. On startup, kill any stale PID left by a crashed `serve`.
- `sleep()` / `wake()`: the HTTP sequence from the spike, sent with `httpx`, which is already a dependency.
- `drain(timeout)`: poll `/metrics` until `vllm:num_requests_running == 0`. This signal works across processes and needs no in-flight bookkeeping on our side.
- `park_for_gpu_job()`: take the lock, drain, sleep, confirm with `/is_sleeping`. If any step fails, kill the PID from `llm.pid`.
- `ensure_awake()`: take the lock. If a forecast job is starting or running, raise `AssistantPaused`. Otherwise wake the model if it is asleep and record the last-used time.
- All state changes go through `locking.file_lock(data_root()/"llm.lock")`, called via `asyncio.to_thread` from async code. This stops a chat-triggered wake from racing with a supervisor-triggered sleep.

### 4. Hook points (existing code)
- **Forecast gate:** in `job_manager.execute_job` (`src/ai_almanac/server/services/job_manager.py`), call `local_llm.park_for_gpu_job()` after `_claim_capacity` succeeds and before `_start_workload`, but only when the job type is `forecast` and the engine is managed. The job is already in `starting`, so new chat turns are refused from that point on.
- **No auto-wake afterwards.** The next chat turn wakes the model lazily.
- **Chat:** call `ensure_awake()` at the top of `llm.stream_response` (`src/ai_almanac/server/services/llm.py`, next to `_acquire_llm_slot`). Map `AssistantPaused` to a user-facing event: "The assistant is paused while a forecast runs. It'll be back when the forecast finishes."
- **Idle sleep:** add a `_run_background_step("llm_idle_sleep", ...)` to `_job_reconciler_loop` in `server/app.py`.
- **Lifecycle:** `start()` / `stop()` run in the `app.py` lifespan when the engine is managed. The server starts vLLM, lets it load, and it sleeps once the idle timeout expires.
- **`/ready` and `GET /api/setup/state`:** report the assistant status as `off`, `loading`, `ready`, `sleeping`, or `paused`.

### 5. Forecast-side memory hygiene
- In `job_workload._run_forecast`, set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` in `process_env`, the same as `modal/forecasts_app.py:87`.
- In `forecast_entrypoint.run_inference`, call `torch.cuda.empty_cache()` between models so the caching allocator doesn't hold the previous model's memory.

### 6. Setup
- **`ai-almanac init` (`cli.py`):**
  - The LLM step asks "Run a model on this machine" (default when `probe_gpu()` finds a GPU and the platform is linux) or "Connect to an existing server" (the current flow).
  - New flags: `--local-llm/--no-local-llm` and `--llm-model <hf-id>`. `--yes` picks local when a GPU is present.
  - Local path: `save_llm(engine="managed", model=...)`, then `ensure_env(include_llm=True)`, then `download_llm_model`. The connection test is skipped at init because the server isn't running yet; `serve` reports readiness.
- **Web wizard:**
  - `LlmStep.svelte` gets the same two choices.
  - `EnvPrepareStep` / `PrepareTask` also install the LLM env and download the weights.
  - `POST /api/setup/llm` accepts the managed option.
  - Run `pixi run generate-api-types`.
- `services/setup.py`: `env_status()` adds `llm`. `save_llm` writes `llm_engine`.

## Critical files
- New: `src/ai_almanac/envs/llm.pixi.toml`, `src/ai_almanac/server/services/local_llm.py`, and one test file.
- Changed: `envs/manager.py`, `settings.py`, `server/services/job_manager.py`, `server/services/llm.py`, `server/app.py`, `server/services/setup.py`, `server/routers/setup.py`, `cli.py`, `server/services/job_workload.py`, `envs/forecast_entrypoint.py`, `web/src/routes/setup/LlmStep.svelte`, and the chat UI where the paused event is shown.

## Deliberately skipped
- Letting a small LLM run alongside a forecast when memory fits. We can add a memory-budget check later if someone wants that.
- Parking for blend/ROMP jobs. They are CPU-bound and much smaller.
- Choosing a model from a list in the UI. For now, one default plus `--llm-model`.
- The cloud/shared deployment keeps using an external LLM.

## Verification
1. **Unit test** `tests/test_local_llm.py`, using a fake vLLM HTTP server (httpx `MockTransport`):
   - `park_for_gpu_job` drains, then sleeps, and kills the PID when the sleep fails.
   - `ensure_awake` raises `AssistantPaused` while a forecast job row is `running`.
   - `ensure_awake` wakes the model when it is asleep and no job is running.
2. `pixi run check && pixi run test`, and `pixi run generate-api-types`.
3. **On the Spark:**
   1. Run `ai-almanac init --yes`. The LLM env installs and the weights download.
   2. Run `ai-almanac serve` and chat. The model answers and tool calls work.
   3. Submit a forecast while chatting. The in-flight reply finishes, the job log shows the model parked, `/is_sleeping` returns true, and `free -g` shows the memory released.
   4. Chat during the forecast shows the paused message. The forecast completes without OOM.
   5. Chat afterwards wakes the model. Note the wake time.
   6. Idle for longer than the timeout and confirm the model sleeps.
   7. Kill `serve` with -9, restart, and confirm the stale vLLM PID is reaped.
4. Run `/security-review` before pushing, because this touches `src/ai_almanac/server/`: localhost-only dev endpoints and the subprocess launch.
