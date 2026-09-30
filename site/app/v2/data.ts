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

// --- Sections below the fold ------------------------------------------------
// The reference page carries a dark product block, a statistics band, a
// mission panel, an investor row, a resources grid and a dark footer CTA.
// Riffle has real material for all of them except investors and a mailing
// list, and those two are substituted rather than faked.

// Each service gets a panel of its own real payload, which is what their
// gradient code panels are doing: showing the thing, not a mock of it.
// Terminal output is stored as coloured segments rather than a string, so the
// panels can syntax-highlight the way the reference's terminals do.
export type TokenKind = "cmd" | "key" | "dim" | "val" | "str" | "ok" | "warn";
export type CodeLine = readonly (readonly [string, TokenKind])[];

export const SERVICE_PANELS = [
  {
    name: "intake",
    icon: "go",
    art: "/v2/art/streak-green.webp",
    lang: "Go",
    title: "The webhook front door",
    body: "Verify the signature, deduplicate by delivery ID, publish, return 200 — inside the ten seconds GitHub allows. Nothing else happens here.",
    budget: "10s",
    budgetLabel: "hard budget",
    tone: ["#7dd3a0", "#2f6f52"],
    code: [
      [["POST ", "dim"], ["/webhook", "cmd"]],
      [["X-GitHub-Delivery: ", "dim"], ["8f2c…a91", "val"]],
      [["X-Hub-Signature-256: ", "dim"], ["sha256=…", "val"]],
      [],
      [["✓ ", "ok"], ["signature  ", "key"], ["verified", "dim"]],
      [["✓ ", "ok"], ["duplicate  ", "key"], ["no", "dim"]],
      [["→ ", "dim"], ["published  ", "key"], ["pr_event", "str"]],
      [["200 ", "ok"], ["in ", "dim"], ["41ms", "val"]],
    ],
  },
  {
    name: "scorer",
    icon: "python",
    art: "/v2/art/streak-indigo.webp",
    lang: "Python",
    title: "Features, model, result",
    body: "Consumes the event, extracts features, runs the tenant's own ranking model, asks the explainer for a sentence, writes the result.",
    budget: "1 model",
    budgetLabel: "per tenant",
    tone: ["#a5b4fc", "#4453b5"],
    code: [
      [["$ ", "dim"], ["riffle score ", "cmd"], ["--delivery ", "dim"], ["8f2c…a91", "val"]],
      [],
      [["features       ", "key"], ["100–300 ", "val"], ["planned", "dim"]],
      [["model          ", "key"], ["tenant:4192 ", "val"], ["v7", "str"]],
      [["fallback       ", "key"], ["global base", "dim"]],
      [],
      [["risk_score     ", "key"], ["0.81", "val"]],
      [["rank_band      ", "key"], ["review_first", "warn"]],
      [["model_version  ", "key"], ["v7 ", "val"], ["(pinned)", "dim"]],
    ],
  },
  {
    name: "explainer",
    icon: "python",
    art: "/v2/art/streak-violet.webp",
    lang: "Python",
    title: "Allowed to fail",
    body: "LLM inference behind an API. Warm GPU, cached, rate-limited, and a hard timeout to a deterministic template. Never on the correctness path.",
    budget: "null",
    budgetLabel: "valid answer",
    tone: ["#c4b5fd", "#6d4fb8"],
    code: [
      [["POST ", "dim"], ["/explain", "cmd"], ["   900ms", "dim"]],
      [["→ ", "dim"], ["cache      ", "key"], ["miss", "dim"]],
      [["→ ", "dim"], ["inference  ", "key"], ["timeout", "warn"]],
      [["→ ", "dim"], ["fallback   ", "key"], ["template", "str"]],
      [],
      [["explanation  ", "key"], ["null", "val"]],
      [["✓ ", "ok"], ["rank still valid", "dim"]],
    ],
  },
  {
    name: "app",
    icon: "typescript",
    art: "/v2/art/streak-orange.webp",
    lang: "TypeScript",
    title: "The only human surface",
    body: "The GitHub App and the dashboard. It reorders the queue, and it never merges a pull request or removes one from review.",
    budget: "0",
    budgetLabel: "auto-merges",
    tone: ["#e0a45e", "#8a5326"],
    code: [
      [["queue ", "dim"], ["riffle/api ", "cmd"], ["(14 open)", "dim"]],
      [],
      [["#2841  ", "key"], ["review_first    ", "warn"], ["0.81", "val"]],
      [["#2838  ", "key"], ["senior_rec.     ", "str"], ["0.64", "val"]],
      [["#2844  ", "key"], ["standard        ", "dim"], ["0.22", "val"]],
      [["#2839  ", "key"], ["standard        ", "dim"], ["0.19", "val"]],
    ],
  },
] as const;

