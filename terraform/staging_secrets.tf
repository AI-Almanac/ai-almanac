resource "google_secret_manager_secret" "staging_db_password" {
  secret_id = "almanac-staging-db-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_db_password" {
  secret_id = google_secret_manager_secret.staging_db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_globus_id" {
  secret_id = google_secret_manager_secret.globus_client_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_globus_secret" {
  secret_id = google_secret_manager_secret.globus_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_llm_api_key" {
  secret_id = google_secret_manager_secret.llm_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_chat_figure_signing_secret" {
  secret_id = google_secret_manager_secret.chat_figure_signing_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_modal_token_id" {
  secret_id = google_secret_manager_secret.modal_token_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_staging_reads_modal_token_secret" {
  secret_id = google_secret_manager_secret.modal_token_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_staging.email}"
}
