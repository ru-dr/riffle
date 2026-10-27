variable "project_id" {
  description = "GCP project that holds everything in this configuration."
  type        = string
}

variable "region" {
  description = "Region for the bucket, BigQuery datasets and Artifact Registry. Keep them together: BigQuery external tables need the dataset and bucket in the same location."
  type        = string
  default     = "us-east4"
}

variable "raw_bucket_name" {
  description = "Globally unique name of the raw-layer bucket. Set in terraform.tfvars; prefixing it with the project id keeps it unique."
  type        = string
}

variable "raw_retention_days" {
  description = "Every object version in the raw bucket is protected from permanent deletion for this many days. With versioning on, the live object can still be replaced or deleted; the old version stays recoverable. The policy is left unlocked so it can still be shortened; lock it once the layout is settled."
  type        = number
  default     = 30
}

variable "billing_account_id" {
  description = "Billing account id (the part after billingAccounts/). Kept in terraform.tfvars, which is gitignored."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly budget for the project, in US dollars. Alerts fire at 50, 90 and 100 percent of actual spend."
  type        = number
  default     = 20
}
