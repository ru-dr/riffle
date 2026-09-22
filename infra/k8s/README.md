# k8s

Manifests for staging and prod. Per-service deployments, one shared GPU node
for the explainer, KEDA autoscaling driven by `riffle_queue_depth`, and
Kubernetes Jobs for per-tenant training.

Training jobs run under a bounded pool — that bound is a product invariant,
not a cost tweak.
