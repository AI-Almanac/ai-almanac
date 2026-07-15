# Forecast Generation Redesign

**Status:** Draft / Proposed
**Date:** 2026-07-15
**Author:** hholb

## Context

Today a "forecast" is a single GPU job tied to a completed blend. Each run produces
two independent deliverables from the same AI weather models:

1. **Raw map-visualization** — one short-lead rollout per model from the latest GFS
   cycle (default 120 h), all variables at full grid, rendered to COGs. Does not use
   the blend.
2. **Season onset scoring** — a 45-day rollout looped over ~26 weekday-cadence init
   dates per model, reduced to daily `tp`, scored against the blend's trained
   coefficients into onset probabilities.

Problems observed:

- A cold 2-model blend over India took ~4 h of A100 time. That cost is triggered
  ad hoc by a single user action, with no gating.
- The raw map deliverable is a second, largely redundant rollout — its window sits
  entirely inside the season rollout.
- Every run rebuilds the whole season from scratch even though most init dates
  never change.

The key enabling fact: the season trajectory cache
(`forecast_pipeline.cached_trajectory`) is **model-scoped, native-grid, and
deterministic** — a rollout from an archived past init date never changes, and one
entry serves any blend or region. The expensive GPU work is therefore a *shared,
cacheable, blend-independent asset*, not a per-user cost. This document reorganizes
the system around that fact.

## Goals

- Move GPU cost out of the per-user forecast path; make it a shared, reusable asset.
- Make "update an existing forecast" cheap and first-class.
- Remove the redundant raw map rollout.
- Support arbitrary user blends over a growing (earth2studio) model set.
- Let scientists choose the forecast **initialization data source** (models are
  sensitive to it).

## Non-goals (deferred)

- **Reforecast archive schema / raw multi-variable retention.** A larger decision
  tied to the decades-scale reforecast archive. For MVP we keep only what monsoon
  onset needs (daily `tp` reduction, as today). What else to retain is decided when
  the archive schema is nailed down. See Open Questions.
- **Custom (non-earth2studio) models** — NeuralGCM, NVIDIA Atlas, etc. Single-digit
  count expected; bespoke integrations written when needed. MVP assumes earth2studio.
- **Retention / eviction policy.** TB-scale storage is affordable short-term;
  long-term storage is being secured separately.

## Decisions

### D1 — Drop the raw map-visualization deliverable

Remove the separate short-lead raw rollout and its COG rendering from the forecast
job. It is redundant with the season rollout and is not part of the onset product.
If raw map products are wanted later, they become a *derived view* off retained
rollout fields rather than a second rollout.

### D2 — Split "generate trajectories" (GPU) from "run a blend forecast" (CPU)

Two distinct job types:

- **Trajectory generation** — the GPU-heavy season rollout for a `(model,
  init_source, season)`. Writes to the shared trajectory store. Deterministic;
  produced once, reused forever.
- **Blend forecast** — reads cached trajectories, runs CPU statistical scoring
  against the blend's frozen coefficients, renders onset products. Nearly GPU-free
  when trajectories are warm.

This is the load-bearing change. Blend forecasts become cheap and ungated;
GPU spend lives in one clearly-owned place.

### D3 — Two-tier generation: admin full-season, user incremental update (MVP)

- **Full-season generation is admin-only**, via an explicit admin UI flow. This is
  the expensive cold rollout (~26 init dates/model) that bears the ~4 h cost.
- A blend forecast can only run once the required `(model, init_source, season)` set
  **exists** (an admin has generated it). A blend referencing a set with no
  generation is accepted but **blocked with a clear "model data not yet available"
  state**; it launches no GPU work.
- **Users may trigger an incremental "update"** on a set that already exists: a
  bounded gap-fill rollout of only the init dates elapsed since the set was last
  generated (typically 1–2 weeks of weekday-cadence dates — a handful of rollouts,
  minutes not hours). See D5.

All GPU work still flows through trajectory-generation jobs (D2 invariant intact);
the only difference is *who* may trigger which scope — admins the full season, users
the incremental gap. Because the store is shared and model-scoped, concurrent user
updates self-limit: the first fills the gap and caches it, the rest find it warm.

This keeps the queue / dedup / per-user-budget machinery out of MVP. Automatic
on-demand generation for *cold* sets (no admin generation yet) remains a post-MVP
upgrade (see Future Work); the job model below is designed so it slots in without
reshaping.

### D4 — Trajectory store keyed by `(model, init_source, season/init_date)`, hardened

The store stays daily-`tp`, native grid (MVP scope), but the key and correctness are
hardened because it is now load-bearing:

- **Init data source is part of the key and identity.** There is no "fuxi
  2026-05-01" — only "fuxi 2026-05-01 **from GFS**." Serving a trajectory from the
  wrong init source silently corrupts results.
- **Format/reduction version in the key** (e.g. `{model}/{source}/v{N}/lead45d/{date}.nc`).
  Any change to the reduction logic, units, or schema bumps `N` so stale entries are
  never served.
- **Single canonical lead horizon** (roll to 45 d; slice shorter windows in memory)
  so `lead30d` and `lead45d` stop being separate keys for the same rollout.

### D5 — "Update forecast" = incremental top-up, user-triggerable

Re-running a blend uses the identical `blend_config_snapshot` (same coefficients) and
recomputes `season_issue_dates` up to today. Cached dates are served for free; the
init dates elapsed since the set was last generated are rolled out as a **bounded
gap-fill generation** (D3) and the season is then re-scored. This is cheap relative
to a full season (a 1–2 week gap is a handful of rollouts), so **users may trigger it
directly** once the set exists. Once the season's final init date is cached, update
is a no-op, not a job.

