variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "name" {
  description = "Prefix of the IP and policy names."
  type        = string
}

variable "github_hook_ranges" {
  description = "GitHub's webhook sender ranges, from the hooks list of https://api.github.com/meta. Check it now and then: GitHub adds ranges."
  type        = list(string)

  validation {
    condition     = length(var.github_hook_ranges) >= 1 && length(var.github_hook_ranges) <= 10
    error_message = "github_hook_ranges must have 1 to 10 entries (Cloud Armor's limit for one rule)."
  }

  validation {
    condition     = alltrue([for r in var.github_hook_ranges : can(cidrhost(r, 0))])
    error_message = "Every github_hook_ranges entry must be a CIDR range."
  }
}

variable "rate_limit_per_minute" {
  description = "Requests a minute one IP may send (GitHub's IPs are not limited)."
  type        = number
  default     = 300
}

variable "waf_preview" {
  description = "Run the SQL injection and XSS rules in preview: log what they would block, block nothing. Turn off once the logs show no false positives."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels on the IP address."
  type        = map(string)
  default     = {}
}
