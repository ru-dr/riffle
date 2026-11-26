# edge

The edge (diagram: "Edge · WAF · Load balancer"). The load balancer itself is created inside
the cluster by the Gateway API (phase 12): it takes this fixed IP, and each backend gets this
Cloud Armor policy through a `GCPBackendPolicy`.

| Rule (checked in this order) | Effect |
| --- | --- |
| 1000 GitHub's webhook ranges | Allowed, and never rate limited |
| 1100 `/webhook` from anyone else | 403: only GitHub can deliver webhooks |
| 1500 SQL injection, 1600 XSS (OWASP rule sets, lowest sensitivity) | 403; in **preview** at first, so they only log what they would block |
| 2000 more than `rate_limit_per_minute` from one IP | 429 for that IP |
| default | Allowed (the dashboard checks its own login) |

| Input | |
| --- | --- |
| `github_hook_ranges` | From the `hooks` list of `https://api.github.com/meta`, at most 10 (one Cloud Armor rule). GitHub adds ranges now and then: compare the list with that page before each apply |
| `rate_limit_per_minute` | 300 by default |
| `waf_preview` | `true` by default; set `false` once the logs show no false positives |

**HTTPS:** there is no domain yet, and a Google-managed certificate needs one. Until then the
load balancer serves HTTP on the fixed IP. When a domain exists, add a Certificate Manager
certificate for it, point its DNS at `ingress_ip`, and add an HTTPS listener to the Gateway.
For testing without buying a domain, a wildcard DNS service such as sslip.io turns the IP into a
hostname (`<ip>.sslip.io`) a certificate can be issued for. GitHub sends webhooks over HTTP
too, signed with the webhook secret, but the payload is then readable in transit, so HTTPS
should come before real customers.

| Output | Used by |
| --- | --- |
| `ingress_ip` | The GitHub App's webhook URL; DNS later |
| `ingress_ip_name` | The Gateway's `spec.addresses` |
| `security_policy_name` | `GCPBackendPolicy` on each backend |
