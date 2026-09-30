// The docs' information architecture, in one place. The sidebar, the
// /docs landing cards, the prev/next pager and the static routes are all
// derived from this list, so a page cannot exist in one and not the others.
//
// `file` is a path relative to site/content/docs. `source` instead points
// outside the site, at a file the repository already maintains - rendered
// as-is so it cannot drift from what CI checks.

export type DocStatus = "accepted" | "proposed" | "planned" | "generated" | "draft";

export type DocPage = {
  slug: string; // under /docs/
  title: string;
  description: string;
  status?: DocStatus;
} & ({ file: string } | { source: string } | { outline: string[] });
// `outline` marks a page that is planned but not yet written: it renders as a
// draft with the list of what it will cover, so every gap is visible and
// has a place to be filled.

export type DocSection = {
  id: string;
  title: string;
  blurb: string;
  pages: DocPage[];
};

export const SECTIONS: DocSection[] = [
  {
    id: "getting-started",
    title: "Getting started",
    blurb: "What Riffle does, how a pull request moves through it, and how to run it locally.",
    pages: [
      { slug: "getting-started/introduction", title: "Introduction", description: "A review queue ordered by risk, learned from your repository's own history.", file: "introduction.md" },
      { slug: "getting-started/how-it-works", title: "How it works", description: "The path of one pull request, from webhook to ranked queue.", file: "how-it-works.md" },
      { slug: "getting-started/quickstart", title: "Quickstart", description: "Run the full stack locally and score a fixture pull request.", status: "planned", file: "quickstart.md" },
      {
        slug: "getting-started/installation",
        title: "Installation",
        description: "Install the GitHub App on an organisation or a single repository.",
        status: "draft",
        outline: [
          "Installing the GitHub App, organisation-wide or per repository",
          "Permissions requested, and why each is needed",
          "Webhook events subscribed",
          "Verifying the installation in shadow mode",
          "Uninstalling, and what data is removed",
        ],
      },
    ],
  },
  {
    id: "concepts",
    title: "Concepts",
    blurb: "The ideas the ranking rests on: bands, the model, labels, and the invariants that bound it.",
    pages: [
      { slug: "concepts/rank-bands", title: "Rank bands", description: "The three bands every pull request lands in, and how they are cut.", file: "rank-bands.md" },
      { slug: "concepts/model", title: "The model", description: "One global model from the first pull request, with a per-repository layer that earns its weight.", status: "accepted", file: "model.md" },
      { slug: "concepts/labels", title: "Labels and maturity", description: "What counts as a bad change, and when an outcome is old enough to learn from.", status: "proposed", file: "labels.md" },
      { slug: "concepts/invariants", title: "Invariants", description: "Six rules that outrank the tests.", file: "invariants.md" },
      {
        slug: "concepts/explanations",
        title: "Explanations",
        description: "How the one-line explanation is produced, and what happens when it is not.",
        status: "draft",
        outline: [
          "What the explainer receives: features only, never code, by default",
          "The deterministic template fallback",
          "Timeouts, caching and rate limits",
          "Choosing an LLM provider, or running with none",
        ],
      },
      {
        slug: "concepts/holdout-and-lift",
        title: "Holdout and lift",
        description: "How Riffle measures whether it is helping.",
        status: "draft",
        outline: [
          "The unranked FIFO holdout and its default share",
          "Lift against FIFO, and recall at the top 20% of effort",
          "Limiting feedback-loop bias",
          "Reading per-tenant quality over time",
        ],
      },
      {
        slug: "concepts/shadow-mode",
        title: "Shadow mode",
        description: "Score and learn without posting anything.",
        status: "draft",
        outline: [
          "What shadow mode does and does not do",
          "Evaluating Riffle on a repository before it touches the queue",
          "Moving from shadow to active",
        ],
      },
    ],
  },
  {
    id: "configuration",
    title: "Configuration",
    blurb: "Organisation defaults and per-repository overrides, as versioned YAML.",
    pages: [
      { slug: "configuration/overview", title: "Overview", description: "Where the files live, how they merge, and how versions are checked.", file: "configuration.md" },
      {
        slug: "configuration/rules",
        title: "Rules",
        description: "Declare how your organisation manages its code, as typed rules the model reads.",
        status: "draft",
        outline: [
          "Rule shape: kind, match, value",
          "Rule kinds: path tiers, path classes, sensitivity, and more",
          "Priority and conflict resolution",
          "How rules compile into fixed model columns",
          "Rules are hashed into model_version",
        ],
      },
      {
        slug: "configuration/reviewer-routing",
        title: "Reviewer routing",
        description: "Suggest or assign reviewers by path, team and availability.",
        status: "draft",
        outline: [
          "Suggest versus assign",
          "Using CODEOWNERS",
          "Senior reviewers and path groups",
          "Open-review limits and availability",
        ],
      },
      {
        slug: "configuration/pull-request-output",
        title: "Pull request output",
        description: "What Riffle posts on a pull request, and the commands it answers to.",
        status: "draft",
        outline: [
          "Check runs, comments and labels",
          "Label prefixes",
          "Commands reviewers can post",
          "Quiet authors and skipped pull requests",
        ],
      },
      { slug: "configuration/reference", title: "Reference", description: "Every rule kind, setting and default, generated from the schemas.", status: "generated", source: "../contracts/REFERENCE.md" },
    ],
  },
  {
    id: "architecture",
    title: "Architecture",
    blurb: "Four services, the boundaries between them, and the contracts that cross those boundaries.",
    pages: [
      { slug: "architecture/services", title: "Services", description: "What each service owns, and where each one is allowed to fail.", file: "services.md" },
      { slug: "architecture/contracts", title: "Contracts", description: "The JSON shapes that cross every service boundary.", file: "contracts.md" },
    ],
  },
  {
    id: "integrations",
    title: "Integrations",
    blurb: "GitHub, LLM providers and notifications.",
    pages: [
      {
        slug: "integrations/github",
        title: "GitHub App",
        description: "Permissions, events and check runs.",
        status: "draft",
        outline: [
          "Required permissions",
          "Subscribed events",
          "Check run behaviour",
          "Enterprise Server support",
        ],
      },
      {
        slug: "integrations/llm-providers",
        title: "LLM providers",
        description: "Which models can write explanations, and what they are sent.",
        status: "draft",
        outline: [
          "Supported providers",
          "What is sent: features only by default",
          "Self-hosted models",
          "Turning explanations off",
        ],
      },
      {
        slug: "integrations/notifications",
        title: "Notifications",
        description: "Where Riffle tells people about ranked pull requests.",
        status: "draft",
        outline: [
          "Channels",
          "What triggers a notification",
          "Quiet hours and availability",
        ],
      },
    ],
  },
  {
    id: "api",
    title: "API",
    blurb: "The two HTTP endpoints Riffle exposes.",
    pages: [
      {
        slug: "api/outcomes",
        title: "Outcomes API",
        description: "Send outcomes from your own systems with POST /v1/outcomes.",
        status: "draft",
        outline: [
          "Request shape (outcome_import schema)",
          "Authentication",
          "Idempotency and retries",
          "How imported outcomes become labels",
        ],
      },
      {
        slug: "api/explain",
        title: "Explain API",
        description: "The scorer-to-explainer contract, POST /v1/explain.",
        status: "draft",
        outline: [
          "Request and response shapes",
          "Timeouts and the template fallback",
          "Caching",
        ],
      },
    ],
  },
  {
    id: "security",
    title: "Security",
    blurb: "What Riffle stores, what never leaves, and how to report a problem.",
    pages: [
      {
        slug: "security/data-handling",
        title: "Data handling",
        description: "What is stored, for how long, and what never leaves your installation.",
        status: "draft",
        outline: [
          "Stored data and retention",
          "Diff storage (off by default)",
          "Outcome sharing (off by default)",
          "Deleting a tenant's data",
        ],
      },
      {
        slug: "security/reporting",
        title: "Reporting a vulnerability",
        description: "How to report a security issue.",
        status: "draft",
        outline: [
          "Contact and scope",
          "Response process",
          "Disclosure",
        ],
      },
    ],
  },
  {
    id: "operations",
    title: "Operations",
    blurb: "Running Riffle yourself: environments, and what to watch once it is live.",
    pages: [
      { slug: "operations/self-hosting", title: "Self-hosting", description: "Environments, dependencies, and what runs where.", status: "planned", file: "self-hosting.md" },
      { slug: "operations/observability", title: "Observability", description: "The metrics and logs that tell you Riffle is healthy.", file: "observability.md" },
      {
        slug: "operations/upgrading",
        title: "Upgrading",
        description: "Moving between versions without losing scores or history.",
        status: "draft",
        outline: [
          "Release cadence and versioning",
          "Config schema migrations",
          "Model version changes and retraining",
        ],
      },
      {
        slug: "operations/troubleshooting",
        title: "Troubleshooting",
        description: "Symptoms, causes and fixes for common problems.",
        status: "draft",
        outline: [
          "Pull requests not scored",
          "Webhooks retried or duplicated",
          "Explanations missing",
          "A repository ranking worse than FIFO",
        ],
      },
    ],
  },
  {
    id: "reference",
    title: "Reference",
    blurb: "Terms used across the docs, defined once.",
    pages: [{ slug: "reference/glossary", title: "Glossary", description: "Tenant, rank band, promotion, fallback, drift.", file: "glossary.md" },
      {
        slug: "reference/changelog",
        title: "Changelog",
        description: "What changed in each release.",
        status: "draft",
        outline: [
          "Release notes by version",
          "Breaking changes and migrations",
        ],
      },
      {
        slug: "reference/roadmap",
        title: "Roadmap",
        description: "What is being built next.",
        status: "draft",
        outline: [
          "Current milestone",
          "Next milestones",
          "Open design decisions",
        ],
      }],
  },
];

export const ALL_PAGES: (DocPage & { section: DocSection })[] = SECTIONS.flatMap((section) =>
  section.pages.map((p) => ({ ...p, section })),
);

export function findPage(slug: string) {
  const i = ALL_PAGES.findIndex((p) => p.slug === slug);
  if (i < 0) return null;
  return { page: ALL_PAGES[i], prev: ALL_PAGES[i - 1] ?? null, next: ALL_PAGES[i + 1] ?? null };
}
