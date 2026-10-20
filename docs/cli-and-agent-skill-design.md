# Command Line Client, Agent Skill, and a Shared Core

**Status:** Draft / Proposed
**Date:** 2026-10-20
**Author:** hholb

## Context

Today ai-almanac has three ways to set up and run work: the web UI forms, the
REST API those forms call, and the in-app assistant's tools. We want a fourth: a
command line client that people and coding agents (Claude Code, through a skill)
can use to do everything the web UI does — discover regions, models and data,
set up and run benchmarks, blends and forecasts, follow jobs, and read results.

Adding a fourth entry point to the current code would multiply an existing
problem. The three entry points we have do not share one implementation; they
converge late, at the `create_*_for_user` functions in
`services/job_submission.py`, and each layer above that carries its own copy of
the rules. A trace of the code (2026-10-20) found:

**Checks that only some paths apply**

| Check | REST / web form | Assistant |
| --- | --- | --- |
| Benchmark guardrails (`guardrails.check_benchmark`) | **never run** — `create_job_for_user` does not call it, although `guardrails.py:12-14` says it does | validation only (`benchmark_domain.py:357`) |
| Region / catalog membership, per-model date coverage, `forecast_window_days >= 30` (`benchmark_domain.py:313-354`) | not run | run |
| Blend members in the observation region (`blend_domain.py:203-208`) | not run | run |
| Benchmark grid mismatch, focus-area conflicts, blend layout errors | run at submit | shown only at submit, not in validation |
| Forecast readiness (`live_forecast_blockers`) | server checks link **and** grid; the web form checks only the link (`forecasts/+page.svelte:142`) | no forecast tools |
| `require_forecasting` | router dependency only (`forecasts.py:41`) | n/a |

**Duplicated definitions**

- ROMP parameters are declared three times — `RompParams`
  (`job_submission.py:61`), `PerModelRompParams` / `BenchmarkAdvancedParams`
  (`chat_tools.py:29-59`), and `SHARED_/PER_MODEL_ROMP_PARAM_KEYS`
  (`benchmark_domain.py:102-130`) — with drift (`wet_threshold` is `float` in
  one and `FiniteFloat` in another). The web form has a fourth copy of the field
  list and coercion (`setup-form.svelte.ts:236-294`).
- Chat config patch models are declared twice (`chat_tools.py:62,83` vs
  `routers/chat.py:153,184`).
- Blend year-spec parsing, coverage, onset parameters and grid matching are
  re-implemented in TypeScript (`blends/year-coverage.ts`, `onset-params.ts`,
  `lib/source-coverage.ts`) and already disagree with the server (the client
  requires 4-digit years with no spaces; the server accepts 1800–2200 and strips
  whitespace).
- Guardrail thresholds the server serves and admins can override are hardcoded
  in the blend form (`MIN_ONSET_YEARS = 10`, `OVERFIT_WARNING_MEMBERS = 3`).
- Defaults are scattered: `"gfs"` four times in `job_submission.py`, the onset
  cutoff as a constant and as a literal, `forecastWindowDays = 30` in the form
  vs `max_forecast_day` from model metadata on the server.

**Reads implemented twice**

The assistant's result tools re-implement access checks, status handling and
serialization next to the REST endpoints rather than sharing them: metrics
bypass `metrics_cache` (only the router writes it), the spatial summary is
computed inline in `benchmark_domain.py:1111-1190` and has no REST equivalent,
the pooled blend summary is parsed in Python (`blend_domain._parse_pooled_summary`)
**and** in TypeScript (`blend-summary.ts:223`), and blend artifact lookup rules
differ between `routers/jobs.py:299-338` and `blend_domain.py:622-665`.

**Access control applied two ways**

REST uses `job_access.can_read / can_modify / listing_filter`. Assistant reads
filter on `user_id == uid` instead, so a shared or example job that
`chat_turns.validate_scope` admits into a session is then "not found" by the
tools, and admins cannot read other users' jobs through chat.

**Logic in the wrong layer**

