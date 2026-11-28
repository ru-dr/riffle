#!/usr/bin/env bash
# Writes infra/credentials.local.md: every password and key of the staging platform, read from
# Secret Manager and the cluster, in one place. That file is gitignored and readable by you only
# (mode 600); never commit, paste or share it. Re-run it after a secret changes.
#
#   infra/fetch-credentials.sh
#
# Needs gcloud (signed in, project riffle-infra-platform), kubectl (cluster credentials) and the
# team-access roles (infra/terraform/modules/team-access). See infra/terraform/README.md, From your
# laptop. A value that does not exist yet (before the apply, or a hand-filled secret not added)
# is written as "(not available ...)".
set -uo pipefail
cd "$(dirname "$0")"
OUT=credentials.local.md
ENV_PREFIX=${RIFFLE_ENV:-riffle-staging}
umask 077

sm() {   # a Secret Manager secret's latest value
  gcloud secrets versions access latest --secret="$ENV_PREFIX-$1" 2>/dev/null \
    || echo "(not available: no value yet, or you lack access)"
}
k8s() {  # one key of a Kubernetes Secret
  local v
  v=$(kubectl -n "$1" get secret "$2" -o "jsonpath={.data.$3}" 2>/dev/null) && [ -n "$v" ] \
    && echo "$v" | base64 -d || echo "(not available: Secret missing, deleted after the password change, or no cluster access)"
}

WEBHOOK=$(sm github-webhook-secret)
SESSION=$(sm dashboard-session-key)
REDIS=$(sm redis-auth)
GRAFANA=$(sm grafana-admin)
CLIENT=$(sm github-app-client-secret)
PEM=$(sm github-app-private-key)
ARGOCD=$(k8s argocd argocd-initial-admin-secret password)

cat > "$OUT" <<EOF
# Riffle staging credentials (local only, gitignored)

Written by infra/fetch-credentials.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Never commit, paste or share
this file. Each value's source is the truth; re-run the script to refresh.

## Web consoles (no public address: reach them with kubectl port-forward)

| What | Open | User | Password |
| --- | --- | --- | --- |
| Grafana | \`kubectl -n observability port-forward svc/grafana 3000:80\`, then http://localhost:3000 | admin | \`$GRAFANA\` |
| Argo CD | \`kubectl -n argocd port-forward svc/argocd-server 8080:443\`, then https://localhost:8080 | admin | \`$ARGOCD\` |
| MLflow | \`kubectl -n riffle-ml port-forward svc/mlflow 5000:5000\`, then http://localhost:5000 | none | none (only port-forward and allowed pods reach it) |

## Secrets the services use (Secret Manager, copied into each service's Kubernetes Secret)

| Secret Manager id | Used by | Value |
| --- | --- | --- |
| $ENV_PREFIX-github-webhook-secret | intake (checks every webhook); paste it into the GitHub App's webhook settings | \`$WEBHOOK\` |
| $ENV_PREFIX-redis-auth | intake (Memorystore AUTH) | \`$REDIS\` |
| $ENV_PREFIX-dashboard-session-key | app-dashboard (signs session cookies) | \`$SESSION\` |
| $ENV_PREFIX-github-app-client-secret | app-dashboard (GitHub sign-in); added by hand | \`$CLIENT\` |
| $ENV_PREFIX-grafana-admin | Grafana (above) | see Grafana |
| $ENV_PREFIX-github-app-private-key | app-github, pr-miner, tenant-workflow (GitHub App login); added by hand | below |

GitHub App private key:

\`\`\`
$PEM
\`\`\`

## Logins with no password

| What | How you log in |
| --- | --- |
| Postgres (people) | Your Google account (db_people, role riffle_readonly) through infra/k8s/tools/db-proxy.yaml; password = \`gcloud sql generate-login-token\` |
| Postgres (services) | Each service's Google account through the Cloud SQL Proxy sidecar |
| Postgres built-in user postgres | Never given a password: cannot log in |
| Prometheus, metrics, alerts | Google login (Cloud Console, or Grafana's Managed Prometheus data source) |
| Loki, Tempo | Inside the cluster only; read through Grafana |
| GitHub Actions to GCP | One-hour token per run (Workload Identity Federation), no key |
| Tenant data keys | Never readable: wrapped by KMS tenant-kek in tenant_keys (ADR 0028) |
EOF
chmod 600 "$OUT"
echo "wrote infra/$OUT (mode 600, gitignored)"
