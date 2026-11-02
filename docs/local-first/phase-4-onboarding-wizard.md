# Phase 4 spec — onboarding wizard (web + CLI)

Two PRs: **4a backend**, **4b frontend + CLI**. Implements Phase 4 of
`docs/local-first-single-process.md` (personal-mode single-user — no mode
selection or admin-account steps; no restart/re-exec needed).

> **Canonical contract note:** env-prepare progress events use Phase 2's
> `EnvProgressEvent` (`kind` ∈ `phase_started|line|phase_finished|
> phase_skipped|phase_failed`; `phase` ∈ `pixi-bootstrap|benchmark|blending|
> blending-source|forecast:*`) — see
> `docs/local-first/phase-2-pixi-bootstrap.md`. The SSE layer here wraps
> those events with `seq` numbering plus its own `state`/`done` framing
> events; it does not define a second event type.

**Hard dependencies:** Phase 2 (`ensure_env(progress)` + `EnvProgressEvent`)
and Phase 3 (secrets bootstrap) must land first. The wizard's LLM step needs
Phase 3's auto-generated `credential_encryption_key` to seal `llm_api_key` —
today `PATCH /settings` 400s on secrets without it.

**Key facts the design leans on (verified):**

- Personal mode makes every request an implicit admin — the only boundary in
  the not-yet-configured state is the bootstrap token + gating middleware.
- Existing routers mount at the root (`/jobs`, `/settings`, …) — **no
  `/api` prefix exists**, so `prefix="/api/setup"` and the SPA page `/setup`
  collide with nothing.
- `@app.middleware("http")` prepends: the **last-registered decorator runs
  outermost**. Current outermost is `_spa_navigation_fallback`. The setup
  gate must be registered *after* it in the file so it runs first.
- SSE precedent: `routers/chat.py` returns
  `StreamingResponse(gen, media_type="text/event-stream",
  headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})`; the
  frontend consumes it with a **fetch-reader parser** (`sseEvents<T>()` in
  `web/src/lib/api/chat.ts`), *not* `EventSource` — so custom headers (the
  setup token) work on the SSE GET.
- Background-work precedent: `asyncio.create_task(...)` in `lifespan`. A
  module-level singleton task in the new service is consistent.
- Settings overlay: `write_settings_overlay()` upserts `app_config` rows,
  seals `SENSITIVE_FIELDS`; `reload_settings()` re-layers (env wins). **No
  migration needed** — `app_config` is a k/v table.
- `tests/conftest.py` gives every test a fresh data dir — without care the
  gate would 403 the entire suite (mitigation below).
- `web/tests/setup-form.test.ts` already exists (benchmark form) — name the
  new test `setup-wizard.test.ts`.

---

## PR 4a — backend: install state, gate, `/api/setup/*`

### New file: `src/ai_almanac/server/services/setup.py`

Shared service layer that both the router and `ai-almanac init` call, so the
two paths cannot drift.

