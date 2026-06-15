# ---------------------------------------------------------------------------
# Secret Manager
# Secrets are created here; populate values manually after first apply via:
#   gcloud secrets versions add globus-client-id --data-file=<(echo -n "YOUR_ID")
#   gcloud secrets versions add globus-client-secret --data-file=<(echo -n "YOUR_SECRET")
#   gcloud secrets versions add almanac-db-password --data-file=<(echo -n "YOUR_PASSWORD")
#   gcloud secrets versions add llm-api-key --data-file=<(echo -n "YOUR_API_KEY")
#   gcloud secrets versions add chat-figure-signing-secret --data-file=<(echo -n "YOUR_RANDOM_SECRET")
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "globus_client_id" {
  secret_id = "globus-client-id"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "globus_client_secret" {
  secret_id = "globus-client-secret"

  replication {
    auto {}
  }
}

# Allow the backend service account to read these secrets
resource "google_secret_manager_secret_iam_member" "backend_reads_globus_id" {
  secret_id = google_secret_manager_secret.globus_client_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_reads_globus_secret" {
  secret_id = google_secret_manager_secret.globus_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "almanac-db-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "backend_reads_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret" "llm_api_key" {
  secret_id = "llm-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "chat_figure_signing_secret" {
  secret_id = "chat-figure-signing-secret"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "backend_reads_llm_api_key" {
  secret_id = google_secret_manager_secret.llm_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_reads_chat_figure_signing_secret" {
  secret_id = google_secret_manager_secret.chat_figure_signing_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

# ---------------------------------------------------------------------------
# Modal credentials — used by ModalRunner to submit ROMP jobs
# Populate after first apply:
#   gcloud secrets versions add modal-token-id --data-file=<(echo -n "TOKEN_ID")
#   gcloud secrets versions add modal-token-secret --data-file=<(echo -n "TOKEN_SECRET")
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "modal_token_id" {
  secret_id = "modal-token-id"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "modal_token_secret" {
  secret_id = "modal-token-secret"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "backend_reads_modal_token_id" {
  secret_id = google_secret_manager_secret.modal_token_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_reads_modal_token_secret" {
  secret_id = google_secret_manager_secret.modal_token_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}
