# memorystore

Redis (diagram: "Redis · dedupe keys · cache · sessions"): intake's delivery claims, caches and
the dashboard's sessions. Everything in it is short-lived and has an expiry.

| Setting | Why |
| --- | --- |
| Private services access | Private IP from the network module's range; reachable only inside the VPC |
| AUTH on | Clients send the AUTH string, kept in Secret Manager (phase 7) and loaded into pods by External Secrets |
| TLS (`SERVER_AUTHENTICATION`) | Traffic is encrypted; clients check the server against `server_ca_cert` |
| `tier` input | `BASIC` in staging; `STANDARD_HA` in prod for a replica in a second zone with automatic failover |
| No persistence | Losing Redis costs at most a duplicate score, which the scorer's unique `delivery_id` stops; intake keeps publishing without it (fail open) |
| `maxmemory-policy = volatile-lru` | When full, evicts the least recently used key with an expiry; every Riffle key has one |
| Sunday 05:00 UTC maintenance | Inside the weekend window the cluster and database use |

| Input | |
| --- | --- |
| `project_id`, `region`, `name` | Where, and the instance name |
| `network_id` | From the network module (create after its private services access) |
| `tier`, `memory_size_gb`, `redis_version` | Size and version (`REDIS_7_2`, 1 GB by default) |
| `deletion_protection` | Required, so each environment decides |

| Output | Used by |
| --- | --- |
| `host`, `port`, `server_ca_cert` | Service settings (not secret) |
| `auth_string` (sensitive) | Secret Manager, phase 7 |

The AUTH string is in Terraform's state, like any value Terraform reads. The state bucket is
private and versioned; only the people who run Terraform can read it.
