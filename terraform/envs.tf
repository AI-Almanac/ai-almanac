# ---------------------------------------------------------------------------
# Environments — prod and staging are two instances of the same module.
# Add env vars, buckets, or secret wiring in modules/almanac-env once and both
# environments pick it up. Shared resources (SQL instance, data bucket,
# secrets, LB, Artifact Registry) stay in this root module.
# ---------------------------------------------------------------------------

locals {
  staging_db_password = var.staging_db_password != "" ? var.staging_db_password : var.db_password

  shared_secrets = {
    globus_client_id = {
      id        = google_secret_manager_secret.globus_client_id.id
      secret_id = google_secret_manager_secret.globus_client_id.secret_id
    }
    globus_client_secret = {
      id        = google_secret_manager_secret.globus_client_secret.id
      secret_id = google_secret_manager_secret.globus_client_secret.secret_id
    }
    credential_encryption_key = {
      id        = google_secret_manager_secret.credential_encryption_key.id
      secret_id = google_secret_manager_secret.credential_encryption_key.secret_id
    }
    llm_api_key = {
      id        = google_secret_manager_secret.llm_api_key.id
      secret_id = google_secret_manager_secret.llm_api_key.secret_id
    }
    chat_figure_signing_secret = {
      id        = google_secret_manager_secret.chat_figure_signing_secret.id
      secret_id = google_secret_manager_secret.chat_figure_signing_secret.secret_id
    }
    modal_token_id = {
      id        = google_secret_manager_secret.modal_token_id.id
      secret_id = google_secret_manager_secret.modal_token_id.secret_id
    }
    modal_token_secret = {
      id        = google_secret_manager_secret.modal_token_secret.id
      secret_id = google_secret_manager_secret.modal_token_secret.secret_id
    }
  }

  # Obs/model data directories, identical in both envs. A list so the
  # container env order is stable — append new entries at the end.
  data_dir_envs = [
    { name = "ETHIOPIA_OBS_DIR", value = var.ethiopia_obs_dir },
    { name = "IMD_2P0_OBS_DIR", value = var.imd_2p0_obs_dir },
    { name = "INDIA_AIFS_MODEL_DIR", value = var.india_aifs_model_dir },
    { name = "INDIA_AIFS_DAILY_MODEL_DIR", value = var.india_aifs_daily_model_dir },
    { name = "INDIA_FUXI_MODEL_DIR", value = var.india_fuxi_model_dir },
    { name = "INDIA_FUXI_S2S_MODEL_DIR", value = var.india_fuxi_s2s_model_dir },
    { name = "INDIA_GENCAST_MODEL_DIR", value = var.india_gencast_model_dir },
    { name = "INDIA_GRAPHCAST_MODEL_DIR", value = var.india_graphcast_model_dir },
    { name = "INDIA_IFS_MODEL_DIR", value = var.india_ifs_model_dir },
    { name = "INDIA_NEURALGCM_MODEL_DIR", value = var.india_neuralgcm_model_dir },
    { name = "ETHIOPIA_AIFS_MODEL_DIR", value = var.ethiopia_aifs_model_dir },
    { name = "ETHIOPIA_FUXI_MODEL_DIR", value = var.ethiopia_fuxi_model_dir },
    { name = "ETHIOPIA_GENCAST_MODEL_DIR", value = var.ethiopia_gencast_model_dir },
    { name = "ETHIOPIA_GRAPHCAST_MODEL_DIR", value = var.ethiopia_graphcast_model_dir },
    { name = "ETHIOPIA_AIFS_SINGLE_V2_MODEL_DIR", value = var.ethiopia_aifs_single_v2_model_dir },
    { name = "ETHIOPIA_AIFS_ENS_V2_MODEL_DIR", value = var.ethiopia_aifs_ens_v2_model_dir },
  ]

  env_config = {
    prod = {
      service_name          = "almanac-backend"
      sa_account_id         = "almanac-backend"
      sa_display_name       = "Almanac Web Backend"
      migrate_job_name      = "almanac-migrate"
      database_name         = "almanac"
      sql_user_name         = "almanac-backend"
      db_password           = var.db_password
      db_password_secret_id = "almanac-db-password"
      uploads_bucket_name   = "almanac-uploads-${var.project_id}"
      outputs_bucket_name   = "almanac-job-outputs-${var.project_id}"
      # Prod keeps uploads and outputs until a user deletes them.
      upload_retention_days = null
      output_retention_days = null
      # Keep one instance warm so the frontend never waits on a cold backend.
      min_instances = var.backend_min_instances
      max_instances = var.backend_max_instances
      frontend_url  = var.frontend_url
    }
    staging = {
      service_name          = "almanac-backend-staging"
      sa_account_id         = "almanac-backend-staging"
      sa_display_name       = "Almanac Web Backend (Staging)"
      migrate_job_name      = "almanac-migrate-staging"
      database_name         = "almanac_staging"
      sql_user_name         = "almanac-backend-staging"
      db_password           = local.staging_db_password
      db_password_secret_id = "almanac-staging-db-password"
      uploads_bucket_name   = "almanac-uploads-staging-${var.project_id}"
      outputs_bucket_name   = "almanac-job-outputs-staging-${var.project_id}"
      upload_retention_days = var.staging_upload_retention_days
      output_retention_days = var.staging_job_output_retention_days
      # Staging tolerates cold starts; default to scaling all the way to zero.
      min_instances = var.staging_backend_min_instances
      max_instances = var.staging_backend_max_instances
      frontend_url  = var.staging_frontend_url
    }
  }
}

