resource "google_service_account" "frontend_staging" {
  account_id   = "almanac-frontend-staging"
  display_name = "Almanac Web Frontend (Staging)"
}

resource "google_service_account" "backend_staging" {
  account_id   = "almanac-backend-staging"
  display_name = "Almanac Web Backend (Staging)"
}

resource "google_storage_bucket_iam_member" "backend_staging_uploads" {
  bucket = google_storage_bucket.uploads_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_storage_bucket_iam_member" "backend_staging_reads_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_storage_bucket_iam_member" "backend_staging_reads_outputs" {
  bucket = google_storage_bucket.job_outputs_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_service_account_iam_member" "backend_staging_signs_urls" {
  service_account_id = google_service_account.backend_staging.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_storage_bucket_iam_member" "worker_reads_uploads_staging" {
  bucket = google_storage_bucket.uploads_staging.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.batch_worker.email}"
}

resource "google_storage_bucket_iam_member" "worker_writes_outputs_staging" {
  bucket = google_storage_bucket.job_outputs_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.batch_worker.email}"
}

resource "google_project_iam_member" "backend_staging_logging_viewer" {
  project = var.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_project_iam_member" "backend_staging_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_service_account_iam_member" "backend_staging_acts_as_batch_worker" {
  service_account_id = google_service_account.batch_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_service_account_iam_member" "ci_acts_as_frontend_staging" {
  service_account_id = google_service_account.frontend_staging.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_service_account_iam_member" "ci_acts_as_backend_staging" {
  service_account_id = google_service_account.backend_staging.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci.email}"
}
