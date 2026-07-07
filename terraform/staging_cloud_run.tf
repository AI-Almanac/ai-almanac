# Staging is a single-image service (FastAPI + bundled SPA) reached through the
# shared load balancer in load_balancer.tf — domain mappings strip the
# Authorization header and break Globus auth.

resource "google_cloud_run_v2_service" "backend_staging" {
  name                = "almanac-backend-staging"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  template {
    service_account = google_service_account.backend_staging.email

    # Staging tolerates cold starts; default to scaling all the way to zero.
    scaling {
      min_instance_count = var.staging_backend_min_instances
      max_instance_count = var.staging_backend_max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.almanac.connection_name]
      }
    }

    containers {
      image = local.app_image

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
        startup_cpu_boost = true
      }

      ports {
        container_port = 8765
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "DATABASE_URL"
        value = "postgresql+psycopg://almanac-backend-staging@/almanac_staging?host=/cloudsql/${google_sql_database_instance.almanac.connection_name}"
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.staging_db_password.secret_id
            version = "latest"
          }
        }
      }

      # Migrations run as a dedicated job (see migrate.tf), never on cold start.
      env {
        name  = "AUTO_MIGRATE"
        value = "false"
      }

      # Shared deployment with app-level Globus token validation (see prod).
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

      env {
        name  = "GCS_DATA_BUCKET"
        value = google_storage_bucket.data.name
      }
      env {
        name  = "GCS_UPLOADS_BUCKET"
        value = google_storage_bucket.uploads_staging.name
      }
      env {
        name  = "GCS_OUTPUTS_BUCKET"
        value = google_storage_bucket.job_outputs_staging.name
      }

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

      env {
        name  = "FRONTEND_URL"
        value = var.staging_frontend_url
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
      env {
        name  = "ETHIOPIA_AIFS_SINGLE_V2_MODEL_DIR"
        value = var.ethiopia_aifs_single_v2_model_dir
      }
      env {
        name  = "ETHIOPIA_AIFS_ENS_V2_MODEL_DIR"
        value = var.ethiopia_aifs_ens_v2_model_dir
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.backend_staging_reads_globus_id,
    google_secret_manager_secret_iam_member.backend_staging_reads_globus_secret,
    google_secret_manager_secret_iam_member.backend_staging_reads_db_password,
    google_secret_manager_secret_iam_member.backend_staging_reads_llm_api_key,
    google_secret_manager_secret_iam_member.backend_staging_reads_chat_figure_signing_secret,
    google_secret_manager_secret_iam_member.backend_staging_reads_modal_token_id,
    google_secret_manager_secret_iam_member.backend_staging_reads_modal_token_secret,
    google_secret_manager_secret_iam_member.backend_staging_reads_credential_encryption_key,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "backend_staging_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend_staging.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "staging_backend_url" {
  value       = google_cloud_run_v2_service.backend_staging.uri
  description = "Staging Cloud Run URL (serves SPA + API). Public traffic enters via the shared LB."
}
