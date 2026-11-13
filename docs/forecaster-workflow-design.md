# Forecaster Workflow: From an Empty Workspace to an Issued Onset Forecast

_Status, 2026-11-13: the path from an empty workspace to a zone-level onset outlook
for the right season is built (F0–F9, B1, B2). Comparison with normal, current-season
observations, the bulletin, scheduled re-issue and season review (B3–B7) are not.
Builds on [agent-workspace-design.md](agent-workspace-design.md); decision ids here are
prefixed **F** (reconfigure) and **B** (build)._

## Who this is for

A forecaster at the Ethiopian Meteorological Institute who issues Kiremt
(June–September) onset advisories to regional agriculture bureaus, zone by zone. Their
season has four phases:

| Phase | When | What they ask the assistant |
| --- | --- | --- |
| **Prepare** | Apr–May | "How well do these models predict onset in North Shewa compared with climatology?" "Use our onset definition." "Blend the best two and hold out the last three years." |
| **Issue** | Weekly or by dekad, May–Jul | "What's the chance onset comes in the next two weeks, per zone?" "Early, normal or late?" "A map for the bulletin." "What changed since last week?" "Draft the advisory paragraph." |
| **Monitor** | In season | "Has onset already happened in East Gojjam?" "What's the risk of a dry spell after an early onset?" |
| **Review** | After the season | "How did this season's forecasts do, zone by zone?" |

## Goals and non-goals

- A forecaster goes from an empty workspace to a defensible, zone-level onset outlook
  for the **right** season in one conversation, with every number traceable.
- **A raw forecast does not require a blend.** Model rainfall for the season, and onset
  read from it, is available as soon as the models have run. A blend is an optional
  calibration layer, not a gate.
- Answers come in the forecaster's units: named zones, their onset definition,
  "early / normal / late", dekads and weeks.
- Cost and time are stated before anything expensive is approved.

Out of scope: the Belg (Feb–May) season, which needs archives initialised in those
months (seasons are data, so it is a configuration addition); station data ingestion,
which enters as a registered data source; dry-spell-after-onset risk and cessation,
which are event types on the event-index track; a general scheduler.

P2 (declarative before imperative) means per-zone summaries and onset detection are
typed primitives with verifiable parameters, not `run_code` recipes. P4 (anything that
costs money needs approval) and P6 (human-readable everywhere) drive F4 and F5.

## Built

Why each piece is the way it is. Where it lives is in the code, by concept.

- **F0 — Workspace settings.** A workspace owns the defaults that frame every run in
  it: the season window and the **study area** (a box or named places, where runs are
  scored unless a run names its own). It does not own per-run choices (years, blend
  formula, models in one comparison), user preferences or secrets. Storage is one
  nullable JSON column parsed into a pydantic model with every field optional and
  `extra="forbid"`; a write with an unknown field is rejected, while a read drops keys
  a newer release wrote so a rollback still opens the workspace. New settings are new
  optional fields, never a new table. Precedence: explicit request value, then
  workspace setting, then platform default. Jobs freeze their resolved config and
  provenance records resolved parameters, so changing a setting never changes a past
  result. The assistant writes settings through one tool per setting, named for what it
  sets (`set_workspace_season`, `set_workspace_area`), because it must never hold a
  tool named for settings. Neither needs approval; the chat shows the change as an
  activity entry.
- **F1 — Forecast without a blend.** The rollout needs only the member models, their
  archive layout and a season start, so a blend was a convenient source of those, not
  a dependency. `run_forecast` takes a blend or a list of models. Without a blend it
  skips scoring and publishes each model's raw season forecast; the shared season
  cache makes "raw now, blended later" free. A raw forecast is a different product, not
  a weaker blend: it is shown beside the model's benchmark skill for the region, or
  "not yet benchmarked here". Raw GraphCast and FuXi score well below Traditional
  Climatology on onset in the Ethiopia 2026 workspace (RPSS −0.71 and −0.83), so this
  framing is a correctness requirement.
- **F1b — Open a registered data source.** `open_source` stages a subset of a
  registered archive or observation dataset into the workspace as a dataset artifact.
  It is `fetch` with a local backend: no network, no approval, still a provenance step
  naming the source and subset. On cloud it reads `gs://` sources the user can see.
- **F2 — Plan the season the forecaster means.** The season window (name, onset search
  start, reference onset, last issue date) is a workspace setting whose defaults equal
  the platform's, so an unset workspace behaves as before. A season must start and end
  in one calendar year. A pure resolver returns `upcoming`, `in_season` or `ended`, and
  an explicit year asks for a past season, labelled a re-forecast. The planner caps
  issue dates at the last issue date, and scoring takes it from the same place, so
  planning and scoring cannot disagree. Results carry the resolved state so the
  assistant says "Kiremt 2026 ended on 31 July; the first 2027 issue date is 1 May".
- **F3 — The workspace blend takes the page blend's fields** (area of interest, season
  dates, threshold, day-level training), so nothing the ruleset promises is silently
  dropped.
- **F4 — Approvals a forecaster can read.** An approval carries a server-computed
  `display` block beside `params`: names for ids, year ranges, season state and cost.
  `display` sits beside `params` because the approved hash covers `params` and must not
  change. The card renders only `display`.
- **F5 — Cost before asking.** Read-only `check_forecast` returns, per model, whether
  its season cache is ready, the issue dates still missing, the GPU estimate (~12 min
  per 45-day issue date, 4 dates per container, at most 12 containers) and the season
  state. The assistant calls it before `run_forecast`.