// Statistics from the dataset plan (PR counts measured from each repository's
// Pull Requests tab, July-September 2026). Every figure carries the plan's
// own status, so a target is never shown as if it were a result.
export type Status = "measured" | "derived" | "estimate" | "decision" | "target";

export const STATS = {
  headline: "Planned on fifty public repositories",
  big: 1899996,
  bigLabel: "pull requests measured across 48 repositories, open and closed",
  bigStatus: "measured" as Status,
  // Measured subtotals per domain; the bar is drawn to these proportions.
  domains: [
    ["Systems & dev tools", 437114],
    ["Web frameworks", 259494],
    ["Data systems", 425940],
    ["Cloud & infra", 272176],
    ["ML & end-user", 505272],
  ] as const,
  cells: [
    ["40 / 10", "repositories for training / unseen-repo validation", "decision"],
    ["100–300", "feature columns per pull request", "estimate"],
    ["0.65+", "ROC-AUC target on repositories never trained on", "target"],
  ] as const,
} as const;

export const CORPUS_SAMPLE = [
  "kubernetes/kubernetes",
  "python/cpython",
  "llvm/llvm-project",
  "facebook/react",
  "apache/spark",
  "pytorch/pytorch",
] as const;

export const MISSION =
  "Our claim is narrower than a verdict: the next incident follows the seams your repository has already broken along, and its own history is where they are written.";

// Stands in for their investor row. These are the papers and datasets the
// approach rests on — the honest version of "backed by".
export const PRIOR_WORK = [
  { name: "Faros AI", domain: "faros.ai" },
  { name: "LinearB", domain: "linearb.io" },
  { name: "GitHub", domain: "github.com" },
  { name: "Apache", domain: "apache.org" },
  // A conference, not a company — no logo to fetch, so it stays as text.
  { name: "MSR 2020", domain: null },
] as const;

export const RESOURCES = [
  {
    kind: "architecture",
    art: "/v2/art/streak-green.webp",
    title: "Four services, four failure modes",
    href: "#architecture",
    tone: ["#7dd3a0", "#2f6f52"],
  },
  {
    kind: "invariants",
    art: "/v2/art/streak-indigo.webp",
    title: "Six rules that outrank the tests",
    href: "#invariants",
    tone: ["#a5b4fc", "#4453b5"],
  },
  {
    kind: "contracts",
    art: "/v2/art/streak-teal.webp",
    title: "The two shapes on the wire",
    href: "#contracts",
    tone: ["#c4b5fd", "#6d4fb8"],
  },
] as const;

export const FOOTER_LINKS = [
  ["Project", [["Repository", REPO], ["Issues", `${REPO}/issues`], ["Licence", `${REPO}/blob/main/LICENSE`]]],
  ["Docs", [["Architecture", `${REPO}#architecture`], ["Invariants", `${REPO}#invariants`], ["Contracts", `${REPO}#contracts`]]],
  ["Status", [["In development", REPO], ["Self-hosted", `${REPO}#deployment`], ["Apache-2.0", `${REPO}#license`]]],
] as const;
