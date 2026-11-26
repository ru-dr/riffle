output "argocd_ui" {
  description = "How to open the Argo CD UI (no public address yet)."
  value       = "kubectl -n argocd port-forward svc/argocd-server 8080:443, then https://localhost:8080 (user admin, password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
}

output "settings_keys" {
  description = "Keys of the riffle-platform-settings ConfigMap in every Riffle namespace."
  value       = sort(keys(local.settings))
}
