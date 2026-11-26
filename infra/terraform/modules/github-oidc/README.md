# github-oidc

GitHub Actions access to GCP without keys (diagram: "CI/CD"). Each workflow run gets a
short-lived token from GitHub; Google checks it against this pool and lets the run act as one
of two accounts for about an hour. Nothing secret is stored in GitHub.

| Piece | What it allows |
| --- | --- |
| Pool + provider `github` | Tokens from `token.actions.githubusercontent.com`, and only from `repository` (checked before anything else) |
| `<name>-ci-images` | Push images to the Artifact Registry repository. Any branch of the repository may use it, so every PR can build and push |
| `<name>-ci-terraform` | `terraform plan` and `apply` with `terraform_roles`. Only runs on `terraform_ref` (main by default) may use it, through the `repo_ref` attribute (`owner/name@refs/heads/main`) |

Why plan is not run on pull requests: refreshing state reads the secret values Terraform
manages, so the account that plans can read secrets. Keeping it to `main` means a PR branch can
never get that power. Pull requests still run `fmt` and `validate`, which need no access.

In a workflow:

```yaml
permissions:
  id-token: write   # lets the run ask GitHub for its token
  contents: read
steps:
  - uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: <output workload_identity_provider>
      service_account: <output ci_images_email or ci_terraform_email>
```

Pool ids stay reserved for 30 days after deletion, so the pool id has a random suffix.

| Output | Used by |
| --- | --- |
| `workload_identity_provider`, `ci_images_email`, `ci_terraform_email` | The `auth` step of GitHub Actions workflows (as repository variables; none of them is secret) |
