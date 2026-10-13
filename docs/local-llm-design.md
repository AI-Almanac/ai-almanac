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
- **Spike results (2026-09-23, DGX Spark, driver 580 / CUDA 13.0):**
  1. vLLM 0.30.0 installs from PyPI wheels (`manylinux_2_28_aarch64`). It pins
     `torch==2.13.0` from PyPI (cu130), so the env takes only Python from
     conda-forge. `tilelang` and `z3-solver` ship only `manylinux_2_34`
     aarch64 wheels, so the manifest needs a glibc 2.34 floor. With 2.28, pixi
     builds their sdists, and that fails. torch lists `sm_120` and runs on
     GB10 (sm_121).
  2. `--enable-sleep-mode` works on unified memory. With
     `--gpu-memory-utilization 0.45` the served model puts ~61 GB in use.
     Level-2 sleep drops that to ~10 GB in 0.5 s.
  3. The wake sequence above works as written; `/is_sleeping` confirms it, and
     output after waking is correct. `POST /sleep` also takes `mode=wait`,
     which lets in-flight requests finish instead of aborting them. A request
     sent while asleep hangs rather than erroring, so the chat gate is
     required. `--api-key` guards only `/v1`, `/v2`, `/inference`, and
     `/cohere`; the dev endpoints stay open whatever the config, so loopback is
     the boundary.
  4. Default model: `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`
     (~20 GB) with `--tool-call-parser qwen3_coder --reasoning-parser
     nemotron_v3 --enable-auto-tool-choice --max-model-len 65536`. A tool call
     parses correctly. `evals/assistant` has not been run against it yet.
  5. Cold start: 438 s (weights 121 s, then profiling, compile, and CUDA graph
     capture). Level-2 wake: 132 s, nearly all of it `reload_weights`
     re-processing the 52 MoE shards on CPU. That is still ~5 min faster than a
     cold start.
  6. Startup tuning (2026-09-24), measured as cold start / decode speed / wake:
     - Default flags: 393 s / 69 tok/s / 105 s.
     - `--max-cudagraph-capture-size 32`: 189 s / 69 tok/s / 103 s. The
       warmup pass drops from 117 s to 34 s and graph capture from 78 s to
       3 s. Now in the default `llm_serve_args`.
     - Adding `--max-num-batched-tokens 1024`: 185 s, within noise. Not kept.
     - `--moe-backend flashinfer_cutlass` or `cutlass`: rejected at load; they
       don't support this checkpoint's NVFP4 scheme. `MARLIN` is the only MoE
       backend that runs it, so the ~110 s weight processing, and with it the
       wake time, can't be cut by choosing a backend.
  7. Load-path spike (2026-09-24). The ~110 s was never the MARLIN repack:
     `process_weights_after_loading` takes 2 s. It was vLLM's default mmap
     safetensors loading page-faulting the checkpoint in (~150 MB/s from page
     cache on the Spark). Measured as cold start / weight load / wake:
     - `--safetensors-load-strategy eager`: 44 s / 13 s / 25 s, same decode
       speed and output. Now in the default `llm_serve_args`.
     - `--safetensors-load-strategy prefetch`: 140 s / 112 s / 107 s. No gain.
     - `--load-format sharded_state`: no gain (119 s load). vLLM's own
       `save_sharded_state` fails on MARLIN-repacked tensors, and a snapshot
       taken before post-processing loads just as slowly because the cost is
       the mmap path. `reload_weights` also raises `NotImplementedError` for
       this format, which breaks wake. Dead end.
     - The warmup pass (34 s in the earlier runs) drops to ~1 s once
       kernel caches under `~/.cache` are warm, with any flags.
  8. llama.cpp spike (2026-09-24): conda-forge `llama.cpp` (CUDA 12.9 build,
     runs on the 13.0 driver) with `ggml-org/...-GGUF:Q4_0` (18.9 GB), the
     same model:
     - Single server: 5.4 s cold start, 19 GB total at 64k context (vLLM at
       0.45 claims ~55 GB), 72 tok/s, correct answer, and the tool call parses
       with `--jinja`.
     - Router mode (`--models-dir`, `--models-max 1`): router up in 0.8 s,
       model load 3.0 s, unload 0.8 s down to 0 GB, reload 3 s. It also
       discovers GGUFs in the Hugging Face cache, so switching models is
       picking a name.
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
- `start()` / `suspend()` / `stop()`:
  - Launch through `run_llm_server` with `--host 127.0.0.1 --enable-sleep-mode` and `VLLM_SERVER_DEV_MODE=1`. The dev endpoints are unauthenticated, so the server must bind to localhost only.
  - Write the PID and the launch flags to `data_root()/llm.pid`. On startup, reuse a live server whose recorded flags match the current ones: one a previous `serve` left asleep, or one still running after a crash. A cold start costs ~6 min. A live server with different flags is replaced.
  - `suspend()` runs at `serve` shutdown. It sleeps the model rather than killing it, so the next start wakes it (~25 s) and an exited `serve` doesn't leave its memory claimed. A server that is still loading is stopped instead. `ai-almanac llm stop` shuts the server down fully.
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
- **Lifecycle:** `start()` / `suspend()` run in the `app.py` lifespan when the engine is managed. The server starts vLLM, lets it load, and it sleeps once the idle timeout expires.
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

## Model catalog
`src/ai_almanac/server/config/local_llm_models.yaml` lists the models offered by
the setup wizard, `ai-almanac init`, `ai-almanac llm models`, and the Settings
model field. Each entry carries its own `vllm serve` flags and memory share,
because tool-call and reasoning parsers differ by model family, and an 80 GB
model needs more than 0.45 of the Spark. A blank `llm_serve_args` uses the
entry's flags. `llm_gpu_memory_utilization` left at its default uses the
entry's share. A model outside the catalog gets only the startup tuning, so
its tool-call parser must be set by hand.

Switching models (`ai-almanac llm use <id>`, or the Settings page) takes effect
on the next chat turn. `ensure_awake` notices that the running server's
recorded flags no longer match and restarts it.

Only Nemotron 3.5 Lightning is `verified: true`. To verify another entry on a
Spark:
1. Run `ai-almanac llm use <id>`.
2. Chat, and confirm a tool call parses.
3. Submit a forecast, and confirm `/is_sleeping` and that the memory is freed.
4. Chat again, and note the wake time.
5. Flip the flag, and record the timings here.

## Deliberately skipped
- Letting a small LLM run alongside a forecast when memory fits. We can add a memory-budget check later if someone wants that.
- Parking for blend/ROMP jobs. They are CPU-bound and much smaller.
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
   7. Kill `serve` with -9 and restart it: the running vLLM is reused. Change `llm_serve_args` and restart: it is replaced.
4. Run `/security-review` before pushing, because this touches `src/ai_almanac/server/`: localhost-only dev endpoints and the subprocess launch.
