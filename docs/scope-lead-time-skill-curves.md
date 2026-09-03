# Scope: lead-time skill curves from ROMP skill-score CSVs

Branch: `feat/lead-time-skill-curves` (worktree `.worktrees/skill-curves`, based on
`origin/develop` @ `4cdacd4`).

Surface ROMP's already-computed probabilistic skill scores in the benchmark
results UI, and present them alongside the spatial metrics in a way that
discourages reading any single metric in isolation.

Two goals, in tension and both load-bearing:

1. The map stays front-and-center for spatial metrics.
2. Every metric in the suite is visible at once, including the ones the
   benchmark does **not** compute — so a user can't mistake an unmeasured
   property (calibration, ensemble spread) for a passing one.

See §5 for the design and the two rejected iterations that got there.

---

## 1. Why

ROMP computes the full probabilistic suite on every ensemble run and writes it to
CSV. Nothing in the platform reads those files.

`services/romp.py:96-100` sets `BS`, `RPS`, `AUC`, `Reliability`, `skill_score` all
`True`, and `save_csv_score: True` (`romp.py:105`). Every `plot_*` flag is `False`
(`romp.py:107-114`), so ROMP draws no figures — the CSVs are the whole output. ROMP writes
`overall_skill_scores_*.csv` and `binned_skill_scores_*.csv` into the job's
`output/` directory (`momp/io/output.py:46-47, 69-72`). Those files are indexed as
`job_artifacts` and downloadable. `grep -rn "overall_skill_scores\|binned_skill_scores"`
returns zero hits outside ROMP itself.

The reason nothing reads them is structural: the results pipeline discovers files
through exactly two globs — `spatial_metrics_*.nc` and `e2s_spatial_metrics_*.nc`
(`storage.py:141-151`, `370-375`) — and `ResultsViewer` builds its metric picker from
NetCDF `data_vars` (`ResultsViewer.svelte:69-80`). Anything that isn't a 2D lat/lon
field cannot reach the UI.

### The sharper version of the problem

A probabilistic benchmark job currently renders **nothing at all**.

`momp/driver.py:72,76` calls `skill_score_in_bins()` then `spatial_far_mr_mae_map()`.
Each self-gates on `cfg.probabilistic`:

| | `probabilistic: false` | `probabilistic: true` |
|---|---|---|
| `spatial_far_mr_mae_map` | runs | early-returns (`momp/app/spatial_far_mr_mae.py:33-34`) |
| `skill_score_in_bins` | early-returns (`momp/app/bin_skill_score.py:38-39`) | runs |
| → `spatial_metrics_*.nc` | ✅ | ❌ |
| → `*_skill_scores_*.csv` | ❌ | ✅ |

The ensemble spatial path that would fill the top-right cell,
`ens_spatial_far_mr_mae_map()`, was imported at `momp/app/bin_skill_score.py:16` but
never called from that module — the call is commented out at
`bin_skill_score.py:185-186`, and its only live call site was that module's own
`if __name__ == "__main__"` block (`momp/app/ens_spatial_far_mr_mae.py:304`), which
`driver.py` never reaches. So the two output families were mutually exclusive, and a
probabilistic job produced no NetCDF at all — `compute_job_metrics` returned
`windows: []`, `ResultsViewer.svelte:139` rendered "No spatial data available for
this run set." in place of `MetricMap` (the map component is never mounted), and
`MetricsTable` rendered "No metric data found."

**Confirmed empirically:** a benchmark run with AIFSENS2 alone showed no metrics
whatsoever.

### Fixed in the ROMP repo

`momp/driver.py` now calls `ens_spatial_far_mr_mae_map()` as a third step
alongside the other two, each self-gating on `cfg.probabilistic`. The function
already honored `save_nc_spatial_far_mr_mae` (which the platform sets `True`) and
`plot_spatial_far_mr_mae` (`False`, so no figures), and it computes the
climatology reference in the same pass — so a probabilistic job now gets
`spatial_metrics_*.nc` with a baseline layer, with no platform-side change.