### D6 — Expose the forecast initialization data source

Thread an `init_source` parameter through the forecast/generation config
(earth2studio already provides GFS, ARCO/ERA5, IFS, GEFS, …). This is distinct from
the **ground-truth** data source used for onset truth and climatology in the
blending/benchmarking packages — two independent provenance axes; do not merge them.
Each generated trajectory and each blend result records its provenance (model +
weights version, earth2studio version, init source, reduction version, timestamp).

## Architecture

```
Admin (UI)
  └─ generate_trajectories(model, init_source, season)   → GPU (A100) → trajectory store
       full-season cold rollout; deterministic; one set per (model, source, season);
       serves all regions & blends

User
  ├─ run_blend_forecast(blend)     → reads existing trajectories → CPU scoring → onset products
  │    set has no generation yet → blocked ("model data not yet available")
  └─ update_blend_forecast(blend)  → bounded gap-fill rollout of elapsed init dates → re-score
       cheap (1–2 weeks of init dates); allowed once the set exists
```

### Trajectory store

- MVP contents: reduced daily `tp`, native grid (unchanged from today).
- Key: `{model}/{init_source}/v{N}/lead45d/{issue_date}.nc`.
- Region-independent: clipping / unit conversion applied downstream on read, so one
  entry serves every region. Adding a region with warm models costs **zero GPU**.
- Backed by local path or `gs://` (as today).
- No eviction (deferred).

### Generation tracking

A small table tracks trajectory-set state so forecasts can check readiness and admins
can see coverage:

- Row per `(model, init_source, season)`: status
  (`pending`/`running`/`complete`/`failed`), init-date coverage, timestamps, the
  triggering admin, provenance.
- Blend forecast readiness check = "are all my required sets `complete` and covering
  the needed init dates?"
- **Repurpose the existing (currently dead) `forecast_runs` table** for these rows —
  it already carries `model_id`, `init_time`, `storage_prefix`, etc. Add the
  `init_source`, `season`, status, coverage, and provenance fields it lacks.

### Job model

- **Trajectory generation job** — `job_type="trajectory_generation"`, GPU=1.
  Idempotent: re-running tops up only the missing init dates. Two triggers —
  **admin full-season** (initial cold rollout, via admin UI) and **user incremental**
  (gap-fill for an existing set, via "Update forecast"). Params: model, init_source,
  season, init cadence, lead horizon. Populates the store + `forecast_runs` row.
- **Blend forecast job** — `job_type="forecast"`, GPU=0 (CPU scoring only) when warm.
  Fails fast / blocks if required trajectories are cold. Params reduce to blend
  (carries region + models); the current smoke-test knobs (`init_time`,
  `max_lead_day`, `max_issue_dates`) stay behind an admin/dev flag, not promoted to
  product options.

## MVP scope

**In:**
- D1 (drop raw maps), D2 (job split), D3 (two-tier generation), D4 (hardened keyed
  store), D5 (user-triggerable incremental update), D6 (init source + provenance).
- Repurposed `forecast_runs` tracking table + readiness gate.
- **Admin UI flow** to trigger full-season generation and view coverage.
- Blend forecast blocks clearly on sets with no generation yet.

**Out (post-MVP):**
- Automatic on-demand generation when a user composes a cold blend.
- Generation queue, dedup across concurrent requesters, per-user GPU budgeting.
- Raw multi-variable retention / reforecast-archive integration.
- Custom (non-earth2studio) model adapters.
- Retention / eviction.

## Open questions (need input)

1. **Reforecast archive schema** — does a defined schema exist to conform to, or is it
   being designed in parallel? Determines whether the trajectory store's on-disk
   layout should already match the archive to avoid a later migration. *(Deferred for
   MVP; MVP keeps only monsoon-onset daily `tp`.)*
2. **Raw retention granularity** — which variables / vertical levels / step frequency
   to retain when the archive lands (heatwave onset, etc.). A science call. Out of
   MVP.
3. **Post-MVP cold-generation gating** — once generation becomes user-triggerable, who
   may trigger it and under what budget/rate limit.

## Future work (post-MVP, designed-for but not built)

- **Automatic generation as an "archive contribution."** Because a trajectory set is
  a durable shared asset, generating it is valuable platform-wide regardless of the
  requesting blend. Upgrade path from D3: blend creation *records* needed cold sets;
  an explicit action (or scheduler) enqueues generation, deduplicated per
  `(model, init_source, season)`; later requesters attach to the in-flight job rather
  than launching their own.
- **Raw-field retention + reforecast-archive integration** (Open Questions 1–2).
- **Custom model adapters** via a `TrajectoryProvider` boundary so the season loop,
  store, and scoring stay model-agnostic.
- **Retention / eviction policy** when storage growth warrants it.

## Rough implementation phases

1. **Split the job types.** Introduce `trajectory_generation` (GPU) and reduce
   `forecast` to CPU scoring; repurpose `forecast_runs` as the tracking table + add
   the readiness gate. Blend forecast blocks on sets with no generation yet.
2. **Harden the store** (D4): source + version in key, single 45-day horizon.
3. **Drop raw maps** (D1) and their rendering path.
4. **Expose init source** (D6) end-to-end with provenance records.
5. **Admin generation UI** — trigger full-season generation, view coverage/status.
6. **"Update forecast"** (D5): user-triggered bounded gap-fill generation + re-score.
```