- `job_submission.py` raises `HTTPException` throughout (46 raises across
  `services/`), so the service layer speaks HTTP and the assistant has to catch
  HTTP errors — an uncaught one fails the whole chat turn (`chat_turns.py:517-538`).
- Cancel, delete, share/unshare and promote-to-example live in
  `routers/jobs.py` (515-630) and are unreachable from anything but REST.
- Benchmark fan-out (one job per model) happens twice — in the browser
  (`setup-form.svelte.ts:345`) and in `benchmark_domain._exec_submit_benchmark`
  — and is not atomic: if model N fails, models 1..N-1 are already submitted.
- Results logic a client needs lives in the browser: deriving Brier skill from
  the summary CSV, window and metric ordering, collapsing re-runs into one
  logical forecast (`forecasts/+page.svelte:48-91`), run grouping
  (`benchmarks.svelte.ts:135`), and the active-status list (copied in four
  places; the backend has `job_manager.ACTIVE_STATUSES`).

A command line client built on today's code would have to either copy these
rules a fifth time or call the REST API and inherit its gaps. This design fixes
the shape first and then adds the client as a thin shell.

## Goals

- **One core.** Every way of doing a thing — web form, REST, assistant tool,
  command line — runs the same function for that thing, with the same checks,
  defaults, access rules and errors.
- **Check before run.** Any plan can be checked without side effects and the
  result is identical to what submitting would enforce.
- **A command line client** covering the web UI's user-facing capabilities, with
  stable machine-readable output so agents can drive it.
- **A Claude Code skill** that teaches an agent the workflow, the plan format,
  and how to read the results.
- **A narrower surface.** Fewer endpoints and models than today, not more: the
  CLI adds no endpoint that the web UI would not also use.

## Non-goals (deferred)

- An MCP server, interactive MCP App views, and remote MCP for claude.ai. The
  core and command set are designed so an MCP server would be a third thin
  shell (see Future work), but nothing here builds one.
- Admin and assistant-administration capabilities in the CLI (rulesets,
  comparisons, LLM profiles, settings, user management, data-source and region
  editing). The CLI covers the researcher's workflow; admins keep the web UI.
- The workspace / provenance primitives from `agent-workspace-design.md`. The
  core here is the layer those primitives would wrap; it does not implement them.
- Supporting `auth_mode=proxy` from the CLI (oauth2-proxy has its own token
  story). Local (`none`) and Globus are supported.
- Deploying any of this before the funding demo. All work lands on `develop`
  behind normal review and ships after the freeze lifts.

## Principles

- **P1 — Interfaces are shells; the core decides.** Routers, assistant tools and
  CLI commands parse input, call one core function, and render its result. They
  contain no rules. "Can I explain what this router does without saying *and*?"
- **P2 — Parse at the edge, trust inside.** Each job kind has one draft type
  (partial, what a form or chat accumulates) and one plan type (complete,
  resolved, checked). Only the core's `check_*` produces a plan, and `submit_*`
  only accepts one.
- **P3 — The core speaks the domain, not HTTP.** Core functions raise typed
  domain errors. Each shell maps them once: FastAPI to status codes, the
  assistant to tool error payloads, the CLI to exit codes.
- **P4 — The CLI is a client, never a back door.** It talks to the server over
  HTTP, even locally, so it goes through exactly the auth, access and checks the
  web UI does. It never imports the core or opens the database.
- **P5 — Servers own facts; clients own presentation.** Defaults, thresholds,
  status vocabulary, metric direction, logical-forecast grouping and derived
  skill are computed on the server. Clients choose layout, wording and colour.

## Decisions

### D1 — A core package with one module per user goal

New package `src/ai_almanac/server/core/`. It must not import `fastapi`,
`pydantic_ai`, or anything under `routers/` (enforced by an import-linter rule
or a test that walks imports).

