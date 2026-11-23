# Workspace on staging: polish plan

Status: proposed, 2026-11-23. Four issues reported after the workspace stack
reached staging. Causes below come from reading the code; none are confirmed
against staging yet, so each work item starts with a diagnostic step.

| # | Symptom | Most likely cause | Confidence |
|---|---------|-------------------|------------|
| 2 | Finished blend shows "No files yet"; blend page fine | The workspace lists **ledger** artifacts, recorded by a one-shot step that downloads every output to `/tmp` and hashes it. On Cloud Run that step fails or runs out of memory, and nothing retries it | High |
| 3 | Job status changes only show after a page refresh | The stream's `jobs` event fails to serialize (a forecast headline's `date` fields go through plain `json.dumps`), so every stream dies before sending a status. See Findings | Confirmed; fixed on `hholb/workspace-staging-polish-f21753` |
| 1 | Every backend read is sluggish | Cold starts (staging `min_instances = 0`), plus workspace reads that make many sequential DB round trips and GCS reads, plus #2's downloads competing for the same instance | Medium |
| 4 | Workspace settings are hard to find | Creation asks only for name and description. Area and season are small ✎ chips in the summary strip | n/a (design) |

Fix #2 first. Its downloads also show up in #1 and #3, so measure those again
once it lands.

---

## 0. Diagnostics (half a day, before writing code)

Run these on the host (gcloud is blocked in the agent sandbox).

```bash
# Instance crashes / OOM on staging
gcloud logging read 'resource.labels.service_name="almanac-backend-staging" AND (textPayload:"Memory limit" OR textPayload:"in the ledger failed" OR textPayload:"artifact indexing failed")' --project ai-almanac --freshness=7d --limit=20

# How long /events streams live (≈240 s = streaming works; seconds = it is being cut)
gcloud logging read 'resource.labels.service_name="almanac-backend-staging" AND httpRequest.requestUrl:"/events"' --project ai-almanac --freshness=2d --limit=20 --format='value(timestamp,httpRequest.status,httpRequest.latency)'

# Slowest routes
gcloud logging read 'resource.labels.service_name="almanac-backend-staging" AND httpRequest.latency>"1s"' --project ai-almanac --freshness=2d --limit=50 --format='value(httpRequest.requestUrl,httpRequest.latency)'

# CPU allocation mode and scaling
gcloud run services describe almanac-backend-staging --region <region> --project ai-almanac --format='yaml(spec.template.metadata.annotations)'
```

**Findings so far**

- **Root cause of #3 (confirmed in logs, 2026-11-17 00:19:11Z):**
  `_jobs_event` raises `TypeError: Object of type date is not JSON
  serializable` at `jobs[1].headline.first_issue_date`.
  - **Why:** `_sse` passes `job.model_dump()` (Python mode) to `json.dumps`, and
    a forecast's headline (`ForecastSummary`, `workspace_summary.py:51`) has
    `date` fields.
  - **Effect:** in any workspace with a forecast, every stream dies on its
    first `jobs` event. No status update ever reaches the page; it only
    changes when `GET /workspaces/{id}` runs on refresh. That route
    serializes through FastAPI's response model in JSON mode, so it works.
  - **Why it looked like a clean end:** after the exception, the front end
    ends the already-200 response cleanly. That's why the client retries
    every ~1 s instead of backing off.
  - **What the ~5 s is:** it isn't a timeout. It's how long building the first
    `jobs` event takes on staging (`list_jobs` + `job_headlines` GCS reads),
    which also measures #1. `log_requests` shows 33 ms because it times only
    until headers are sent.
  - **Fix:** serialize every event in JSON mode (`model_dump(mode="json")`, or
    one `_sse` that takes models). Add a test that a workspace with a finished
    forecast streams a `jobs` event. The other three events (`artifacts`,
    `analyses`, `follow_ups`) use the same plain `model_dump()`, so cover
    them in the same change.

- 2026-11-17 00:19:13Z: staging instance hit `Memory limit of 4096 MiB exceeded
  with 4127 MiB used` (revision `almanac-backend-staging-00206-skb`). This is the
  only OOM in 7 days, and there are no `in the ledger failed` warnings. That
  fits a process killed partway through ledger recording rather than one that
  failed cleanly. Still to check: does the time line up with the blend's
  `completed_at` / `artifacts_published_at`?