Note this is **not** the two-line uncomment it appears to be: line 185 reads
`if case_cfg['plot_spatial_far_mr_mae']:`, and `services/romp.py:107` hardcodes
that flag `False`, so uncommenting alone produces nothing. Calling from the
driver bypasses the mislabelled gate — it controlled a NetCDF write, not a plot.

ROMP is a separate repo on a separate branch. That change must land and be
released before probabilistic runs show maps here; until then the portrait's
spatial rows stay empty for ensemble models, which the design handles.

Ensemble data sources force this path automatically: `data_sources.py:287-290` sets
`probabilistic = True` whenever the source has an ensemble member dim.

---

## 2. Data contract

### Filenames

`{overall,binned}_skill_scores_{model}_{window}.csv`, flat in the job's `output/`
directory, no subdirectories.

- `{model}` is `romp_safe_model_name(...)` — whitespace runs collapsed to `_`, so
  "AIFS Single v2" → `AIFS_Single_v2`. **Model tokens contain underscores.**
- `{window}` is `tuple_to_str(verification_window)` = `"-".join(map(str, item))`
  (`momp/utils/printing.py:19-20`), so `1-15` and `16-30` given
  `romp.py:87`'s hardcoded `verification_window_list = ((1, 15), (16, 30))`.
- Same window token as the NetCDF filenames, so the two families align.

Because model names contain underscores, parse by anchoring on the trailing window
token rather than splitting:

```python
_SKILL_RE = re.compile(
    r"^(?P<kind>overall|binned)_skill_scores_(?P<model>.+)_(?P<window>\d+[-,]\d+)\.csv$"
)
```

The `[-,]` mirrors the legacy comma fallback already present in
`find_nc_output_file` (`storage.py:153-160`). Normalize to `-`, matching
`metrics.py:169`.

### `overall_skill_scores_{model}_{window}.csv`

Header + one data row (`momp/io/output.py:21-47`):

```
Fair_Brier_Score,Fair_Brier_Skill_Score,Fair_RPS,Fair_RPS_Skill_Score,AUC,AUC_ref
```

### `binned_skill_scores_{model}_{window}.csv`

One row per lead-time bin (`momp/io/output.py:56-72`):

```
Bin,clean_bins,Fair_Brier_Skill_Score,AUC,AUC_ref,Fair_Brier_Score_Forecast,Fair_Brier_Score_Climatology
```

- `Bin` is e.g. `Days 1-5`; `clean_bins` is the same with `"Days "` stripped
  (`output.py:53`).
- Rows come from `get_target_bins` (`momp/stats/bins.py:24-37`), which keeps only
  labels starting with `Days ` — the `Before day N` / `After day N` tail bins are
  already excluded. Sorted by leading day.
- Bins are further filtered to the case's verification window
  (`momp/app/bin_skill_score.py:57-58` → `filter_bins_in_window`,
  `momp/lib/control.py:264-268`), yielding exactly 3 rows per window out of
  `romp.py:90`'s six `day_bins`.
- Missing values are `np.nan` written by pandas as **empty strings**. Every
  `float()` must be guarded — see `blend_domain._parse_pooled_summary` and its
  empty-input contract test (`tests/test_blend_results.py:36-38`).

> **Verified against real output.** Confirmed by pointing the parser at a real
> GENCAST probabilistic benchmark: both CSVs parsed, all six overall scores and
> the per-bin values populated, and both verification windows resolved. The
> earlier concern that the contract was inferred from `momp/io/output.py` source
> is closed. Note the skill CSVs were already on disk from before this work —
> `save_csv_score` has always been `True`, so any past ensemble run has them.

### Mapping to `romp.yaml` metric ids

`GET /config/metrics` (`server/routers/config.py:28-30`) already serves the
probabilistic definitions — `src/ai_almanac/settings.py:469-473` concatenates
`metrics.deterministic + metrics.probabilistic + e2s_metrics` — so labels, units,
and `lower_is_better` come through for free if we key the API response by these ids:

| CSV column | `romp.yaml` id | `lower_is_better` |
|---|---|---|
| `Fair_Brier_Score` | `brier_score` | true |
| `Fair_Brier_Skill_Score` | `brier_skill_score` | false |
| `Fair_RPS` | `ranked_probability_score` | true |
| `Fair_RPS_Skill_Score` | `ranked_probability_skill_score` | false |
| `AUC` | `auc` | false |
| `AUC_ref` | — (reference baseline, no id) | — |

The `Fair_` prefix is dropped in the mapping. If we care about the distinction,
add `fair: true` to the yaml descriptions rather than inventing new ids — every
score ROMP persists is the ensemble-size-debiased variant.

---

## 3. Constraints established during investigation

**One job = exactly one model.** `romp.py:47` writes `"model_list": (model_name,)`,
a one-element tuple. Multi-model selections fan out into N jobs sharing a `run_id`
(`benchmark_domain.py:506-564`), with `probabilistic` clamped per model at
`benchmark_domain.py:473-474`. So a run group can mix deterministic jobs (maps, no
curves) and probabilistic jobs (curves, no maps) — the UI must handle both in one
view.

**No new storage method is required.** `read_result_text(job_id, kind, filename)`
exists on both backends (`storage.py:105-110` local, `307-312` GCS) and GCS reads
straight to memory via `blob.download_as_text()` — no download-to-temp step, unlike
the NetCDF path. `list_result_files` (`130-139`, `345-354`) exists on both for
discovery. Do **not** take `_nc_lock`; it guards HDF5 global state only.

**No caching.** Per window this is one row of 6 floats plus 3 rows of 7 columns —
a few KB of pure-Python `csv` parsing, versus `compute_job_metrics`' NetCDF opens
and full-grid percentiles. Do not extend `metrics_cache`. It is written once
(`routers/jobs.py:383`), read once (`:360`), and never nulled anywhere including
migrations. A breaking reshape wouldn't corrupt anything —
`_parse_metrics_cache` (`jobs.py:77-85`) validates against `JobMetrics`, logs
"Ignoring invalid persisted metrics cache" on failure, and recomputes — but pydantic
ignores *extra* fields by default, so an additive reshape would silently serve stale
rows with the skill fields missing and never self-heal. If profiling later disagrees,
add a separate column.

**Return empty, not 404.** For a deterministic job with no skill CSVs, return
`models: []`. The frontend `request()` wrapper (`api/core.ts:56-121`) throws on
non-OK, which would surface as a red error state instead of a neutral empty state,
and would defeat the module-level cache in `benchmarks.svelte.ts`.

---

## 4. Backend changes

### New: `src/ai_almanac/server/services/skill_scores.py`

Response schemas live in the service module, not the router — house pattern per
`services/metrics.py:27-93`.

```python
class SkillBin(BaseModel):
    bin: str                 # "Days 1-5"
    label: str               # "1-5"
    lead_day_min: int
    lead_day_max: int
    brier_skill_score: float | None
    auc: float | None
    auc_ref: float | None
    brier_score_forecast: float | None
    brier_score_climatology: float | None

class WindowSkillScores(BaseModel):
    model: str
    window: str
    overall: dict[str, float | None]   # keyed by romp.yaml metric ids + auc_ref
    bins: list[SkillBin]

class JobSkillScores(BaseModel):
    job_id: str
    windows: list[WindowSkillScores]

def compute_job_skill_scores(job_id: str, storage: StorageBackend) -> JobSkillScores: ...
```

Implementation: `list_result_files` → filter `kind == "output"` and the filename
regex → group by `(model, window)` → `read_result_text` each → `csv.DictReader`
over `io.StringIO` → guarded float coercion. Sort windows by leading day, matching
`metrics.py:199`'s stable-ordering habit.

Synchronous, per the module docstring convention at `metrics.py:1-5`.

### New route in `routers/jobs.py`

Place near `get_metrics` (after ~L386). Copy `get_grid`'s shape:

```python
@router.get("/{job_id}/skill-scores", response_model=JobSkillScores)
async def get_skill_scores(job_id: str, job: ReadableJob):
    _require_complete(job)
    try:
        return await asyncio.to_thread(compute_job_skill_scores, job_id, get_storage())
    except Exception as e:
        logger.exception("Error reading skill scores for job %s", job_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
```