```
server/core/
  actor.py        Actor: who is asking (user id, admin, anonymous)
  errors.py       domain error types (D4)
  catalog.py      regions, observation datasets, models, forecast models,
                  init sources, defaults and thresholds
  benchmarks.py   check_benchmark, submit_benchmark
  blends.py       check_blend, submit_blend
  forecasts.py    check_forecast, submit_forecast, refresh_forecast
  jobs.py         list, get, logs, cancel, rerun, delete, share, unshare,
                  promote_to_example
  results.py      summary, metrics, skill_scores, grids, cell, map_image,
                  blend_summary, blend_cell_metrics, blend_forecast, files
```

Existing `services/*` modules that are already storage, compute or integration
code (`metrics.py`, `skill_scores.py`, `blend_cells.py`, `storage.py`,
`job_manager.py`, `romp.py`, `artifact_store.py`, …) stay where they are and the
core calls them. What moves into the core is the *orchestration and rules*
currently split across `job_submission.py`, `benchmark_domain.py`,
`blend_domain.py`, `chat_tools.py` and the fat routers.

Every core function has the shape `(actor, parsed input) -> result`, raising
domain errors. Example:

```python
# core/benchmarks.py
async def check_benchmark(actor: Actor, draft: BenchmarkDraft) -> BenchmarkCheck:
    """Resolve a draft against the catalog and report everything submit would enforce."""
    resolved = await _resolve(actor, draft)          # sources, access, readiness, grids
    findings = _rules(resolved, await catalog.thresholds())
    return BenchmarkCheck.from_findings(resolved, findings)

async def submit_benchmark(actor: Actor, plan: BenchmarkPlan) -> BenchmarkRun:
    """Submit a checked plan as one run: all per-model jobs or none."""
    check = await check_benchmark(actor, plan.as_draft())   # re-check: the catalog may have moved
    if not check.runnable:
        raise InvalidPlan(check.errors)
    await quota.require(actor, job_count=len(check.plan.models))
    return await _dispatch(actor, check.plan)
```

`submit_*` re-runs `check_*` itself instead of trusting a plan from the client.
That keeps "check" and "submit" incapable of disagreeing, and makes `submit` safe
to call directly (as REST callers will).

### D2 — One draft type and one plan type per job kind, in a shared schemas module

New module `src/ai_almanac/schemas/` (outside `server/`, pure pydantic, no
database or server imports) holds the request and response models every shell
uses:

| Kind | Draft (partial) | Plan (complete) | Replaces |
| --- | --- | --- | --- |
| Benchmark | `BenchmarkDraft` | `BenchmarkPlan` | `BenchmarkRunSpec`, `JobCreate`, `RompParams`, `PerModelRompParams`, `BenchmarkAdvancedParams`, `SHARED_/PER_MODEL_ROMP_PARAM_KEYS`, `BenchmarkConfigPatch(In)` |
| Blend | `BlendDraft` | `BlendPlan` | `BlendRunSpec`, `BlendCreate`, `BlendParams`, `BlendConfigPatch(In)` |
| Forecast | `ForecastDraft` | `ForecastPlan` | `ForecastCreate`, `ForecastParams` |

- A **benchmark is multi-model**, as users and the assistant already think of
  it: shared settings plus per-model overrides. The per-model expansion into
  jobs (today done in the browser *and* in `_exec_submit_benchmark`) becomes a
  private step in `core/benchmarks.py`.
- Drafts are what the web form and an assistant session accumulate: every field
  optional, unknown ids kept (not silently dropped, as `benchmark_domain.py:505`
  does today) so the check can report them.
- A chat session row stores a draft. The session-specific code shrinks to
  "load draft, apply patch, save draft".
- The CLI reads drafts from YAML or JSON plan files with the same schema.
- Defaults live in one place, a `Defaults` model in `core/catalog.py` (init
  source, onset cutoff and thresholds, forecast window, true-holdout share,
  event type). They are served to clients (D6) and applied in `check_*`, never
  in a client.

`schemas/` is also what the CLI imports to parse server responses (P2 on the
client side). It is not a generated client: the CLI and server ship in the same
wheel, and the CLI checks the server's version on first contact (D8).

### D3 — Check and submit are separate, side-effect-free and side-effecting

Each `create_*_for_user` splits at the line the trace already identified:

