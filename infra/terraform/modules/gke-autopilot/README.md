# gke-autopilot

The Kubernetes cluster (diagram: "Kubernetes cluster"). Autopilot means Google runs the nodes
and bills per pod: there are no node pools here.

| Setting | Why |
| --- | --- |
| Regional (`location` = region) | The control plane and nodes span zones, so one zone failing does not stop Riffle |
| Private nodes, private IP endpoint | No node has a public IP; outbound traffic leaves through Cloud NAT's one IP |
| DNS endpoint open, IAM-checked | People and CI run `kubectl` with their Google login; no IP allow-list to maintain |
| Own node service account (`<name>-nodes`) | Only writes logs and metrics and pulls images, instead of the default account's Editor role |
| Workload Identity | Pods act as Google service accounts (phase 9), never with keys |
| Gateway API (standard channel) | Creates the load balancer for `/webhook` and `/app` (phase 12) |
| Managed Prometheus | Collects the `riffle_*` metrics |
| Cost management | Cost per namespace in billing |
| Weekend maintenance window (UTC) | Upgrades happen when GitHub traffic is lowest |
| `deletion_protection` input | Off in staging (easy destroy), on in prod |

Get credentials once it exists:
`gcloud container clusters get-credentials <name> --region <region> --dns-endpoint`

| Input | |
| --- | --- |
| `project_id`, `region`, `name` | Where, and the cluster name (5–24 characters) |
| `network_id`, `subnet_id`, `pods_range_name`, `services_range_name` | From the network module |
| `release_channel` | `REGULAR` by default |
| `deletion_protection` | Required, so each environment decides |

| Output | Used by |
| --- | --- |
| `name`, `location`, `dns_endpoint` | kubectl, Argo CD bootstrap (phase 12) |
| `workload_pool` | Service account bindings (phase 9) |
| `node_service_account` | Reference |