`_require_complete` gives 409 for incomplete jobs (`jobs.py:145-149`). No
`FileNotFoundError` branch — missing files are the empty case, not an error.

Then `pixi run generate-api-types` and commit `web/src/lib/api-types.gen.ts`. CI
fails on a stale file (`.github/workflows/ci.yml:61-62`), and this is called out
explicitly in the review workflow.

### `services/stub_outputs.py` + `job_workload._run_stub`

`_run_stub` currently ignores `probabilistic` entirely
(`job_workload.py:240-260`). Add `write_skill_score_csvs(output_dir, model, window)`
emitting the exact column sets above with plausible values, and call it from the
same loop gated on `(config.get("romp_params") or {}).get("probabilistic")`.

Pre-existing divergence to fix: `stub_outputs.WINDOWS = ("1-7","8-14","15-21","22-30")`
(`stub_outputs.py:16`) does not match real ROMP's `1-15`/`16-30` (`romp.py:87`).
Align it — genuinely one line. Nothing reads `stub_outputs.WINDOWS` outside
`job_workload.py:251`; the only test mentioning `1-7`
(`tests/test_artifacts_publication.py:33,54`) hand-writes its own filenames.

To mirror reality, the stub should also **skip** the NetCDF writes when
`probabilistic` is true, which makes the empty-map state reachable in local dev —
the state most of this UI work needs to be tested against. Larger blast radius than
it looks: `_run_stub`'s NetCDF loop also drives figure output
(`job_workload.py:240-260`), and `tests/conftest.py:27` forces `RUNNER_MODE=stub` for
the entire suite. Gate narrowly and call it out in the PR description.

---

## 5. Frontend design (third iteration — this is the one)

The two earlier iterations are recorded because the reasoning matters.

**Iteration 1 — tabs split by metric family.** `Spatial Metrics` /
`Probabilistic Scores`. Rejected: that boundary makes whichever tab you land on
the metric family you reason from, which is the over-indexing failure this
feature exists to prevent.

**Iteration 2 — a horizontal scorecard rail, no tabs.** One always-visible card
per metric above the map. Rejected once metric names had to be spelled out:
"Ranked Probability Skill Score" is 29 characters, so four cards consume the full
width and the rest scroll out of view — the dropdown problem in new packaging.

**Iteration 3 — tabs split by *view*, portrait covers all metrics.**

```
BENCHMARK SUMMARY
[ Map ] [ All Metrics ]
────────────────────────────────────────────
Map tab           existing MetricMap + MetricsTable, untouched
All Metrics tab   stage: selected metric's lead-time curve
                  portrait: every metric × every model × both windows
```

The critical difference from iteration 1: **the tab boundary is map vs.
everything-else, not spatial vs. probabilistic.** The portrait contains spatial
rows as regional averages alongside the probabilistic rows, so no metric is
hidden behind whichever tab you didn't pick. The map tab is the *one* metric view
that can't be folded into a table.

This is explicitly **interim**. A metric-map rewrite is planned; keeping the new
views in their own tab decouples them from that work entirely — no `MetricMap`,
`MetricMapControls`, or `lensSelection.ts` changes are required. After the
rewrite the portrait should drive the map directly and the tab split can go away.

### Naming rule

**Metric names are always spelled out.** "Brier Skill Score", never "BSS";
"Area Under ROC Curve", never "AUC". `romp.yaml` carries an `abbreviation` field
per metric — it must not be used for display. Applies to chart titles, axis
labels, tooltips, portrait rows, tab labels, and empty-state copy.

### `MetricPortrait.svelte` + `metric-portrait.ts`

Rows are metrics grouped spatial / probabilistic. Columns are grouped
**window-major**: both verification windows are shown simultaneously, and within
each window the competing models sit side by side with climatology pinned beside
them. Model-major grouping would put one model's two windows together, comparing
a model against itself rather than against its competitors.

Rules enforced in `metric-portrait.ts`:

- **Normalize within a (row, window) group.** Never across rows — each metric has
  its own unit and direction. And never across windows: skill degrades with lead
  time, so pooling both windows would make the shading mostly encode lead time
  and drown out the model-vs-model difference, which is the comparison the view
  exists for.