| Today | Becomes |
| --- | --- |
| `create_job_for_user` 1436-1557 (resolve + build config) | `benchmarks._resolve` + `_rules` |
| `create_job_for_user` 1559-1645 (persist + dispatch) | `benchmarks._dispatch` |
| `create_blend_for_user` 811-909 / 911-979 | `blends._resolve` + `_rules` / `_dispatch` |
| `create_forecast_for_user` 1083-1235 / 1237-1317 | `forecasts._resolve` + `_rules` / `_dispatch` |

All rules from every current path move into `_rules`, so the table in Context
collapses to "every check, every path". Specifically, the benchmark guardrails
and the assistant-only catalog, coverage and window checks become submit-time
checks, and grid, focus-area and layout errors appear in `check_*`.

A `*Check` result carries:

```python
class BenchmarkCheck(BaseModel):
    runnable: bool
    plan: BenchmarkPlan | None        # set iff runnable: normalized, defaults applied
    errors: list[Finding]             # block submission
    warnings: list[Finding]           # shown, never block
    missing: list[str]                # draft fields still required
    jobs: int                         # how many jobs submit would create
    estimate: CostEstimate            # CPU/GPU class and rough hours; GPU ⇒ forecast
```

Two refinements:

- **Atomic benchmark submission.** `_dispatch` inserts all per-model jobs in one
  transaction, then hands them to the runner. If dispatch of a later job fails,
  earlier ones are canceled and the run reports the failure as a whole.
- **One quota function** replaces the three copies (`job_submission.py:925,
  1251, 1575`).

The forecast readiness gate (`trajectory_sets.set_is_ready`) and
`live_forecast_blockers` move into `forecasts._rules`, and `require_forecasting`
moves from the router dependency into `forecasts` so every shell inherits it.

### D4 — Domain errors, mapped once per shell

`core/errors.py`:

```python
class DomainError(Exception): ...
class NotFound(DomainError): ...            # also used for "exists but not yours"
class Forbidden(DomainError): ...           # authenticated but not allowed (admin-only)
class InvalidPlan(DomainError):             # carries findings
    def __init__(self, errors: list[Finding]): ...
class Conflict(DomainError): ...            # e.g. delete an active job
class QuotaExceeded(DomainError): ...
class FeatureDisabled(DomainError): ...     # forecasting off
class Unavailable(DomainError): ...         # upstream (boundaries, storage) failed
```

| Error | REST | Assistant tool | CLI exit |
| --- | --- | --- | --- |
| `InvalidPlan` | 422 with findings | `{"error", "findings"}` payload the model can explain | 2 |
| `NotFound` | 404 | `{"error": "not found"}` | 4 |
| `Forbidden` | 403 | `{"error": …}` | 3 |
| `Conflict` | 409 | `{"error": …}` | 1 |
| `QuotaExceeded` | 429 | `{"error": …}` | 6 |
| `FeatureDisabled` | 404 | tool not offered | 1 |
| `Unavailable` | 502 | `{"error": …}` | 1 |

One FastAPI exception handler in `app.py`, one wrapper in the assistant's tool
adapter, one in the CLI. `HTTPException` disappears from `services/` and
`core/` (a test greps for it). This also fixes the failure mode where an HTTP
error raised during a chat submit fails the whole turn.

### D5 — One access policy, used by every read and write

`Actor` replaces the bare `user_id: str` parameters. `job_access`'s
`can_read / can_modify / listing_filter` become the only access predicates, and
every `core/jobs.py` and `core/results.py` function applies them. Consequences:

- Assistant reads stop hand-filtering on `user_id == uid`. Session scope is an
  *additional* filter on top of access, not a replacement, so shared and example
  jobs in scope become readable, and admins see what they see on the web.
- `_resolve_parent_blend` and `refresh_forecast_for_user`'s private read checks
  go away.
- Anonymous actors get example jobs only, as today.

### D6 — Results and facts the clients need come from the server

New or changed endpoints, each backed by one `core/results.py` or
`core/catalog.py` function. The web UI switches to them and deletes its copies.