- 2026-11-23: every `/events` stream on staging lasts **4.5–6.5 s** (status
  200) instead of 240 s, and a new one starts about 1 s later. That constant
  1 s gap means each stream ended cleanly: the client only resets to its 1 s
  retry delay after a clean end, and doubles it after an error. A local repro
  (uvicorn, the same five `@app.middleware("http")` layers, `StreamingResponse`
  + `request.is_disconnected()`) holds the stream for its full length, so the
  route code alone doesn't cut it. The page opens the stream once per
  workspace id, so the client isn't aborting it either. Remaining suspects are
  between the browser and uvicorn (Cloud Run front end / LB), or
  `is_disconnected()` returning True only behind them.
- What follows from that:
  - **#3:** every reconnect sends a fresh `jobs` event, so a status that's
    current in the DB should reach the page within ~7 s. A status that stays
    stale until refresh therefore points at the **DB row being stale**
    (reconciler not ticking, branch B in §2), assuming the client applies
    events.
  - **#1:** each open tab re-runs `job_headlines` (GCS reads) on every
    reconnect, and the client calls `refreshSummary()` (more GCS reads) on
    every `jobs` event. That's two heavy reads per tab every ~6.5 s.
- Next diagnostics:
  - Log why `_activity_events` ended (disconnect / deadline / error) and how
    long it ran.
  - `curl -N` the events endpoint through `staging.ai-almanac.org` and
    through the `run.app` URL, to see which hop closes it.
  - Compare a running job's `jobs.status` with Modal's view of the same call.

In the browser: DevTools → Network → `events` → EventStream tab. Check whether
`jobs` events arrive while a run is in flight.

For the stuck blend, in the staging DB:

```sql
SELECT j.id, j.status, j.artifacts_published_at, s.completed_at, s.outputs
FROM jobs j LEFT JOIN provenance_steps s ON s.job_id = j.id
WHERE j.id = '<blend job id>';
```

If `artifacts_published_at` is set and `s.completed_at` is NULL, #2 is
confirmed.

---

## 1. Blend files missing from the workspace (#2)

**What happens today**

- The blend page reads `job_artifacts`, filled by `index_job_artifacts`
  (`services/artifacts.py:37`).
- The workspace reads ledger artifacts linked through `workspace_items`
  (`services/workspaces.py:684`). Those are created by
  `ledger.complete_job_step` (`services/ledger.py:416`), which
  `index_job_artifacts` calls only *after* it has committed its own
  `artifacts_published_at` claim.
- `complete_job_step` stages **every** output file into a
  `TemporaryDirectory` (`ledger.py:170`) to hash and summarize it. Cloud Run's
  `/tmp` is memory-backed (4 GiB limit), and an India 0.25° blend writes large
  NetCDFs.
- A failure is logged as a warning (`artifacts.py:92`) and never retried,
  because the claim is already taken. An OOM kill leaves no log at all.
- Day-level outputs (`day_level_published_at` claim) never call
  `_record_in_workspaces`, so those files never reach a workspace either.

**Plan**

1. Make ledger recording its own retryable pass with its own marker (an
   additive column, e.g. `jobs.ledger_recorded_at`). Publication and ledger
   recording stop sharing one claim. A pass that fails runs again on the next
   tick, with backoff and an attempt cap so a permanently bad job doesn't spin.
2. Hash by streaming, not staging: `open_bytes` + `provenance.hash_stream`
   already exist (`ledger.current_hash`). Summaries of NetCDFs read headers only
   (open lazily from `gs://` and read dims/vars/attrs), or are deferred to first
   view.
