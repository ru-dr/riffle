# intake (Go)

Webhook front door. Verify signature, dedupe by delivery ID, publish, return
200 inside 10 seconds. **Nothing else** — no database writes, no feature
extraction, no outbound GitHub calls.

Invariant: the same delivery ID must never be published twice.

```bash
go run ./cmd/intake      # :8080
go test ./...
```
