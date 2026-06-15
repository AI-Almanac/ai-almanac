terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    # bucket is passed at init time:
    #   terraform init -backend-config="bucket=YOUR_BUCKET_NAME"
    # or via a backend config file:
    #   terraform init -backend-config=backend.hcl
    prefix = "web"
  }
}
