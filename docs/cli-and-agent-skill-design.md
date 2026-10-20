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
  results.py      summary, metrics, skill_scores, grids, cell,
                  blend_summary, blend_cell_metrics, blend_forecast, files
  telemetry.py    the @operation decorator and request context (D11)
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
```

`Finding` (today `guardrails.Finding`: `key`, `severity`, `message`) gains an
optional `field` path (e.g. `models[1].eval_start_year`) so forms can attach a
server finding to the input that caused it (D10). Cost estimates are left out
until cost becomes a real problem; the job count and the job kind (forecasts
are the GPU path) are enough to confirm against for now.

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

**Web form validation** is two-tier (D10). `year-coverage.ts`,
`onset-params.ts`, `gridsMatch`, `unlinkedModels`, the hardcoded thresholds and
the payload builders are deleted. The hand-written TS
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
- `run` always checks first and prints the check (findings, job count, job
  kind). It asks for confirmation unless `--yes`, and refuses if not
  `runnable`.
- `results map` renders a PNG **in the CLI** from `GET /jobs/{id}/grids` and the
  region boundary GeoJSON, using matplotlib (already a runtime dependency,
  `pyproject.toml:43`). This is the easiest first pass and adds no endpoint:
  drawing a map is presentation, which clients own (P5), exactly as the web UI
  draws its maplibre map from the same JSON. The grid never enters an agent's
  context; the agent reads the PNG. `results download --figures` fetches the
  figure PNGs ROMP already writes. A server-side image endpoint is revisited
  only if a second client needs the same picture.
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
- On first contact the client reads a new **unauthenticated**
  `GET /config/client`: `api_version`, `auth_mode`, and for Globus the public
  client id and API scope. (`/config/capabilities` can't serve this because it
  requires a signed-in user.) The CLI refuses to run against an incompatible
  API with a clear message. Nothing in this response is secret: the web app
  already embeds the same client id (`VITE_GLOBUS_CLIENT_ID`) and scope
  (`web/src/lib/auth.ts:6-7`).
- `auth_mode=none`: no login.
- `auth_mode=globus`: `login` uses `globus_sdk.UserApp` with the **web app's
  existing public client id**, read from `/config/client`, requesting the API
  scope plus `offline_access`. It uses Globus's copy-paste redirect
  (`https://auth.globus.org/v2/web/auth-code`), so it works over SSH and in
  headless shells, and keeps the SDK's default token storage.
  - **Setup:** add that redirect URI to the existing client. No new client
    registration and no server change; the server already introspects any token
    issued for its scope.
  - **Rejected: delegating to `globus login`.** The Globus CLI can obtain a
    token for our scope (`globus session consent <scope>`), but it never hands
    tokens to other programs. Using one would mean reading its private
    `~/.globus/cli/storage.db`, an undocumented format that can change in any
    release.
  - **Trade-off:** Globus sees CLI and web sign-ins as the same app (same
    consent screen name). Telling them apart in our own records is handled by
    D11's `channel`, not by the client id. If separate branding or auditing at
    Globus is ever needed, registering a dedicated native client is cheap and
    changes only the id `/config/client` returns.

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

### D10 — Form validation: generated field rules in the browser, plan rules on the server

Instant, as-you-type validation and a single rule layer are compatible if the
rules are split by **what they need to know**, and each kind has exactly one
home:

| Tier | What it covers | Where it is written | How the form gets it |
| --- | --- | --- | --- |
| **Field rules** | one value against static constraints: required, type, range, enum, string pattern, list length | pydantic constraints on the draft/plan models in `schemas/` (`Field(ge=30)`, `Literal[...]`, `Annotated[str, StringConstraints(pattern=...)]`) | **generated** zod schemas from the OpenAPI document, produced by the same `generate-api-types` task that writes `api-types.gen.ts` (e.g. the zod plugin of `@hey-api/openapi-ts`, or `openapi-zod-client`; pick at implementation) |
| **Plan rules** | anything cross-field or data-dependent: coverage and year splits, grid compatibility, region membership, guardrails, forecast readiness, access | `core/*._rules` only | `POST /*/check`, debounced (~300 ms after typing stops, and on step changes); findings attach to inputs through `Finding.field` |

The forms validate field rules synchronously with zod on every keystroke, and
show plan findings as they arrive. Nothing is written twice: field rules are
written once in Python and compiled to TypeScript; plan rules exist only on the
server.

