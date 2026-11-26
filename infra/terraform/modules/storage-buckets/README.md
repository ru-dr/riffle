# storage-buckets

Cloud Storage buckets (diagram: "Object storage", "Score log"). One generic module: the
environment lists the buckets and their rules, so adding one is a new entry, not new code.

Every bucket gets:

| Setting | Why |
| --- | --- |
| Public access prevention enforced | Can never be made public, even by mistake |
| Uniform bucket-level access | Permissions per bucket only (phase 9), no per-object ACLs |
| Soft delete (7 days by default) | A deleted or overwritten object can be restored |
| Name `<name_prefix>-<key>` | Bucket names are global across GCP; the prefix includes the project and environment |
| `bucket` label | Cost per bucket in billing |
| `force_destroy` input | On in staging (destroy works with objects inside), off in prod |

Per bucket (`buckets` input):

| Field | Effect |
| --- | --- |
| `purpose` | One line saying what is stored there |
| `versioning` | Keep old versions of overwritten objects |
| `nearline_after_days` | Move objects to Nearline (cheaper to keep, dearer to read) |
| `delete_after_days` | Delete objects after this many days |
| `noncurrent_versions_kept` | With versioning: keep this many old versions (old versions are also deleted after 30 days) |

Encryption: Google encrypts every object. Per-tenant encryption (ADR 0025) happens in the
services, with each tenant's data key from KMS (phase 7), before an object is written.

| Output | Used by |
| --- | --- |
| `names`, `urls` | Bucket permissions (phase 9), service settings, Loki and Tempo |
