# State moves from the flat prod/staging layout into modules/almanac-env.
# Safe to delete once every workspace has run a plan/apply after the refactor.

# --- prod ---

moved {
  from = google_service_account.backend
  to   = module.env["prod"].google_service_account.backend
}

moved {
  from = google_storage_bucket.uploads
  to   = module.env["prod"].google_storage_bucket.uploads
}

moved {
  from = google_storage_bucket.job_outputs
  to   = module.env["prod"].google_storage_bucket.job_outputs
}

moved {
  from = google_storage_bucket_iam_member.backend_uploads
  to   = module.env["prod"].google_storage_bucket_iam_member.backend_uploads
}

moved {
  from = google_storage_bucket_iam_member.backend_reads_data
  to   = module.env["prod"].google_storage_bucket_iam_member.backend_reads_data
}

moved {
  from = google_storage_bucket_iam_member.backend_reads_outputs
  to   = module.env["prod"].google_storage_bucket_iam_member.backend_reads_outputs
}

moved {
  from = google_service_account_iam_member.backend_signs_urls
  to   = module.env["prod"].google_service_account_iam_member.backend_signs_urls
}

moved {
  from = google_sql_database.almanac
  to   = module.env["prod"].google_sql_database.db
}

moved {
  from = google_sql_user.backend
  to   = module.env["prod"].google_sql_user.backend
}

moved {
  from = google_project_iam_member.backend_sql_client
  to   = module.env["prod"].google_project_iam_member.backend_sql_client
}

moved {
  from = google_secret_manager_secret.db_password
  to   = module.env["prod"].google_secret_manager_secret.db_password
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_db_password
  to   = module.env["prod"].google_secret_manager_secret_iam_member.backend_reads_db_password
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_globus_id
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["globus_client_id"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_globus_secret
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["globus_client_secret"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_credential_encryption_key
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["credential_encryption_key"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_llm_api_key
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["llm_api_key"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_chat_figure_signing_secret
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["chat_figure_signing_secret"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_modal_token_id
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["modal_token_id"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_reads_modal_token_secret
  to   = module.env["prod"].google_secret_manager_secret_iam_member.reads["modal_token_secret"]
}

moved {
  from = google_cloud_run_v2_service.backend
  to   = module.env["prod"].google_cloud_run_v2_service.backend
}

moved {
  from = google_cloud_run_v2_service_iam_member.backend_public
  to   = module.env["prod"].google_cloud_run_v2_service_iam_member.public
}

moved {
  from = google_cloud_run_v2_job.migrate
  to   = module.env["prod"].google_cloud_run_v2_job.migrate
}

moved {
  from = google_storage_bucket_iam_member.worker_reads_uploads
  to   = module.env["prod"].google_storage_bucket_iam_member.worker_reads_uploads
}

moved {
  from = google_storage_bucket_iam_member.worker_writes_outputs
  to   = module.env["prod"].google_storage_bucket_iam_member.worker_writes_outputs
}

moved {
  from = google_project_iam_member.backend_logging_viewer
  to   = module.env["prod"].google_project_iam_member.backend_logging_viewer
}

moved {
  from = google_project_iam_member.backend_run_developer
  to   = module.env["prod"].google_project_iam_member.backend_run_developer
}

moved {
  from = google_service_account_iam_member.backend_acts_as_batch_worker
  to   = module.env["prod"].google_service_account_iam_member.backend_acts_as_batch_worker
}

moved {
  from = google_service_account_iam_member.ci_acts_as_backend
  to   = module.env["prod"].google_service_account_iam_member.ci_acts_as_backend
}

# --- staging ---

moved {
  from = google_service_account.backend_staging
  to   = module.env["staging"].google_service_account.backend
}

moved {
  from = google_storage_bucket.uploads_staging
  to   = module.env["staging"].google_storage_bucket.uploads
}

moved {
  from = google_storage_bucket.job_outputs_staging
  to   = module.env["staging"].google_storage_bucket.job_outputs
}

moved {
  from = google_storage_bucket_iam_member.backend_staging_uploads
  to   = module.env["staging"].google_storage_bucket_iam_member.backend_uploads
}

moved {
  from = google_storage_bucket_iam_member.backend_staging_reads_data
  to   = module.env["staging"].google_storage_bucket_iam_member.backend_reads_data
}

moved {
  from = google_storage_bucket_iam_member.backend_staging_reads_outputs
  to   = module.env["staging"].google_storage_bucket_iam_member.backend_reads_outputs
}

moved {
  from = google_service_account_iam_member.backend_staging_signs_urls
  to   = module.env["staging"].google_service_account_iam_member.backend_signs_urls
}

moved {
  from = google_sql_database.almanac_staging
  to   = module.env["staging"].google_sql_database.db
}

moved {
  from = google_sql_user.backend_staging
  to   = module.env["staging"].google_sql_user.backend
}

moved {
  from = google_project_iam_member.backend_staging_sql_client
  to   = module.env["staging"].google_project_iam_member.backend_sql_client
}

moved {
  from = google_secret_manager_secret.staging_db_password
  to   = module.env["staging"].google_secret_manager_secret.db_password
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_db_password
  to   = module.env["staging"].google_secret_manager_secret_iam_member.backend_reads_db_password
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_globus_id
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["globus_client_id"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_globus_secret
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["globus_client_secret"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_credential_encryption_key
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["credential_encryption_key"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_llm_api_key
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["llm_api_key"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_chat_figure_signing_secret
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["chat_figure_signing_secret"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_modal_token_id
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["modal_token_id"]
}

moved {
  from = google_secret_manager_secret_iam_member.backend_staging_reads_modal_token_secret
  to   = module.env["staging"].google_secret_manager_secret_iam_member.reads["modal_token_secret"]
}

moved {
  from = google_cloud_run_v2_service.backend_staging
  to   = module.env["staging"].google_cloud_run_v2_service.backend
}

moved {
  from = google_cloud_run_v2_service_iam_member.backend_staging_public
  to   = module.env["staging"].google_cloud_run_v2_service_iam_member.public
}

moved {
  from = google_cloud_run_v2_job.migrate_staging
  to   = module.env["staging"].google_cloud_run_v2_job.migrate
}

moved {
  from = google_storage_bucket_iam_member.worker_reads_uploads_staging
  to   = module.env["staging"].google_storage_bucket_iam_member.worker_reads_uploads
}

moved {
  from = google_storage_bucket_iam_member.worker_writes_outputs_staging
  to   = module.env["staging"].google_storage_bucket_iam_member.worker_writes_outputs
}

moved {
  from = google_project_iam_member.backend_staging_logging_viewer
  to   = module.env["staging"].google_project_iam_member.backend_logging_viewer
}

moved {
  from = google_project_iam_member.backend_staging_run_developer
  to   = module.env["staging"].google_project_iam_member.backend_run_developer
}

moved {
  from = google_service_account_iam_member.backend_staging_acts_as_batch_worker
  to   = module.env["staging"].google_service_account_iam_member.backend_acts_as_batch_worker
}

moved {
  from = google_service_account_iam_member.ci_acts_as_backend_staging
  to   = module.env["staging"].google_service_account_iam_member.ci_acts_as_backend
}
