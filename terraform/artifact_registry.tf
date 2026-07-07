# ---------------------------------------------------------------------------
# Artifact Registry — Docker repo for almanac service images
#
# Image path convention:
#   us-central1-docker.pkg.dev/PROJECT/almanac/IMAGE:TAG
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "almanac"
  description   = "Docker images for almanac services"
  format        = "DOCKER"
}

# Allow Cloud Run service agents to pull images from this repo
resource "google_artifact_registry_repository_iam_member" "cloud_run_pull" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:service-${data.google_project.project.number}@serverless-robot-prod.iam.gserviceaccount.com"
}

# Backend SAs need AR read to validate images when calling create_job
resource "google_artifact_registry_repository_iam_member" "backend_pull" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${module.env["prod"].backend_sa_email}"
}

resource "google_artifact_registry_repository_iam_member" "backend_staging_pull" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${module.env["staging"].backend_sa_email}"
}

# Cloud Run Jobs pulls images using the job's service account (batch_worker)
resource "google_artifact_registry_repository_iam_member" "batch_worker_pull" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.batch_worker.email}"
}

# Service account for GitHub Actions CI to push images
resource "google_service_account" "ci" {
  account_id   = "almanac-ci"
  display_name = "Almanac CI (GitHub Actions image push)"
}

resource "google_artifact_registry_repository_iam_member" "ci_push" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_service_account_iam_member" "ci_token_creator" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.ci.email}"
}

output "ci_service_account_email" {
  value = google_service_account.ci.email
}

data "google_project" "project" {
  project_id = var.project_id
}

# Convenience locals — default image paths in this project's AR repo.
# cloud_run.tf and batch.tf reference these instead of raw var.*_image.
locals {
  ar_prefix = "${var.region}-docker.pkg.dev/${var.project_id}/almanac"

  app_image  = var.app_image != "" ? var.app_image : "${local.ar_prefix}/ai-almanac-web:latest"
  romp_image = var.romp_image != "" ? var.romp_image : "${local.ar_prefix}/romp:latest"
}