- **Direction unknown means unranked.** `bias` has `lower_is_better: null`.
- **Missing values are unranked, not worst.** A null must never read as failing.
- **Red only below climatology.** Teal by position; warm red reserved for values
  actually worse than the reference, so "worst of three good models" doesn't read
  as failure.
- **No aggregate score, no overall-rank row.** A single summary number is the
  thing this view exists to avoid.

### Cross-metric disagreement

`markDisagreements` compares model orderings **pairwise** and **per window**. For
each pair of models within a window, the consensus is whichever direction most
metrics prefer; a row is flagged if it strictly reverses at least one pair.

Four cases this gets right:

- A **fully tied** row expresses no ordering and contradicts nothing. An earlier
  keyed-string implementation flagged ties as dissent.
- A pair the metrics **split evenly** on has no consensus and is skipped.
- **Per-window** rather than pooled: models can legitimately agree at short range
  and diverge at extended range, and pooling would hide exactly that. The row
  records `disagreeingWindows`, and the marker renders in the window it applies
  to.
- Unrankable rows are ignored when finding the majority.

The flag points at rows worth a second look and says nothing about which model is
better.

### Reference values

Every skill score is a ratio, so it renders beside its reference, resolved per
window:

| Row | Reference | Source |
|---|---|---|
| Brier Skill Score | `0` | by definition |
| Ranked Probability Skill Score | `0` | by definition |
| Area Under ROC Curve | `auc_ref` | overall CSV, per window |
| Brier Score | mean of per-bin climatology | binned CSV, per window |
| Ranked Probability Score | none | not on disk |
| spatial rows | climatology model row | `JobMetrics`, per window |

This is what defuses a misread Brier Skill Score: because it equals
1 − BS_forecast / BS_reference, a value near zero means either both forecasts are
good or both are useless, and only the paired values disambiguate.

### `SkillCurveChart.svelte`

Built from `BlendSkillChart.svelte` (correct lifecycle, tokenized CSS) rather
than `MaeSeriesChart.svelte` (double-constructs, hardcoded colors). Differences
that matter:

- **Y range unbounded below.** A skill score under zero means worse than
  climatology and must stay visible. Both existing charts clamp — one floors at
  0, the other caps at 1.
- **Reference line** at the no-skill value. Neither existing chart has one, and
  without it the curves are uninterpretable.
- **One shared 1–30 lead axis** across both windows; x comes from bin midpoints,
  so window boundaries are invisible and a model reads as one continuous curve.
- **Values format as `0.820`, not `82.0%`.** `romp.yaml:84` types the Area Under
  ROC Curve as `fraction`, which the shared `formatMetricValue` renders as a
  percentage — right for False Alarm Rate and Miss Rate, wrong here. Hence
  `formatSkillValue`.
- **ResizeObserver attaches in `buildChart`, not `onMount`.** The host sits
  behind an `{#if}` and doesn't exist at mount.

Only the Brier Skill Score and the Area Under ROC Curve have per-bin values in
ROMP's binned CSV. The Ranked Probability Score and its skill score are
cumulative by construction and ROMP writes `N/A` per bin, so selecting them shows
the pooled value and an explanation instead of an empty chart. Selecting a
spatial row points at the map.

### `api/jobs.ts` and `benchmarks.svelte.ts`

`getJobSkillScores(id)` after `getJobCell`, auto-exported by `index.ts`. Add
`_skillCache` keyed by `jobId` mirroring `getCachedJobGrid`, and
`_skillCache.delete(jobId)` in `invalidateJobCaches`.

`AllMetricsPanel`'s fetch effect tracks `jobs` directly rather than the derived
`jobsKey`: a job flipping running → complete does not change the id set, so
keying on `jobsKey` would never notice it finish. Derived values still use the
`jobsKey` + `untrack` idiom, because the polling loop hands a fresh array every
3s.

### Cell shading: one diverging scale, one meaning

