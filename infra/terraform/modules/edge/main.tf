# The edge (diagram: "Edge · WAF · Load balancer"). The load balancer itself is created in
# the cluster by the Gateway API (phase 12); it uses this fixed IP and this Cloud Armor policy.
#
# Cloud Armor checks rules from the lowest priority number up and stops at the first match:
#   1000  GitHub's webhook IP ranges       -> allow (webhooks never hit the limits below)
#   1100  anyone else calling /webhook     -> 403
#   1500  SQL injection patterns           -> 403 (preview: logged only, until tuned)
#   1600  cross-site scripting patterns    -> 403 (preview: logged only, until tuned)
#   2000  more than N requests a minute    -> 429 for that IP
#   default                                -> allow

resource "google_compute_global_address" "ingress" {
  project    = var.project_id
  name       = "${var.name}-ingress"
  ip_version = "IPV4"
  labels     = var.labels
}

resource "google_compute_security_policy" "edge" {
  project     = var.project_id
  name        = "${var.name}-edge"
  description = "WAF in front of the Riffle load balancer"
  type        = "CLOUD_ARMOR"

  rule {
    priority    = 1000
    action      = "allow"
    description = "GitHub's webhook senders (api.github.com/meta, hooks)"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = var.github_hook_ranges
      }
    }
  }

  rule {
    priority    = 1100
    action      = "deny(403)"
    description = "Only GitHub may call /webhook"
    match {
      expr {
        expression = "request.path.startsWith('/webhook')"
      }
    }
  }

  rule {
    priority    = 1500
    action      = "deny(403)"
    description = "SQL injection (OWASP rules)"
    preview     = var.waf_preview
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
      }
    }
  }

  rule {
    priority    = 1600
    action      = "deny(403)"
    description = "Cross-site scripting (OWASP rules)"
    preview     = var.waf_preview
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
      }
    }
  }

  rule {
    priority    = 2000
    action      = "throttle"
    description = "At most ${var.rate_limit_per_minute} requests a minute per IP"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = var.rate_limit_per_minute
        interval_sec = 60
      }
    }
  }

  rule {
    priority    = 2147483647
    action      = "allow"
    description = "Default"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}
