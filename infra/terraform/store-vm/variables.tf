variable "project" {
  description = "GCP project that holds everything below."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "name" {
  description = "Name of the VM, its service account, address and firewall rules."
  type        = string
  default     = "riffle-data"
}

variable "bucket" {
  description = "Bucket for the mined tables (datasets live only here, ADR 0015) and the DVC remote."
  type        = string
}

variable "bucket_location" {
  type    = string
  default = "US"
}

variable "machine_type" {
  description = "The store, Loki, Grafana, Airflow and its Postgres share the VM; Airflow's task processes need the most (16 GB for 12 at once with room to spare). e2-small is enough for the store alone, e2-standard-2 for the store with Loki."
  type        = string
  default     = "e2-standard-4"
}

variable "disk_gb" {
  description = "Boot disk. Logs, traces, health files, the SQLite index and Loki's chunks live here; datasets do not."
  type        = number
  default     = 50
}

variable "domain" {
  description = "Name that points at the VM's address. Blank uses <address>.sslip.io, which needs no DNS."
  type        = string
  default     = ""
}

variable "repo_url" {
  type    = string
  default = "https://github.com/ru-dr/riffle.git"
}

variable "repo_ref" {
  description = "Branch or tag the VM runs. Change it and run `sudo google_metadata_script_runner startup` on the VM (README)."
  type        = string
  default     = "main"
}

variable "repo_token_secret" {
  description = "Secret Manager secret that holds a read-only token for a private repo. Blank for a public repo."
  type        = string
  default     = ""
}

variable "enable_logs" {
  description = "Loki and Grafana (infra/compose.logging.yaml) beside the store, which then pushes the miners' traces to Loki (ADR 0017)."
  type        = bool
  default     = true
}

variable "enable_airflow" {
  description = "Airflow with the riffle_miner DAG (ADR 0013, ADR 0017)."
  type        = bool
  default     = true
}

variable "airflow_users" {
  description = "Airflow logins as name:role, e.g. [\"alice:admin\", \"bob:viewer\"]. Passwords are made on the VM."
  type        = list(string)
  default     = ["lead:admin"]
}