```python
# --- install state ---
def setup_required() -> bool            # personal mode and not settings.setup_complete
def grandfather_existing_install() -> bool
    # If setup_complete unset but the install has prior use, mark complete.
    # Rule: llm_is_configured() OR _load_db_overlay() non-empty OR
    # config_yaml_path().exists(). Called from lifespan and from `serve`.

# --- bootstrap token (file-backed: survives restarts mid-setup and the
# --- uvicorn --reload subprocess; Jupyter-style) ---
def get_or_create_bootstrap_token() -> str   # token_urlsafe(32) → data_root()/"setup_token", 0600
def verify_bootstrap_token(candidate: str | None) -> bool   # secrets.compare_digest
def clear_bootstrap_token() -> None          # unlink on finish

# --- probes ---
def detect_platform() -> dict     # envs pixi platform + platform.machine()
def probe_gpu() -> dict | None    # nvidia-smi --query-gpu=name,memory.total, 5s timeout, None on failure
def env_status() -> dict          # per-env "ready"|"missing" (benchmark/blending: pixi.toml +
                                  # .pixi/envs/default present; blending also source marker);
                                  # forecast: "ready"|"partial"|"missing"|"unsupported"

# --- LLM test (mirrors services/llm.py _build_model) ---
async def test_llm_connection(base_url, model, api_key) -> LlmTestResult
    # AsyncOpenAI(base_url=..., api_key=api_key or "placeholder", timeout=10)
    # 1) models.list() → models_ok + first N ids
    # 2) chat.completions.create(..., max_tokens=1) → completion_ok
    # Never raises; {ok, models_ok, completion_ok, models, error}

# --- persistence (thin wrappers; router and CLI share validation) ---
def save_storage(output_dir, dataset_mount_roots) -> None  # validate → overlay + reload
def save_llm(base_url, model, api_key | None) -> None      # overlay (api_key sealed) + reload
def finish_setup() -> None                                 # overlay setup_complete=True + reload + clear token

# --- env-prepare task singleton ---
class PrepareTask:
    status: Literal["idle", "running", "done", "failed"]
    events: list[dict]        # ring buffer, monotonically increasing "seq"
    def start(include_forecast: bool) -> bool   # no-op if running (idempotent)
        # asyncio.create_task(asyncio.to_thread(ensure_env, progress=self._on_progress, ...))
        # _on_progress marshals thread→loop via call_soon_threadsafe
    async def subscribe(after: int) -> AsyncIterator[dict]  # replay seq > after, then follow
prepare_task = PrepareTask()   # module singleton — personal mode is single-process
```

### New file: `src/ai_almanac/server/routers/setup.py`

`router = APIRouter(prefix="/api/setup", tags=["setup"])`. **No
`CurrentUser`/`AdminUser` anywhere in this file** — the dependency is:

```python
async def require_setup_token(request: Request) -> None:
    if not setup_service.setup_required():
        raise HTTPException(404)      # endpoints vanish post-setup
    if not verify_bootstrap_token(request.headers.get("x-setup-token")):
        raise HTTPException(401, "Invalid or missing setup token")
```

Endpoints (all with pydantic response models so `generate-api-types` emits
usable types):

| Endpoint | Behavior |
|---|---|
| `GET /api/setup/state` | `{platform, gpu, data_dir, config_yaml_path, dataset_mount_roots, llm: {configured, base_url, model}, envs: env_status(), prepare: {status, last_seq}}` — includes prepare status so a reopened browser knows to reattach |
| `POST /api/setup/storage` | `{output_dir?, dataset_mount_roots?}` → validate + `save_storage` |
| `POST /api/setup/llm` | `{base_url, model, api_key?, test_only}` → `test_llm_connection`; persist only when test passed and not `test_only`; returns `LlmTestResult` |
| `POST /api/setup/envs/prepare` | `{include_forecast}` → `prepare_task.start(...)`, 202 `{status}`; current status if already running |
| `GET /api/setup/envs/events?after=N` | SSE over `prepare_task.subscribe(after)`, chat.py headers |
| `POST /api/setup/finish` | `finish_setup()` → `{ok: true}` |

### SSE wire format

`data: <json>\n\n`, `: keepalive` comment every 15 s.

```jsonc
{"type": "state", "seq": 0, "status": "running", "envs": {...}}   // snapshot, first on (re)connect
{"type": "env", "seq": 7, "kind": "phase_started", "phase": "benchmark", "detail": "Solving benchmark environment"}
{"type": "env", "seq": 8, "kind": "line", "phase": "benchmark", "line": "  + numpy 2.1.0"}
{"type": "env", "seq": 41, "kind": "phase_finished", "phase": "forecast:aifs2", "detail": "..."}
{"type": "env", "seq": 42, "kind": "phase_skipped", "phase": "forecast:base", "detail": "unsupported on osx-arm64"}
{"type": "done", "seq": 99, "ok": true, "error": null, "envs": {"benchmark": "ready", ...}}
```

