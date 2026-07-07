# One AI Almanac environment: Cloud Run service + migrate job, per-env
# database/user on the shared SQL instance, uploads/outputs buckets, backend
# service account, and secret access. Shared resources (SQL instance, data
# bucket, the secrets themselves, LB, Artifact Registry) live in the root.

locals {
  database_url = "postgresql+psycopg://${var.sql_user_name}@/${var.database_name}?host=/cloudsql/${var.sql_connection_name}"
}

# --- Service account & storage ---

resource "google_service_account" "backend" {
  account_id   = var.sa_account_id
  display_name = var.sa_display_name
}

resource "google_storage_bucket" "uploads" {
  name          = var.uploads_bucket_name
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  dynamic "lifecycle_rule" {
    for_each = var.upload_retention_days != null ? [var.upload_retention_days] : []
    content {
      condition {
        age        = lifecycle_rule.value
        with_state = "ANY"
      }
      action {
        type = "Delete"
      }
    }
  }

  # Delete incomplete multipart uploads after 7 days
  lifecycle_rule {
    condition {
      age        = 7
      with_state = "ANY"
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }
}

resource "google_storage_bucket" "job_outputs" {
  name          = var.outputs_bucket_name
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  dynamic "lifecycle_rule" {
    for_each = var.output_retention_days != null ? [var.output_retention_days] : []
    content {
      condition {
        age = lifecycle_rule.value
      }
      action {
        type = "Delete"
      }
    }
  }
}

resource "google_storage_bucket_iam_member" "backend_uploads" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_storage_bucket_iam_member" "backend_reads_data" {
  bucket = var.data_bucket_name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.backend.email}"
}

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

# Batch worker reads this env's uploads and writes its job outputs
resource "google_storage_bucket_iam_member" "worker_reads_uploads" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.batch_worker_email}"
}

resource "google_storage_bucket_iam_member" "worker_writes_outputs" {
  bucket = google_storage_bucket.job_outputs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.batch_worker_email}"
}

# Backend launches batch jobs and reads Cloud Logging for failure details
resource "google_project_iam_member" "backend_logging_viewer" {
  project = var.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_project_iam_member" "backend_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_service_account_iam_member" "backend_acts_as_batch_worker" {
  service_account_id = var.batch_worker_sa_name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.backend.email}"
}

# CI needs to act as the service account it deploys revisions for
resource "google_service_account_iam_member" "ci_acts_as_backend" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.ci_sa_email}"
}

# --- Database ---

resource "google_sql_database" "db" {
  name     = var.database_name
  instance = var.sql_instance_name
}

resource "google_sql_user" "backend" {
  name     = var.sql_user_name
  instance = var.sql_instance_name
  password = var.db_password
}

resource "google_project_iam_member" "backend_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# --- Secrets ---

resource "google_secret_manager_secret" "db_password" {
  secret_id = var.db_password_secret_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "backend_reads_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "reads" {
  for_each = var.shared_secrets

  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

# --- Cloud Run service ---

resource "google_cloud_run_v2_service" "backend" {
  name     = var.service_name
  location = var.region
  # The SPA calls the API same-origin; app-level Globus token validation
  # protects all non-health endpoints, so the service stays public.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  # CI deploys image revisions out of band and stamps client metadata,
  # labels, and service-level scaling; don't let Terraform fight it.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      scaling,
      template[0].labels,
      template[0].containers[0].image,
    ]
  }

  template {
    service_account = google_service_account.backend.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    # Connect to Cloud SQL via built-in Auth Proxy socket
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.sql_connection_name]
      }
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        # Faster warm-up on scale-up and revision rollouts.
        startup_cpu_boost = true
      }

      ports {
        container_port = 8765
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Database — password injected from Secret Manager at runtime
      env {
        name  = "DATABASE_URL"
        value = local.database_url
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      # Migrations run as a dedicated job (see below), never on cold start.
      env {
        name  = "AUTO_MIGRATE"
        value = "false"
      }

      # Shared deployment: the backend validates Globus bearer tokens itself,
      # so it stays public with no proxy.
      env {
        name  = "DEPLOYMENT_MODE"
        value = "shared"
      }
      env {
        name  = "AUTH_MODE"
        value = "globus"
      }
      env {
        name  = "ADMIN_EMAILS"
        value = var.admin_emails
      }
      env {
        name  = "ADMIN_SUBJECTS"
        value = var.admin_subjects
      }
      env {
        name = "CREDENTIAL_ENCRYPTION_KEY"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["credential_encryption_key"].secret_id
            version = "latest"
          }
        }
      }

      # GCS bucket names — backend uses these to build GCS paths and signed URLs
      env {
        name  = "GCS_DATA_BUCKET"
        value = var.data_bucket_name
      }
      env {
        name  = "GCS_UPLOADS_BUCKET"
        value = google_storage_bucket.uploads.name
      }
      env {
        name  = "GCS_OUTPUTS_BUCKET"
        value = google_storage_bucket.job_outputs.name
      }

      # Globus auth credentials
      env {
        name = "GLOBUS_CLIENT_ID"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["globus_client_id"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GLOBUS_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["globus_client_secret"].secret_id
            version = "latest"
          }
        }
      }

      # Modal credentials — used by ModalRunner to submit ROMP jobs
      env {
        name = "MODAL_TOKEN_ID"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["modal_token_id"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "MODAL_TOKEN_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["modal_token_secret"].secret_id
            version = "latest"
          }
        }
      }

      # Frontend origin for CORS. Leave empty to allow all origins.
      env {
        name  = "FRONTEND_URL"
        value = var.frontend_url
      }

      env {
        name  = "LLM_BASE_URL"
        value = var.llm_base_url
      }
      env {
        name  = "LLM_MODEL"
        value = var.llm_model
      }
      env {
        name  = "ENABLE_RUN_CODE"
        value = tostring(var.enable_run_code)
      }
      env {
        name  = "ENABLE_RUN_CODE_SANDBOX"
        value = tostring(var.enable_run_code_sandbox)
      }
      env {
        name = "LLM_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["llm_api_key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "CHAT_FIGURE_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.shared_secrets["chat_figure_signing_secret"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ROMP_IMAGE"
        value = var.romp_image
      }

      # Job runner and data config
      env {
        name  = "STORAGE_BACKEND"
        value = "gcs"
      }
      env {
        name  = "JOB_RUNNER"
        value = var.job_runner
      }
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "BATCH_WORKER_SA"
        value = var.batch_worker_email
      }

      dynamic "env" {
        for_each = var.data_dir_envs
        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.reads,
    google_secret_manager_secret_iam_member.backend_reads_db_password,
  ]
}

# Unauthenticated invocation — Globus token validation is handled at the app layer
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Migrations — dedicated Cloud Run job ---
# The backend sets AUTO_MIGRATE=false; CI executes this job before routing
# traffic to a new revision:
#   gcloud run jobs execute <migrate_job_name> --region REGION --wait

resource "google_cloud_run_v2_job" "migrate" {
  name                = var.migrate_job_name
  location            = var.region
  deletion_protection = false

  # CI deploys image revisions out of band; don't let Terraform fight it.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
    ]
  }

  template {
    template {
      service_account = google_service_account.backend.email
      max_retries     = 1

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.sql_connection_name]
        }
      }

      containers {
        image   = var.image
        command = ["ai-almanac", "db", "upgrade"]

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        env {
          name  = "DATABASE_URL"
          value = local.database_url
        }
        env {
          name = "DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_password.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.backend_reads_db_password,
    google_project_iam_member.backend_sql_client,
  ]
}
