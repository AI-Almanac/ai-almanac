# Rollback runbook

When a deploy fails post-deploy verification (the `verify` job) or a bad
release is discovered later. Rollback is a deliberate human decision — the
AI triage in the workflow summary recommends, you decide.

## Roll traffic back (fast path, ~1 minute)

Cloud Run keeps previous revisions. Point traffic at the last good one:

```bash
# Find the previous revision (staging: almanac-backend-staging)
gcloud run revisions list --service almanac-backend --region us-central1 --limit 5

# Route 100% of traffic to it
gcloud run services update-traffic almanac-backend \
  --region us-central1 --to-revisions <previous-revision>=100
```

Verify: `curl -s https://ai-almanac.org/ready` shows all checks true.

Note: a later push to `main`/`develop` deploys a new revision with 100%
traffic again — rolling back traffic does not stop the pipeline. Revert or
fix the offending commit promptly.

## The migration caveat

Migrations run **before** traffic routing, so a rolled-back revision runs
old code against the new schema. This is why migrations must be
backward-compatible one version (see CLAUDE.md): additive first,
destructive changes only in a later release once no deployed revision
reads the old shape. If a migration itself is the failure, do not roll
traffic back blindly — check whether the old code can read the current
schema first.

## Fix-forward (preferred when the fix is obvious)

Small, verified fix → PR to `develop` → staging soak → merge to `main`.
The verify job re-checks each hop. Use `@claude` on the fix PR for a
fast independent review.

## After any rollback

- Note what the verify job caught (or missed) and tighten
  `scripts/post_deploy_check.sh` thresholds or smoke endpoints if the
  failure mode wasn't covered.
- If the failure was caught by users rather than the pipeline, that's the
  trigger to adopt Sentry + uptime checks (Phase 4).
