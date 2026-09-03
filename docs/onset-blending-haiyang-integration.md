# Onset Blending `haiyang` Integration

**Status:** Draft / Proposed
**Date:** 2026-08-27
**Author:** hholb

## Context

`caitken1729/onset_blending_for_laude@haiyang` is a fork of our blending
package carrying 33 commits of work. It is a clean fast-forward from
`a99a5034` — the ref pinned in `src/ai_almanac/envs/manager.py`
(`BLENDING_REPO_REF`) for the local blend env. The Modal blending app pins a
*different* ref, `8ba308eb` (`modal/blending_app.py:DEFAULT_REPO_REF`), which
is 4 performance commits past `a99a5034` on a divergent line that `haiyang`
does not contain.

What `haiyang` adds, grouped:

- **Generalization.** Repeatable `--forecast MODEL SPEC_ID` training inputs
  replacing the fixed `aifs`/`aifs_ens` roles (legacy CLI preserved);
  configurable spatial target IDs (`target_id`) with legacy `adm3_name`
  compatibility; configurable onset definitions (trigger window, wet-day
  threshold, follow-day anchoring, dry-spell parameters); configurable
  per-model rainfall horizons with formula resolution; windowed/prefixed
  climatology configuration; portable country template specs (regrid → CV →
  combine → connect → operational) with an India example.
- **Performance.** Sparse CSR regrid/aggregation transforms, fused
  regrid+onset for forecasts and ground truth, parallel Stage 1 NetCDF
  processing, parallel global CV across holdout years, parallel/vectorized
  KDE climatology, `--cores` flags on `run_training.py` and
  `1_blend_evaluation.py`.
- **Correctness.** Observed-weight transform ordering fix; NaN outcomes
  preserved through Platt calibration (previously treated as negatives);
  ground-truth handoff fix in training orchestration; saved formula/feature
  schema enforced at application time; stricter CV fold, threshold-join, and
  daily-calendar validation.
- **Hygiene.** `.gitignore` added; tracked data/artifacts (NetCDF, pickles,
  shapefiles) removed from the tree. `Monsoon_Data/grid_to_district_mapping.csv`
  and `Monsoon_Data/dissemination_cells.csv` — required by
  `_adm3_support_paths()` — survive, as does the source marker
  `python/prepare_data/nc_utils.py`.

The CLI contract ai-almanac drives is preserved on `haiyang`:
`1_blend_evaluation.py --spec_id/--work_dir/--results_dir [--cores]`,
`3_fit_final_model.py --coef_tag final`, `apply_blend_model.py
--coef_tag/--input_path`, and the `coefs_blended_model_global_final.pkl`
artifact name all still exist. New CLI surface is additive.

## Goals

- One canonical blending repo and one pin: merge `haiyang` into
  `hholb/onset_blending-adm3` and point both `BLENDING_REPO_REF` and
  `DEFAULT_REPO_REF` at the same commit.
- No behavior change for existing Ethiopia blends at the pin-bump step
  (bit-parity where feasible, statistical parity otherwise).
- Expose the new configurability in the UI in stages: quick wins first, then
  onset/horizon panels, then non-Ethiopia polygon domains.

## Non-goals

- Migrating existing blend jobs or cached intermediates. The intermediates
  cache is keyed on the repo ref (`modal/blending_app.py:_cache_key`), so a
  pin bump invalidates it by design; old jobs keep their stored outputs.
- Re-training or re-issuing any published forecast.
- A general "bring your own country" self-service flow. Phase 4 lands the
  plumbing; template authoring stays an admin task.

## Phase 0 — Upstream merge (repo: `hholb/onset_blending-adm3`)

1. Fetch `caitken1729/onset_blending_for_laude@haiyang` (`2a59cec0`) into the
   fork and create an integration branch from it.
2. Reconcile the 4 perf commits unique to the `8ba308eb` line. They touch
   `nc_utils.py`, `onset_utils.py`, `remap_nc.py`, and `.gitignore` — all
   files `haiyang` rewrote far more extensively (its `nc_utils.py` diff is
   ~5× larger), and `haiyang` reimplements the same optimizations (sparse
   weights, vectorized onset, parallel preprocessing). **Decision: drop the
   4 commits; adopt `haiyang`'s implementations.** The two standalone timing
   harnesses (`preprocess_timing_harness.py`, `scale_preprocess_timing.py`)
   cherry-pick cleanly if we want to keep them for the parity benchmark below.
