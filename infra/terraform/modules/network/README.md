# network

The private network the platform runs in (diagram: the cloud boundary and the NAT gateway).

| Resource | Why |
| --- | --- |
| VPC (`<name>`) with one subnet (`<name>-<region>`) | Node addresses, plus the secondary ranges `pods` and `services` that GKE uses |
| Private services access (`<name>-private-services`) | Cloud SQL and Memorystore get private addresses in this range, reachable only from the VPC |
| Cloud Router + Cloud NAT + one static IP (`<name>-egress`) | Nodes have no public IP; every outbound call (GitHub API, check runs) leaves from this one address, which customers can allow-list |

Invariant: nothing in this module has a public address except the egress IP.

| Input | |
| --- | --- |
| `project_id`, `region`, `name` | Where, and the name prefix |
| `subnet_cidr`, `pods_cidr`, `services_cidr` | Node, pod and service ranges; must not overlap |
| `private_services_cidr` | Range for Cloud SQL and Memorystore, /24 or larger |

| Output | Used by |
| --- | --- |
| `network_id`, `network_name`, `subnet_id`, `pods_range_name`, `services_range_name` | GKE |
| `private_services_connection` | Cloud SQL and Memorystore (create them after it) |
| `egress_ip` | Customers' allow-lists |
