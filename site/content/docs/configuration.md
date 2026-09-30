Riffle is configured with versioned YAML: organisation-wide defaults, and
per-repository overrides that merge on top. Settings never change what the
model sees or learns. Anything that does belongs in rules, where it is hashed
into `model_version`.

## Where the files live

| File | Location | Scope |
| --- | --- | --- |
| Organisation settings | `.github/riffle.yml` in the org's `.github` repository | Every repository |
| Repository settings | `.github/riffle.yml` in the repository | That repository; merges over the org file |
| Organisation rules | `riffle/rules.yml` in the org's `.github` repository | Every repository |
| Repository rules | `.github/riffle/rules.yml` | Appended after org rules, limited to that repository |

Rules are read from the default branch only, and point-in-time from the git
history of the rules file.

## An organisation file

```yaml
# your-org/.github  →  .github/riffle.yml
schema_version: "5.0"
organization:
  outcome_sharing: { enabled: false }
  data_retention: { store_diffs: false, retention_days: 180 }
  locked_keys: ["/organization", "/llm/provider", "/llm/send"]
scope:
  quiet_authors: ["dependabot[bot]", "renovate[bot]"]
llm:
  provider: none
  send: features_only
reviewer_routing: { mode: suggest, use_codeowners: true }
review_targets: { standard_hours: 8 }
```

## A repository file

```yaml
# your-org/your-repo  →  .github/riffle.yml
schema_version: "5.0"
mode: active
scope:
  base_branches: ["develop", "release/.*"]
rank_bands:
  percentile: { senior_recommended_top_fraction: 0.05, review_first_top_fraction: 0.25 }
floors:
  paths:
    - { pattern: "src/auth/**", min_band: senior_recommended }
reviewer_routing:
  senior_reviewers: ["@your-org/backend-leads"]
  groups:
    - { name: frontend, paths: ["web/**"], reviewers: ["@your-org/frontend"] }
  max_open_reviews_per_reviewer: 5
```

## Modes

| `mode` | Scores | Learns | Posts to pull requests |
| --- | --- | --- | --- |
| `active` | yes | yes | yes |
| `shadow` | yes | yes | no, logs only |
| `off` | no | no | no |

`shadow` is the safe way to evaluate Riffle on a repository before it touches
anyone's queue.

## How files merge

Arrays either replace or union when the repository file merges over the
organisation file; the schema marks which with `x-riffle-merge`, and the
default is `replace`. Keys listed in `organization.locked_keys` cannot be
overridden by a repository at all.

## Versions

Config files declare `schema_version: "major.minor"`. A minor bump adds
optional keys; a major bump renames or removes keys, and old keys stay
accepted for one minor version.

| File vs schema | Result |
| --- | --- |
| Same major, minor at or below the schema's | Accepted |
| Different major | Rejected, with a notice |

**An invalid file never stops scoring.** Riffle keeps the last valid version
and posts one notice.

Every key, allowed value and default is in the
[configuration reference](/docs/configuration/reference).