3. Run the fork's own validation on the Ethiopia configuration: legacy
   two-model CLI (`aifs` + `aifs_ens`), `clim_mok_date` cutoff, existing
   specs. Compare CV summary metrics and final coefficients against a
   `8ba308eb` run. The correctness fixes (Platt NaN handling, observed-weight
   ordering) mean outputs will *not* be identical — document each expected
   delta and confirm it traces to a named fix, not a regression.
4. Merge to the fork's default branch. Mechanically: branch at the `haiyang`
   tip (`2a59cec0`), `git merge -s ours origin/main` (rehearsed: clean, tree
   identical to `haiyang`), carry over the two harness files, fast-forward
   `main`. **The pin is the `haiyang` tip SHA `2a59cec0`**, not the merge
   commit — it is an ancestor of the merged `main`, so `git fetch origin
   <sha>` from the hholb fork resolves it once `main` is pushed, and it lets
   the ai-almanac PR land independently of the exact merge-commit SHA.

## Phase 1 — Pin bump + compatibility (ai-almanac, no UI change)

Single PR against `develop`:

- `src/ai_almanac/envs/manager.py`: `BLENDING_REPO_REF` → `2a59cec0…`.
- `modal/blending_app.py`: `DEFAULT_REPO_REF` → same SHA (unifying the two
  pins; today they silently differ). `tests/test_blending_pins.py` asserts
  the two constants stay equal and are full SHAs.
- **Ordering:** the Phase 0 branch (or `main`) must be pushed to the hholb
  fork before this PR deploys, or `ensure_blending_env()` and the Modal
  image build cannot fetch the ref.
- Verify `ensure_blending_env()` re-materializes cleanly: the checkout logic
  fetches the new ref and checks `BLENDING_SOURCE_MARKER`, which still exists.
- Cache: no code change needed — the intermediates key embeds the repo ref,
  so new runs miss the cache and rebuild. Expect the first post-deploy blend
  per scope to be slow; note this in the deploy message.
- Pass `cores` from `run_blend` into `train_blending_model_bundle` (the
  plumbing existed but `run_blend` never set it), sized to `run_blend`'s CPU
  request via `RUN_BLEND_TRAINING_CORES`. On `haiyang`, `--cores` also
  drives holdout-year parallel CV, so this banks the parallel CV work for free.
- Parity gate on staging: submit the canonical Ethiopia blend
  (same obs source, models, and year split as a recent prod job) and compare
  `summary_models_pooled*` metrics and `blended_forecast_probabilities.csv`
  against the prod job's stored outputs. Accept deltas attributable to the
  Phase 0 fix list; anything else blocks.
- `pixi run check` / `pixi run test`; no API change, so no
  `generate-api-types` needed.

## Phase 2 — Quick UI wins

Small PRs, each independently shippable:

1. **Expose params the stack already accepts.** `threshold_mm`,
   `cutoff_month_day`, `mok_month_day` flow end-to-end
   (`BlendParams` → `run_blend` → intermediates) but no form field sets them.
   Add them to the Advanced block in `web/src/routes/blends/+page.svelte`
   and to `BlendRunSpec` / `_blend_create_body` so the assistant path can
   set them too. Backend change: none. Regenerate api types only if the
   pydantic schema descriptions change.
2. **Show the effective formula.** `haiyang` records the resolved formula in
   CV and final coefficient artifacts (predictors the user typed can be
   dropped by rainfall-horizon resolution). Surface it on the blend summary
   page next to the submitted `formula_text`, with a notice when they differ.
   Requires plumbing the value out of the coef bundle into the job outputs
   read by `routers/jobs.py`.
3. **Fix the guardrail desync.** `MIN_ONSET_YEARS` and the member-count
   warning are duplicated in `web/src/routes/blends/year-coverage.ts` rather
   than served from the API; admin overrides via `settings.guardrail_*`
   desync the form. Serve them from a config endpoint before Phase 3 adds
   more validated fields.

## Phase 3 — Onset and horizon configurability

All of these hit the same chokepoint: `run_blend`'s explicit param
allowlists (`modal/blending_app.py` ~L397–403 and L418–420) silently drop
unknown keys. Work items:

1. **Schema.** Extend `BlendParams` (`job_submission.py`) with an optional
   `onset` group (trigger window, wet-day min mm, follow days, dry-spell
   params — today hardcoded at `blending_app.py` ~L1199–1211) and an optional
   per-model `rain_horizon_days` map. Validate server-side against the same
   bounds the package enforces.
2. **Threading.** Pass the new groups through the allowlists into
   `build_lat_lon_intermediates_bundle` and `_build_blend_spec`. Replace the
   silent-drop behavior with an explicit "unknown blend param" error so the
   next field added cannot be lost.
3. **Cache correctness.** Onset parameters are currently invisible to the
   intermediates cache key (acknowledged in the comment at
   `blending_app.py` ~L1299 — only the repo ref invalidates them). Once they
   are user-settable they **must** enter the key digest. Add them to the
   hashed param set and bump `BLEND_INTERMEDIATES_CACHE_VERSION`.
4. **UI.** An "Onset definition" advanced panel and a per-model horizon input
   in the model checkbox grid. Surface the package's horizon/formula
   validation errors in the form rather than at job runtime where possible.
5. **Assistant.** Mirror the new fields in `BlendRunSpec` and
   `update_blend_config` so chat-driven configuration keeps parity with the
   form.

`pixi run generate-api-types` after the schema change; commit the result.

## Phase 4 — Non-Ethiopia polygon domains

The Ethiopia/ADM3 coupling is concentrated in two literals:
`ADM3_DOMAIN_REGIONS = {"ethiopia"}` (`modal/blending_app.py:64`) and
`usesAdm3Polygons()` checking `region_id === 'ethiopia'`
(`web/src/lib/components/BlendForecastMap.svelte:285`). `haiyang`'s
`target_id` contract and country templates make generalizing worthwhile:

1. **Region metadata.** Make "polygon domain" a per-region property instead
   of a hardcoded set: extend the region model (seeded from `regions.yaml`)
   with `spatial_domain: grid | polygon` plus pointers to the support
   artifacts (shapefile/weights, ID registry, dissemination cells). DB
   change is additive → satisfies the one-version-back migration rule.
2. **Support-file registry.** `haiyang` stopped tracking shapefiles and
   pickles in the repo, so polygon support files must be supplied per region
   — likely rows in (or alongside) the data-sources registry pointing at
   `gs://almanac-data-ai-almanac/` prefixes, validated at registration like
   dataset pointers. `_adm3_support_paths()` resolves from the registry
   instead of the checkout.
3. **Compute.** Replace `_should_use_adm3_domain` region checks with the
   metadata flag; feed `target_id` registries into the spec builder using
   the fork's country-template structure.
4. **Frontend.** Join map features on `target_id` with `adm3_name` fallback
   (`web/src/lib/components/blend-map/adm3.ts:25`); drive the
   polygon-vs-centroid rendering switch from region metadata rather than the
   region literal.
5. **Template flow (admin-only).** A documented procedure — not UI — for
   instantiating the fork's country templates for a new region, using the
   India example as the reference walk-through.

## Risks

- **Output drift at the pin bump.** The correctness fixes change numbers.
  Mitigation: the Phase 0/1 parity gates with a per-fix accounting of
  expected deltas; staging soak before `main`.
- **Two pins have already drifted.** Local env runs `a99a5034`, Modal runs
  `8ba308eb` — local and cloud blends do not currently agree. Phase 1 fixes
  this; treat it as an existing bug, and consider a follow-up check (CI or
  startup assert) that the two constants match.
- **Silent param drops.** The allowlist chokepoint has already hidden three
  UI-less params. Phase 3 item 2 converts it to an explicit error.
- **Cache-key gaps.** Exposing onset params without hashing them would serve
  stale intermediates. Phase 3 item 3 is a hard prerequisite for item 4.
- **Upstream divergence.** After Phase 0, `caitken1729`'s fork may keep
  moving. Adopting the fork's default branch as the collaboration point (or
  agreeing on PRs into `hholb/onset_blending-adm3`) avoids repeating this
  reconciliation.

## Sequencing and deploys

Phases are ordered PRs against `develop`; each merge soaks on staging before
promotion to `main`. Phase 1 is the only step with fleet-wide blast radius
(every new blend re-derives intermediates); schedule it away from active
forecast issuance. Phases 2–4 are additive and independently revertible.
Before any push touching `modal/` or `src/ai_almanac/server/`, run
`/security-review` per repo policy.
