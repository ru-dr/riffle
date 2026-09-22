# fixtures

Sample GitHub webhook payloads and a seed tenant, so the pipeline and the
dashboard can be exercised without GitHub.

```bash
make seed                               # load the fixture tenant
make replay FIXTURE=fixtures/pr_opened.json
make train TENANT=fixture               # CPU, minutes
```

Fixtures must validate against `contracts/`. A fixture that drifts from a
schema hides the break it was meant to catch.