| Endpoint | Returns | Replaces |
| --- | --- | --- |
| `GET /jobs/{id}/summary` | compact structured summary: per-model, per-window headline metrics and skill, best/worst areas, coverage, warnings | assistant-only `get_spatial_summary`; gives the CLI and agents a few-KB answer instead of `/grids` |
| `GET /jobs/{id}/blend-summary` | **structured JSON** (per-lead metrics, Brier skill vs climatology, members in display order) | raw CSV + `blend-summary.ts` + `_parse_pooled_summary` |
| `GET /jobs/{id}/maps/{model}/{metric}/{window}.png` | server-rendered static skill map | nothing (new); used by CLI `results map` and image previews |
| `GET /forecasts` | logical forecasts (latest active, else latest complete), with `superseded_by_failure` | `specKey` / `representativeRun` / `newerRunFailed` in the forecasts page |
| `GET /config/defaults` | the `Defaults` model (D2) | hardcoded defaults in forms; extends the existing `/config` group |
| `GET /config/guardrails` | (exists) now *used* by the blend form | `MIN_ONSET_YEARS`, `OVERFIT_WARNING_MEMBERS` constants |
| `GET /config/metrics` | (exists) include `lower_is_better`, window order, display order | `mapUi.ts:7`, `FALLBACK_ORDER`, window ordering in `ResultsViewer` |
| Job responses | add `ui_url`, `is_active`, `kind`, `run_id` grouping key | client-side active-status lists, run grouping fallback |

All job-kind check endpoints:

- `POST /benchmarks/check`, `POST /benchmarks` (submit a benchmark plan as one run)
- `POST /blends/check`, `POST /blends`
- `POST /forecasts/check`, `POST /forecasts`, `POST /forecasts/{id}/refresh`

`POST /jobs` (single-model benchmark job) is retired in the same release that
the web form switches to `POST /benchmarks`. The SPA ships in the same wheel as
the API, so there is no external client to keep compatible; a stale open tab
gets a 404 and reloads.

**Web form validation.** The blend, benchmark and forecast forms call
`POST /*/check` (debounced) and render its `errors`, `warnings` and `missing`.
`year-coverage.ts`, `onset-params.ts`, `gridsMatch`, `unlinkedModels`, the
hardcoded thresholds and the payload builders are deleted. The hand-written TS
types in `lib/api/{jobs,blends,forecasts,config,chat}.ts` move to
`api-types.gen.ts`, as `lib/api/index.ts:1` already asks.

Status labels stay a client concern (P5), but the status *enum* and `is_active`
come from the generated types, and the four copies of the active list collapse
to one helper per client.

### D7 — The assistant's tools become adapters over the core

`chat_tools.py` keeps only what is chat-specific: loading and saving the
session draft, session scope, and the payload envelope (`tool_payload`). Every
tool body is "load draft → core call → payload". Concretely:

- `validate_*_config` → `core.*.check_*` on the session draft.
- `propose_*_submit` → `check_*`, then the existing pydantic-ai approval request
  carrying the checked plan; `submit_*_for_session` → `core.*.submit_*`. The
  "config changed after approval" comparison stays.
- `list_jobs`, `get_job_info`, `get_job_logs`, metrics, skill scores, spatial
  summary, blend results → `core/jobs.py` and `core/results.py` (gaining the
  metrics cache and uniform access).
- `rerun_job` → `core.jobs.rerun`, which dispatches by job kind, fixing reruns
  of blend jobs (today they fail as "Unknown model").
- Forecast tools (`check_forecast`, `submit_forecast`, `list_forecasts`) become
  possible and are added behind `require_forecasting` and approval, but are not
  required for this design's MVP.

`routers/chat.py`'s duplicate patch models and the `{"error"} → 400` mapping go
away; the routes call the same adapters.

### D8 — The command line client

The existing Typer app (`src/ai_almanac/cli.py`) gains client command groups.
The server commands (`serve`, `db`, `env`, `reset`, hidden runners) are
unchanged. Client commands live in `src/ai_almanac/client/` and use only
`httpx` and `ai_almanac.schemas`.

