# Agent Workspace and Provenance

**Status:** Accepted. As of 2026-11-13 the ledger, workspaces, typed primitives for
benchmarks, blends and forecasts, acquisition (ERA5-ARCO), `run_code`, follow-ups and
the workspace UI are built. Event-index composition, event scoring, recipes and the
transform and forecast-catalog primitives are not (see
[Implementation phases](#implementation-phases)).
**Date:** 2026-09-22 (revised 2026-11-13)
**Author:** hholb

Provenance is a **per-user ledger** with one step per real execution, and a
**workspace is a view** over it (D1–D3). Decisions D-R1 to D-R26 came from a
section-by-section review of the first prototype; they are folded into the sections
below and indexed in [Review decisions](#review-decisions-2026-09-25).

## Context

The assistant today is a set of typed tools bolted onto two pages. A chat session
is created by the Benchmarks or Blends page with a `ChatScope` of kind
`benchmark_setup`, `benchmark_run_group`, `blend_setup` or `job_set`
(`src/ai_almanac/server/services/benchmark_state.py:41-47`); the scope decides which
jobs the tools may read and which prompt sections apply. Submitting a benchmark or
blend rewrites the scope server-side. The session list shows only sessions for the
current page's scope. There is no standalone chat route.

The tool surface (`src/ai_almanac/server/services/llm.py:197-450`) is five
toolsets: **benchmark** (list regions/datasets/models, get/update/validate/submit
config), **blend** (same shape plus `get_blend_results`), **jobs**, **metrics**
(`get_job_metrics`, `get_skill_scores`, `get_spatial_summary`) and **analysis**
(`run_code`, `run_code_sandbox`). Forecast generation, data-source registration,
region creation and the ROMP onset-rule registry are not reachable from chat.
`run_code` sees only a completed job's aggregated `spatial_metrics_*.nc` files.

What the platform does well is enforcement and evaluation: `ApprovalRequired` on
every submission, statistical guardrails applied at the job-submission chokepoint
regardless of caller, admin-versioned prompt `Ruleset`s, blind A/B comparison, and
per-turn `assistant_turn_logs` (ruleset version, model, tokens, tool names, guardrail
keys). That is provenance of the _conversation_. What we lack is provenance of the
_data_: a figure downloaded from the chat gallery carries nothing; `chat_artifacts`
holds `id, session_id, user_id, kind, storage_key, created_at` and no link to the
tool call, code or jobs that produced it; `jobs.config_json` records parameters but
not the ROMP or onset_blending version, image digest or onset-rule definition that
ran; ROMP's output NetCDFs record only
`model, years, tolerance_days, verification_window, max_forecast_day, mok`.

### Reference design: Rhiza `weather-skills`

Rhiza Research's [weather-skills](https://github.com/rhiza-research/weather-skills),
[weather-skills-core](https://github.com/rhiza-research/weather-skills-core) and
[weather-skills-chat](https://github.com/rhiza-research/weather-skills-chat) take
the opposite shape: ~37 small CLI scripts (fetch, clip, aggregate, plot, …) that
share one Zarr contract, so any step's output is any other step's input, composed
at run time by an agent. Four ideas from that design carry over:

1. **Primitives with one shared I/O contract.** Fixed dimension vocabulary
   (`lat, lon, time, init_time, prediction_timedelta, member`), canonical flag
   names, pint units. Composition needs no glue.
2. **Provenance stamped on the artifact by the runtime, not the agent.** Every
   output carries `weather_skills_history`: an append-only list of
   `{skill, version, args, input: {basename, hash}}`. PNGs carry it in `tEXt`
   chunks. A `provenance` tool renders it as lineage, JSON or a reproduction script.
   Freeform-code outputs have their provenance stripped so only runtime-stamped
   chains exist.
3. **Cheap read-only "capability" tools** alongside data tools: `inspect-zarr`,
   `resolve-region`, `resolve-time`, `--probe-latest`. They stop the model guessing.
4. **A workspace the agent lists first**, reusing artifacts whose provenance shows
   they already answer the question.

We deliberately do not take the skill-pack model (git clone → codegen tool per
`SKILL.md` → argv passthrough), the Open WebUI shell, or Landlock sandboxing. Our
surface should stay typed, scope-bound and enforced in code; code execution already
runs on Modal.

### Why now

Users are asking for composable event indices and for one place to run
benchmarks, blends and forecasts. Both require the model to see the whole platform.
And the platform is meant to inform real-world decisions: anyone acting on a number
produced here must be able to audit every step — theirs and the model's — that led
to it. That requirement, not flexibility, is the design constraint.

## Goals

- One persistent **workspace** per project where a user and the assistant run
  benchmarks, blends, forecasts, event-index detection, transforms and sandbox
  code against a shared set of artifacts.
- The assistant sees **every platform capability** through one tool surface, with
  the same approval and guardrail semantics everywhere.
- **Complete, verifiable provenance** on every artifact: inputs by content hash,
  the primitive and its version, its parameters, the package/image versions that
  ran, the chat turn (ruleset version, model) that proposed it, and the human
  approval that authorised it. This covers three separate properties (D-R1):
  - **Attribution:** who asked, who approved, and with which model and ruleset.
    _Required for MVP_, for every step including jobs, and for numbers the
    assistant quotes in chat (D-R8).
  - **Integrity:** the files still match the record. _Required for MVP._
  - **Reproducibility:** the step can be re-run to the same result. _Designed
    for, deferred_ (pinned job images, recipes).
- Provenance is **renderable** as a human lineage, machine JSON, and a re-runnable
  pipeline spec.
- Existing Benchmarks/Blends/Forecasts pages keep working and become entry points
  into a workspace.

## Non-goals (deferred)

- **Natural-language data acquisition from arbitrary sources.** Fetch primitives
  are designed for; only ERA5-ARCO (already in Modal) and one credential-free
  forecast catalog are in MVP scope.
- **Compatibility with weather-skills.** We borrow its ideas and dimension
  names, but take no dependency on `weather-skills-core` and do not aim to run
  its scripts (D2, D-R25).
- **Multi-user shared workspaces.** Per-user projects first; sharing is a visibility
  flag added later, as with data sources.
- **Non-onset event types in ROMP** (cessation, heat). The composable-index design
  makes them reachable; the ROMP input-loader work is a separate track.
- **Replacing Modal or the job supervisor.**

## Principles

- **P1 — The runtime stamps provenance; the model never does.** Provenance is
  written by the server or the Modal harness after a primitive returns. Anything a
  model-authored script writes that looks like provenance is stripped.
- **P2 — Declarative before imperative.** If an operation can be expressed as a
  typed primitive with a schema, it is one. `run_code` is the escape hatch, and its
  outputs are second-class in provenance (they carry a code hash, not a parameter
  schema).
- **P3 — Enforcement lives in code paths, not prompts.** Guardrails, bounds,
  approvals and scope checks run at chokepoints shared by chat, REST and UI, as
  they do today for job submission.
- **P4 — Anything that costs money or leaves the workspace requires approval.**
  Modal jobs, GPU inference, external fetches, publishing.
- **P5 — Immutable by identity.** Artifacts, event indices, recipes and provenance
  steps are never edited in place; new versions get new ids. A `supersedes`
  pointer is added only where a versioned object needs one (event indices,
  recipes), not speculatively on artifacts (D-R4).
- **P7 — Bytes only through storage.** Nothing outside the storage layer turns an
  artifact into a filesystem path. Local disk and GCS are both first-class
  (D-R12).
- **P8 — One writer per session.** At most one turn streams on a chat session at
  a time, and the server enforces it, whether the turn was started by the user or
  by a follow-up (D-R16).
- **P6 — Human-readable everywhere.** Every id has a label; every chain renders to
  prose; every parameter has a unit and a doc string.

## Decisions

### D1 — A workspace is a per-user project: a view over the user's ledger

```
workspaces
  id, user_id, name, description, settings_json, created_at, updated_at

workspace_items
  workspace_id, item_kind           -- artifact | job
  artifact_id | job_id              -- exactly one, per item_kind
  label                             -- the model's handle; unique within the workspace
  title, supporting, added_by, added_at
```

A user may have many workspaces. A chat session belongs to exactly one. The
workspace **owns no provenance**. Artifacts and steps live in the user's ledger
(D2, D3), and a workspace is the set of items a project has pulled in, with its own
labels. Adding a finished job or an existing artifact to a second workspace is a
new item row, not a copy and not a new step. Deleting a workspace deletes items,
never audit records.

Rationale: per-session workspaces make reuse across conversations impossible
without copying; a single per-user workspace grows without bound and gives the
model an unbounded "what is already here" problem. Named projects match how
users already think about a region-plus-season investigation.

### D2 — Artifacts are first-class, with our own contract in a weather-skills-compatible shape

```
artifacts
  id, user_id, created_at
  kind                    -- dataset | metrics | figure | table | model | document
  format                  -- zarr | netcdf | png | csv | json | pickle | cog
  location_kind           -- owned | job_output
  path                    -- owned: key under the user's artifact prefix
  job_id, job_file        -- job_output: (kind, filename) of a published job file
  content_hash            -- sha256 of the stored bytes (see "Hashing" below)
  size_bytes
  summary                 -- JSON: dims, coords ranges, variables, units, bbox, time range
  provenance_step_id      -- FK → provenance_steps (the first step that produced these bytes)
```

Artifacts are **per user and content-addressed**: the same bytes produced twice
are one artifact. Every producing step lists it in `outputs`, and lineage shows
all producers, not only the first (D-R6). Labels live on `workspace_items` (D1).

**Location is typed, and bytes go through storage** (D-R5, D-R12, P7). The storage
layer exposes `open(artifact) -> stream`, for hashing, previews and download, and
`stage(artifact, dir) -> Path`, which downloads from GCS or returns the local path,
for code that needs a file. The prototype encoded location as a `job:` prefix in
`storage_key` and resolved it with `result_file_path`, which is `None` under GCS.
So in the cloud no job output ever reached a workspace.

**Dataset contract.** A `dataset` artifact is an xarray-openable store (Zarr or
NetCDF) whose dimensions use the ontology `lat, lon, time, init_time,
prediction_timedelta, member, vertical, point_id`. Synonyms accepted on read and
normalised on write: `latitude/longitude`, `step/lead_time → prediction_timedelta`,
`number/realization → member`. Every data variable carries a CF `units` string
parseable by pint. Precipitation is stored as a rate (`mm day-1`) unless a
`aggregation_period` attribute says it is a period total (`mm`). This is ROMP's
`dim_fmt`/`dim_fmt_model` vocabulary (`momp/utils/standard.py`) made explicit, and
it is the same ontology weather-skills uses, so their fetchers' outputs would open
here unchanged.

**Named types** are shorthand for required-dimension sets, checked by primitives on
input: `spatial` (lat, lon), `observations` (lat, lon, time), `forecast`
(lat, lon, init_time, prediction_timedelta), `ensemble_forecast` (+ member),
`point_obs` (point_id, time), `event_dates` (lat, lon, [init_time]; a
`datetime64` variable), `metrics` (lat, lon; ROMP/e2s skill fields).

**Why not depend on `weather-skills-core`.** It is pre-1.0 and moving daily. We
mirror its dimension names and provenance entry shape so interoperation is a
translation at the boundary, not a rewrite, and we own our release cadence.

### D3 — Provenance is a per-user ledger of hash-linked steps, one per real execution

```
provenance_steps
  id                      -- uuid
  user_id
  created_at              -- when the execution started (a job's created_at)
  completed_at            -- when its outputs were attached; null while a job runs
  actor_kind              -- assistant | user | system
  primitive               -- e.g. "romp.detect_events", "run_code", "fetch.era5_arco"
  primitive_version       -- semver of the primitive definition in ai-almanac
  params                  -- JSON, schema-validated for typed primitives; no labels
  params_hash             -- sha256 of canonical JSON
  inputs                  -- JSON list of {role, artifact_id, content_hash}
  outputs                 -- JSON list of artifact_id
  runtime                 -- JSON: {sandbox, env_digest, image_digest, packages: {…}, modal_call_id}
  job_id                  -- nullable FK → jobs
  code_hash               -- sha256 of source, run_code only
  turn_id, session_id     -- nullable; the chat turn that invoked it
  ruleset_id, ruleset_version, model_name   -- copied from the turn log at write time
  approval_id             -- nullable FK → approvals
  parent_step_ids         -- JSON list; the steps that produced `inputs`
  step_hash               -- sha256 over (primitive, primitive_version, params_hash,
                          --   sorted (role, content_hash) pairs, env/image digest, parent step_hashes)
```

**One step per execution** (D-R2). A step is written where the work actually
happens. A primitive's step is written by the decorator (D4). A **job's step is
written at submission**, carrying the turn, ruleset, model and approval of
whoever submitted it, and its outputs are attached when the job publishes. A job
therefore has exactly one history, however many workspaces show it.

**Inputs are keyed by role** (D-R11). Primitives take workspace labels, but the
decorator resolves them to artifact ids before recording, so the ledger holds no
labels (labels are per-workspace view names). Sealing `(role, hash)` pairs means
swapping two inputs, such as forecast and observations, changes the seal. The
prototype sealed `sorted(input_hashes)`, which did not.

```
approvals
  id, user_id, session_id, turn_id, created_at
  kind                    -- submit_benchmark | submit_blend | run_forecast | save_index | external_fetch | publish
  payload_hash            -- sha256 of the exact payload the user saw
  decision                -- approved | denied
```

**What a chain proves.** Given any artifact, walking `provenance_step_id →
parent_step_ids` yields a DAG whose leaves are fetches, registered data sources or
uploads. Because `step_hash` covers the input content hashes and the parent step
hashes, a chain that has **drifted** (a file changed, went missing, or a row was
edited without re-sealing) is detected. Two artifacts with equal `step_hash` were
produced by identical computation on identical inputs. The `approval_id` on a step
ties it to the exact payload a human saw and approved; `turn_id` ties it to the
ruleset version and model that proposed it. This is the audit trail: for any number
a user acts on, the chain answers _what data, what definition, what code version,
who proposed it, who approved it, and when_.

**What it does not prove** (D-R1). Seals live in the same database as the rows they
seal, so anyone who can write a row can recompute its seal. That makes the chain
drift detection, not tamper-proofing. The UI and docs must not call it
"tamper-evident" until seals are anchored outside the database (see Future work:
signed chains). Verification also does not re-execute anything. It shows that the
files and the record agree, not that the computation reproduces.

**Stamping at download** (D-R10). Files are stored **exactly as produced**. The chain
is written only into the copy a user downloads: a JSON list in the
`ai_almanac_history` attribute on Zarr/NetCDF (entries
`{step, primitive, version, params, inputs:[{role, hash}], step_hash}`), a `tEXt`
chunk of the same name on PNG, an EXIF UserComment on JPEG, a `<meta>` tag on
HTML. The database is the record, and the stamped download is its portable copy.
The key name differs from `weather_skills_history` on purpose: a consumer can tell
which runtime stamped a file, and we never claim their verification mark.

**Hashing.** A content hash is **plain sha256 of the stored bytes**, computed by
streaming (P7). Job outputs are hashed once, when the job publishes. The
checksums in `job_artifacts` can't be reused, because GCS reports MD5 and local
storage SHA-256. Because nothing is stamped at rest, no format-aware hashing is
needed. The prototype stamped at rest, which forced hashes that skip the
provenance attributes: NetCDF hashing decoded every variable into memory, and PNG
hashing re-parsed chunks. Both are removed.

**Verification.** `GET /workspaces/{id}/artifacts/{aid}/provenance?verify=true`
re-hashes each input still present in storage and recomputes `step_hash` along the
chain, reporting `verified | input_missing | hash_mismatch` per step. With byte
hashes this is cheap enough to run whenever a file is viewed, and the UI shows the
status inline (D-R21). The assistant's `verify_provenance` tool calls the same
code.

**Approvals** (D-R3). One `record_approval` at the submission chokepoint writes an
`approvals` row for every gated action: primitives, benchmark and blend
submission, `rerun_job`, and REST calls (auto-approved with the caller's id). Every
chain then has the same shape whatever surface started it. The prototype recorded
approvals for primitives only.

**Freeform code.** A `run_code` step records `code_hash` and the full source is
stored as a `document` artifact so it is auditable, but its `params` are empty and
its outputs are labelled `derived (unverified computation)` in every rendering.
Before any file the code wrote is registered, the harness strips any
`ai_almanac_history` or `weather_skills_history` attribute or chunk from it (P1).

### D4 — Capabilities are typed primitives registered through one decorator

Every capability the assistant can invoke is a **primitive**: a Python function
declared with the `@primitive` decorator (name, version, approval and availability
flags) in the server's primitives package. The decorator:

- derives the PydanticAI tool (name, docstring, Pydantic input schema) so the LLM
  surface and the REST surface share one definition;
- validates input artifacts against their declared named type before the body runs;
- resolves `workspace_id` and scope from `ChatDeps`, and resolves label arguments
  to artifact ids with their roles (D-R11);
- raises `ApprovalRequired` when `needs_approval` or when `cost` exceeds the
  workspace policy, and records the approval (D-R3);
- on return, registers output artifacts, computes hashes, writes the
  `provenance_steps` row and adds the outputs to the workspace as items (D3);
- appends the primitive name, and any artifacts it read, to the turn log (D-R8).

Primitive groups, with the MVP set marked ●. **Not built:** the transform group, the
event and score groups, `probe_latest`, `plot_timeseries`, `plot_compare`, and the
forecast catalogue and refresh tools. The forecast group as built is `check_forecast`,
`run_forecast`, `update_forecast`, `get_forecast_results`, `summarize_by_area` and
`detect_onset`.

| Group                | Primitives                                                                                                                                     | Notes                                                                                                                                                                                                   |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **inspect** ●        | `list_artifacts`, `inspect_artifact`, `describe_provenance`, `verify_provenance`, `list_capabilities`                                          | Read-only. `inspect_artifact` returns dims, coord ranges, variables, units, bbox, time span — never data. `list_capabilities` returns the registry with schemas so the prompt need not enumerate tools. |
| **resolve** ●        | `resolve_region(name \| bbox \| geojson)`, `resolve_time(expr, as_of)`                                                                         | Region resolution reuses `regions.yaml` + Natural Earth admin-0/1; result is an ad-hoc region artifact scoped to the workspace, not a global catalog row. `resolve_time` is calendar math only.         |
| **acquire**          | `list_sources`, `plan_fetch(request)`, `fetch(request)`, `probe_latest(source)`                                                                | See D13. Public catalog sources from `ai-almanac-tools`; first `era5_arco`, then one forecast catalog. `fetch` needs approval (P4).                                                                     |
| **transform**        | `select_variables`, `clip`, `mask`, `regrid`, `aggregate_temporal`, `convert_to_totals`, `difference`, `summarize_dim`                         | See D12. Ops in `ai-almanac-tools`, run in the network-blocked analysis environment and exposed from its manifest.                                                                                      |
| **event** ●          | `list_event_indices`, `compose_event_index(spec)`, `preview_event_index(index, obs, region, year)`, `detect_events(index, ds)` → `event_dates` | See D7. `detect_events` runs ROMP's rule on obs or forecast.                                                                                                                                            |
| **score** ●          | `score_events(forecast_events, obs_events, windows, tolerance)` → `metrics`, `skill_scores(…)`                                                 | ROMP's contingency and probabilistic layers, with `verification_window_list`, `tolerance_days_list`, `day_bins` as parameters instead of the hardcoded values in `services/romp.py:87-90`.              |
| **benchmark** ●      | `submit_benchmark(spec)`, `validate_benchmark(spec)`, `rerun_job(job_id, overrides)`, catalogue `list_*`                                       | Stateless and typed (D-R7): the full spec is parsed at the boundary and errors come back as validation results. Submit and rerun need approval, write the job's step and link the job.                  |
| **blend** ●          | `submit_blend(spec)`, `validate_blend(spec)`, `get_blend_results`                                                                              | As above.                                                                                                                                                                                               |
| **forecast**         | `list_forecast_models`, `list_init_sources`, `run_forecast(blend, init_time, models, lead_hours)`, `refresh_forecast`                          | Wraps `routers/forecasts.py`; gated by `require_forecasting`; A100 cost ⇒ approval.                                                                                                                     |
| **figure** ●         | `plot_map(ds, var, region)`, `plot_timeseries`, `plot_compare(a, b)`                                                                           | Deterministic, stamped. Replaces most current `run_code` matplotlib use.                                                                                                                                |
| **code** ●           | `run_code(inputs: [artifact_id], code)`                                                                                                        | D5.                                                                                                                                                                                                     |
| **jobs / metrics** ● | existing                                                                                                                                       | Unchanged.                                                                                                                                                                                              |

**Workspaces use typed tools only** (D-R7). The page chats (Benchmarks, Blends) keep
their draft-config tools (`update_*_config` → `validate` → `submit`, one draft per
session). A workspace session does not get them. In the prototype it did, which
meant submissions bypassed the chokepoint, a workspace could hold one draft at a
time, and `rerun_job` created jobs with no approval and no workspace link.

**Read sets** (D-R8). Read-only tools write no step, but every data-reading tool
(`get_job_metrics`, `get_skill_scores`, `get_spatial_summary`,
`get_blend_results`, `get_forecast_results`, `inspect_artifact`) returns
`source_artifacts: [label]`. The turn log records the turn's **read set**: the
artifact ids its tools read. A number the assistant quotes in chat is then
attributable to the files it came from, and the UI shows them as a "based on"
footer (D9).

Primitive **versions** are semver constants next to the function, bumped by hand
in the PR that changes behaviour and checked by a test that diffs the schema
against a committed snapshot (a behaviour change without a bump fails CI). They are
what `provenance_steps.primitive_version` records; the _package_ versions that ran
go in `runtime`.

The **denylist** `SELF_CONFIGURATION_TOOL_PATTERNS` (`llm.py:458`) applies to
primitive names too. No primitive may create or modify rulesets, guardrails,
settings, users, or approvals.

### D5 — Execution: Modal functions for primitives, a network-blocked `modal.Sandbox` for code

Typed primitives run as Modal functions in the existing apps (`almanac-romp`,
`almanac-blending`, `almanac-forecasts`) or, for cheap transforms and inspection,
as a new lightweight `almanac-workspace` app with only xarray/zarr/gcsfs. Long
steps (benchmark, blend, forecast) remain **jobs** under the supervisor, with the
provenance step written when the job completes and its outputs are registered.

`run_code` changes from a plain `subprocess.run` (`modal/app.py:124-142`, `_run_generated_code`; the
docstrings claim "no network access" but nothing enforces it) to a
`modal.Sandbox` with `block_network=True`, the requested input artifacts staged
read-only under `/inputs/{label}/`, an `/outputs/` directory for results, CPU/memory
limits as today, and the service-account credentials never present. The harness
contract stays `compute(inputs: dict[label, path]) -> dict` with `save_figure`.
Every file under `/outputs/` becomes an artifact after provenance stripping (D3).
`run_code_sandbox` (no inputs) is kept for pure computation.

**One harness, two sandboxes.** One explicit setting, parsed at startup, picks
where `run_code` runs (D-R14): `code_sandbox: off | srt | modal`. It replaces
`enable_local_code_execution`, and the prototype's habit of inferring the sandbox
from `JOB_RUNNER`.

- `modal` (staging, prod): a fresh `modal.Sandbox` per call in the
  `almanac-workspace` app (`services/modal_code_sandbox.py`), with no network, no
  secrets, inputs copied in, and only `/outputs` copied back. Built 2026-09-25.
- `srt` (personal installs): the same harness under sandbox-runtime
  (`services/code_sandbox.py`), with read-only input copies and no network. With
  `srt` set, startup fails if the srt probe fails, instead of silently hiding the
  tool.

**One analysis environment** (D-R13). Both sandboxes run the same pinned package
set, defined once in `envs/analysis.pixi.toml` with its lockfile. Locally, srt
runs that environment's interpreter, not the server's, which also keeps server
packages out of the model's reach. On Modal, the Sandbox image is built from the
same package list. Every `run_code` step records `runtime.env_digest`: the
lockfile hash locally, the Modal image id in the cloud. That makes code steps
comparable and sealed on every deployment. (The prototype's srt path used the
server interpreter and recorded no environment.)

`run_code` needs no approval (D-R15, answering open question 3). Its outputs are
labelled _derived, unverified computation_, and the turn's read set (D4) records
what it consumed.

**Job images** (deferred with reproducibility, D-R1). Each Modal image used by a
job should eventually be pinned to a digest recorded in `runtime.image_digest`.
The current `romp:latest` tag (`modal/app.py:42-45`) would be replaced by a digest
resolved at deploy time and stored in settings, and the local pixi env's ROMP pin
(`envs/benchmark.pixi.toml:36`) bumped to the same commit the image is built from.

### D6 — Sessions attach to a workspace; scope becomes a link set, not a filter

Add `ChatScope.kind = "workspace"` with `key = workspace_id` and no `job_ids`
filter. Tools that today scope job reads by `run_id`/`job_ids`
(`benchmark_domain._scope_conditions`) instead read "jobs linked to this
workspace". Submitting a benchmark or blend from a workspace session **links** the
new job rather than rewriting the scope (`benchmark_domain.py:616-633`,
`blend_domain.py:387` today). The existing four scope kinds remain valid; a
session of those kinds can be **promoted** into a workspace, which creates the
workspace, links the session's jobs and registers their outputs.

_As built,_ the session's scope JSON (`{kind: "workspace", key}`) is the link, and
`chat_sessions.workspace_id` was not needed.

The system prompt for `workspace` scope gets a per-turn **artifact digest**, so the
model starts every turn knowing what already exists (Rhiza's "list the workspace
first" made structural rather than instructional). The digest is **capped**
(D-R9): counts per group, the ~20 most recent items, and everything referenced in
the last few turns, each with label, title, kind and a breadcrumb of its last three
steps, then "N more — call `list_artifacts`". The prototype listed every artifact
on every turn, so the prompt grew with the workspace's whole history.

Rulesets keep their `scope_kinds` mechanism, and sections can also declare
`requires_any_tool`, so the prompt never describes a tool the deployment didn't
register. The caveats section is `required` for `workspace` as for every other
kind.

### D7 — Event indices are declarative specs compiled to ROMP `OnsetRule`s, with the definition in the chain

ROMP already has the abstraction (`momp/stats/onset_rule.py`): a registry of
frozen-dataclass rules over the primitives `lead_sum`, `lead_all`, `has_dry_run`,
`min_sub_sum`, each rule exposing `params()`, `schema()`, `label()` and
`fingerprint()`. ai-almanac plumbs none of it — `event_type` is a label that never
reaches ROMP. This decision has three parts.

**Plumb what exists.** `BenchmarkRunSpec.event_type` becomes `event_index_id`;
`render_romp_config` and the Modal `ROMP_*` env path emit `onset_rule` and
`onset_rule_params`; the three registered rules are seeded as built-in
`event_indices` rows; ROMP's `save_metrics_to_netcdf` gains `onset_rule`,
`onset_rule_params`, `onset_rule_fingerprint`, `momp_version` in `allowed_attrs`.

**Composable specs.** An index is an expression over a whitelisted vocabulary that
maps onto ROMP's window helpers:

```
field(var, source)                      threshold(x, op, value | per_cell(artifact))
window_sum(x, days)  window_all(c, days)  window_count(c, days)
no_dry_run(x, days, run, below)          min_sub_sum(x, days, sub)
and(...)  or(...)  not(...)              first_true(mask)
```

Each node declares its lookahead; the compiler sums them and enforces
`lookahead < max_forecast_day` as `ROMPValidator._check_monsoon_logic` does.
ROMP gains a `ComposedRule` registered as `composed` whose params _are_ the spec,
so the generic conformance tests (causality, NaN fail-closed, broadcasting) cover
every user-defined index. The definition lives in ROMP, not in ai-almanac, because
ROMP is the single place "what is an event" is defined and tested.

```
event_indices
  id, workspace_id (nullable = built-in), user_id, name, description
  spec (JSON), fingerprint (ROMP's), compiled_label, lookahead_days
  romp_rule_version, created_from_turn_id, created_at, supersedes
```

**In the chain.** `detect_events` and `submit_benchmark` steps record
`params.event_index_id` and `params.event_index_fingerprint`; `compute_job_metrics`
refuses to cache metrics for a job whose NetCDF `onset_rule_fingerprint` does not
match the job row. Saving an index requires approval (P4) because it becomes a
named thing other jobs reference. `preview_event_index` runs detection on
observations only for one season and region — a few hundred cells by ~120 days —
and returns an onset-date map and no-onset fraction, so a definition is checked
before any Modal benchmark spends money on it.

### D8 — Approvals and guardrails extend to the new surface

Approval cards render the exact payload (`approvals.payload_hash`) for: benchmark
and blend submission, `rerun_job`, `run_forecast`, external fetch, and
`save_index`. Each approval is recorded through the one recorder (D3, D-R3).
`run_code` needs none (D5, D-R15). The apples-to-oranges risk of cross-job code is
addressed by the `mixed_definitions` guardrail and by read sets, not by a prompt on
every analysis step. Guardrails
gain `index_lookahead_margin` (warn when `lookahead ≥ 0.5 × max_forecast_day`),
`preview_no_onset_fraction` (error above a settings threshold), and
`mixed_definitions` (error when `score_events` receives event-date artifacts
produced under different index fingerprints). Thresholds stay in settings, not
rulesets, as today.

### D9 — The workspace owns the UI; pages become entry points

Route `/workspace/[id]` has three zones: **products · stage · assistant**. The
products list (left) and the assistant (right) each collapse to a strip, on their
own. The stage state (`overview → run → file`) is mirrored in the URL. One event
stream per page sends `jobs`, `artifacts` and `follow_ups` updates and reconnects
with `?since=`.

```
┌──────────────┬──────────────────────────────────────────┬─────────────────────┐
│ Products  ⇤  │ Benchmark · Amhara zones · 1998–2024     │ Assistant        ⇥  │
│ + New        │   comparison table, map, zone table      │ words, short tables,│
│ Benchmarks 2 │                                          │ cards that open on  │
│ Blends     1 │                                          │ the stage           │
│ Forecasts  0 │                                          │ [ Ask…            ] │
└──────────────┴──────────────────────────────────────────┴─────────────────────┘
   14rem / 3rem              the rest                         28rem / 3rem
```

**Why this arrangement.** Chat carries words; the stage carries data. Maps and wide
tables rendered inside a narrow conversation column were unreadable, and a
conversation-centred layout let results scroll away with it. Wide content therefore
never renders in chat: a table over three columns, or any figure, appears in the
reply as a compact card that opens on the stage.

- **Every workflow is reachable without the assistant.** Starting a benchmark, blend
  or forecast, adding an existing run, setting the season and area, reading skill by
  zone, exporting a map, cancelling or repeating a run each have a direct control. A
  workspace is fully usable with the assistant collapsed and on an install where no
  assistant is configured. The assistant and the direct controls call the same
  server operations and leave the same record of how a product was made.
- **Products list.** Entries are grouped by kind with a count, named by what
  distinguishes them, and carry their headline number. Collapsed, it is a strip of
  one icon per kind with its count, so the size of the workspace stays visible. A
  "New" control starts a run or adds an existing one.
- **Assistant.** Resizable, 28rem by default. Collapsed, it is a strip whose button
  shows when the assistant is working or has an unread reply. It stays mounted while
  collapsed so a running turn is not interrupted.
- **Collapse state is remembered per person.** The assistant starts open; the products
  list starts open at 1280 px and wider and collapsed below that.
- **An empty workspace** shows its starting points on the stage (run a benchmark,
  train a blend, add a run, and a prompt box with starter questions where an
  assistant is configured).
- **Below 1050 px** the zones stack and the assistant becomes a sheet that opens from
  the bottom of the screen.

Alternatives considered:

| Option | Why not |
| --- | --- |
| Products as a dropdown above the stage | Hides how much the workspace holds; every switch takes two clicks |
| Products and assistant as tabs in one side pane | The two are mutually exclusive; a new workspace has nowhere to type |
| Conversation in the centre with results inline | Maps and tables scroll away with the conversation |
| Assistant on the left, products beside the stage | List, then detail, then assistant reads more naturally, and the optional zone stays at the edge |

- **Chat:** the existing `ChatPanel` internals (streaming, approval cards,
  guardrail banners, figure lightbox). Each burst of tool calls collapses into one
  step. Each assistant reply ends with a **"based on" footer** of artifact chips
  taken from the turn's read set (D-R20). Turns started by a follow-up render as a
  system note, not a user bubble (D11).
- **Stage, grouped by lineage** (D-R18): a derived file appears under every run
  its ancestors came from, not in a catch-all bucket. An **Analyses** view groups
  derived files by the turn and question that produced them.
- **File view:** preview, details and download, with **integrity status inline**
  (✓ or ⚠ drift) and Verify kept as a re-check (D-R21).
- **Lineage view:** the DAG rendered from `provenance_steps`. Each node shows the
  primitive, version, params and runtime, and **who decided**: "proposed by
  <model> under <ruleset vN>, approved by you at <time>", or "ran by you" for REST
  and UI actions (D-R19). It links into the transcript and lists every producer of
  the file's bytes (D-R6). Export as JSON or as a recipe (D10, not built).
- `/workspaces` lists projects; the global nav gains "Workspace".

Benchmarks, Blends and Forecasts pages keep their panels and gain "Open in
workspace", which promotes the session (D6). Existing behaviour is unchanged for
users who never open a workspace.

### D10 — Recipes: a chain rendered as a re-runnable spec (designed-for, post-MVP)

A provenance DAG containing only typed primitives is already a pipeline
specification. `export_recipe(artifact_id)` emits it as JSON with parameters that
were dates or regions marked as inputs; `run_recipe(recipe_id, inputs)` re-executes
it through the same primitives, producing a new chain. Recipes are how a composed
index plus a scoring configuration becomes a shareable, schedulable object. A
recipe containing a `run_code` step is exportable but marked unverified.

### D11 — Follow-ups: the assistant continues when its jobs finish

_Built in the prototype; revised by D-R16 and D-R17._ After submitting runs whose
results it needs, the assistant calls `continue_after_jobs(job_ids,
instruction)` instead of asking the user to come back:

```
follow_ups
  id, workspace_id, session_id, user_id, job_ids, instruction
  status                  -- waiting → running → done | failed | canceled
  created_at, started_at, completed_at
```

The existing reconciler tick starts a follow-up once every job is terminal and
every completed job has published, so the continuation can read the outputs. It
never starts while the chat is streaming or waiting on an approval. The claim is a
conditional `UPDATE … WHERE status = 'waiting'`, so two instances can't both start
the same row. A follow-up claimed more than five minutes ago whose chat has gone
idle lost its turn, for example to a restart, and is marked `failed`. The turn runs
under the session's pinned ruleset. Only jobs linked to the workspace, and owned by
the user, can be waited on. While waiting, a follow-up can be canceled.

- **One writer per session** (D-R16, P8). `stream_chat_turn` refuses with
  `session_busy` while a turn is streaming, checked inside the transaction that
  already locks the session row, and `start_ready` claims at most one follow-up
  per session per tick. The prototype had neither: two follow-ups ready in the
  same tick both started, and the last one to write `provider_state` silently
  dropped the other's model history.
- **Triggers are recorded** (D-R17). Turns carry `trigger: user |
follow_up:<id>`, and the turn log records it. The prototype persisted the
  continuation as a `role="user"` message, attributing text the platform wrote to
  the person.

### D12 — Data transforms and fetchers live in `ai-almanac-tools`, run in the analysis environment

A primitive has two halves. The **platform half** — scope and approval checks,
label resolution, the ledger step, output registration, read sets — stays in the
server as the D4 chokepoint. The **data half** — datasets and typed params in,
datasets out — moves to a separate package in this repo,
`packages/ai-almanac-tools/` (import `ai_almanac_tools`), which never imports
`ai_almanac` (D-R23):

```
src/ai_almanac_tools/
  contract.py    # D2 dataset contract: parse_dataset(ds, kind) -> Dataset
  sources/       # catalog entries + fetchers (D13)
  ops/           # regrid, mask, select_variables, clip, aggregate_temporal, ...
  registry.py    # @op, @source, manifest()
  harness.py     # python -m ai_almanac_tools.harness {op|fetch} request.json out_dir
```

`contract.py` is where D2's contract becomes code: every fetcher's output and
every op's inputs go through `parse_dataset`, which normalises dimension names and
units and checks the named type. A malformed dataset fails there, not three ops
later.

```python
class RegridParams(BaseModel):
    target: str = Field(description="Label of the dataset whose grid to match")
    method: Literal["bilinear", "nearest", "conservative"] = "bilinear"

@op("regrid", version="1.0.0", inputs=("source", "target"))
def regrid(inputs: dict[str, xr.Dataset], params: RegridParams) -> dict[str, xr.Dataset]:
    """Interpolate `source` onto the lat/lon grid of `target`."""
```

- **Ops run where `run_code` runs.** The package is a dependency of
  `analysis.pixi.toml` (D-R13), so the srt environment and the Modal Sandbox image
  both carry it. An op call is the D5 harness with a fixed, server-built script
  (`harness op`) instead of model code: network blocked, inputs staged read-only,
  outputs from `/outputs`. `run_code` can import the same package.
- **The server learns ops from the deployed environment.** `manifest()` returns
  `{name, version, doc, inputs, params_schema}` per op; the server calls it in the
  analysis environment at startup and builds one PydanticAI tool per entry with
  `Tool.from_schema`, dispatching through a generic form of the `@primitive`
  wrapper. Each session snapshots the manifest when it starts, and the turn log
  records its version, so the tool list cannot change under a live session.
- **Provenance.** The op's semver is `primitive_version` and `runtime.env_digest`
  pins the package set. Unlike `run_code`, the code that ran is a released,
  versioned artifact, so op outputs are first-class (P2).
- **Shipping** (D-R26). For now `ai_almanac_tools` ships inside the `ai-almanac`
  wheel, so tool changes ride server deploys. The server imports it, and each
  sandbox run gets a fresh copy of its source on `sys.path` (srt runs Python with
  `-I`, which ignores PYTHONPATH); `runtime.ai_almanac_tools` records a hash of
  that source. The package keeps its own `pyproject.toml` and never imports the
  server, so publishing it separately, the step that lets a tool ship without a
  server redeploy, is a packaging change.
- **What stays in the server:** anything that needs the database or the job
  system — submit/validate/rerun, the inspect and provenance tools,
  `resolve_region`. Plot primitives can move, since they only render data.
- **Tool count.** One tool per op while the set is small. If it grows past what
  fits comfortably in a turn, switch to one `apply_op(name, params)` tool plus the
  schemas `list_capabilities` already returns.

### D13 — Data enters a workspace only through fetch steps; sandboxes stay offline

`run_code` and ops never get network access. Raw data arrives as a **fetch**: a
typed step that runs a released fetcher from `ai-almanac-tools` with network on
and writes a `dataset` artifact, which then reaches sandboxes through normal
staging. Nothing with network access runs model-written code: the fetch harness
takes a JSON request, parses it, and calls a registered fetcher, and the server
builds that script itself, so no fetch request can carry code.

| Runs       | Code from                  | Network            | Credentials | Output                        |
| ---------- | -------------------------- | ------------------ | ----------- | ----------------------------- |
| `run_code` | the model                  | none               | none        | derived, unverified artifacts |
| op         | `ai-almanac-tools` release | none               | none        | `dataset` artifacts           |
| fetch      | `ai-almanac-tools` release | the source's hosts | none        | `dataset` artifact (a leaf)   |

**Sources are code plus catalog data.** A source declares its hosts, its
variables (store name, units, canonical name and target units), grid and time
step, and a fetcher that opens the store lazily and subsets **before** any bytes
move:

```python
@source("era5_arco", version="1.0.0", kind="observations",
        hosts=("storage.googleapis.com",),
        variables={"precip": Var("total_precipitation", units="m", to="mm day-1")},
        grid=Grid(res_deg=0.25), time_step="1h")
def fetch_era5_arco(req: ObsRequest) -> xr.Dataset:
    return subset(xr.open_zarr(ARCO_URL, chunks={}), req)
```

Requests are parsed at the boundary: `ObsRequest{source, variables, bbox, start,
end}`, and `ForecastRequest` adds `init_times`, `leads`, `members`. `variables`
are catalog names, never raw store names; `bbox` comes from `resolve_region`.

**Plan, approve, fetch, register.**

1. `plan_fetch(request)` is read-only and offline. It returns the normalised
   request and a size estimate computed from catalog metadata (variables × grid
   × times × members × dtype). A request over the `max_fetch_bytes` setting is a
   validation error, so it is never offered for approval.
2. `fetch(request)` needs approval (`external_fetch`, P4); the card shows the
   estimate.
3. The fetch runs in the analysis environment with network on. Locally, srt's
   `allowedDomains` is set to the source's `hosts`, a real egress restriction. On
   Modal, `outbound_cidr_allowlist` cannot express object-store hosts, so the
   Sandbox runs with network on and the boundary is **released code only**
   (D-R22). It has no GCP credentials either way.
4. The step records `primitive="acquire.fetch"` with the parsed request as
   `params`, and in `runtime` (which the step hash covers) the source, its catalog
   version, `source_version` (the store's version marker, ETag or, for ERA5-ARCO,
   the last day of its record) and the tools and analysis-environment digests. The
   step's `created_at` is when it was fetched.

`probe_latest(source)` is the one read that needs network: a fetch-mode harness
call that returns metadata and writes no artifact.

**Sources change.** Forecast catalogs append and sometimes revise, so equal fetch
params do not imply equal bytes. `step_hash` includes `source_version` when the
source has one. Without one, lineage shows the fetch "as of `fetched_at`" and the
content hash of the bytes is the anchor, which is all D3 verification claims
anyway.

**Bytes do not pass through Cloud Run.** Cloud Run's filesystem is memory, so a
multi-GB fetch cannot land in server scratch the way `run_code` figures do. On
Modal, the server creates the artifact's storage key and a signed resumable upload
URL for that one object; the fetch Sandbox uploads directly, and the server
stream-hashes and registers it. Locally the fetcher writes to the output
directory. Large _inputs_ have the same problem in reverse: staging a big dataset
into a Modal Sandbox should read it in place, not copy it through the server (open
question 8).

**One file per dataset artifact.** Fetches and ops write NetCDF4, because staging
copies a single file and hashing is sha256 of stored bytes; a Zarr directory
breaks both. Zipped Zarr (`ZipStore`) is the upgrade if chunked partial reads
matter; it is still one file.

**Public catalog only.** Fetch sandboxes hold no credentials, so user-registered
private `gs://` data sources stay out of workspace fetches; they keep reaching
workspaces through benchmark jobs. There is no shared fetch cache (D-R24): a
repeat fetch fetches again, and the per-turn artifact listing steers the model to
reuse what the workspace already has.

**First sources.** ERA5-ARCO moves from `modal/app.py`
(`_fetch_era5_daily_precip_from_arco`) into `sources/era5_arco.py`, so benchmark
jobs and workspaces share one loader; then one credential-free forecast catalog
(dynamical.org's public Zarr).

## Architecture

```mermaid
flowchart LR
  subgraph UI[web /workspace]
    Chat[ChatPanel] --- Tree[Artifact tree + lineage]
  end
  subgraph Server[FastAPI]
    Agent[PydanticAI agent] --> Reg[Primitive registry]
    Reg --> Prov[Provenance writer]
    Reg --> Appr[Approvals + guardrails]
    Prov --> DB[(SQLite/Postgres: ledger: artifacts, provenance_steps, approvals; views: workspaces, workspace_items; follow_ups)]
    Reg --> Store[Storage port: open / stage]
  end
  subgraph Modal
    W[almanac-workspace: run_code Sandbox; later inspect, transform, figure]
    R[almanac-romp: detect, score, benchmark]
    B[almanac-blending]
    F[almanac-forecasts]
  end
  Chat <-->|SSE| Agent
  Reg --> W & R & B & F
  W & R & B & F --> GCS[(user artifact prefix + job outputs)]
  Store --> GCS
  Tree --> DB
  Tree -.download: chain stamped into a copy.-> Store
```

Turn flow: the model calls a primitive → the decorator checks scope and resolves
labels to artifact ids → approval if required, recorded → Modal call or job
submission (a job's step is written now) → outputs stored as produced → sha256
computed → `provenance_steps` row written or completed with turn, ruleset and
approval ids → artifacts added to the workspace as items → `artifacts` SSE event to
the UI → tool result (summary plus `source_artifacts`, never bulk data) returned to
the model → the turn log records the read set.

## Provenance chain example

A `metrics` artifact produced by scoring a composed index on forecast and
observation event dates, rendered as JSON (abridged). The ledger stores artifact
ids; an export shows each artifact's label in the workspace being viewed:

```json
[
  {
    "primitive": "fetch.era5_arco",
    "version": "1.0.0",
    "params": {
      "start": "2026-06-01",
      "end": "2026-09-15",
      "bbox": [16.7, -17.5, 12.3, -11.3]
    },
    "inputs": [],
    "runtime": {
      "image_digest": "sha256:…",
      "packages": { "ai_almanac": "0.9.2" }
    },
    "approval": "apr_3f…",
    "turn": {
      "session": "s_…",
      "turn": 4,
      "ruleset": "builtin@7",
      "model": "claude-sonnet-4-6"
    },
    "outputs": [{ "label": "era5_senegal_2026", "hash": "sha256:9a…" }],
    "step_hash": "sha256:…"
  },

  {
    "primitive": "event.detect_events",
    "version": "1.2.0",
    "params": {
      "event_index_id": "idx_two_stage_v1",
      "event_index_fingerprint": "c1a9…",
      "dim": "time"
    },
    "inputs": [
      {
        "role": "observations",
        "artifact": "era5_senegal_2026",
        "hash": "sha256:9a…"
      }
    ],
    "runtime": { "image_digest": "sha256:…", "packages": { "momp": "0.2.0" } },
    "outputs": [{ "label": "obs_onsets_2026", "hash": "sha256:41…" }],
    "step_hash": "sha256:…"
  },

  {
    "primitive": "score.score_events",
    "version": "1.0.0",
    "params": {
      "windows": [
        [1, 7],
        [8, 14],
        [15, 21],
        [22, 28]
      ],
      "tolerance_days": 3
    },
    "inputs": [
      {
        "role": "forecast_events",
        "artifact": "aifs_onsets_2026",
        "hash": "sha256:7c…"
      },
      {
        "role": "observed_events",
        "artifact": "obs_onsets_2026",
        "hash": "sha256:41…"
      }
    ],
    "runtime": { "image_digest": "sha256:…", "packages": { "momp": "0.2.0" } },
    "guardrails": ["small_sample_years"],
    "outputs": [{ "label": "weekly_far_mr_2026", "hash": "sha256:e0…" }],
    "step_hash": "sha256:…"
  }
]
```

Rendered as lineage:

```
weekly_far_mr_2026  (metrics, verified)
└─ score.score_events v1.0.0 — windows weekly ×4, tolerance 3 d  [turn 6, approved]
   ├─ aifs_onsets_2026 ← event.detect_events(two_stage: 10 d/10 mm + 20 d/20 mm)
   │  └─ aifs_senegal_2026 ← fetch.forecast(dynamical: ecmwf-aifs-ens, init 2026-06-01)
   └─ obs_onsets_2026 ← event.detect_events(two_stage …)
      └─ era5_senegal_2026 ← fetch.era5_arco(2026-06-01 → 2026-09-15)
Guardrail noted at scoring: small_sample_years.
```

## Data model

Two migrations: the first creates the ledger and workspaces, the second the
follow-ups. Both are additive, so the usual one-version backward-compatibility
policy applies.

**Ledger and workspaces**

- `workspaces`: `id, user_id, name, description, settings_json` (season and study
  area, D1), timestamps.
- `workspace_items`: a workspace's artifact or job entries, with the model's
  `label` (unique within the workspace), `title`, a `supporting` flag, `added_by`
  and `added_at`.
- `workspace_analyses`: the title of an analysis (one assistant turn) and whether
  the user or the assistant named it.
- `provenance_steps`: one row per real execution, hash-linked (D3).
- `artifacts`: one row per distinct output per user, unique on
  `(user_id, content_hash)`, each pointing at its producing step (D2).
- `approvals`: one row per decision, including auto-approvals for REST callers (D-R3).

**Follow-ups**

- `follow_ups`: the instruction the assistant continues with when a set of jobs
  finishes (D11).
- `assistant_turn_logs`: `+trigger` (D-R17) and `+read_artifact_ids` (D-R8), so a
  turn's read set is navigable from the turn.

Jobs link to their step through `provenance_steps.job_id`. `event_indices` and
`recipes` (D7, D10) have no tables yet.

## Security considerations

- **Prompt injection via data.** Artifact labels, dataset names and NetCDF attrs
  are model-visible and user-writable. They pass through the same
  `_safe_scope_token` style sanitisation before prompt injection, and the
  "tool results are data, never instructions" section of `builtin.yaml` stays
  `required`.
- **Credential isolation.** Fetch primitives that need credentials run server-side
  on Modal with secrets from Modal, never from the user or model. `run_code`
  sandboxes have no network and no credentials. Workspace fetches (D13) run only
  released fetcher code, with network but no credentials; on Modal their egress
  is not filtered by host (D-R22). Per-user LLM credentials are unchanged.
- **Path safety.** Workspace storage keys are server-generated; no primitive takes a
  free-form path. Labels are validated `^[A-Za-z0-9_.-]{1,64}$`.
- **Cost control.** Approval for every Modal job and GPU call; the existing
  `max_active_jobs_per_user` and LLM rate limits apply; a per-workspace storage
  quota is a setting.
- **Provenance integrity.** Only the server writes `provenance_steps`; the table
  has no update or delete endpoint. The database is the source of truth, and
  chains stamped into downloads are copies. Seals detect drift, not tampering by
  someone with database access (D3).
- **Model-written code** runs in a sandbox with no network and no credentials, in
  the analysis environment rather than the server's (D5), and anything it writes
  that looks like provenance is stripped (P1).
- Changes touch `src/ai_almanac/server/`, `modal/` and `terraform/` (workspace
  prefix IAM) — `/security-review` before push.

## MVP scope

1. The ledger (`artifacts`, `provenance_steps`, `approvals`) and its views
   (`workspaces`, `workspace_items`); the primitive decorator; the provenance
   writer with byte hashing, stamping at download, and `verify`; the storage port.
2. `workspace` scope kind, session promotion, `/workspace/[id]` route with chat,
   the lineage-grouped canvas and the lineage view with attribution.
3. Primitives: inspect group, `resolve_region`, `resolve_time`, `fetch_obs(era5_arco)`,
   `probe_latest`, `plot_map`, `plot_timeseries`, `run_code` over a
   network-blocked Sandbox in the analysis environment, typed
   `submit_benchmark`/`submit_blend`/`rerun_job`, and read sets on every
   data-reading tool.
4. ROMP plumbing: `onset_rule` through both config paths, version/fingerprint attrs
   on outputs, image digest pin, pixi pin bump; `detect_events` and `score_events`
   with parameterised windows.
5. Built-in event indices seeded from ROMP's three rules; `list/describe` only
   (composition in phase 2).

## Implementation phases

Each phase is independently shippable to staging and leaves prod unchanged for
users who do not open a workspace.

**Phase 0 — Provenance foundation (no UI).** _Built._ Tables, decorator, writer,
hashing, `verify`, stripping of model-written provenance.

**Phase 1 — Workspace + session.** _Built._ `workspace` scope, promotion,
`/workspace/[id]` with chat and canvas, lineage view, follow-ups, `run_code` under
srt locally and in a Modal Sandbox in the cloud.

**Review rework (D-R1 to D-R21).** _Built._ Steps, in dependency order:

- **A. One writer per session** (D-R16). A small, standalone bug fix.
- **B. Ledger core** (D-R2 to D-R6, D-R10 to D-R12, D-R3). The per-user ledger with `workspace_items`; job steps at
  submission; typed locations behind the storage port; byte hashing and stamping
  at download; role-keyed inputs; one approval recorder.
- **C. Tools** (D-R7 to D-R9, D-R17). This needs B, because jobs need their step
  at submission. Typed submit, validate and rerun primitives; `source_artifacts`
  and turn read sets; the capped digest; turn triggers.
- **D. Analysis environment** (D-R13, D-R14). This is independent of B and C.
  `analysis.pixi.toml`; the Modal image built from its list; `env_digest`; the
  `code_sandbox` setting.
- **E. UI** (D-R18 to D-R21). This needs C. Lineage grouping and the Analyses view;
  attribution in lineage; "based on" footers; inline integrity status.

**Phase 2 — ROMP integration.** _Onset-rule plumbing and output attrs built._ **Not
built:** the `detect_events` and `score_events` primitives, built-in event-index
rows, `preview_event_index`, `compose_event_index` with `ComposedRule` in ROMP, and
the guardrails for composed indices.

**Phase 3 — Acquisition and transforms** (D12, D13). In order: the
`ai-almanac-tools` skeleton with `contract.py` _(built)_; ERA5-ARCO moved into
`sources/` _(built)_; `list_sources`/`plan_fetch`/`fetch` on the local srt runner
_(built; hidden where `code_sandbox` is not `srt`)_; one op
(`select_variables` or `clip`) to prove manifest → tool; the Modal fetch runner
with signed-URL upload; one forecast catalog; the remaining transforms and
figure primitives.

**Phase 4 — Forecasts and recipes.** Forecast toolset behind `require_forecasting`;
`export_recipe`/`run_recipe`; scheduled recipes.

## Open questions (need input)

1. **Where does `preview_event_index` run** for local (non-Modal) deployments?
   Options: in-process with a hard cell/day cap, or unavailable like `run_code`.
2. **Retention.** Workspace artifacts are user data with real storage cost. Proposal:
   soft-delete with 30-day purge, provenance rows kept forever (they are small and
   they are the audit record). Needs a decision on whether purged inputs make
   downstream artifacts `input_missing` (proposed) or are pinned by reference.
   With the ledger (D3), provenance rows are per user and outlive any workspace,
   so deleting a workspace only removes items.
3. ~~**Approval granularity for `run_code`.**~~ _Resolved: never_ (D-R15).
4. **Should a composed index be shareable across users** at creation, or only via a
   `visibility` flag added later, like data sources? Proposed: later.
5. ~~**Turn ↔ step linkage for REST callers.**~~ _Resolved:_ REST submissions write
   an auto-approved `approvals` row with the caller's id (D-R3).
6. **Job image digests.** When does pinning ROMP, blending and forecast images by
   digest move from deferred (reproducibility) into scope?
7. **Modal Sandbox smoke test.** The cloud `run_code` path is only tested with a
   fake. It needs one real run on the staging Modal environment, which also
   checks that the pinned packages resolve and how long the first image build
   takes.
8. **Staging large inputs into Modal Sandboxes** (D13). A read-only
   `CloudBucketMount` with `key_prefix` set to the workspace prefix would let a
   Sandbox read artifacts in place. Needs checking that Modal does not expose the
   mount's HMAC secret inside the container; if it does, use signed download URLs
   fetched by a trusted pre-step instead.

## Review decisions (2026-09-25)

| ID    | Decision                                                                                       | Section   |
| ----- | ---------------------------------------------------------------------------------------------- | --------- |
| D-R1  | MVP delivers attribution and integrity; reproducibility is deferred; seals are drift detection | Goals, D3 |
| D-R2  | Per-user provenance ledger; the workspace is a view; a job's step is written at submission     | D1, D3    |
| D-R3  | One approval recorder for primitives, submissions, rerun and REST                              | D3, D8    |
| D-R4  | Drop `superseded_by` and `workspaces.archived`                                                 | P5, D1    |
| D-R5  | Typed artifact location instead of the `job:` prefix                                           | D2        |
| D-R6  | Lineage shows every producer of an artifact's bytes                                            | D2, D9    |
| D-R7  | Workspaces use stateless typed `submit_benchmark`, `submit_blend` and `rerun_job`              | D4        |
| D-R8  | `source_artifacts` on data-reading tools; turn read sets                                       | D4        |
| D-R9  | Capped per-turn artifact digest                                                                | D6        |
| D-R10 | Store bytes as produced; plain sha256; stamp the chain at download                             | D3        |
| D-R11 | Role-keyed step inputs; no labels in the ledger                                                | D3        |
| D-R12 | Storage port (`open`, `stage`); GCS is first-class                                             | P7, D2    |
| D-R13 | One pinned analysis environment for both sandboxes; `env_digest`                               | D5        |
| D-R14 | Explicit `code_sandbox: off \| srt \| modal` setting                                           | D5        |
| D-R15 | `run_code` needs no approval                                                                   | D5, D8    |
| D-R16 | One turn at a time per session, enforced by the server                                         | P8, D11   |
| D-R17 | Turns record their trigger; follow-ups render as system notes                                  | D11       |
| D-R18 | Canvas grouped by lineage, with an Analyses view                                               | D9        |
| D-R19 | Lineage shows who decided each step                                                            | D9        |
| D-R20 | "Based on" footer on assistant replies                                                         | D9        |
| D-R21 | Inline integrity status                                                                        | D9        |

## Tools and acquisition decisions (2026-09-30)

| ID    | Decision                                                                                     | Section |
| ----- | -------------------------------------------------------------------------------------------- | ------- |
| D-R22 | On Modal, fetch egress is bounded by running only released code, not by a host filter        | D13     |
| D-R23 | Transforms and fetchers live in `ai-almanac-tools`, in this repo, never importing the server | D12     |
| D-R24 | No shared fetch cache                                                                        | D13     |
| D-R25 | Borrow weather-skills ideas; no compatibility goal                                           | D2, D12 |
| D-R26 | `ai_almanac_tools` ships in the platform wheel until publishing it separately pays off       | D12     |

## Future work (post-MVP, designed-for but not built)

- Porting useful weather-skills fetchers and transforms into `ai-almanac-tools` as
  sources and ops (D-R25: their ideas, our contract).
- Fetches from private registered data sources, via server-signed read URLs.
- Cessation and heat-wave indices once ROMP's input layer accepts temperature.
- Shared workspaces and per-artifact visibility.
- A provenance export in a standard vocabulary (W3C PROV-JSON) for external audit.
- Signed chains (server key over `step_hash`) so a downloaded artifact can be
  verified without database access.

## Appendix — mapping to weather-skills concepts

| weather-skills                                                     | ai-almanac (this design)                                                                  |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| `SKILL.md` + `scripts/*.py` skill                                  | `@primitive` function with schema and version                                             |
| `weather_skills_history` attr / PNG `tEXt`                         | `ai_almanac_history` attr / `tEXt` stamped at download; `provenance_steps` is the record  |
| `{skill, version, args, input:{basename, hash}}`                   | `{primitive, version, params, inputs:[{role, hash}], runtime, turn, approval, step_hash}` |
| `_SKILL_VERSION` bumped by CI                                      | primitive semver + schema-snapshot test; package versions in `runtime`                    |
| `inspect-zarr`, `resolve-region`, `resolve-time`, `--probe-latest` | inspect and resolve groups                                                                |
| `provenance --format human\|json\|script`                          | lineage view, JSON export, `export_recipe`                                                |
| `forecaster.md` "list the workspace first"                         | per-turn artifact listing injected into the `workspace` prompt section                    |
| Landlock chat sandbox                                              | `modal.Sandbox`, `block_network=True`, staged inputs                                      |
| `sanitize_output_artifact_bytes`                                   | provenance stripping on `run_code` outputs (P1)                                           |
| Skill packs from git                                               | not adopted                                                                               |