- **F6 — Name the areas.** `list_areas(region, level)` gives unit names for adm1–adm3.
  Scoring stays adm1/adm2 because 0.25° cells are larger than many woredas; adm3 is
  offered for summaries only, with that caveat in the ruleset.
- **F7 — Day-level onset probabilities.** "Within the next two weeks" needs days, not
  weeks. The workspace blend takes `train_day_level_blend`, following the deployment
  flag when unset. The flag stays off until the staging trial runs.
- **F8 — A forecasting prompt section** for workspace sessions: resolve the season
  first, check cost before running, offer raw first when there is no blend and frame it
  by benchmark skill, answer by zone, and never name tools. Tool-name leakage is caught
  by the `names_internal_terms` telemetry flag and an assistant eval case, not by
  filtering output.
- **F9 — Re-issue in one step.** `update_forecast` replays a frozen forecast spec for
  new issue dates, with an "already up to date" short-circuit.
- **B1 — Summarize by zone.** `summarize_by_area` aggregates a gridded or point
  artifact to named units, and `plot_area_map` draws the result; `plot_map` takes the
  same outlines as an overlay. Weights are by cell overlap with each unit's outline,
  measured on a lattice eight times finer than the grid, so small zones are not decided
  by one cell; each row reports the share of the area the forecast covers. Points
  without a forecast on an issue date drop out of that date's mean instead of counting
  as no chance. Named-unit points (Ethiopia's woredas) stand for the area of the same
  name and average equally into larger ones.
- **B2 — Read onset from a raw forecast.** `detect_onset` applies a ROMP onset rule to
  a raw season forecast in the code sandbox, through a server-written script that
  matches ROMP's own forecast scoring cell for cell. It writes an onset-day dataset and
  a day-level window table that `summarize_by_area` reads, flagged as one run
  (`members` = 1) so summaries call their values area shares, not probabilities. The
  server checks the definition, the forecast's length and its units (metres are
  refused) before the sandbox runs.
  - ROMP's package requires its mapping stack, which grows the analysis environment
    from 629 MB to 917 MB (and the Modal sandbox image by the same) though onset
    detection imports only the onset-rule module. Making that stack an optional extra
    in ROMP would remove the cost.
  - Ensemble members are averaged before the trajectory store, so the season file holds
    the ensemble mean and "fraction of members with onset" is not yet possible.
    `detect_onset` says so for ensemble models: averaging spreads light rain and hides
    dry spells, so onset read from the mean comes early.
  - The wet-day threshold defaults to ROMP's 20 mm; per-cell threshold files are not
    supported.

## Not built

### B3 — Compare with normal

`compare_to_normal(forecast, normal)` turns onset probabilities into the advisory
format: the chance onset comes earlier than, near, or later than normal. Benchmarks
already publish `climatology_onset_doy_*` and blends carry Traditional Climatology
probabilities per window; terciles of the climatological onset date per cell define the
categories, and B1 summarizes them by zone.

### B4 — This season's observations

Blend scoring already drops a cell's issue dates after that cell's observed onset, but
only when the observation directory holds a current-year file, and nothing provides
one. Needed: a `chirps_daily` fetch source (preliminary product, record end a few days
behind); `onset_so_far(obs, onset_rule)` to answer "has onset happened in East Gojjam?"
and produce a per-cell mask; and forecast jobs that accept that mask so cells where
onset has been observed grey out. The source must be the same observation product the
blend was trained against, or the mask and comparison are biased, so an observation
source records which product it is.

### B5 — Bulletin package

`compose_bulletin(forecast, areas, language)` writes a document artifact: zone map,
zone table, comparison with normal, and an advisory paragraph (English or Amharic) the
assistant drafts and the forecaster edits. Provenance links every number back to the
forecast job.

### B6 — Re-issue on a schedule

Follow-ups gain a time trigger (`due_at`, optional weekly or by-dekad `recurrence`
bounded by the season's last issue date); each firing calls `update_forecast` and
re-runs the same summaries. Approval is the hard part under P4: a cached refresh costs
nothing, but a gap-fill rollout uses a GPU. Proposal: one standing approval scoped to
the exact forecast spec, cadence, season end and a GPU-hour cap, with any firing that
would exceed the cap stopping to ask. It needs its own design for both the local runner
and the cloud deployment, and is the first concrete recipe.

### B7 — Review the season

Once observations cover the season (B4), `review_forecasts(forecast_job_ids)` scores
every issued forecast against observed onset: Brier and ranked probability skill per
issue date and per zone, against the Traditional Climatology comparison blend results
already use.

### Order

B4 first (it unblocks B7 and the mask), then B3 and B5 (the advisory format), then B7;
B6 waits for its design.

## Open questions

1. **Which settings come next?** The workspace stores the season window and study area.
   Candidates: the onset definition, the observation dataset, preferred models, and the
   analysis source for live forecasts. Region is deliberately not one: a region is an
   optional attribute, not something a workspace is set up around.
2. **Local GPU.** Without a GPU, local users' raw access is limited to the hindcast
   archives (F1b). Route their live rollouts to Modal, or treat live forecasting as a
   cloud or GPU-host feature?
3. **Single-run models.** A single predicted onset date from a deterministic model is
   misleading but useful. B2 keeps it, framed by the model's benchmark MAE; how to
   present it better is open.
