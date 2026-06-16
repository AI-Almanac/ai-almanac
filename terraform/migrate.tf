# ---------------------------------------------------------------------------
# Database migrations — dedicated Cloud Run jobs
#
# Migrations run here, not in the serving container's startup. The backend
# services set AUTO_MIGRATE=false; CI executes the matching job before routing
# traffic to a new revision:
#
#   gcloud run jobs execute almanac-migrate --region REGION --wait
#   gcloud run jobs execute almanac-migrate-staging --region REGION --wait
#
# Each job reuses its backend's service account (already a Cloud SQL client and
# db-password secret accessor), image, Cloud SQL socket, and DATABASE_URL.
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "migrate" {
  name                = "almanac-migrate"
  location            = var.region
  deletion_protection = false

  # CI deploys image revisions out of band; don't let Terraform fight it.
  lifecycle {
    ignore_changes = [
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
          instances = [google_sql_database_instance.almanac.connection_name]
        }
      }

      containers {
        image   = local.backend_image
        command = ["ai-almanac", "db", "upgrade"]

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

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
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.backend_reads_db_password,
    google_project_iam_member.backend_sql_client,
  ]
}

resource "google_cloud_run_v2_job" "migrate_staging" {
  name                = "almanac-migrate-staging"
  location            = var.region
  deletion_protection = false

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
    ]
  }

  template {
    template {
      service_account = google_service_account.backend_staging.email
      max_retries     = 1

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.almanac.connection_name]
        }
      }

      containers {
        image   = local.backend_image
        command = ["ai-almanac", "db", "upgrade"]

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
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.backend_staging_reads_db_password,
    google_project_iam_member.backend_staging_sql_client,
  ]
}