**Commands** (verbs follow the user's goals, not our endpoints):

```
ai-almanac login | logout | whoami
ai-almanac server use <name|url>              # local, staging, prod, or a URL
ai-almanac server show

ai-almanac regions list
ai-almanac datasets list [--region R]
ai-almanac models list --region R [--for benchmark|blend|forecast]
ai-almanac defaults show                      # defaults, thresholds, metric definitions

ai-almanac benchmark new --region R [--models a,b] > plan.yaml   # starter plan from defaults
ai-almanac benchmark check plan.yaml
ai-almanac benchmark run plan.yaml [--yes] [--wait]
ai-almanac blend new | check | run            # same shape
ai-almanac forecast new | check | run
ai-almanac forecast update <forecast-id> [--yes]

ai-almanac jobs list [--kind K] [--status S] [--mine]
ai-almanac jobs show <id>
ai-almanac jobs logs <id> [--tail N]
ai-almanac jobs wait <id>... [--timeout 30m]
ai-almanac jobs cancel <id> | rerun <id> | delete <id> | share <id> | unshare <id>

ai-almanac results summary <id>
ai-almanac results skill <id>
ai-almanac results metrics <id> [--window W] [--bbox …]
ai-almanac results map <id> --model M --metric X --window W -o map.png
ai-almanac results download <id> [--to DIR]
ai-almanac results open <id>                  # opens ui_url in the browser
```

**Behaviour:**

- `--json` on every command prints the response model exactly (the shape
  `schemas/` defines). Without it, TTY output is a readable table or summary.
  The skill tells agents to use `--json`.
- `run` always checks first and prints the check (findings, job count,
  estimate). It asks for confirmation unless `--yes`, and refuses if not
  `runnable`. GPU-class estimates (forecasts) always print a cost line.
- `--wait` and `jobs wait` poll `GET /jobs/{id}` with backoff and exit with the
  job's terminal state. They never wait unbounded (default timeout 30 m,
  overridable).
- Exit codes: 0 success, 1 general error, 2 invalid plan, 3 auth, 4 not found,
  5 job ended failed/canceled (for `--wait` / `jobs wait`), 6 quota, 7 timeout.
- Plan files are YAML or JSON drafts (D2). `new` writes a commented starter
  file built from `/config/defaults` and the region's catalog, so the CLI never
  hardcodes a default.

**Servers and auth:**

- `~/.config/ai-almanac/client.toml` holds named servers (`local` =
  `http://127.0.0.1:8765` built in) and the active one; `AI_ALMANAC_URL` and
  `--server` override.
- On first contact the client reads `/config/capabilities` (extended with
  `api_version` and `auth_mode`) and refuses to run against an incompatible
  API with a clear message.
- `auth_mode=none`: no login.
- `auth_mode=globus`: `login` runs the Globus native-app flow (`globus-sdk`
  `NativeAppAuthClient`, PKCE, local redirect or copy-paste code) for the
  existing API scope plus `offline_access`, and stores refresh tokens in the OS
  keyring (`keyring`), falling back to a `0600` file. Needs one new native-app
  client registration. The server is unchanged: it already introspects any token
  for its scope.

### D9 — The Claude Code skill

`.claude/skills/ai-almanac/SKILL.md` in this repo, plus reference files:

- `workflow.md` — discover → `new` → edit plan → `check` → show the user the
  check → `run` only after the user agrees → `jobs wait` → `results summary` →
  `results map` / `open`.
- `plans.md` — the plan file schema, generated from `schemas/` by a script so it
  cannot drift (checked by a test like `generate-api-types`).
- `reading-results.md` — the domain knowledge the in-app assistant's rulesets
  already encode: onset definitions, verification windows, Brier skill vs
  climatology, what the guardrail warnings mean, why a blend's true-holdout
  years matter. Sourced from the existing ruleset text so both agents explain
  the platform the same way.

Rules the skill states explicitly: always `--json`; never `run` without showing
the check and getting a yes; never `--yes` a forecast; prefer `results summary`
over downloading grids.

