# kms

The key-encryption key of ADR 0025 (diagram: "Key vault · per-tenant keys").

How it is used: the tenant workflow makes a data key for each new tenant, asks KMS to wrap
(encrypt) it with `tenant-kek`, and stores only the wrapped copy. A service that writes or
reads a tenant's objects (score log, snapshots, backfill) asks KMS to unwrap it. On uninstall
the tenant's data key is destroyed, which makes every copy of its objects unreadable. The
key-encryption key itself never leaves KMS.

| Setting | Why |
| --- | --- |
| Key ring with a random suffix | Key rings and keys can never be deleted in GCP; the suffix lets staging be rebuilt |
| Rotation every 90 days | New wraps use a new version; old versions stay so older data keys still unwrap |
| Destroyed versions restorable for 30 days | A mistaken destroy can be undone |
| `SOFTWARE` protection | `HSM` is available for prod if a customer needs hardware-backed keys |

Who may wrap and unwrap is set per service in phase 9 (`roles/cloudkms.cryptoKeyEncrypterDecrypter`
on this key only).

| Output | Used by |
| --- | --- |
| `tenant_kek_id` | Service settings and permissions (phase 9) |
| `key_ring` | Reference |
