# ---------------------------------------------------------------------------
# Service accounts
# ---------------------------------------------------------------------------

resource "google_service_account" "frontend" {
  account_id   = "almanac-frontend"
  display_name = "Almanac Web Frontend"
}

resource "google_service_account" "backend" {
  account_id   = "almanac-backend"
  display_name = "Almanac Web Backend"
}

# ---------------------------------------------------------------------------
# Backend — GCS permissions
# ---------------------------------------------------------------------------

# Read/write user uploads
resource "google_storage_bucket_iam_member" "backend_uploads" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend.email}"
}

# Read pre-loaded obs + model data (for resolving demo dataset paths)
resource "google_storage_bucket_iam_member" "backend_reads_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.backend.email}"
}

# Read benchmark outputs and manage chat-generated figure artifacts stored in
# the same bucket under chat-figures/.
resource "google_storage_bucket_iam_member" "backend_reads_outputs" {
  bucket = google_storage_bucket.job_outputs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend.email}"
}

# Sign GCS URLs on behalf of itself (needed for google.cloud.storage signed_url)
resource "google_service_account_iam_member" "backend_signs_urls" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.backend.email}"
}