The *enforcement* of cost and approval is not in the skill (P3 of the workspace
design: enforcement lives in code paths, not prompts). It is the check in
`submit_*`, the quota, and the CLI's confirmation prompt. The project
`.claude/settings.json` gains `ask` permission rules for
`Bash(ai-almanac * run*)`, `Bash(ai-almanac forecast update*)` and
`Bash(ai-almanac jobs delete*)` so Claude Code prompts even if the skill is
ignored.

Distribution to people outside this repo (a plugin) is future work; the MVP
audience is the team.

## Architecture

```
  Web UI (SvelteKit)     Claude Code ── skill ──┐
        │                                       ▼
        │                         ai-almanac CLI (client/, httpx)
        │  REST + generated types               │  REST + schemas/
        ▼                                       ▼
  ┌──────────────────────── FastAPI routers ────────────────────────┐
  │ parse → core call → serialize; DomainError → HTTP (one handler) │
  └───────────────┬─────────────────────────────────────────────────┘
                  │                         In-app assistant (pydantic-ai)
                  │                           chat_tools: session draft + payload
                  ▼                                     │
  ┌─────────────────────────── server/core ─────────────┴───────────┐
  │ catalog · benchmarks · blends · forecasts · jobs · results      │
  │ Actor + job_access on every call; check_* ⊂ submit_*            │
  └───────────────┬─────────────────────────────────────────────────┘
                  ▼
  services/ (metrics, skill_scores, blend_cells, storage, job_manager,
            romp, artifact_store, guardrails predicates, …) · DB · runner
```

Two paths reach the core: HTTP (web UI and CLI share it completely) and
in-process (the assistant). Both call the same functions; neither has rules of
its own.

## Data model changes

None required. Atomic benchmark submission uses the existing `jobs` table inside
one transaction, and `run_id` already groups a benchmark's jobs. Chat session
rows keep their JSON config column; its contents become a serialized
`BenchmarkDraft` / `BlendDraft`, read through a parser that accepts the current
`BenchmarkRunSpec` / `BlendRunSpec` shape, so existing sessions keep working (no
migration, one-version compatible).

## Security considerations

- The CLI uses the same bearer-token path as the SPA; no new auth mechanism on
  the server. Tokens are stored in the OS keyring, never printed, and the CLI
  never echoes `Authorization` headers in `--debug` output.
- Moving every rule into `submit_*` *tightens* REST: benchmark guardrails and
  catalog/coverage checks now apply to direct API calls, closing the gap where
  the assistant was more constrained than a hand-written HTTP request.
- `require_forecasting` moving into the core means a future shell cannot
  forget it.
- Per P4 the CLI has no privileged path; anything it can do, a user with a
  token could already do with `curl`.
- Changes touch `src/ai_almanac/server/`, so each PR runs `/security-review`.

## Testing

- **Core:** behaviour tests per function (a draft with X is reported as Y; a
  submitted run creates N jobs or none). These replace most of the
  router-level and assistant-level duplicates of the same assertions.
- **Agreement:** one parametrized test feeds the same drafts through
  `POST /*/check`, the assistant's `validate_*` tool and the CLI's
  `check --json`, and asserts identical findings. This is the test that
  catches a rule creeping back into a shell.
- **Layering:** a test fails if `core/` imports `fastapi`, `pydantic_ai` or
  `routers`, or if `HTTPException` appears in `core/` or `services/`.
- **CLI:** commands run against the ASGI app through `httpx.ASGITransport`, so
  CLI tests exercise real routes and the real core without a network or a
  running server.