Every cell resolves to a **skill-relative-to-reference** number via
`skillAgainstReference`, and that single quantity drives the color: `0` matches
climatology, `1` is perfect, negative is worse than climatology. Teal above,
muted rust (`166, 84, 60`) below, unshaded at or without a reference.

| Row kind | Formula |
|---|---|
| Skill scores | the value itself — already this quantity |
| Lower-is-better (Brier, Miss Rate, Mean Absolute Error) | `1 − value / reference` |
| Area Under ROC Curve | `(value − reference) / (reference − 0.5)` |
| No reference on disk (Ranked Probability Score) | null → unshaded |

Two earlier attempts were wrong:

**Rank-within-row fill.** Color encoded position among models. With a single
model — the common case — `max === min`, so every cell normalized to 1 and
rendered at full saturation, making "only value present" look identical to "best
in class". Suppressing shading when fewer than two distinct values existed fixed
the lie but left the single-model table almost entirely unshaded.

**Rank fill plus a flat below-climatology tint.** Color then encoded two
different things through one channel. On the real GenCast run eight of ten cells
came out the same flat pink, so the table could not say *how far* below
climatology a value sat, and the legend advertised a teal ramp that appeared
nowhere.

The diverging scale fixes both because climatology is a *meaningful zero*:
distance from it is an absolute statement that holds for one model, and rank
between models falls out as a side effect (the better model sits further toward
teal). Verified against the real run — Days 16–30 grades darker than 1–15 in all
four referenced rows, which the flat tint could not express.

Magnitude is clamped at ±1 before mapping to alpha (`0.08 + magnitude * 0.42`);
past "twice as bad as climatology" the exact figure stops being actionable and
letting it run saturates everything. The legend swatches sample that same curve,
so they cannot drift from the cells.

`isBest` still requires two distinct values — "best of one" is not a claim worth
making — but it now only sets bold weight, not color.

Note the Brier Score row's shading is by construction identical to the Brier
Skill Score row's, since `1 − BS/BS_ref` *is* the Brier Skill Score. Accepted:
the two rows show different numbers, and collapsing them would hide the raw
score.

### Known data caveat

Portrait spatial values are **unweighted** region means. ROMP has
`domain_average` with cosine-latitude weighting at `momp/utils/region.py:179` and
never calls it. Harmless for Ethiopia; misleading for a wide-latitude domain, and
the portrait would present it with false authority.

---

## 6. Tests

Backend — new `tests/test_skill_scores.py`, structured after
`tests/test_blend_results.py`:

1. Pure parser tests over literal CSV strings, including the empty-string-for-NaN
   case and a model name containing underscores.
2. Service test against a duck-typed `FakeStorage` over `tmp_path`
   (`tests/test_metrics_service.py:21-41`).
3. HTTP test: insert a job row with raw SQL, write CSVs through
   `get_storage().result_file_path`, hit `GET /jobs/{id}/skill-scores`.
4. Empty case — deterministic job returns `windows: []`, not 404.
5. 409 for a non-complete job.

Frontend:

- `web/tests/skill-series.test.ts` — pure-function tests for lead-axis
  composition, series alignment, and formatting, following `blend-summary.test.ts`
  (no DOM).
- `web/tests/skill-scores-panel.test.ts` — panel empty/error states and
  `ResultsViewer` tab switching, with the chart stubbed as
  `vi.mock('.../SkillCurveChart.svelte', () => ({ default: {} }))` — the idiom from
  `chat-panel.test.ts`. uPlot itself is untestable in jsdom, which is why neither
  existing chart has a test.

`pixi run check` and `pixi run test` before every commit, per CLAUDE.md. Git hooks
enforce this if `pixi run install-hooks` has been run in this worktree.

---

## 7. Status and remaining work

### Landed