3. Run the pass in a background task separate from job-status reconciliation,
   so a slow hash never delays a status update (this is part of #3).
4. Cover day-level outputs with the same pass.
5. Backfill: existing jobs have `artifacts_published_at` set and no completed
   step. The new pass picks them up because its marker starts NULL. No manual
   step needed.
6. UI wording: a complete run whose files aren't recorded yet should say
   "Preparing files…", not "No files yet" (`RunDetail.svelte:185`).

**Done (2026-11-23):** items 1–3 and 5.
- **Retry without a migration:** a published job whose step is still open is
  "unrecorded" (`ledger.unrecorded_job_ids`).
  `workspaces.record_published_outputs` retries each one with an in-process
  backoff (60 s, doubling, capped at 1 h).
- **Concurrency:** two instances racing on one job collide on the
  `(user_id, content_hash)` key, and the loser retries later, so no claim
  column is needed.
- **Remote files are streamed:** hashed in one pass, then described from the
  NetCDF header through the same seekable handle.
- **Separate loop:** publication no longer records inline. Recording runs in
  its own loop (`_output_recording_loop`).

**Still open:**
- **Item 4 (day-level outputs):** their step is already complete when they
  arrive, and the ledger never changes a completed step (P5). That needs a
  design choice: a second step for the day-level pass, or relaxing P5 for job
  steps.
- **Item 6 (UI wording): done.** A run with no files says "Files appear here
  when the run finishes", "Preparing the files…" or "This run made no files".

*Alternative considered:* the Modal job writes a manifest (sha256, size,
summary) next to its outputs, so the server never reads the bytes. This is
cleaner long-term, but it means redeploying the Modal apps and their pinned
images. Do it later if streaming hashes turn out too slow.

**Tests:** a job whose files exceed a size threshold is recorded without
staging. A failed recording is retried on the next pass. Day-level outputs
appear in a linked workspace.

---

## 2. Live job status (#3)

**What happens today:** the page holds an SSE stream (`watchWorkspace`,
`web/src/lib/api/workspaces.ts:156`). The server polls `workspaces.activity()`
every 3 s for up to 240 s (`routers/workspaces.py:402`). Statuses come from the
`jobs` table, which the reconciler loop updates every 5 s from Modal
(`app.py:297`). That loop runs status reconciliation, day-level hand-off, and
artifact publication (including #2's downloads) **one after another** in the
same tick.

**Decide by diagnostic result:**

- **A. No events in the EventStream tab, or streams cut short:** the transport
  is the problem (proxy buffering, an LB timeout, or the stream erroring and
  backing off to 30 s). Fix in deploy config. As a client fallback, after N
  failed connects, poll `GET /workspaces/{id}` every 10 s.
- **B. Events arrive but the status inside is stale:** the reconciler isn't
  ticking.
  1. Split the tick: status reconciliation in one task, publication and
     ledger recording in another, so status never waits on file work.
  2. Don't depend on an in-process loop on a request-scaled service. Add an
     authenticated `POST /internal/reconcile` triggered by Cloud Scheduler every
     minute (OIDC, Cloud Run invoker on a dedicated SA), and keep the in-process
     loop for local runs. If you want completions to show faster than a minute,
     the Modal function can call the same endpoint when it finishes.

Either way, add a test that a status change written by another process reaches
an open stream as a `jobs` event (the loop is already testable through
`activity_changes`).

Needs `/security-review` (touches `server/` and `terraform/`).

---

## 3. Staging sluggishness (#1)

**Where time likely goes**

- **Cold starts:** staging scales to zero (`staging_backend_min_instances = 0`).
  Startup waits for the DB, seeds regions and rulesets, and reconciles all jobs
  before it serves the first request (`app.py:120`).
- **Workspace reads fan out serially.** `GET /workspaces/{id}` →
  `job_headlines` reads each finished blend's pooled CSV and each forecast's
  probabilities CSV from GCS, one at a time (`workspace_summary.py:365`).
  `listing()` loads each owner's *entire* ledger (`ledger.user_steps`, already
  marked ponytail). `GET /summary` re-reads the same CSVs. Every `jobs` event
  re-runs `job_headlines` on the server **and** triggers `refreshSummary()` on
  the client.
- **Database:** one `db-f1-micro` (shared-core) instance serves prod and
  staging. `pool_pre_ping` adds a round trip on every `get_db()`, and the read
  paths open many short connections.
- **Background contention:** #2's downloads run on the same instance as
  requests.

**Plan (measure → cut biggest first)**

1. Instrument: add a `Server-Timing` header and a per-route duration field to
   the existing `log_requests` middleware, then rank routes by p50/p95 from
   Cloud Run logs.
2. Cache headlines on the job row at publication time (like `metrics_cache`),
   so listing a workspace never reads GCS. Until then, `asyncio.gather` the
   reads.
3. Drop the client's `refreshSummary()` on every `jobs` event. Refresh only
   when a job reaches a terminal state, or have the server send the summary in
   the event.
4. Scope `listing()` to the steps the workspace's artifacts actually need.
**Done (2026-11-23):** the biggest known cost in the job list.
- **The cost:** a finished forecast's headline downloaded its whole
  `blended_forecast_probabilities.csv` and parsed every probability into a
  numpy table, synchronously on the event loop. That happened on every
  workspace load, `jobs` event and summary request, just to find the first and
  last issue date. At India 0.25° size (396k rows, 18 MiB) the parse alone
  takes 0.9 s on a laptop, and longer on Cloud Run's 2 vCPU.
- **The fix:** the issue dates are now worked out once per forecast, in a
  thread, from the `time` column alone (0.19 s). They're stored beside its
  outputs through `derived_outputs.load_or_build`
  (`forecast_issue_dates.v1.json`).
- **Item 1 (instrumentation):** `log_requests` already logs per-route
  durations, which is enough for non-streaming routes.
- **Items 2–4** remain for blends and ledgers; re-measure first.

5. Infra (separate PR, cost decision): staging `min_instances = 1` while it's
   actively used, and a DB tier above f1-micro. Check Cloud SQL Query Insights
   first.

---

## 4. Workspace settings in the creation flow (#4)

**Today:** settings are `season` and `study_area`
(`services/workspace_settings.py:24`). Both are optional and fall back to
platform defaults. They can only be edited from the summary strip's chips. The
create form (`routes/workspaces/+page.svelte`) asks for name and description
only.

**Plan**

1. `POST /workspaces` accepts optional `settings: WorkspaceSettings`, parsed at
   the boundary with the existing model and written in the same transaction.
   Then `pixi run generate-api-types`.
2. The create form becomes **Name → Area → Season → (description)**:
   - Area: reuse the `FocusAreaMap` editor. Split `AreaControl` into a
     standalone `AreaEditor` plus the popover wrapper, the way `SeasonForm` is
     already separate from `SeasonControl`.
   - Season: `SeasonForm`, prefilled with the preset for the chosen area's
     country, with the **year shown and editable**.
3. Inside the workspace: label the strip's chips ("Area", "Season") so they
   read as workspace settings. An empty workspace (`WorkspaceStart`) lists
   unset settings as its first starting point.

**Done (2026-11-23):** items 1 and 2.
- **Create form:** "New workspace" opens a form with name, description, an
  optional area (`FocusAreaMap`), and a season choice. The choice is either
  "the usual season here", which follows the area's country, or "set my own
  dates".
- **API:** `POST /workspaces` takes optional `settings`, and the new
  `GET /workspaces/default-season?country=` reuses `default_season`.
- **Shared fields:** the season fields now live in `SeasonFields`, used by
  both the create form and the header's `SeasonForm`.
- **Area optional:** area is skippable, in keeping with settings being
  optional.
- **Item 3, partly done:** the header chips now carry their names (AREA,
  SEASON). Listing unset settings in an empty workspace is not done.

**Open question:** should Area be required at creation, or skippable ("decide
later")? Required keeps every run scored somewhere deliberate. Skippable
respects regions being an optional attribute.

---

## Seen in the screenshot (not in the four issues)

- The workspace is "India 2026" and its forecast was issued 2026, but the
  season chip reads **Summer monsoon 2027**. The default season appears to
  resolve to the *next* season relative to today. Item 4's explicit year
  addresses this for new workspaces. Existing ones need the default rule
  checked.
- The setup line lists models twice ("AIFS v2, aifs_v2, Graphcast 0.25,
  graphcast_0_25"). Display names and ids are being unioned in
  `setup_summary`. **Fixed:** models are now matched on letters and digits only.

## Suggested PR sequence

1. Diagnostics (no code).
2. Retryable, streaming ledger recording + day-level coverage (#2).
3. Split reconciler tick; scheduled reconcile if branch B (#3).
4. Read-path performance: timing, headline cache, summary refresh (#1).
5. Creation flow with settings (#4) + season-year fix.
6. Infra sizing (min instances, DB tier), as a separate cost decision.

## 5. Going back to a product (added 2026-11-23)

**What happened:** every product on the stage was wrapped in
`{#key openRun.key}`. Switching products destroyed the view: its data, map
settings (zoom, layers, tab) and scroll went with it, and coming back fetched
everything again.

**Done:**
- `WorkspaceCanvas` keeps the last `KEPT_VIEWS` (4) product views alive, one
  per product, so nothing chosen in one run carries to another.
- Views not on show stay in place with `content-visibility: hidden` and
  `inert`. Each scrolls on its own, so its scroll position, map canvases and
  charts keep their state and size.
- `keepView` in `canvas-view.ts` decides which views stay. Kept views never
  move in the page, and past the limit the least recently viewed is dropped.
- The cap is there because each view can hold map canvases, and browsers allow
  only a handful of live WebGL contexts.
- **Checked in a local browser:** going back to a benchmark found the same
  view, with its tab and map zoom, and made no API requests.
