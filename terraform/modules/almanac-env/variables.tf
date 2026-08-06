variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

# --- Names (prod and staging follow different suffix conventions, so each
# name is explicit rather than derived from a single prefix) ---

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
}

variable "sa_account_id" {
  description = "Backend service account id"
  type        = string
}

variable "sa_display_name" {
  type = string
}

variable "migrate_job_name" {
  description = "Cloud Run migrate job name"
  type        = string
}

# --- Database (instance is shared across envs; database/user are per-env) ---

variable "sql_instance_name" {
  type = string
}

variable "sql_connection_name" {
  description = "Cloud SQL instance connection name for the Auth Proxy socket"
  type        = string
}

variable "database_name" {
  type = string
}

variable "sql_user_name" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "db_password_secret_id" {
  description = "Secret Manager secret id holding this env's DB password"
  type        = string
}

# --- Storage (data bucket is shared; uploads/outputs are per-env) ---

variable "data_bucket_name" {
  type = string
}

variable "uploads_bucket_name" {
  type = string
}

variable "outputs_bucket_name" {
  type = string
}

variable "upload_retention_days" {
  description = "Delete uploads after N days; null keeps them indefinitely"
  type        = number
  default     = null
}

variable "output_retention_days" {
  description = "Delete job outputs after N days; null keeps them indefinitely"
  type        = number
  default     = null
}

# --- Service shape ---

variable "image" {
  type = string
}

variable "min_instances" {
  type = number
}

variable "max_instances" {
  type = number
}

variable "cpu" {
  type    = string
  default = "2"
}

variable "memory" {
  type    = string
  default = "4Gi"
}

# --- Shared secrets (created once in the root module) ---
# id feeds IAM bindings, secret_id feeds Cloud Run secret_key_ref.

variable "shared_secrets" {
  description = "Secrets both envs read: globus_client_id, globus_client_secret, credential_encryption_key, llm_api_key, chat_figure_signing_secret, modal_token_id, modal_token_secret"
  type = map(object({
    id        = string
    secret_id = string
  }))
}

# --- App configuration ---

variable "frontend_url" {
  type = string
}

variable "romp_image" {
  type = string
}

variable "job_runner" {
  type = string
}

variable "batch_worker_email" {
  type = string
}

variable "batch_worker_sa_name" {
  description = "Full resource name of the batch worker SA (for actAs bindings)"
  type        = string
}

variable "ci_sa_email" {
  description = "CI service account that deploys this env's Cloud Run revisions"
  type        = string
}

variable "admin_emails" {
  type = string
}

variable "admin_subjects" {
  type = string
}

variable "admin_groups" {
  type = string
}

variable "llm_base_url" {
  type = string
}

variable "llm_model" {
  type = string
}

variable "enable_run_code" {
  type = bool
}

variable "enable_run_code_sandbox" {
  type = bool
}
