// Every number and claim on this page comes from the repository README, so
// the page and the docs cannot drift. Nothing here is invented: the site has
// no customers, no public star count and no funding, and those sections of
// the reference layout are therefore not reproduced.

export const REPO = "https://github.com/ru-dr/riffle";

export const EVIDENCE = [
  {
    stat: "441.5",
    unit: "%",
    claim: "longer median review time",
    source: "Faros AI, The Acceleration Whiplash",
    detail: "Bugs per developer up 54%. 31% of PRs now merge with no human review at all.",
  },
  {
    stat: "4.6",
    unit: "×",
    claim: "longer wait for first review",
    source: "LinearB, 2026 Benchmarks",
    detail:
      "Across 8.1M PRs. AI-authored changes are accepted within 30 days 32.7% of the time, against 84.4% unassisted.",
  },
  {
    stat: "1 in 5",
    unit: "",
    claim: "reviews now involve an agent",
    source: "GitHub",
    detail: "The bottleneck moved from writing code to verifying it, and reviewer capacity did not move with it.",
  },
] as const;

export const SERVICES = [
  {
    name: "intake",
    lang: "Go",
    job: "Verify the webhook signature, deduplicate by delivery ID, publish to the queue, return 200.",
    constraint: "Must never be slow",
    reason: "GitHub allows 10 seconds.",
  },
  {
    name: "scorer",
    lang: "Python",
    job: "Consume events, extract features, run the tenant ranking model, call the explainer, write the result.",
    constraint: "Must never lose work",
    reason: "An unranked PR is a broken promise.",
  },
  {
    name: "explainer",
    lang: "Python",
    job: "LLM inference behind an API. Warm GPU, cached, rate-limited, times out to a template fallback.",
    constraint: "Allowed to fail",
    reason: "A missing sentence costs quality, not correctness.",
  },
  {
    name: "app",
    lang: "TypeScript",
    job: "GitHub App plus the dashboard. Everything that talks to GitHub or to humans.",
    constraint: "Must never mislead",
    reason: "It is the only surface a reviewer sees.",
  },
] as const;

export const INVARIANTS = [
  ["Idempotent scoring", "The same delivery ID must never produce two scores."],
  ["Version pinning", "A request never mixes a new model with an old feature extractor."],
  ["Tenant fairness", "One monorepo pushing 500 PRs an hour must not starve a small team."],
  ["Training backpressure", "Bounded job pool. Excess requests queue or defer, never run."],
  ["Fail open, not closed", "If scoring fails the PR still appears, unranked and flagged."],
  ["Explanation is optional", "The explainer's failure degrades quality only."],
] as const;

export const PR_EVENT = `{
  "delivery_id": "string, GitHub X-GitHub-Delivery header",
  "tenant_id":   "string, installation id",
  "repo":        "string, owner/name",
  "pr_number":   0,
  "action":      "opened | synchronize | reopened",
  "head_sha":    "string",
  "received_at": "RFC3339 timestamp"
}`;

export const SCORE_RESULT = `{
  "delivery_id":   "string",
  "tenant_id":     "string",
  "pr_number":     0,
  "risk_score":    0.0,
  "rank_band":     "review_first | standard | senior_recommended",
  "model_version": "string, pinned for this request",
  "features":      { "...": "the vector used, for audit" },
  "explanation":   "string or null when the explainer timed out",
  "scored_at":     "RFC3339 timestamp"
}`;

export const DATA = [
  ["AIDev", "Primary training data — agent-authored PRs with merge outcomes, CI results, reviewer interactions"],
  ["On the Shoulders of Giants", "69 engineered features for PR outcome prediction (MSR 2020)"],
  ["ApacheJIT", "106,674 commits, 28,239 labelled bug-inducing. Bootstraps the defect signal"],
  ["Live ingestion", "Labels from merge, revert and follow-up-fix history on connected repositories"],
] as const;

export const METRICS = [
  ["riffle_intake_latency_seconds", "Must stay far under the 10s GitHub budget"],
  ["riffle_scoring_duration_seconds", "Event received to result written"],
  ["riffle_explainer_timeouts_total", "How often we degrade to no explanation"],
  ["riffle_queue_depth", "Drives KEDA autoscaling"],
  ["riffle_tenant_model_auc", "Per-tenant ranking quality over time"],
  ["riffle_drift_score", "Feature distribution shift per tenant"],
] as const;

export const BANDS = [
  ["review_first", "Highest risk in the queue. Read this one before anything else."],
  ["standard", "Normal review. No signal that it needs special attention."],
  ["senior_recommended", "Touches a path this repository has broken along before."],
] as const;