`type: "env"` events are `asdict(EnvProgressEvent)` plus `type`/`seq`;
`state`/`done` are the SSE layer's framing.

### Modified: `src/ai_almanac/settings.py`

Add `setup_complete: bool = False`. Flows through the overlay machinery for
free; `SETUP_COMPLETE=1` env wins (used by conftest and cloud deploys). Do
**not** add to `_FIELD_GROUPS` — never a settings-UI field.

### Modified: `src/ai_almanac/server/app.py`

1. Lifespan: after the second `_reload_user_config()`, call
   `grandfather_existing_install()`; re-reload if it wrote.
2. Extend `/ready`: add **informational** top-level keys — not in `checks`
   (an unprepared env must not 503 a servable app):
   `{"setup_complete": not setup_required(), "envs": env_status()}`.
3. New `_setup_gate` middleware — **defined after `_spa_navigation_fallback`**
   (register-last = run-first):
   - Pass through when `not setup_required()`.
   - Allowlist: `/setup` (exact), `/api/setup/` prefix, `/config.js`,
     `/ready`, `/health`, and static assets — GET resolving to an existing
     file under `_STATIC_DIR` with a `resolve().is_relative_to(_STATIC_DIR)`
     containment check.
   - Document GET (reuse `_is_page_navigation`) to any other path →
     `RedirectResponse("/setup", 307)`.
   - Everything else →
     `JSONResponse({"detail": "Setup required", "code": "setup_required"}, 403)`.
4. `app.include_router(setup.router)`.

### Modified: `src/ai_almanac/server/routers/config.py`

Add `"setupRequired": setup_required()` to the `/config.js` payload
(allowlisted by the gate) so the SPA client-side-redirects with no API call.

### Modified: `src/ai_almanac/cli.py` (`serve`)