The discipline that makes this hold: **a rule that needs only the field's own
value must be a declarative constraint, not code in a `field_validator`**,
because validator bodies do not appear in JSON Schema and so would silently
become server-only. Year specs are the main case: the syntax becomes a
`pattern` on the string type (generated into zod), while range bounds and
coverage stay plan rules. A lint-style test lists every `field_validator` /
`model_validator` in `schemas/` against an allowlist of ones that are
genuinely cross-field, so new single-field validators get pushed into
constraints.

What this costs: cross-field checks that are static (e.g. start year ≤ end
year) appear after the debounce instead of instantly. That is the price of not
duplicating them, and the check round trip is cheap: it reads the catalog, it
does not touch data.

The CLI needs neither tier locally: `check` is the server call, and `schemas/`
parsing of a plan file reports field-rule errors before the request is sent,
from the same pydantic models.

### D11 — Observability: one instrumentation point in the core

The shared core makes cross-interface observability nearly free: every action,
from any interface, passes through one function, so it can be measured in one
place.

**What exists today:**

- Plain-text logs from `logging.basicConfig` (`app.py:47`).
- A per-request id: generated in the `log_requests` middleware, returned as
  `X-Request-ID`, and already used by the web app's feedback breadcrumbs.
- `audit_events` / `usage_events` rows on job submission and LLM use
  (`services/events.py`).
- The assistant turn log (`services/turn_log.py`).
- Cloud Monitoring alerts on uptime, 5xx ratio and the database being down
  (`terraform/monitoring.tf`).

**What is missing:**

- Logs are not structured, so Cloud Logging cannot filter or count by field.
- Nothing records *which interface* an action came from.
- Unexpected errors are not tracked as errors.
- Job failures are not watched.

**Decisions:**

1. **Channel on the actor.** `Actor` carries `channel` (`web`, `cli`,
   `assistant`, `api`) and `client_version`. The web app and the CLI send
   `X-Almanac-Client: web/<version>` or `cli/<version>`. A request without the
   header is `api`. The assistant sets `assistant` in-process. The header is
   self-reported and used for analytics only, never for decisions.
2. **One decorator on every core function.** Each core function is declared
   with `@operation("benchmarks.submit")`. The decorator emits one structured
   log record per call:
   - `operation`, `channel` and `client_version`;
   - user id, plus `request_id`, or the assistant's `turn_id` when the call
     came from a chat turn;
   - `duration_ms`;
   - `outcome`: `ok`, a domain error class (D4), or `unexpected`;
   - resource ids (job, run, blend).

   This replaces ad-hoc logging in routers and tools, and it is the only place
   to add a metric for a new capability.
3. **JSON logs when deployed.** In deployed mode a small stdlib JSON formatter
   writes fields Cloud Logging understands (`severity`, `message`,
   `logging.googleapis.com/trace`). Locally the human-readable format stays,
   with `ai-almanac serve --log-format json` available for debugging. No new
   dependency.
4. **Correlation end to end.**
   - The middleware accepts an inbound `X-Request-ID` from the CLI, validated as
     short hex, and generates one otherwise.
   - The id goes into a context variable read by `@operation` and by
     `events.audit` / `events.usage`, whose `metadata` gains `channel` and
     `request_id`. That needs no migration, because `metadata` is already JSON.
   - A job row can then be traced back to the CLI command, web click or chat
     turn that created it.
   - The CLI prints the request id with every error
     (`error: … (request 3f2a9c…)`) so a user report can be matched to the
     server log.
5. **Errors split by kind.**
   - **Domain errors** are expected outcomes. They are counted by operation,
     channel and class, but don't alert. A spike in `InvalidPlan` from
     `channel=cli` usually means the skill is teaching the wrong plan format.
     That is a useful signal, and it is visible only because outcomes are
     labelled.
   - **Unexpected exceptions** are logged at `ERROR` with the stack trace.
     Cloud Run sends those to Error Reporting with no new service.
   - A log-based metric plus an alert in `monitoring.tf` covers the rate of
     unexpected errors.
6. **Job outcomes.** The supervisor logs a `job.finished` record (kind, status,
   channel, duration) when a job reaches a terminal state. A log-based metric
   gives the failure ratio per job kind, and an alert fires when it crosses a
   threshold. This is the gap that matters most for unattended agent use.
7. **Usage questions are answered from the same records.** Examples:
   - Which interfaces are used, and by whom?
   - What fraction of checks lead to a submit?
   - Which CLI versions are in use?
   - Is any agent looping, i.e. repeated failing submits from one user?

   Log-based metrics labelled by `operation`, `channel` and `outcome` feed a
   Cloud Monitoring dashboard in Terraform. Deeper questions use SQL over
   `usage_events`. A BigQuery log sink is future work if that stops being
   enough.