module "env" {
  source   = "./modules/almanac-env"
  for_each = local.env_config

  project_id = var.project_id
  region     = var.region

  service_name          = each.value.service_name
  sa_account_id         = each.value.sa_account_id
  sa_display_name       = each.value.sa_display_name
  migrate_job_name      = each.value.migrate_job_name
  database_name         = each.value.database_name
  sql_user_name         = each.value.sql_user_name
  db_password           = each.value.db_password
  db_password_secret_id = each.value.db_password_secret_id
  uploads_bucket_name   = each.value.uploads_bucket_name
  outputs_bucket_name   = each.value.outputs_bucket_name
  upload_retention_days = each.value.upload_retention_days
  output_retention_days = each.value.output_retention_days
  min_instances         = each.value.min_instances
  max_instances         = each.value.max_instances
  frontend_url          = each.value.frontend_url

  sql_instance_name       = google_sql_database_instance.almanac.name
  sql_connection_name     = google_sql_database_instance.almanac.connection_name
  data_bucket_name        = google_storage_bucket.data.name
  image                   = local.app_image
  romp_image              = local.romp_image
  job_runner              = var.job_runner
  batch_worker_email      = google_service_account.batch_worker.email
  batch_worker_sa_name    = google_service_account.batch_worker.name
  ci_sa_email             = google_service_account.ci.email
  admin_emails            = var.admin_emails
  admin_subjects          = var.admin_subjects
  llm_base_url            = var.llm_base_url
  llm_model               = var.llm_model
  enable_run_code         = var.enable_run_code
  enable_run_code_sandbox = var.enable_run_code_sandbox
  shared_secrets          = local.shared_secrets
  data_dir_envs           = local.data_dir_envs
}

output "backend_url" {
  value       = module.env["prod"].service_url
  description = "Prod Cloud Run URL (serves SPA + API). Public traffic enters via the shared LB."
}

output "staging_backend_url" {
  value       = module.env["staging"].service_url
  description = "Staging Cloud Run URL (serves SPA + API). Public traffic enters via the shared LB."
}

output "uploads_bucket_name" {
  value = module.env["prod"].uploads_bucket_name
}

output "job_outputs_bucket_name" {
  value = module.env["prod"].job_outputs_bucket_name
}

output "staging_uploads_bucket_name" {
  value = module.env["staging"].uploads_bucket_name
}

output "staging_job_outputs_bucket_name" {
  value = module.env["staging"].job_outputs_bucket_name
}