After `reload_settings()`/`ensure_layout()` (grandfather check must run here
too — lifespan hasn't yet):

```python
if setup_required():
    token = get_or_create_bootstrap_token()
    url = f"http://127.0.0.1:{port}/setup?token={token}"
    typer.echo(f"First-run setup: {url}")     # also what --open opens
```

Works under `--reload` because the token is file-backed.

### Modified: `tests/conftest.py`

`os.environ["SETUP_COMPLETE"] = "1"` next to the existing `LLM_BASE_URL`
line — otherwise the gate 403s the whole suite. Setup tests
`monkeypatch.delenv` + set `settings.setup_complete = False`.

### New: `tests/test_setup_api.py`

- Gate: pre-setup `/jobs` → 403 `code=setup_required`; `/ready`, `/health`,
  `/config.js` pass; document GET `/blends` → 307 `/setup`; post-`finish`
  the gate lifts and `/api/setup/state` → 404.
- Token: missing/wrong header → 401; correct → 200.
- `state`: platform/data-dir/env-status shape; `probe_gpu` monkeypatched.
- `llm`: mock `AsyncOpenAI` (or `respx`) for pass/fail/`test_only`.
- Prepare + SSE: monkeypatch `ensure_env` with a fake emitting a scripted
  `EnvProgressEvent` sequence; assert full stream; reattach with `?after=`
  mid-stream and assert replay-without-duplication; second POST prepare is
  a no-op.
- `finish`: `setup_complete=true` overlay row written; settings reloaded;
  token file unlinked.
- Grandfathering rule.

### PR 4a ordered steps

1. Branch off `develop`; confirm Phase 2/3 merged.
2. `settings.py`: `setup_complete`.
3. `envs/manager.py`: `env_status()` helper (if Phase 2 didn't add it).
4. `services/setup.py`.
5. `routers/setup.py`; wire into `app.py` (router, gate, lifespan
   grandfather, `/ready`).
6. `routers/config.py` `setupRequired`; `cli.py` serve printout.
7. `conftest.py` guard + `tests/test_setup_api.py`.
8. `pixi run generate-api-types` — **commit the regenerated
   `web/src/lib/api-types.gen.ts`** (new routes change the schema; CI fails
   on stale output).
9. `pixi run check && pixi run test`; `/security-review`
   (unauthenticated-by-design endpoints, token compare, path containment,
   SSE task); push.

---

## PR 4b — frontend wizard + `ai-almanac init`

### Frontend — new files

| File | Contents |
|---|---|
| `web/src/lib/api/sse.ts` | Lift `sseEvents<T>()` out of `api/chat.ts` (chat.ts re-imports; no behavior change) |
| `web/src/lib/api/setup.ts` | Standalone client — deliberately not `core.ts`'s `request()` (no auth/401 logic pre-setup). `getSetupToken()`/`storeSetupToken()` on `sessionStorage['almanac-setup-token']`; every call sends `X-Setup-Token`. `getSetupState`, `saveStorage`, `testLlm`, `saveLlm`, `startPrepare`, `streamPrepareEvents(after)` (fetch + `sseEvents`, auto-reconnect with last-seen `seq`). Types from `api-types.gen.ts`. |
| `web/src/lib/setup/wizard.svelte.ts` | `SetupWizardState` modeled on `ConfigSettingsState`: `$state` for step, state payload, per-step errors, prepare log lines + per-env status map. `load()` jumps to the envs step and reattaches (`streamPrepareEvents(0)`) when `prepare.status === "running"`, else to the earliest incomplete step — key to re-enterability. |
| `web/src/routes/setup/+page.svelte` | Shell: reads `?token=` on mount → `storeSetupToken()` → `history.replaceState` strips it from the URL; renders linear steps; token-missing state says "open the URL printed in the terminal". |
| `web/src/routes/setup/SystemStep.svelte` | platform / GPU / data dir readout |
| `web/src/routes/setup/StorageStep.svelte` | output dir + dataset mount roots; reuse input/description markup from `routes/settings/[section]/+page.svelte` |
| `web/src/routes/setup/LlmStep.svelte` | base URL / model / key; "Test connection" (`test_only`), then save; surfaces model list from the probe |
| `web/src/routes/setup/EnvPrepareStep.svelte` | forecast toggle (default `gpu.available && platform.startsWith('linux')`); per-env progress rows + scrolling log pane from SSE; resume is automatic via reattach |
| `web/src/routes/setup/FinishStep.svelte` | POST finish → `window.location.href = '/'` (full reload so `/config.js` re-evaluates) |
| `web/tests/setup-wizard.test.ts` | Vitest: step transitions incl. resume-into-running-prepare; token capture/strip/header; SSE reattach dedup by `seq` (mock fetch with scripted ReadableStream) |

### Frontend — modified files

- `web/src/routes/+layout.svelte` — when
  `window.__ALMANAC_CONFIG__?.setupRequired` and path isn't `/setup`,
  `goto('/setup')`; when path starts with `/setup`, skip `<Nav/>`,
  `<Footer/>`, and `account.load()` (which would 403 against the gate).
- `web/src/lib/api/core.ts` — add `setupRequired?: boolean` to the
  `__ALMANAC_CONFIG__` declaration; optionally redirect to `/setup` on a 403
  body with `code: "setup_required"`.
- `web/src/routes/settings/+page.svelte` — **fix the stale copy**: changes
  land in the database overlay, not config.yaml; config.yaml is the
  hand-editable seed.

### CLI — `ai-almanac init` in `src/ai_almanac/cli.py`

```
ai-almanac init [--yes] [--llm-base-url URL] [--llm-model NAME] [--llm-api-key KEY]
                [--dataset-mount-roots CSV] [--output-dir PATH]
                [--prepare-envs/--no-prepare-envs] [--include-forecast/--no-include-forecast]
                [--skip-llm-test]
```

Flow (every step calls `services/setup.py` — nothing reimplemented):

1. `reload_settings()`, `ensure_layout()`, apply migrations (overlay needs
   the table), Phase-3 secrets bootstrap.
2. Already `setup_complete` → confirm re-run (or exit 0 with `--yes`).
3. Show `detect_platform()`/`probe_gpu()`/data dir; data dir is env-driven,
   so the prompt is confirm-only ("set `AI_ALMANAC_DATA_DIR` to change").
4. Prompt storage values → **write to `config.yaml`** (merge-preserving;
   init seeds the hand-editable file).
5. Prompt LLM endpoint/model/key → `asyncio.run(test_llm_connection(...))`;
   on failure re-prompt (abort under `--yes` unless `--skip-llm-test`).
   base_url/model → config.yaml; **api_key via `save_llm` → sealed DB
   overlay** (never plaintext YAML).
6. Optional env prepare: `ensure_env(progress=<typer.echo adapter>,
   include_forecast=...)` — same callback contract, streamed to terminal.
7. `finish_setup()`; print `ai-almanac serve` next-step.

New `tests/test_cli_init.py`: `CliRunner` with
`ensure_env`/`test_llm_connection` monkeypatched — headless `--yes` writes
config.yaml + sealed key + `setup_complete`; failed LLM test aborts nonzero.

### PR 4b ordered steps

1. Branch off `develop` (4a merged).
2. `sse.ts` lift + `api/setup.ts`.
3. `wizard.svelte.ts` + `/setup` route components.
4. Root layout gating/redirect + `core.ts` type.
5. Settings-page copy fix.
6. `init` command + tests.
7. `web/tests/setup-wizard.test.ts`.
8. `pixi run generate-api-types` (should be a no-op — CLI adds no routes).
9. `pixi run check && pixi run test`; `/security-review` (token-in-URL
   handling); push.

---

## Verification (both PRs)

```bash
pixi run check && pixi run test
pixi run generate-api-types && git diff --exit-code web/src/lib/api-types.gen.ts
pytest tests/test_setup_api.py tests/test_cli_init.py -x
cd web && npm test -- --run tests/setup-wizard.test.ts

# End-to-end smoke on a virgin install:
AI_ALMANAC_DATA_DIR=$(mktemp -d) ai-almanac serve --no-open   # prints /setup?token=…
curl -s localhost:8765/jobs                                   # → 403 setup_required
curl -s localhost:8765/ready | jq '.setup_complete, .envs'
curl -s -H "X-Setup-Token: $T" localhost:8765/api/setup/state | jq
# browser: run wizard, kill tab mid-prepare, reopen /setup → reattached log
AI_ALMANAC_DATA_DIR=$(mktemp -d) ai-almanac init --yes \
  --llm-base-url http://localhost:11434/v1 --llm-model llama3 \
  --skip-llm-test --no-prepare-envs
```

## Open questions

1. **Grandfathering rule** for pre-Phase-4 installs upgrading in place —
   proposed `llm_is_configured() or overlay non-empty or config.yaml
   exists`; too loose/tight?
2. **Prepare-task durability**: `ensure_env` runs in a thread of the server
   process; a restart mid-prepare kills it. Proposed: acceptable — pixi
   install is idempotent/resumable; snapshot shows `idle` + partial
   `env_status`; UI offers resume (re-POST). The detached-supervisor
   alternative is heavy for setup.
3. **Wizard persists to DB overlay while `init` seeds config.yaml** —
   intentional, but an install configured both ways gets
   overlay-beats-yaml precedence. Acceptable?
4. **`/api/setup/*` in the public OpenAPI schema** (needed for generated
   types) — document it, or `include_in_schema=False` with hand-written
   frontend types?
5. Gate response for APIs: 403 with `code=setup_required` (chosen) vs 503;
   `/docs` stays gated (chosen: yes).