| Area | State |
|---|---|
| `momp/driver.py` — probabilistic runs emit spatial metrics | done, **ROMP repo, uncommitted** |
| `services/skill_scores.py` — CSV parser + schemas | done, logic verified |
| `GET /jobs/{id}/skill-scores` | done |
| `stub_outputs.py` / `_run_stub` — skill CSVs, windows aligned to `1-15`/`16-30`, NetCDF skipped when probabilistic | done |
| `api/jobs.ts` + `_skillCache` in `benchmarks.svelte.ts` | done |
| `chart-colors.ts`, with `MaeSeriesChart` and `BlendSkillChart` migrated onto it | done |
| `skill-series.ts` — lead-axis composition, series alignment, formatting | done, verified |
| `SkillCurveChart.svelte` | done |
| `metric-portrait.ts` — per-window normalization, ranking, pairwise per-window disagreement | done, verified |
| `MetricPortrait.svelte` — window-major columns, both windows at once | done |
| `AllMetricsPanel.svelte` — stage + portrait | done |
| `SegmentedTabs.svelte` + `ResultsViewer` Map / All Metrics split | done |
| Full metric names throughout | done |

### Manual cleanup required

The sandbox could not unlink files, so two things need doing by hand:

```bash
rm web/src/lib/components/SkillScoresPanel.svelte          # superseded by AllMetricsPanel
git mv web/tests/skill-scores-panel.test.ts \
       web/tests/all-metrics-panel.test.ts                 # contents already updated
rm /Users/hayden/code/ai-almanac/.git/worktrees/skill-curves/index.lock
```

`SkillScoresPanel.svelte` is orphaned — nothing imports it — but it will still
compile and lint, so it won't fail CI. It is dead code.

### Verified

The whole gate now runs, including Vitest (its only blocker was a missing
`@rollup/rollup-linux-arm64-gnu`; `npm install --no-save` on it is enough):

```
ruff check src tests modal scripts   → All checks passed
ruff format --check ...              → 131 files already formatted
svelte-check --tsconfig ...          → 0 errors, 0 warnings
prettier --check .                   → all matched files clean
vitest run                           → 12 files, 102 tests passed
```

Three failures found and fixed along the way, all of them test-side:

**An unrealistic fixture hidden by optional chaining.** `skillFor` in
`metric-portrait.test.ts` never populated `ranked_probability_score`, so that row
was never built, and `expect(rps?.referenceByWindow['1-15']).toBeNull()` compared
`undefined` against `null`. Real ROMP always writes `Fair_RPS`, and it is the one
row with no reference — precisely the case those assertions exist to cover. The
fixture now matches the real CSV and the assertions dropped the `?.` so a missing
row fails loudly.

**`vi.mock(..., () => ({ default: {} }))` does not work for a component that is
actually rendered.** Svelte throws "default is not a function" on instantiation.
Worse, when it happens inside an effect it surfaces as an *unhandled* error rather
than a failure, so `all-metrics-panel.test.ts` was reporting 6 passing tests
alongside 4 unhandled errors — Vitest's own warning is "might cause false positive
tests". Child components are now mocked to `tests/fixtures/StubComponent.svelte`,
a real inert component.

**`vi.mock` factories are hoisted above `const` declarations,** so a shared
`stub` helper is read before initialization. Each factory has to be inline.

### Still host-only

| Step | Note |
|---|---|
| `pixi run test-python` | Needs the full dependency tree; `tests/test_skill_scores.py` is still unrun. Ruff passes on it. |
| `/security-review` | Required by CLAUDE.md for changes under `src/ai_almanac/server/`, which this touches — a new route plus an LLM tool that reads job files. |

`pixi run generate-api-types` has been run: `api-types.gen.ts` gained the
`/jobs/{job_id}/skill-scores` path and the `JobSkillScores` / `WindowSkillScores`
/ `SkillBin` schemas (+99 lines).

**Check `pixi.lock` before committing.** It shows as modified after running
`pixi install` in the worktree, but at an identical byte size and with no
dependency change in this PR — so it is probably a re-solve artifact and should be
reverted to keep the diff honest. Lockfiles are tool-owned per CLAUDE.md.

### Known risks going into staging

**GCS is untested.** Staging uses `GCSStorage`, and the parser's path there
(`list_result_files` → `read_result_text`) has only been exercised against
`LocalStorage`. No new storage method was added and `read_result_text` already
returns a `str` on both backends, so this should work — but it is the most likely
place for a staging-only surprise. Worth checking one probabilistic job's
`/jobs/{id}/skill-scores` immediately after deploy.

