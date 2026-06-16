# ---------------------------------------------------------------------------
# Cloud SQL — PostgreSQL
# Cloud Run connects via the built-in Cloud SQL Auth Proxy (no VPC needed).
# Connection string passed to backend as DATABASE_URL env var.
# ---------------------------------------------------------------------------

resource "google_sql_database_instance" "almanac" {
  name             = "almanac-db"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier    = var.db_tier  # e.g. "db-f1-micro" for dev, "db-g1-small" for prod
    edition = "ENTERPRISE"  # ENTERPRISE_PLUS is the new default but requires different tier names

    backup_configuration {
      enabled    = true
      start_time = "03:00"
    }

    ip_configuration {
      # Public IP with IAM-based auth via Cloud SQL Auth Proxy.
      # No VPC required — Cloud Run connects through the proxy socket.
      ipv4_enabled = true
    }

    insights_config {
      query_insights_enabled = true
    }
  }

  deletion_protection = true
}

resource "google_sql_database" "almanac" {
  name     = "almanac"
  instance = google_sql_database_instance.almanac.name
}

resource "google_sql_user" "backend" {
  name     = "almanac-backend"
  instance = google_sql_database_instance.almanac.name
  password = var.db_password  # set in terraform.tfvars (gitignored)
}

# Allow the backend service account to connect via Cloud SQL Auth Proxy
resource "google_project_iam_member" "backend_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

output "cloud_sql_connection_name" {
  value       = google_sql_database_instance.almanac.connection_name
  description = "Used in Cloud Run cloud_sql_instances annotation and DATABASE_URL"
}