8. **The CLI does not phone home.** There is no client-side telemetry.
   Everything worth measuring already happens on the server, and that keeps the
   CLI's privacy story simple.

The assistant's turn log stays as it is. It answers assistant-quality
questions (ratings, flags, offered tools), and `turn_id` in `@operation`
records links a turn to the core calls it made.

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
  the server. The one new unauthenticated endpoint, `/config/client`, returns
  only values the public web bundle already contains. Tokens stay in the
  globus-sdk token store, are never printed, and the CLI
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
- **Field rules:** a shared fixture file of field values (valid and invalid)
  is consumed by a pytest test against the pydantic models and a vitest test
  against the generated zod schemas, asserting the same accept/reject verdicts.
  It catches a generator gap or a rule that slipped into a validator body.
- **Telemetry:** every public `core/` function carries `@operation` (a test
  walks the package); a domain error and an unexpected error each produce one
  record with the right `outcome` and `channel`.
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

8. Generated field schemas for the forms (D10) and the observability
   chokepoint with JSON logs (D11).

## Rough implementation phases

Each phase is one or more PRs into `develop`; none deploys before the demo.

- **Phase 0 — Fix divergent checks in place.** Call `check_benchmark` on the
  submit path; run the assistant-only catalog, coverage and window checks at
  submit; make benchmark fan-out atomic; send the metrics cache through one
  function; correct the forecast form's readiness check; add `warnings` to the
  TS `Blend` type. Small, independently reviewable, and each closes a real gap.
- **Phase 1 — Core skeleton.** `core/errors.py`, `Actor` (with `channel`),
  `core/telemetry.py` (`@operation`, request context, JSON log formatter,
  inbound `X-Request-ID`), the FastAPI exception handler, the layering test. Move `create_*_for_user` into
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
- **Phase 4 — Web UI on the core.** zod generation added to
  `generate-api-types`; forms validate field rules with the generated schemas
  and plan rules with debounced check calls; `X-Almanac-Client` header;
  hand-written types replaced by generated ones; client rule copies and
  parsers deleted; `POST /jobs` retired.
- **Phase 5 — Assistant adapters.** `chat_tools` reduced to session handling
  plus core calls; access via `job_access`; kind-aware rerun.
- **Phase 6 — CLI.** `client/` package, server profiles, version handshake,
  `/config/client`, Globus login on the web app's client (add the copy-paste
  redirect URI to it), the D8 commands including `results map`, the
  `X-Almanac-Client` and `X-Request-ID` headers, ASGI-transport tests.
- **Phase 6b — Watching it.** `job.finished` records from the supervisor;
  log-based metrics, an unexpected-error alert, a job-failure-ratio alert and a
  usage dashboard in `terraform/monitoring.tf`. Applied after the demo along
  with everything else.
- **Phase 7 — Skill.** Skill files, the `plans.md` generator and check,
  permission rules, optional eval.

Phases 0–1 are prerequisites. 2 and 3 can run in parallel. 4 and 5 depend on
2–3. 6 needs 2–3 but not 4–5, so the CLI can be built while the web and
assistant migrations are under review.

## Resolved decisions (2026-10-20)

- **Command name:** client commands extend `ai-almanac`. Users who want a
  shorter name can alias it themselves.
- **Login:** reuse the web app's public Globus client (D8). No new
  registration; `globus login` delegation rejected.
- **Cost estimates:** out of scope until cost is a real problem.
- **Map images:** rendered in the CLI from `/grids` for the first pass (D8). No
  server image endpoint.
- **Form validation:** generated field rules in the browser, plan rules on the
  server (D10).
- **Observability:** one `@operation` chokepoint in the core (D11).

## Open questions (need input)

1. **zod generator.** `@hey-api/openapi-ts` (zod plugin) or
   `openapi-zod-client`: pick by how well each maps pydantic's `pattern`,
   `ge`/`le` and discriminated unions. Settled by a spike in Phase 4.
2. **Job-failure alert threshold.** What failure ratio per job kind should
   page? It needs a few weeks of `job.finished` data before it can be set
   sensibly; start as a dashboard-only metric.

## Future work (post-MVP, designed-for but not built)

- **MCP server.** A third thin shell over the same core or over the CLI's
  client, using the D8 command set as the tool list. No core changes.
- **Plugin distribution** of the CLI skill for people outside this repo.
- **Workspace primitives.** `agent-workspace-design.md`'s `@primitive`
  registry wraps `core/` functions; provenance is stamped where `submit_*`
  dispatches.
- **Assistant forecast tools** beyond what D7 enables.
- **Proxy-mode CLI auth.**
