# ---------------------------------------------------------------------------
# Secret Manager — secrets shared by prod and staging.
# Per-env DB password secrets and all accessor IAM live in modules/almanac-env.
# Secrets are created here; populate values manually after first apply via:
#   gcloud secrets versions add globus-client-id --data-file=<(echo -n "YOUR_ID")
#   gcloud secrets versions add globus-client-secret --data-file=<(echo -n "YOUR_SECRET")
#   gcloud secrets versions add almanac-db-password --data-file=<(echo -n "YOUR_PASSWORD")
#   gcloud secrets versions add almanac-staging-db-password --data-file=<(echo -n "YOUR_PASSWORD")
#   gcloud secrets versions add llm-api-key --data-file=<(echo -n "YOUR_API_KEY")
#   gcloud secrets versions add chat-figure-signing-secret --data-file=<(echo -n "YOUR_RANDOM_SECRET")
#   gcloud secrets versions add credential-encryption-key --data-file=<(echo -n "$KEY")
#   gcloud secrets versions add modal-token-id --data-file=<(echo -n "TOKEN_ID")
#   gcloud secrets versions add modal-token-secret --data-file=<(echo -n "TOKEN_SECRET")
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

# Encrypts stored user credentials; required in shared mode.
resource "google_secret_manager_secret" "credential_encryption_key" {
  secret_id = "credential-encryption-key"

  replication {
    auto {}
  }
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

# Modal credentials — used by ModalRunner to submit ROMP jobs
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