- **Skill:** extend `evals/` with a small headless Claude Code run against a
  throwaway local server: given a goal ("benchmark these two models over
  Ethiopia"), the agent checks before running and reports a summary. Post-MVP
  if it proves flaky.

## MVP scope

1. Divergence fixes that are bugs today (Phase 0).
2. `server/core` and `schemas/` for benchmarks, blends, jobs and results;
   forecasts' check/submit moved too, since `require_forecasting` and readiness
   must not stay in the router.
3. Check endpoints, `/benchmarks`, `/jobs/{id}/summary`, structured
   `/blend-summary`, `/config/defaults`, logical `/forecasts`, `ui_url`.
4. Web forms on check endpoints and generated types; client-side rule copies
   deleted.
5. Assistant tools as adapters.
6. CLI with the command set in D8, including `login` for Globus.
7. Skill with `workflow.md`, generated `plans.md`, `reading-results.md`.

The static map endpoint (`/maps/…png`) and `results map` are in scope if
server-side rendering fits without new heavyweight dependencies; otherwise they
follow in the next phase.

## Rough implementation phases

Each phase is one or more PRs into `develop`; none deploys before the demo.

- **Phase 0 — Fix divergent checks in place.** Call `check_benchmark` on the
  submit path; run the assistant-only catalog, coverage and window checks at
  submit; make benchmark fan-out atomic; send the metrics cache through one
  function; correct the forecast form's readiness check; add `warnings` to the
  TS `Blend` type. Small, independently reviewable, and each closes a real gap.
- **Phase 1 — Core skeleton.** `core/errors.py`, `Actor`, the FastAPI
  exception handler, the layering test. Move `create_*_for_user` into
  `core/{benchmarks,blends,forecasts}.py` split into `_resolve` / `_rules` /
  `_dispatch`, raising domain errors. Routers become thin. No API shape changes
  yet.
- **Phase 2 — Schemas and plans.** `schemas/` with drafts and plans; collapse
  the duplicate ROMP, blend and patch models; `check_*` endpoints;
  `POST /benchmarks`; session drafts with the backward-compatible parser.
- **Phase 3 — Reads and jobs in the core.** `core/jobs.py` (including cancel,
  delete, share, promote moved out of the router) and `core/results.py`;
  `/summary`, structured `/blend-summary`, `/config/defaults`, logical
  `/forecasts`, `ui_url`.
- **Phase 4 — Web UI on the core.** Forms call check endpoints; hand-written
  types replaced by generated ones; client rule copies and parsers deleted;
  `POST /jobs` retired.
- **Phase 5 — Assistant adapters.** `chat_tools` reduced to session handling
  plus core calls; access via `job_access`; kind-aware rerun.
- **Phase 6 — CLI.** `client/` package, server profiles, version handshake,
  Globus login, the D8 commands, ASGI-transport tests.
- **Phase 7 — Skill.** Skill files, the `plans.md` generator and check,
  permission rules, optional eval.

Phases 0–1 are prerequisites. 2 and 3 can run in parallel. 4 and 5 depend on
2–3. 6 needs 2–3 but not 4–5, so the CLI can be built while the web and
assistant migrations are under review.

## Open questions (need input)

1. **Command name.** Extend `ai-almanac` (one entry point, server and client
   commands side by side) or add a short `almanac` alias for client commands?
   Proposed: extend `ai-almanac`, add the alias only if typing it gets old.
2. **Globus native-app client.** Who registers it, and under which project
   (same as `ai-almanac-api`)?
3. **Cost estimates.** Is "CPU vs GPU class plus rough hours" enough for
   `check`, or do we want per-job estimates from past run durations?
4. **Static maps.** matplotlib is already a runtime dependency
   (`pyproject.toml:43`). Render `/maps/…png` on request in the server (cached
   like `metrics_cache`), or have the runner write map images as job artifacts?
5. **Web form check latency.** Debounced `POST /*/check` replaces instant
   client-side validation. Is a ~200 ms round trip acceptable, or do we keep a
   *syntax-only* client check (no rules) for instant feedback?

## Future work (post-MVP, designed-for but not built)

- **MCP server.** A third thin shell over the same core or over the CLI's
  client, using the D8 command set as the tool list. No core changes.
- **Plugin distribution** of the CLI skill for people outside this repo.
- **Workspace primitives.** `agent-workspace-design.md`'s `@primitive`
  registry wraps `core/` functions; provenance is stamped where `submit_*`
  dispatches.
- **Assistant forecast tools** beyond what D7 enables.
- **Proxy-mode CLI auth.**
