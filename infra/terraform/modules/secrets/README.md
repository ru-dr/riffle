# secrets

Secret Manager entries (diagram: "Key vault · App key"). Pods never read Secret Manager
directly: External Secrets (phase 12) copies each secret into a Kubernetes Secret in the
namespace that needs it.

| Kind (`set_by`) | How the value gets there |
| --- | --- |
| `hand` | Created empty. Add the value once: `gcloud secrets versions add <id> --data-file=<file>` |
| `terraform` | Terraform writes the value it has (e.g. Redis's AUTH string, a generated session key) |

| Setting | Why |
| --- | --- |
| Id `<name>-<key>` | One project can hold staging and prod side by side |
| Stored in one region | Nothing leaves the installation's region |
| `source` label, `purpose` annotation | The console shows where each value comes from and what it is for |

Values written by Terraform are also in Terraform's state (`secret_data`). Terraform 1.11+
could avoid that with write-only values, but CI runs 1.9. The state bucket is private and
versioned; only the people who run Terraform can read it. Values added by hand never touch
Terraform.

Who may read which secret is set per service in phase 9.

| Output | Used by |
| --- | --- |
| `ids` | External Secrets manifests (phase 12) |
| `set_by_hand` | The list of values still to add after the first apply |
| `resource_ids` | Permissions (phase 9) |
