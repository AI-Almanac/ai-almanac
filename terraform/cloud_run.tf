resource "google_cloud_run_v2_service" "frontend" {
  name                = "almanac-frontend"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  template {
    service_account = google_service_account.frontend.email

    containers {
      image = local.frontend_image

      ports {
        container_port = 3000
      }

      env {
        name  = "BACKEND_URL"
        value = google_cloud_run_v2_service.backend.uri
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "frontend_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.frontend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Custom domain mapping for the frontend
# Uses the v1 API domain mapping which works with v2 services.
# GCP provisions a Google-managed SSL certificate automatically.
#
# After terraform apply, GCP will show the IPs/CNAME to add to your DNS:
#   gcloud beta run domain-mappings describe --domain=YOUR_DOMAIN --region=REGION
# ---------------------------------------------------------------------------
resource "google_cloud_run_domain_mapping" "frontend" {
  count    = var.custom_domain != "" ? 1 : 0
  location = var.region
  name     = var.custom_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.frontend.name
  }
}

resource "google_cloud_run_domain_mapping" "backend" {
  count    = var.api_domain != "" ? 1 : 0
  location = var.region
  name     = var.api_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.backend.name
  }
}

resource "google_cloud_run_v2_service" "backend" {
  name     = "almanac-backend"
  location = var.region
  # Frontend calls the backend directly from the browser (CORS), so it must be public.
  # App-level auth (Globus token validation) protects all non-health endpoints.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  template {
    service_account = google_service_account.backend.email

    # Keep one instance warm so the frontend never waits on a cold backend.
    scaling {
      min_instance_count = var.backend_min_instances
      max_instance_count = var.backend_max_instances
    }

    # Connect to Cloud SQL via built-in Auth Proxy socket
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.almanac.connection_name]
      }
    }

    containers {
      image = local.backend_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        # Faster warm-up on scale-up and revision rollouts.
        startup_cpu_boost = true
      }

      ports {
        container_port = 8000
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Database — password injected from Secret Manager at runtime
      env {
        name  = "DATABASE_URL"
        value = "postgresql+psycopg://almanac-backend@/almanac?host=/cloudsql/${google_sql_database_instance.almanac.connection_name}"
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

      # Migrations run as a dedicated job (see migrate.tf), never on cold start.
      env {
        name  = "AUTO_MIGRATE"
        value = "false"
      }

      # Shared deployment: the backend validates Globus bearer tokens itself
      # (GLOBUS_CLIENT_ID/SECRET below), so it stays public with no proxy.
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
            secret  = google_secret_manager_secret.credential_encryption_key.secret_id
            version = "latest"
          }
        }
      }

      # GCS bucket names — backend uses these to build GCS paths and signed URLs
      env {
        name  = "GCS_DATA_BUCKET"
        value = google_storage_bucket.data.name
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
            secret  = google_secret_manager_secret.globus_client_id.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GLOBUS_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.globus_client_secret.secret_id
            version = "latest"
          }
        }
      }

      # Modal credentials — used by ModalRunner to submit ROMP jobs
      env {
        name = "MODAL_TOKEN_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.modal_token_id.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "MODAL_TOKEN_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.modal_token_secret.secret_id
            version = "latest"
          }
        }
      }

      # Frontend origin for CORS — set var.frontend_url after first deploy
      # to lock down CORS. Leave empty to allow all origins initially.
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
            secret  = google_secret_manager_secret.llm_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "CHAT_FIGURE_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.chat_figure_signing_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ROMP_IMAGE"
        value = local.romp_image
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
        value = google_service_account.batch_worker.email
      }
      env {
        name  = "ETHIOPIA_OBS_DIR"
        value = var.ethiopia_obs_dir
      }
      env {
        name  = "IMD_2P0_OBS_DIR"
        value = var.imd_2p0_obs_dir
      }
      env {
        name  = "INDIA_AIFS_MODEL_DIR"
        value = var.india_aifs_model_dir
      }
      env {
        name  = "INDIA_AIFS_DAILY_MODEL_DIR"
        value = var.india_aifs_daily_model_dir
      }
      env {
        name  = "INDIA_FUXI_MODEL_DIR"
        value = var.india_fuxi_model_dir
      }
      env {
        name  = "INDIA_FUXI_S2S_MODEL_DIR"
        value = var.india_fuxi_s2s_model_dir
      }
      env {
        name  = "INDIA_GENCAST_MODEL_DIR"
        value = var.india_gencast_model_dir
      }
      env {
        name  = "INDIA_GRAPHCAST_MODEL_DIR"
        value = var.india_graphcast_model_dir
      }
      env {
        name  = "INDIA_IFS_MODEL_DIR"
        value = var.india_ifs_model_dir
      }
      env {
        name  = "INDIA_NEURALGCM_MODEL_DIR"
        value = var.india_neuralgcm_model_dir
      }
      env {
        name  = "ETHIOPIA_AIFS_MODEL_DIR"
        value = var.ethiopia_aifs_model_dir
      }
      env {
        name  = "ETHIOPIA_FUXI_MODEL_DIR"
        value = var.ethiopia_fuxi_model_dir
      }
      env {
        name  = "ETHIOPIA_GENCAST_MODEL_DIR"
        value = var.ethiopia_gencast_model_dir
      }
      env {
        name  = "ETHIOPIA_GRAPHCAST_MODEL_DIR"
        value = var.ethiopia_graphcast_model_dir
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.backend_reads_globus_id,
    google_secret_manager_secret_iam_member.backend_reads_globus_secret,
    google_secret_manager_secret_iam_member.backend_reads_db_password,
    google_secret_manager_secret_iam_member.backend_reads_llm_api_key,
    google_secret_manager_secret_iam_member.backend_reads_chat_figure_signing_secret,
    google_secret_manager_secret_iam_member.backend_reads_modal_token_id,
    google_secret_manager_secret_iam_member.backend_reads_modal_token_secret,
    google_secret_manager_secret_iam_member.backend_reads_credential_encryption_key,
  ]
}

# Unauthenticated invocation — Globus token validation is handled at the app layer
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "backend_url" {
  value       = google_cloud_run_v2_service.backend.uri
  description = "Backend Cloud Run URL — use as VITE_API_URL when building the frontend"
}

output "frontend_url_output" {
  value       = google_cloud_run_v2_service.frontend.uri
  description = "Frontend Cloud Run URL — set as var.frontend_url to lock down CORS"
}
