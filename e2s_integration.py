import marimo

__generated_with = "0.23.8"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md(r"""
    # Earth2Studio Integration Plan

    This notebook is the shared design space for solidifying the Earth2Studio (E2S) integration
    into the benchmarking system. Use it to iterate on the plan, record decisions, and track open
    questions before writing code.

    ---

    ## Current state (what already exists)

    The integration is further along than it might look. Here's what's already wired up:

    | Layer | Status | Notes |
    |---|---|---|
    | **Obs fetching — ARCO ERA5** | ✅ Done | `_fetch_era5_daily_precip_from_arco` in `modal/app.py` |
    | **Obs fetching — CDS daily stats** | ✅ Done | `_fetch_era5_daily_precip_from_cds` in `modal/app.py` |
    | **Obs fetching — generic E2S source** | ✅ Done | `_fetch_e2s_obs` dispatches on `e2s_class` |
    | **Extended metrics (RMSE, MAE, ACC, Bias)** | ✅ Done | `_compute_extended_metrics` using E2S statistics |
    | **Modal benchmark image** | ✅ Done | `benchmark_image` adds `earth2studio[data]`, `gcsfs`, `zarr` |
    | **CDSAPI credential injection** | ✅ Done | `_modal_local_runtime_env` injects `CDSAPI_KEY` for modal-local |
    | **Remote obs provider routing** | ✅ Done | `REMOTE_OBS_PROVIDERS`, `_uses_remote_obs`, `_fetch_remote_obs` |
    | **Output file naming** | ✅ Done | `e2s_spatial_metrics_{model}_{window}.nc` convention |
    | **Metrics service reads E2S files** | ✅ Done | `list_nc_output_files`, `find_nc_output_file` handle both prefixes |
    | **`e2s_metrics` in romp.yaml** | ✅ Done | RMSE, MAE, ACC, Bias definitions |
    | **`get_metric_definitions` merges them** | ✅ Done | ROMP + E2S metrics returned together |
    | **ERA5 demo datasets** | ✅ Done | `era5-ethiopia`, `era5-india` in `datasets.yaml` (via ARCO) |
    | **Unit tests for modal helpers** | ✅ Done | `test_modal_metric_helpers.py` |

    ---

    ## Gaps and open questions

    ### Gap 1 — Model file format alignment
    `_compute_extended_metrics` reads model files from `local_model/*.nc` and
    looks for the variable named `model_var` (default `"tp"`). ROMP model input files are
    annual NetCDF files (`{year}.nc`) with precipitation forecasts. But:

    - What are the actual dimension names in model files? `(time, lat, lon)` or
      something ROMP-specific like `(TIME, LATITUDE, LONGITUDE)`?
    - Is `model_var` always `"tp"` for the models we have, or do some use a different name?
    - `_select_metric_variable` falls back to the first variable if the preferred name
      is missing — is that safe?

    **Action:** Run `_compute_extended_metrics` against a real job's staged model files
    and log what variable names and dimension shapes come out. Add a test case.

    ---

    ### Gap 2 — Time alignment between obs and model
    `_compute_extended_metrics` does:
    ```python
    obs_da, model_da = xr.align(obs_da, model_da, join="inner")
    ```
    This silently drops non-overlapping timesteps. The risk:
    - If model files only cover forecast lead days (e.g., 1–30 days ahead) but obs are
      calendar dates, the inner join might produce an empty or near-empty time axis.
    - The `romp_params` time clip is applied before the align, which should help, but the
      exact overlap depends on how model files encode their time coordinate.

    **Action:** Add an assertion or warning when `obs_da.sizes["time"] == 0` after the align.
    Consider logging the overlap count before proceeding.

    ---

    ### Gap 3 — Spatial regridding correctness
    When model and obs grids differ, we interpolate:
    ```python
    model_da = model_da.interp(lat=obs_da.lat, lon=obs_da.lon, method="linear")
    ```
    Linear interpolation is fine for small grid differences but not great if model resolution
    is much coarser than obs. ERA5 (0.25°) vs a 2° obs grid would be over-interpolated.

    **Open question:** Should we expose a `regrid_method` config option, or is linear always
    acceptable for precipitation benchmarking?

    ---

    ### Gap 4 — E2S datasets in `datasets.yaml`
    The `earth2studio` provider path in `config.py` and `modal/app.py` is fully implemented,
    but **no datasets currently use it** — the ERA5 entries use `era5_arco`. To test the
    `e2s_class`-based dispatch we need at least one dataset entry with `provider: earth2studio`
    and a non-CDS `e2s_class` (e.g., `"GFS"`, `"HRES"`, `"IMERG"`).

    **Decision needed:** Do we want any non-ERA5 E2S observation sources?
    - ERA5 via CDS is already covered by the `era5_arco` → CDS fallback in `_fetch_e2s_obs`.
    - The generic E2S path is useful for GPM-IMERG, GFS analysis, or other sources E2S supports.
    - **Recommendation:** add an `imerg-ethiopia` or `imerg-india` entry as the first real
      `provider: earth2studio` dataset to exercise the full dispatch path.

    ---

    ### Gap 5 — Frontend display of E2S metrics
    E2S metrics land in the same `windows` list as ROMP metrics (from `compute_job_metrics`).
    An E2S window looks like:
    ```json
    { "window": "all", "model": "fuxi", "metrics": { "rmse": {...}, "mae": {...}, "acc": {...}, "bias": {...} } }
    ```
    while ROMP windows have specific labels (`"1-7"`, `"8-14"`, etc.) and metrics
    (`false_alarm_rate`, `miss_rate`, etc.).

    **Open questions:**
    - Does the current frontend UI distinguish E2S windows from ROMP windows?
    - Is `window = "all"` a label the user sees, or filtered out?
    - The map grid endpoint (`GET /jobs/{id}/grid`) needs `?model=&window=all&metric=rmse` —
      does the frontend know to send `window=all` for E2S metrics?

    ---

    ### Gap 6 — MapLibre spatial visualization
    This branch (`hholb/earth2s-maplibre`) suggests we want to render E2S spatial metric
    grids (2D lat/lon arrays of RMSE/MAE/ACC/Bias) on a MapLibre GL map.

    `compute_job_grid` already returns the 2D grid data. The frontend just needs a component
    that takes `{ lats, lons, values, min, max }` and renders a heatmap layer.

    **Open questions:**
    - Is the plan to replace the existing OpenLayers `MetricMap` component, or add a parallel
      MapLibre-based one alongside it?
    - Color scale: what colormap is appropriate for RMSE (sequential), ACC (diverging), Bias
      (diverging)?
    - Should spatial metrics be overlaid on a basemap, or shown as a standalone panel?

    ---

    ### Gap 7 — E2S-only jobs (no ROMP)
    Currently every job runs ROMP first and then computes E2S metrics. But for models that
    are not supported by ROMP (e.g., non-seasonal global weather models), we might want a
    path that skips ROMP entirely and only runs E2S metrics.

    **Decision needed:** Is this in scope now, or do we always require ROMP?

    ---

    ## Proposed next steps (ordered by priority)

    ### Step 1 — Validate model file format (prerequisite for everything)
    Before any frontend work, confirm that `_compute_extended_metrics` can read the actual
    model files we have. Run a debug job or write a standalone script that:
    1. Loads a sample `{year}.nc` model file
    2. Calls `_select_metric_variable` and `_canonicalize_data_array` on it
    3. Prints dimension names, shapes, time range, and variable name

    ### Step 2 — Add an E2S dataset entry to `datasets.yaml`
    Add one entry with `provider: earth2studio` and a known `e2s_class` (e.g., `"CDS"` for ERA5
    or an IMERG class) to ensure the full provider dispatch path is exercised in tests.

    ### Step 3 — Improve time alignment robustness
    Add a check in `_compute_extended_metrics`:
    - Log the time overlap count
    - Raise an informative error (not a silent empty result) if overlap is zero

    ### Step 4 — Frontend: render E2S window in metric summary
    Update the frontend to:
    - Label the `"all"` window as "Full Period" or similar in the UI
    - Show E2S metrics (RMSE, MAE, ACC, Bias) in the results view alongside ROMP metrics

    ### Step 5 — MapLibre heatmap component
    Build a frontend component that accepts grid data from `GET /jobs/{id}/grid` and renders
    it as a MapLibre GL fill-color layer with a color scale legend.

    ### Step 6 — E2S-only job path (optional / later)
    Add a `job_type: e2s_only` flag that skips `_run_romp_entry` and only calls
    `_compute_extended_metrics`. This unlocks benchmarking global weather models that ROMP
    doesn't support.

    ---

    ## Decisions log

    | # | Decision | Rationale | Date |
    |---|---|---|---|
    | — | (record decisions here as we make them) | | |
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## Memory blow-up in ERA5 fetching

    Both fetching paths have concrete bugs that cause memory to blow up in Modal containers.
    Here is the analysis and proposed fixes.

    ---

    ### Path A — ARCO Zarr (`_fetch_era5_daily_precip_from_arco`)

    **Root cause: the ARCO store has globally-sized spatial chunks.**

    The store is `full_37-1h-0p25deg-chunk-1.zarr-v3`:
    - Time chunk = **1 hour** per chunk (1 timestep)
    - Spatial chunk = **721 × 1440** (the entire globe per timestep)

    Each Zarr chunk is `1 × 721 × 1440 × float32 ≈ 4 MB`. For a 24-hour day, `.load()`
    reads 24 chunks = **96 MB** even when the target region is tiny (Ethiopia ≈ 48 × 60
    grid cells = ~100 KB of actual data). Bandwidth waste ratio is ~960:1.

    For a 27-year climatology: `27 years × 5 months × 30 days × 96 MB ≈ 390 GB` downloaded.
    Memory blows up because Zarr's internal `LRUStoreCache` accumulates recently-read
    chunks across all the `.load()` calls on a single open store. With hundreds of calls,
    the cache fills several GB before eviction kicks in.

    **Secondary issue: `source` stays open across the entire 135-iteration nested loop**
    (27 windows × ~5 months). The store — and its cache — lives for the full duration.

    **Why `gc.collect()` doesn't help:**
    `del hourly; gc.collect()` frees the xarray object, but the Zarr store's own Python-level
    LRU cache is not cleared. Only closing the store or disabling the cache fixes it.

    ---

    ### Path B — CDS daily statistics (`_fetch_era5_daily_precip_from_cds`)

    **Bug 1: `by_year` accumulates ALL months in memory before writing anything.**

    ```python
    by_year: dict[int, list] = {}
    for start, end in ranges:           # 27 iterations
        for year, month, days in ...:   # ~5-6 each = ~135 total
            ...
            by_year.setdefault(year, []).append(da.load())  # never freed until end
    _write_romp_daily_obs_files(by_year, ...)               # everything written at once
    ```

    ~135 loaded DataArrays held simultaneously. For Ethiopia at 0.25° that is ~810 MB
    peak; larger regions or longer climatologies scale this up directly.

    **Bug 2: temp files accumulate on disk and are never deleted.**

    Each month writes `era5_daily_precip_{year}_{month:02d}.zip` and an extracted
    directory. Neither is cleaned up. Over 135 months this exhausts the container's
    `/tmp` filesystem before the job finishes.

    ---

    ### Proposed fixes

    The unifying principle: **never hold more than one year in memory at a time;
    delete every temp file immediately after reading it.**

    #### Fix A — ARCO: disable the Zarr LRU cache; close/reopen store per window

    ```python
    import zarr
    import zarr.storage

    for start, end in ranges:
        # Open a fresh store with no cache so chunks are discarded after each .load()
        raw_store = zarr.storage.FSStore(source_url, token="anon")
        ds = xr.open_zarr(raw_store, chunks=None, consolidated=True)
        source = _rename_canonical_coords(ds[data_var])

        # ... process months for this window ...

        ds.close()
        del source, ds
        gc.collect()  # now truly frees the per-window data
    ```

    Reopening per window costs one GCS metadata read (~milliseconds) and guarantees
    the Zarr chunk cache is flushed between seasons. `zarr.storage.FSStore` has no
    LRU layer by default; unlike `xr.open_zarr(url, ...)` which may wrap in an
    `LRUStoreCache` internally.

    #### Fix B — CDS: stream to disk year-by-year; clean up immediately

    Refactor `_fetch_era5_daily_precip_from_cds` into two parts:

    1. `_fetch_cds_month(client, year, month, days, ...) -> DataArray` — downloads,
       extracts, loads, **deletes the ZIP and extracted dir**, returns the DataArray.

    2. The outer loop groups months by year and calls `_write_romp_daily_obs_files`
       for each year as soon as all its months are ready, then `del` before continuing:

    ```python
    months_by_year = _group_months_by_year(ranges)

    for year in sorted(months_by_year):
        parts = [
            _fetch_cds_month(client, yr, month, days, ...)
            for (yr, month, days) in months_by_year[year]
        ]
        _write_romp_daily_obs_files({year: parts}, obs_var, local_obs)
        del parts
        gc.collect()
    ```

    Memory is bounded to one year's worth of monthly DataArrays at a time, and disk
    usage for each month's temp files is zero by the time the next month starts.

    ---

    ### Summary of concrete changes needed

    | Change | File | What it fixes |
    |---|---|---|
    | Disable Zarr LRU cache (use `FSStore` directly) | `modal/app.py` | Eliminates ARCO cache accumulation |
    | Close ARCO store after each year-window | `modal/app.py` | Flushes residual cache between seasons |
    | Extract `_fetch_cds_month` helper that cleans up temp files | `modal/app.py` | Eliminates CDS disk leak |
    | Write CDS data year-by-year instead of accumulating all | `modal/app.py` | Caps CDS RAM to ~1 year at a time |

    ### What we are NOT changing

    - The per-day loop structure in ARCO — the 960:1 bandwidth waste is a fundamental
      property of the ARCO chunk layout and cannot be fixed without a different data
      source. It is slow but not the memory killer.
    - The month-temp-file strategy for ARCO — the `.era5_arco_{year}_{month}.nc` files
      and their merge into annual files is already the right structure. We just need the
      Zarr cache not to accumulate while writing them.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## Avoiding the 390 GB download: GCP subsetter vs pre-cached GCS obs

    The fixes above cap memory within a job run, but they don't fix the fundamental
    inefficiency: we still download ~390 GB of global ERA5 data per job to produce
    ~400 MB of regional obs files. The question is whether a GCP-side preprocessing
    service would help, or just move the problem.

    ---

    ### Does a GCP Cloud Run subsetter buy anything?

    The ARCO ERA5 Zarr store lives in GCS (`gcp-public-data-arco-era5`). A Cloud Run
    service running in GCP reads from that bucket over GCP's internal network, which is:

    - **Free** — same-region GCS-to-Compute egress costs nothing
    - **Fast** — 10–100 Gbps internal bandwidth vs ~1–5 Gbps internet

    So the 390 GB of global chunk reads would happen inside GCP, and only the ~400 MB
    regional subset would cross the internet to Modal. From Modal's perspective, the
    download shrinks from 390 GB to 400 MB — a 1000× improvement.

    **But it doesn't eliminate the 390 GB reads.** The fundamental problem is that
    ARCO's spatial chunks are global-sized: even a service running inside GCP still has
    to read the full global chunk to extract a regional slice from it. You've moved
    the waste from "Modal → GCS" to "Cloud Run → GCS", where it's fast and free but
    still happening.

    ---

    ### The better answer: pre-cached regional obs in GCS

    ERA5 historical reanalysis **doesn't change**. The ARCO and CDS fetching paths
    are preprocessing pipelines that currently run on every benchmark job. They should
    run **once per region** when a dataset is added to the system, not once per job.

    Pre-caching means:
    1. Run a one-time Cloud Batch job that reads ARCO (within GCP, free), extracts
       the regional precipitation, and writes annual `{year}.nc` files to a GCS bucket:
       `gs://ai-almanac/era5-obs/ethiopia/1998.nc`, `…/1999.nc`, etc.
    2. Update the dataset entry in `datasets.yaml` to use that GCS path as `obs_dir`
       instead of `provider: era5_arco`.
    3. All future benchmark jobs call `_stage_gcs_years` (which already exists and
       downloads only the years in the job's date range), downloading only the ~400 MB
       they actually need.

    The system already supports this path: `_stage_gcs_benchmark_inputs` falls through
    to `_stage_gcs_prefix` when `_uses_remote_obs()` is false and `obs_dir` is a
    `gs://` URI. No new backend code needed.

    ---

    ### Comparison

    | Approach | Per-job Modal download | 390 GB reads | Setup |
    |---|---|---|---|
    | Current (ARCO direct) | ~390 GB | On Modal, paid egress | None |
    | GCP Cloud Run subsetter | ~400 MB | On GCP, free | New service to maintain |
    | **Pre-cached GCS obs (recommended)** | **~400 MB** | **Once ever, on GCP** | One script + one-time batch job |
    | CDS direct | ~400 MB | None (CDS serves regional) | None, but slow/queued |

    The GCP subsetter service would work fine but adds a running service to maintain and
    still does the wasteful global reads — just in the right place. Pre-caching is simpler
    and eliminates the reads entirely after the first run.

    The CDS path (`_fetch_era5_daily_precip_from_cds`) already does the right thing in
    terms of bandwidth — CDS returns only the requested region. It's the right fallback
    for new regions without a pre-cached GCS dataset, but it's slow (queued CDS API,
    up to several hours for long climatologies).

    ---

    ### Recommended path forward

    **Tier 1 (preferred):** Pre-process ERA5 obs to GCS, change dataset entries to
    use `obs_dir: gs://...`. This is the permanent fix for established regions.

    **Tier 2 (fallback for new regions):** CDS path — slow but correct bandwidth usage,
    no GCS setup required.

    **Tier 3 (avoid):** ARCO direct from Modal — only useful for prototyping. The memory
    fixes we landed make it survivable but it is still slow and bandwidth-wasteful.

    ---

    ### What `prepare_era5_obs.py` looks like

    A script (or Cloud Batch job) that reuses the existing fetching logic:

    ```python
    # scripts/prepare_era5_obs.py
    # Run once per region to populate GCS with preprocessed ERA5 obs.
    # Usage: uv run scripts/prepare_era5_obs.py --region ethiopia --bucket ai-almanac

    from modal.app import _fetch_era5_daily_precip_from_arco
    from google.cloud import storage as gcs
    from pathlib import Path
    import tempfile, argparse

    REGIONS = {
        "ethiopia": {
            "lat_bounds": [3, 15], "lon_bounds": [33, 48],
            "arco_url": "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
        },
        "india": {
            "lat_bounds": [5, 40], "lon_bounds": [65, 100],
            "arco_url": "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
        },
    }

    def main(region: str, bucket: str, start_year: int, end_year: int):
        cfg = REGIONS[region]
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir = Path(tmp)
            _fetch_era5_daily_precip_from_arco(
                dataset_config=cfg,
                romp_params={"start_date": f"{start_year}-01-01",
                             "end_date": f"{end_year}-12-31",
                             "start_year_clim": start_year,
                             "end_year_clim": end_year},
                local_obs=obs_dir,
            )
            # upload annual files to GCS
            client = gcs.Client()
            bkt = client.bucket(bucket)
            for nc in sorted(obs_dir.glob("*.nc")):
                blob = bkt.blob(f"era5-obs/{region}/{nc.name}")
                blob.upload_from_filename(str(nc))
                print(f"  uploaded: gs://{bucket}/era5-obs/{region}/{nc.name}")
    ```

    After running this, update `datasets.yaml`:

    ```yaml
    # Before:
    - id: era5-ethiopia
      name: "ERA5 Ethiopia"
      provider: era5_arco
      arco_url: "gs://gcp-public-data-arco-era5/..."
      ...

    # After:
    - id: era5-ethiopia
      name: "ERA5 Ethiopia"
      obs_dir: "gs://ai-almanac/era5-obs/ethiopia"
      region: ethiopia
      obs_file_pattern: "{}.nc"
    ```

    The `provider` field is removed, so `_uses_remote_obs()` returns false, and the
    existing `_stage_gcs_years` path handles the rest.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## Architecture rethink: benchmark evaluator, not data pipeline

    The system has been designed as if it needs to *generate* data at benchmark time.
    It doesn't. All data that a benchmark needs exists before the job runs:

    - **Obs climatology**: ERA5, CHIRPS, IMD, IMERG — historical, stable, doesn't change
    - **Model forecasts**: produced offline by model runs, not generated on demand

    The ARCO and CDS fetching code in `modal/app.py` is a data pipeline misplaced inside
    a benchmark executor. The job runner's only job should be: *pull pre-processed files,
    run ROMP + E2S metrics, return results*.

    ---

    ### The correct separation of concerns

    ```
    ┌────────────────────────────────────────────────────────────┐
    │  PHASE 1: Data preparation  (offline, one-time per region) │
    │                                                            │
    │  scripts/prepare_obs.py                                    │
    │    Fetch raw obs (ERA5/CHIRPS/IMERG), convert to ROMP      │
    │    annual NetCDF format, write to GCS.                     │
    │    Run once when adding a new obs dataset.                 │
    │                                                            │
    │  scripts/prepare_forecasts.py                              │
    │    Extract regional bbox from global model output,         │
    │    convert to ROMP format, write to GCS.                   │
    │    Run once per model × region × year-range.               │
    └────────────────────────────────────────────────────────────┘
                               │
                               ▼  writes to GCS
    ┌────────────────────────────────────────────────────────────┐
    │  PHASE 2: Configuration  (YAML, checked into the repo)     │
    │                                                            │
    │  regions.yaml    bbox, mask, climatology parameters        │
    │  datasets.yaml   obs datasets → GCS paths                  │
    │  models.yaml     model × region → GCS paths, date ranges   │
    └────────────────────────────────────────────────────────────┘
                               │
                               ▼  read at job submission
    ┌────────────────────────────────────────────────────────────┐
    │  PHASE 3: Benchmark execution  (online, per job)           │
    │                                                            │
    │  Backend: validate request, resolve GCS paths, create job  │
    │  Modal:   stage obs (~400 MB) + model files (~n × year),   │
    │           run ROMP, run E2S metrics, upload results        │
    └────────────────────────────────────────────────────────────┘
    ```

    No raw data fetching in Phase 3. No code changes needed to add a new region —
    only YAML changes and one-time prep scripts.

    ---

    ### Why generalization breaks today

    **Models are duplicated per region in `models.yaml`.** FuXi for India and FuXi for
    Ethiopia are two separate YAML entries that repeat `display_name`, `model_type`,
    `model_var`, `unit_cvt`, and `init_days`. To add FuXi for a third region you'd add
    a third block. This isn't unworkable, but it hides that there are really two separate
    things: a *model identity* (what it is) and a *deployment* (what data exists for which
    region and which date range).

    **The `region` field on models is a GCS path prefix, not a geographic concept.**
    `INDIA_FUXI_MODEL_DIR` is a path-naming convention. The lat/lon bounds for the region
    are only defined inside `datasets.yaml`, not shared with models. If you want to run
    E2S extended metrics that need to align obs and model grids spatially, you need the
    region's bbox in both places.

    **There is no region-level concept of "which models have data here."** Adding a new
    region means manually cross-checking which models have forecast files available for
    that bbox and date range, then adding entries one by one.

    **Runtime obs fetching papers over missing pre-processed data.** The ERA5 ARCO path
    exists because nobody ran a one-time prep step to extract and store the regional obs.
    It made prototyping fast but obscured the architectural requirement.

    ---

    ### Proposed config restructure

    Introduce `regions.yaml` as a first-class file that everything else can reference:

    ```yaml
    # backend/app/config/regions.yaml
    - id: ethiopia
      name: "Ethiopia"
      lat_bounds: [3, 15]
      lon_bounds: [33, 48]
      season_start: "05-01"   # monsoon start (month-day)
      season_end:   "09-30"   # monsoon end

    - id: india
      name: "India"
      lat_bounds: [5, 40]
      lon_bounds: [65, 100]
      season_start: "06-01"
      season_end:   "09-30"

    - id: west-africa          # adding a new region is just a YAML block
      name: "West Africa"
      lat_bounds: [-5, 20]
      lon_bounds: [-20, 25]
      season_start: "06-01"
      season_end:   "10-31"
    ```

    Factor model identity out of the per-region deployments in `models.yaml`:

    ```yaml
    # model identity — defined once
    - id: fuxi
      display_name: "FuXi"
      model_type: AIWP
      model_var: tp
      unit_cvt: 1000
      init_days: "0,3"
      probabilistic: false

    # per-region deployment — only what differs between regions
    - id: fuxi
      region: india
      start_date: "1964-05-01"
      end_date:   "2024-07-31"
      start_year_clim: 1964
      end_year_clim:   2024

    - id: fuxi
      region: west-africa      # add this block when forecast data exists
      start_date: "2019-05-01"
      end_date:   "2024-07-31"
      start_year_clim: 1991
      end_year_clim:   2024
    ```

    `datasets.yaml` becomes simpler — no `provider: era5_arco`, just GCS paths:

    ```yaml
    - id: era5-ethiopia
      name: "ERA5 Ethiopia"
      region: ethiopia
      obs_dir: "gs://ai-almanac/obs/era5/ethiopia"
      obs_file_pattern: "{}.nc"

    - id: era5-west-africa     # added when prepare_obs.py has been run
      name: "ERA5 West Africa"
      region: west-africa
      obs_dir: "gs://ai-almanac/obs/era5/west-africa"
      obs_file_pattern: "{}.nc"
    ```

    ---

    ### The model forecast preparation gap

    Global AIWP models (FuXi, GraphCast, AIFS, GenCast) produce global or large-domain
    forecasts. Before a new region can be benchmarked, someone has to:

    1. Locate the model's global forecast archive (GCS, S3, or local)
    2. Extract the region's bbox for each forecast year
    3. Convert to ROMP format: `{year}.nc` with `(time, lat, lon)` and the right variable
    4. Upload to `gs://ai-almanac/models/{model}/{region}/`

    This is `scripts/prepare_forecasts.py`. It's model-specific (each model has different
    output formats and archive locations) but region-generic once you have the model's data.

    **What the system cannot do automatically** is conjure forecast data for a region
    where no model runs have been done. Adding West Africa for FuXi requires actual FuXi
    forecasts that cover the West African domain to exist somewhere. The system's job is
    to make it easy to register and use that data, not to produce it.

    ---

    ### What changes, what stays the same

    **Remove from the job runner:**
    - `REMOTE_OBS_PROVIDERS` constant and the `_uses_remote_obs()` dispatch
    - `_fetch_remote_obs`, `_fetch_era5_daily_precip_from_arco`, `_fetch_era5_daily_precip_from_cds`
    - The `era5_arco` and `earth2studio` provider paths from `config.py` and `datasets.py`
    - The `cdsapi_url` / `cdsapi_key` settings (or move them to prep scripts only)

    **Move to prep scripts:**
    - All ARCO/CDS/E2S fetching logic → `scripts/prepare_obs.py`
    - Regional bbox extraction from global model outputs → `scripts/prepare_forecasts.py`

    **Add:**
    - `backend/app/config/regions.yaml` — geographic metadata shared by models and datasets
    - `get_regions()` loader in `config.py` (mirrors `get_model_registry`, `get_demo_datasets`)
    - `prepare_obs.py` and `prepare_forecasts.py` scripts
    - A new API endpoint `GET /config/regions` so the frontend can show available regions
      without hardcoding them

    **Unchanged:**
    - All three job runners (Docker, Modal, Cloud Run)
    - GCS staging paths (`_stage_gcs_prefix`, `_stage_gcs_years`)
    - ROMP invocation and E2S metrics computation
    - The metrics service and storage layer
    - The frontend (regions come from the API, not hardcoded)

    ---

    ### Migration path from current state

    1. Run `prepare_obs.py` for `ethiopia` and `india` using the existing ARCO/CDS code —
       this produces the GCS obs files that are currently generated at runtime.
    2. Update `datasets.yaml` to swap the ERA5 entries from `provider: era5_arco` to
       `obs_dir: gs://...`. Verify benchmarks still work.
    3. Add `regions.yaml` with existing regions. Wire up `GET /config/regions`.
    4. Refactor `models.yaml` to separate identity from deployment.
       This is purely cosmetic for existing regions but necessary for clean generalization.
    5. Delete the ARCO/CDS runtime code from `modal/app.py` and the backend config.
    6. Add a new region (West Africa or Bangladesh) end-to-end using only YAML + prep scripts.
       If that works cleanly, the architecture is right.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## How ROMP handles regions — and what it means for the architecture

    ### What ROMP actually does

    ROMP performs its own spatial subsetting inside `region_select()`. The subsetting
    pipeline, applied to every input file at load time, is:

    1. Normalise lon axis (handles both [-180, 180] and [0, 360])
    2. Normalise lat to ascending
    3. Slice to bounding box: `ds.sel(lat=slice(lat_s, lat_n), lon=slice(lon_w, lon_e))`
    4. Optionally apply a binary NetCDF mask (`nc_mask`)
    5. Optionally apply a land/sea mask (`land_only=True`)
    6. Optionally apply a cartopy country boundary (`shp_only=True`)

    **Consequence: input files do not need to be pre-clipped to the exact region.**
    You can give ROMP a file covering a larger domain and it subsets at load time.

    ### Named vs. custom regions

    ROMP has three hardcoded named regions in `momp/params/region_def.py`:
    - `"Ethiopia"`: (3–15°N, 33–48°E)
    - `"Sub_Ethiopia"`: (7.5–13.5°N, 37.5–40°E)
    - `"India"`: (6.46–35.51°N, 68.11–91.4°E)

    For any other region, use `region="custom"` with explicit `lat_min/max`, `lon_min/max`.
    These map to `ROMP_REGION=custom` and `ROMP_LAT_MIN` / `ROMP_LAT_MAX` / `ROMP_LON_MIN`
    / `ROMP_LON_MAX` env vars. **No ROMP code changes needed to add a new region.**

    ### Irregular-shape regions: the `nc_mask` mechanism

    For regions that aren't well-described by a bounding box (e.g., monsoon zones,
    river basins, administrative boundaries), ROMP accepts a binary 0/1 NetCDF mask
    file via `ROMP_NC_MASK`. It aligns the mask to the data coords and applies
    `xarray.where(mask == 1)`, turning non-region cells to NaN. The mask file just
    needs `lat` and `lon` dimensions.

    This is the right mechanism for adding West Africa, Bangladesh, etc. — create
    a mask NetCDF once, store it in GCS, reference it in the dataset config.

    ---

    ### Implications for the data pipeline

    **Good news:** ROMP's own subsetting means we don't need pixel-perfect pre-clipping.
    Obs and model files can cover a somewhat larger bbox (e.g., "all of South Asia"
    rather than exact India bounds) and ROMP will clip correctly. This simplifies the
    prep scripts.

    **But model file sizes force pre-clipping anyway.** A global daily-precip model
    file at 0.25° resolution for one year is ~1–2 GB. Downloading 26 years × 1–2 GB
    to Modal on every job is not feasible. Pre-clipping model files to a regional bbox
    before staging to GCS is still necessary for bandwidth reasons.

    For obs files: ERA5 daily precip for Ethiopia (12° × 15°, 0.25°) is ~1 MB/year;
    for India (35° × 23°, 0.25°) it's ~4 MB/year. Even a large domain like
    "all of Sub-Saharan Africa" would be ~20 MB/year — perfectly fine to stage without
    global pre-clipping.

    ---

    ### The concrete plumbing gap: region → ROMP params

    Right now the system passes `ROMP_REGION=Ethiopia` or `ROMP_REGION=India` because
    those strings are hardcoded in `models.yaml` and flow through to `ROMP_REGION` env
    var via `romp_params["region"]`.

    For a new region like "west-africa" or "bangladesh", ROMP would receive
    `ROMP_REGION=west-africa` and fail — it doesn't know that string. The fix is a
    small translation step in `routers/jobs.py` that, when building ROMP params:

    1. Looks up the model's region in `regions.yaml` to get `lat_bounds`/`lon_bounds`
    2. If the region has a native ROMP name (Ethiopia, India), passes that name as-is
    3. Otherwise, sets `region=custom` and passes the four lat/lon params explicitly

    ```python
    # in create_job(), after building romp_params
    region_cfg = region_registry.get(model_cfg["region"])
    if region_cfg:
        romp_name = region_cfg.get("romp_name")  # "Ethiopia", "India", or None
        if romp_name:
            romp_params.setdefault("region", romp_name)
        else:
            romp_params.setdefault("region", "custom")
            romp_params.setdefault("lat_min", region_cfg["lat_bounds"][0])
            romp_params.setdefault("lat_max", region_cfg["lat_bounds"][1])
            romp_params.setdefault("lon_min", region_cfg["lon_bounds"][0])
            romp_params.setdefault("lon_max", region_cfg["lon_bounds"][1])
        if region_cfg.get("nc_mask_gcs"):
            # stage mask file alongside obs/model and pass local path
            romp_params.setdefault("nc_mask", "/data/masks/region_mask.nc")
    ```

    And `regions.yaml`:

    ```yaml
    - id: ethiopia
      romp_name: "Ethiopia"           # pass directly to ROMP_REGION
      lat_bounds: [3, 15]
      lon_bounds: [33, 48]

    - id: india
      romp_name: "India"
      lat_bounds: [5, 40]
      lon_bounds: [65, 100]

    - id: west-africa                 # no romp_name → uses region=custom
      lat_bounds: [-5, 20]
      lon_bounds: [-20, 25]
      nc_mask_gcs: "gs://ai-almanac/masks/west-africa.nc"  # optional
    ```

    ---

    ### Complete picture: what needs to happen for a new region

    | Step | Who | When | Code change? |
    |---|---|---|---|
    | Add entry to `regions.yaml` | Engineer | Once per region | No code change |
    | Run `prepare_obs.py` to write annual ERA5 NetCDF files to GCS | Engineer | Once per region | Script already wraps existing ARCO/CDS code |
    | Add obs entry to `datasets.yaml` with `obs_dir: gs://...` | Engineer | Once per region | No code change |
    | Run `prepare_forecasts.py` to clip global model output to region bbox | Engineer | Once per model × region | New script needed |
    | Add model entry to `models.yaml` with region + date range | Engineer | Once per model × region | No code change |
    | `create_job` translates region → ROMP params | Backend | Runtime | One-time code change |
    | Stage mask file alongside obs/model if `nc_mask_gcs` is set | Modal | Runtime | Small addition to staging code |

    After the one-time backend change to the region→ROMP translation, **adding any future
    region requires zero code changes**: YAML + prep scripts only.

    ---

    ### What `prepare_forecasts.py` does

    This is the new piece. For global models (FuXi, GraphCast, AIFS), it:

    1. Reads global annual NetCDF files (from GCS, HPC, or local disk)
    2. Subsets to `region_cfg["lat_bounds"]` + `region_cfg["lon_bounds"]` + a small buffer
       (e.g., +2° on each side so ROMP's own subsetting has room)
    3. Renames variables/dimensions to ROMP's expected names if needed
       (`lat`, `lon`, `time`; model precip var set by `model_var` config)
    4. Writes annual `{year}.nc` output files
    5. Uploads to `gs://ai-almanac/models/{model_id}/{region_id}/`

    The buffer matters: prep to bbox + 2°, let ROMP apply the exact cut. This decouples
    the prep step from ROMP's exact subsetting behaviour and makes the GCS files reusable
    across slight region boundary changes.

    The script is model-specific for the *reading* part (each model has different output
    formats and archive locations) but region-generic for the *writing* part.

    ---

    ### What to build, in order

    1. **`regions.yaml` + `get_regions()` loader** — the foundation everything else references
    2. **Region→ROMP translation in `create_job`** — unblocks running any new region
    3. **`prepare_obs.py`** — wraps existing ARCO/CDS code, writes to GCS, run for Ethiopia + India
    4. **Flip `datasets.yaml`** — ERA5 entries → `obs_dir: gs://...`, delete `provider: era5_arco` entries
    5. **`prepare_forecasts.py`** — clips global model output to regional bbox + uploads to GCS
    6. **Remove ARCO/CDS runtime code** from `modal/app.py` and the backend
    7. **Test: add one new region end-to-end** using only YAML + prep scripts
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## Scratch space — working notes

    Use the cells below for iterating on the plan, running experiments, or pasting in
    code snippets to evaluate approaches.
    """)
    return


@app.cell
def _():
    # ---- working notes ----
    # Add cells below this one as the plan evolves.
    return


if __name__ == "__main__":
    app.run()
