# Rollback runbook

When a deploy fails post-deploy verification (the `verify` job) or a bad
release is discovered later. Rollback is a deliberate human decision — the
AI triage in the workflow summary recommends, you decide.

## Roll back (one command, a few minutes)

Every deploy ships the backend and the Modal apps (`app`, `blending_app`,
`forecasts_app`) together, so a rollback has to move both. `scripts/rollback.sh`
does that:

```bash
scripts/rollback.sh --dry-run prod   # show the target and the commands first
scripts/rollback.sh prod             # or: staging; append <sha> for a specific release
```

It:

1. Picks the newest ready Cloud Run revision older than the one serving
   traffic that was built from a different commit — or, given `<sha>`, the revision
   built from that commit (read from the `commit-sha` label CI puts on each
   revision).
2. Checks that commit out in a temporary git worktree and installs its
   pixi environment (a few minutes when uncached). This happens before any
   traffic moves, so a broken checkout or lockfile stops the rollback early.
3. Routes 100% of traffic to the target revision, then runs `modal deploy`
   for each app into the matching Modal environment (`main` for prod,
   `staging` for staging).
4. Smokes `/ready` until it returns 200.

It refuses to run while a `deploy-<env>.yml` run hasn't finished its `deploy`
job, because that job ends by routing traffic to latest and would undo the
rollback. A run waiting for approval counts; one only soaking in `verify`
doesn't. If `gh` can't answer, check the Actions tab yourself and pass
`--skip-deploy-check`. It asks for confirmation before changing anything
(`--yes` skips). You need `gcloud` authenticated for `ai-almanac`, `jq`,
`pixi`, `gh`, and a Modal profile that can deploy to the target environment.

If a `modal deploy` still fails (e.g. Modal auth), traffic has already
moved: rerun with the same `<sha>` (the traffic switch is a no-op when that
revision already serves) or deploy the apps by hand from a checkout of that
commit.

Note: the rollback pins traffic to one revision. The deploy workflows route
traffic back to the latest revision after every deploy, so the next push to
`main`/`develop` undoes the rollback — it does not stop the pipeline.
Revert or fix the offending commit before pushing again.

A failed deploy can leave Modal ahead of Cloud Run: CI deploys the Modal
apps before migrations and the Cloud Run deploy, so if either of those
fails, the old revision keeps serving while Modal runs the new commit. Don't
run a bare `rollback.sh` then (it would go one release further back).
Resync Modal to the serving release instead:
`scripts/rollback.sh <env> <serving-sha>`, which leaves traffic where it is.

## The migration caveat

`rollback.sh` never touches the schema. Migrations run **before** traffic
routing, so a rolled-back revision runs old code against the new schema.
This is why migrations must be backward-compatible one version (see
CLAUDE.md): additive first, destructive changes only in a later release
once no deployed revision reads the old shape. If a migration itself is
the failure, do not roll traffic back blindly — check whether the old code
can read the current schema first.

The same applies to seeded rows. Packaged regions are re-seeded on every
`GET /regions`, so removing a region from `regions.yaml` and deleting its
row in one release lets old revisions re-insert it mid-rollout (migration
0015's delete was undone this way; 0028 redid it). Drop the region from `regions.yaml`
first; delete its row in a later release, once no deployed revision
packages it.

## Fix-forward (preferred when the fix is obvious)

Small, verified fix → PR to `develop` → staging soak → merge to `main`.
The verify job re-checks each hop. Use `@claude` on the fix PR for a
fast independent review.

## After any rollback

- Note what the verify job caught (or missed) and tighten
  `scripts/post_deploy_check.sh` thresholds or smoke endpoints if the
  failure mode wasn't covered.
- If the failure was caught by users rather than the pipeline or the
  alerts in `terraform/monitoring.tf` (uptime on `/ready`, 5xx ratio,
  Cloud SQL up), add the missing signal there — or adopt Sentry.
