# ---------------------------------------------------------------------------
# Workload Identity Federation — keyless GitHub Actions auth.
# Deploy workflows impersonate almanac-ci via OIDC instead of a long-lived
# JSON key. Trust is pinned to the repository *id* (survives renames/moves).
# ---------------------------------------------------------------------------

locals {
  github_repository_id      = "1200599198" # AI-Almanac/ai-almanac
  romp_github_repository_id = "1209562774" # hholb/ROMP
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
  }

  attribute_condition = "assertion.repository_id == \"${local.github_repository_id}\""
}

resource "google_service_account_iam_member" "ci_workload_identity" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${local.github_repository_id}"
}

output "workload_identity_provider" {
  description = "Full provider resource name for google-github-actions/auth"
  value       = google_iam_workload_identity_pool_provider.github.name
}

# hholb/ROMP's image publish workflow. Its own provider accepts tokens only
# from main and release tags, and those tokens can impersonate only
# romp-publisher: the almanac-ci binding above matches almanac's repository id.
resource "google_iam_workload_identity_pool_provider" "romp" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-romp"
  display_name                       = "GitHub OIDC (hholb/ROMP)"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
  }

  attribute_condition = join(" && ", [
    "assertion.repository_id == \"${local.romp_github_repository_id}\"",
    "(assertion.ref == \"refs/heads/main\" || assertion.ref.startsWith(\"refs/tags/v\"))",
  ])
}

resource "google_service_account_iam_member" "romp_publisher_workload_identity" {
  service_account_id = google_service_account.romp_publisher.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${local.romp_github_repository_id}"
}

output "romp_workload_identity_provider" {
  description = "Provider resource name for hholb/ROMP's google-github-actions/auth step"
  value       = google_iam_workload_identity_pool_provider.romp.name
}
