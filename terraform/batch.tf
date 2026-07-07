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

# Allow CI to deploy new image revisions to Cloud Run
resource "google_project_iam_member" "ci_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

output "batch_worker_service_account" {
  value = google_service_account.batch_worker.email
}
