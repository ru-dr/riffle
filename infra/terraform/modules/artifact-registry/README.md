# artifact-registry

The container registry (diagram: "Container registry"). CI pushes images; the cluster's
nodes pull them (the node service account has `roles/artifactregistry.reader`, phase 3).

| Setting | Why |
| --- | --- |
| Docker format, in the cluster's region | Pulls stay in the region: fast, no transfer cost |
| Immutable tags | A tag always points at the same image, so what was tested is what runs |
| Keep the 20 newest images per service | Room to roll back |
| Delete untagged images older than 30 days | Leftovers from rebuilds do not pile up |

| Output | Used by |
| --- | --- |
| `url` | Image names: `<url>/<service>:<tag>` (CI and Helm values) |
| `repository` | Push permission for CI (github-oidc module) |
