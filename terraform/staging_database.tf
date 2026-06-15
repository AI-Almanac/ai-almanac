locals {
  staging_db_password = var.staging_db_password != "" ? var.staging_db_password : var.db_password
}

resource "google_sql_database" "almanac_staging" {
  name     = "almanac_staging"
  instance = google_sql_database_instance.almanac.name
}

resource "google_sql_user" "backend_staging" {
  name     = "almanac-backend-staging"
  instance = google_sql_database_instance.almanac.name
  password = local.staging_db_password
}

resource "google_project_iam_member" "backend_staging_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend_staging.email}"
}
