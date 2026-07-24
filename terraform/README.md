# Infrastructure

OpenTofu configuration for the GCP deployment (project `ai-almanac`). Use the
`tofu` CLI, not `terraform`.

## Layout

- `envs.tf` — prod and staging, two instances of `modules/almanac-env`.
  Add env vars, buckets, or secret wiring in the module once and both
  environments pick it up. Per-env values (names, scaling, retention) live in
  `local.env_config`. Datasets are not wired here: they are registered at
  runtime through the app's Data Sources page as `gs://` pointers.
- `modules/almanac-env/` — one environment: Cloud Run service + migrate job,
  database/user on the shared SQL instance, uploads/outputs buckets, backend
  service account, IAM, and secret access.
- Shared resources stay in the root: `database.tf` (SQL instance),
  `storage.tf` (data bucket), `secrets.tf` (Secret Manager secrets),
  `load_balancer.tf` (one LB fronting both envs by hostname),
  `artifact_registry.tf` (image repo + CI service account),
  `batch.tf` (ROMP batch worker), `wif.tf` (GitHub Actions keyless auth).
- `moved.tf` — state moves from the pre-module layout; delete once every
  workspace has applied past the refactor.

## Getting started

```bash
cp backend.hcl.example backend.hcl          # state bucket: ai-almanac-tf-state
cp terraform.tfvars.example terraform.tfvars # fill in the two DB passwords
tofu init -backend-config=backend.hcl
tofu plan
```

You need `roles/editor` (or equivalent granular roles) on the `ai-almanac`
project and read/write access to the `ai-almanac-tf-state` GCS bucket.

## Deploys

CI owns image rollouts: pushes to `develop` deploy staging, pushes to `main`
deploy prod (`.github/workflows/deploy-*.yml`), authenticating via Workload
Identity Federation as `almanac-ci@`. Terraform deliberately ignores image,
label, and scaling drift that CI deploys create — `tofu apply` never rolls a
revision unless the config itself changed.

## Manual, out-of-band steps

- **Secret values.** Terraform creates the Secret Manager secrets empty;
  values are populated manually (`gcloud secrets versions add …`, see the
  header of `secrets.tf`). The two DB passwords must match between
  `terraform.tfvars` (which sets them on the SQL users) and their Secret
  Manager secrets (which Cloud Run reads at runtime).
- **DNS.** A records for `ai-almanac.org` and `staging.ai-almanac.org` point
  at the LB IP (`lb_ip` output) and are managed outside Terraform.
- **PyPI Trusted Publisher** for `release.yml` is configured on pypi.org
  against this repo's owner/name.
