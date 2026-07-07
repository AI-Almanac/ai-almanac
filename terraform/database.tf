# ---------------------------------------------------------------------------
# Cloud SQL — PostgreSQL
# One shared instance; each env gets its own database and user via
# modules/almanac-env. Cloud Run connects via the built-in Auth Proxy socket.
# ---------------------------------------------------------------------------

resource "google_sql_database_instance" "almanac" {
  name             = "almanac-db"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier    = var.db_tier  # e.g. "db-f1-micro" for dev, "db-g1-small" for prod
    edition = "ENTERPRISE" # ENTERPRISE_PLUS is the new default but requires different tier names

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

output "cloud_sql_connection_name" {
  value       = google_sql_database_instance.almanac.connection_name
  description = "Used in Cloud Run cloud_sql_instances annotation and DATABASE_URL"
}