**The ROMP pin gates half the feature.** `src/ai_almanac/envs/benchmark.pixi.toml:36`
pins momp to a git SHA. The `driver.py` fix reaches staging only once it is merged
in `hholb/ROMP` and that `rev` is bumped. Until then probabilistic runs still
produce no spatial NetCDF, so the Map tab stays empty for them — which the design
handles, and which the skill-score half does not depend on (it reads CSVs the
current pin already writes).

**Earth2Studio window collision.** When `compute_e2s_metrics` is enabled, E2S
writes `verification_window="all"` (`services/e2s.py:156-166`), so the portrait
would gain a third column group in which every ROMP row is an em-dash. Honest but
probably ugly; no rule yet.

### Chat integration

`get_skill_scores` reaches the assistant through the usual three layers
(`benchmark_domain._exec_get_skill_scores` → `chat_tools` → `llm._metrics_toolset`).
Two details worth keeping:

- The response carries a `notes` block stating that scores are the fair variants,
  pooled over the region, measured against climatology, and — most importantly —
  that reliability, Continuous Ranked Probability Score, spread–skill and rank
  histograms are *not computed*, so their absence must not be read as a pass.
- `get_job_metrics` now redirects to `get_skill_scores` when it finds no spatial
  files, instead of reporting "no metrics found". That message was the bug: for a
  probabilistic job the assistant would deny the existence of metrics the user was
  looking at.

Suggested chat prompts are derived from the run set (`chatSuggestions` in
`routes/benchmarks/+page.svelte`) rather than hardcoded, because the previous
defaults offered false alarm rate and mean absolute error — neither of which a
probabilistic run computes.

---

## 8. Decisions

1. **One shared lead-day axis** for both verification windows.
   `filter_bins_in_window` gives 3 non-overlapping bins each, so `1-15` and
   `16-30` compose into a single 1→30 axis. Revisit if real output shows the bins
   collide.
2. **Scores render as `0.820`, not `82.0%`.** Leave `romp.yaml:84`'s
   `unit: fraction` alone — it is semantically correct, and `false_alarm_rate` /
   `miss_rate` are also `fraction` and *should* render as percentages. Probabilistic
   scores format through `formatSkillValue` instead.
3. **Metric names are never abbreviated.** "Brier Skill Score", not "BSS".
   `romp.yaml`'s `abbreviation` field is not for display.
4. **No aggregate score and no overall-rank row** in the portrait. Colors and the
   disagreement flag do the work; a single summary number is what this view
   exists to avoid.
5. **The portrait is the metric navigation.** No rail, no separate list, no
   dropdown — its first column already lists every metric, and a separate picker
   would duplicate it and narrow the map.

### Still unresolved

§2's column contract is inferred from `momp/io/output.py` rather than observed.
The stub now encodes the same inference, so stub-based tests cannot detect a
wrong guess. Generate one real probabilistic job's output before trusting the
parser.

---

## 9. Explicitly out of scope

ROC curves, per-grid-point skill maps (needs ROMP to group the forecast–observation
pairs by lat/lon instead of pooling them), and the blend-side dropped columns
(`rps_skill`, `brier_week*`, `pietra`).

**Reliability diagrams** deserve their own issue and are the highest-value
addition once this lands, because calibration is currently unmeasured and the
Area Under ROC Curve does not cover it — a model can discriminate perfectly and
still be badly calibrated. `plot_reliability_diagram` already computes the full
10-bin table with binomial error bars (`momp/graphics/reliability.py:120`), but
its caller discards the return value (`momp/app/bin_skill_score.py:139`), and
`services/romp.py:109` sets `plot_reliability: False` so it never runs on a
platform job at all. Needs ROMP to persist the table as CSV *and* a platform flag
change. The portrait names Reliability in its not-computed footer in the
meantime, so the gap is at least visible to users.

**Cosine-latitude weighting.** ROMP has `domain_average` at
`momp/utils/region.py:179` and never calls it, so every regional aggregate —
including the portrait's spatial rows — is unweighted. Harmless for Ethiopia,
wrong for a wide-latitude domain.
