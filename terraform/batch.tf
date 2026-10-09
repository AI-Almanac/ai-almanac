# ---------------------------------------------------------------------------
# Cloud Batch — ROMP compute jobs
# Terraform provisions the worker service account and permissions.
# Actual job submission happens in Python (batch_runner.py) via the
# Google Cloud Batch SDK — no job template resource needed here.
# Per-env grants (uploads/outputs access, backend actAs, CI actAs) live in
# modules/almanac-env.
# ---------------------------------------------------------------------------

resource "google_service_account" "batch_worker" {
  account_id   = "almanac-batch-worker"
  display_name = "Almanac Batch Worker (ROMP jobs)"
}

# Read obs + model data from the shared data bucket
resource "google_storage_bucket_iam_member" "worker_reads_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.batch_worker.email}"
}

# Modal jobs cache blend intermediates and season forecasts in the data bucket.
# Writes are scoped to those prefixes so a job can never modify shared datasets.
locals {
  worker_cache_prefixes = ["blend-intermediates/", "season-forecasts/"]
}

resource "google_storage_bucket_iam_member" "worker_writes_data_caches" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.batch_worker.email}"

  condition {
    title       = "worker-cache-prefixes"
    description = "Only the blend and season-forecast cache prefixes"
    expression = join(" || ", [
      for prefix in local.worker_cache_prefixes :
      "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.data.name}/objects/${prefix}\")"
    ])
  }
}

# Allow CI to deploy new image revisions to Cloud Run
resource "google_project_iam_member" "ci_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

output "batch_worker_service_account" {
  value = google_service_account.batch_worker.email
}
