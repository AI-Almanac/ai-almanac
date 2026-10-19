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
   traffic that runs a different image — or, given `<sha>`, the revision
   built from that commit (revision images are tagged with the commit SHA).
2. Routes 100% of traffic to it. This is the user-visible fix and lands
   within a minute.
3. Checks that commit out in a temporary git worktree and runs
   `modal deploy` for each app into the matching Modal environment
   (`main` for prod, `staging` for staging). Expect a few minutes for the
   pixi environment install; until it finishes, the old backend is calling
   the new Modal apps.
4. Smokes `/ready` until it returns 200.

It asks for confirmation before changing anything (`--yes` skips). You need
`gcloud` authenticated for `ai-almanac`, `jq`, `pixi`, and a Modal profile
that can deploy to the target environment.

If the Modal step fails, traffic has already moved: rerun with the same
`<sha>` (it skips the traffic switch when that revision already serves)
or deploy the apps by hand from a checkout of that commit.

Note: a later push to `main`/`develop` deploys a new revision with 100%
traffic again — rolling back does not stop the pipeline. Revert or fix the
offending commit promptly.

## The migration caveat

`rollback.sh` never touches the schema. Migrations run **before** traffic
routing, so a rolled-back revision runs old code against the new schema.
This is why migrations must be backward-compatible one version (see
CLAUDE.md): additive first, destructive changes only in a later release
once no deployed revision reads the old shape. If a migration itself is
the failure, do not roll traffic back blindly — check whether the old code
can read the current schema first.

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
