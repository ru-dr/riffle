# Contracts reference

Generated from the schemas by `gen_reference.py`. Do not edit by hand.

- [Rules](#rules-org_rulesschemajson)
- [Settings](#settings-org_configschemajson)
- [Mined features](#mined-features-mined_featuresschemajson)
- [Outcome import](#outcome-import-outcome_importschemajson)

## Rules (`org_rules.schema.json`)

**Version:** 5.4

How an organisation manages its code, as flat typed rules (kind + match + value). Orgs may write any number of rules, but the compiler always reduces them to the fixed column set in x-riffle-model-columns. Undeclared means null, never 0. Adding a column is a schema version bump, a retrain, and a decision record. Rules are read point-in-time from git history of the rules file, and the compiled hash is pinned into model_version. Read from the default branch only; an invalid file keeps the last valid compiled rules and posts a notice. Org file: riffle/rules.yml in the org's .github repo. Repo file: .github/riffle/rules.yml, appended after org rules and limited to that repo.

### Fields every rule accepts

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `id` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | Rule identifier |
| `kind` | string |  |  | Rule kind, which decides the other fields the rule accepts |
| `applies_to.repos` | list of string | `["*"]` |  | Repositories the rule applies to; * is every repo |
| `applies_to.paths` | list of string |  |  | Limit to a monorepo package, e.g. packages/react-native/** |
| `applies_to.branches` | list of string |  |  | Branches the rule applies to |
| `priority` | integer 0–100 | `50` |  | Conflict resolution: higher wins, then the more specific glob, then the later rule. |
| `enabled` | boolean | `true` |  | false turns the rule off without removing it |
| `note` | string |  |  | Free-text note about the rule |

### Rule kinds

#### `path_tier`

**Sink:** feature  
**Compiles to:** `max_criticality`, `frac_lines_in_critical`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob |
| `value` | `low`, `normal`, `elevated`, `critical` |  | required | Criticality of the matching paths, low to critical, as 0-3 in max_criticality |

#### `path_class`

**Sink:** feature | filter  
**Compiles to:** `effective_lines_changed`, `test_lines_ratio`, `docs_only`, `config_only`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob |
| `value` | `excluded`, `vendored`, `generated`, `lockfile`, `test`, `test_fixture`, `docs`, `config`, `build_config`, `dependency_manifest`, `i18n` |  | required | Class of the matching files |

#### `sensitivity`

What the code handles. Split from change_signal so authn and authz stay distinct.

**Sink:** feature  
**Compiles to:** `n_sensitivity_tags`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob |
| `value` | `authn`, `authz`, `payments`, `pii`, `secrets`, `crypto`, `infra`, `public_api` |  | required | What the matching code handles |

#### `change_signal`

Risky change types. Handled by the extractor's own path rules too; these declarations override its defaults per repo.

**Sink:** feature  
**Compiles to:** `sharpens extractor touches_* features; no new columns`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob |
| `value` | `schema_migration`, `wire_protocol`, `infra_as_code`, `build_system`, `dependency_bump`, `feature_flag`, `concurrency`, `native_interop`, `release_config` |  | required | Change type the matching paths signal |

#### `component`

Names a monorepo package or module. The name is free text, but the model only sees generic stats computed per component from history.

**Sink:** feature  
**Compiles to:** `n_components_touched`, `max_criticality`, `max_exposure`, `max_data_class`, `regulated_scope_count`, `log_max_blast_radius`, `touches_deprecated`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | e.g. streams/** or packages/virtualized-lists/** |
| `value` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  | required | Component name |
| `tier` | integer 1–4 |  |  | Availability tier, 1 is most critical |
| `exposure` | `internal`, `partner`, `external` |  |  | Who the component is exposed to; max_exposure encodes internal 0, partner 1, external 2 |
| `data_class` | `public`, `internal`, `confidential`, `restricted` |  |  | Class of data the component holds; max_data_class encodes public 0 to restricted 3 |
| `regimes` | list of `pci`, `gdpr`, `hipaa`, `sox`, `soc2`, `fedramp` |  |  | Compliance regimes the component falls under, counted in regulated_scope_count |
| `lifecycle` | `experimental`, `active`, `stable`, `deprecated` |  |  | Lifecycle stage; deprecated sets touches_deprecated |
| `blast_radius` | integer 0– |  |  | Declared downstream dependents |

#### `owner`

Path ownership. import_codeowners reads CODEOWNERS instead of listing paths.

**Sink:** feature  
**Compiles to:** `author_is_owner`, `owning_teams_touched (extractor, not a declared column)`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob, or the CODEOWNERS path when import_codeowners is true |
| `value` | string |  | required | Team or login, e.g. @apache/kafka-streams |
| `import_codeowners` | boolean | `false` |  | Read ownership from the CODEOWNERS file at match instead of listing paths |

#### `policy`

Team policies checked against the diff. Each firing policy adds one to policy_violations; the specific policy is shown in the comment, not given its own column.

**Sink:** feature  
**Compiles to:** `policy_violations`, `required_tests_missing`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob the policy covers |
| `value` | `required_tests`, `high_scrutiny`, `perf_critical`, `no_import_from`, `flag_required`, `migration_reversible`, `migration_no_destructive`, `dependency_allowlist`, `dependency_denylist`, `regen_pair` |  | required | Policy to check against the diff |
| `target` | string or list of string |  |  | no_import_from: forbidden path glob. dependency_*: package names. regen_pair: generated path glob. |
| `tests` | string |  |  | required_tests: test glob that satisfies it; defaults to path_class test |
| `languages` | list of `java`, `python`, `typescript`, `javascript`, `go`, `kotlin` |  |  | no_import_from only: languages to parse imports for. Others are skipped, and the feature is null for files in them |

Phase notes: `no_import_from` v1 for listed languages via tree-sitter import queries; other languages later; `flag_required` later: heuristic on known flag SDK calls only

#### `freeze_window`

Declared code freeze. days_to_freeze_start is left to the extractor.

**Sink:** feature  
**Compiles to:** `in_freeze_window`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `start` | string (date-time) |  | required | When the freeze starts |
| `end` | string (date-time) |  | required | When the freeze ends |
| `match` | string | `"*"` |  | Branch glob |

#### `pr_label`

Maps PR labels at open time. Different from issue_label, which is about linked issues.

**Sink:** feature  
**Compiles to:** `label_priority`, `label_is_hotfix`, `label_security`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Exact PR label name |
| `value` | `priority`, `hotfix`, `security`, `safe` |  | required | What the label means; priority also needs level |
| `level` | integer 0–3 |  |  | priority only: 3 is highest (P0) |

#### `safe_change`

Which change kinds this team treats as safe. No match; the extractor classifies lines.

**Sink:** feature  
**Compiles to:** `is_safe_change_only`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `value` | `docs`, `tests`, `formatting`, `comments`, `generated`, `i18n` |  | required | Change kind treated as safe |

#### `pattern`

**Sink:** label | feature  
**Compiles to:** `task_type`, `is_backport`, `labels: revert, bugfix`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string (regex) |  | required | Regex matched against field |
| `value` | `revert`, `bugfix`, `hotfix`, `backport`, `conventional_type`, `security_fix` |  | required | What a match marks the change as |
| `field` | `commit_message`, `commit_trailer`, `pr_title`, `pr_body`, `branch_name` | `"commit_message"` |  | Text the regex is matched against |

#### `link`

Connects objects Riffle would otherwise treat as unrelated. match must contain a named group (?<ref>...).

**Sink:** link  
**Compiles to:** `commit-PR-issue graph used by every label source; cherry picks dedupe labels across branches`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string (regex) |  | required | Regex with a named group (?<ref>...) that captures the reference |
| `field` | `commit_message`, `commit_trailer`, `pr_title`, `pr_body` | `"commit_trailer"` |  | Text the regex is matched against |
| `value` | `pr_ref`, `revert_target`, `cherry_pick_source`, `issue_key`, `external_change_id` |  | required | What the captured reference points to |
| `tracker` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | For issue_key: which sources.trackers entry |

#### `identity`

Recovers the real person when a bot lands the commit or email differs from login. match needs a named group (?<login>...) or (?<email>...).

**Sink:** feature  
**Compiles to:** `author_* features use the resolved author`, `reviewer_count`, `reviewer_path_experience`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string (regex) |  | required | Regex with a named group (?<login>...) or (?<email>...) |
| `on` | `commit_trailer`, `commit_message`, `pr_body`, `co_authored_by` | `"commit_trailer"` |  | Text the regex is matched against |
| `value` | `author`, `co_author`, `reviewer`, `lander` |  | required | Role of the recovered person |

#### `merge_signal`

What counts as merged. Needed when PRs are closed and the change lands another way.

**Sink:** label  
**Compiles to:** `merged_at, merge_commit_sha for every outcome window`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `value` | `github_merge`, `linked_commit_on_default`, `bot_close_with_commit`, `direct_push` |  | required | How a change counts as merged |
| `match` | string | `"*"` |  | Actor login for bot_close_with_commit, else '*' |

#### `issue_field`

Tracker fields beyond labels, e.g. Jira issuetype and priority.

**Sink:** label

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `tracker` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  | required | sources.trackers entry |
| `field` | string |  | required | e.g. issuetype, priority, customfield_10020 |
| `match` | string |  | required | Field value to match |
| `value` | `bug`, `incident`, `regression`, `security`, `not_a_bug` |  | required | What a matching issue counts as |
| `severity` | integer 1–4 |  |  | 1 is worst; scales label weight |

#### `issue_label`

**Sink:** label

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `tracker` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` | `"github"` |  | Tracker the issue label is read from |
| `match` | string |  | required | Issue label to match |
| `value` | `bug`, `incident`, `regression`, `security`, `not_a_bug` |  | required | What a matching issue counts as |
| `severity` | integer 1–4 |  |  | 1 is worst; scales label weight |

#### `author_class`

**Sink:** feature  
**Compiles to:** `agent_in_critical`, `author_is_maintainer, author_is_external, author_is_new (extractor)`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Value to match in the field named by on |
| `on` | `login`, `email_domain`, `branch_prefix`, `commit_trailer` | `"login"` |  | What match is compared with |
| `value` | `bot`, `agent`, `maintainer`, `employee`, `external` |  | required | Class given to matching authors |
| `new_contributor_days` | integer 1–730 |  |  | Optional; overrides the default tenure cut for authors this rule matches |

#### `ci_check`

**Sink:** feature | label  
**Compiles to:** `ci_required_failed`, `ci_failures_non_flaky`, `label: post_merge_ci`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `source` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | sources.ci entry; default is GitHub checks |
| `match` | string |  | required | Check name to match |
| `value` | `required`, `flaky`, `informational`, `auxiliary` |  | required | auxiliary (CodeQL, Scorecards, coverage) never triggers ci_fail |
| `stage` | `pre_merge`, `post_merge`, `nightly`, `release` | `"pre_merge"` |  | When the check runs |

#### `test_class`

Test-level flakiness from sources.test_reports, for repos with one giant CI check.

**Sink:** feature | label  
**Phase:** later

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Test id glob, e.g. org.apache.kafka.streams.integration.* |
| `value` | `flaky`, `quarantined`, `slow` |  | required | Class given to matching tests |

#### `label_source`

Weight of an outcome source as a sample weight in this org's per-repo layer. Never applied to the global model. Windows: revert 30d, ci_fail on required checks only, hotfix is a hotfix-labelled PR on the same lines within 7d, follow_up_fix is any PR on the same lines within 14d.

**Sink:** label

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | `revert`, `ci_fail`, `hotfix`, `szz`, `follow_up_fix`, `route_override`, `incident_link`, `external_import` |  | required | Outcome source the weight applies to |
| `value` | number 0–2 |  | required | Sample weight for this source in the org's per-repo layer, 0 to 2 |
| `window_days` | integer 1–90 |  |  | Window in days for this source, in place of its default |

#### `branch_class`

**Sink:** feature | label  
**Compiles to:** `targets_release_branch`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Branch glob |
| `value` | `default`, `release`, `hotfix`, `backport`, `long_lived`, `experimental` |  | required | Class of the matching branches; release and hotfix set targets_release_branch when they are the base branch |

#### `stack`

Stacked PR tooling, so a stack is understood as related changes.

**Sink:** feature  
**Compiles to:** `stack_depth`, `stack_position`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `value` | `ghstack`, `graphite`, `sapling`, `spr`, `branch_chain` |  | required | Stacked PR tool |
| `match` | string |  | required | Branch pattern or body marker the tool leaves |

### `sources` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `sources.trackers` | list of objects |  |  | Issue trackers, referenced by link and issue rules through their id. |
| `sources.trackers[].id` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | Tracker identifier, referenced by the tracker field of link, issue_field and issue_label rules |
| `sources.trackers[].provider` | `github`, `jira`, `linear`, `bugzilla`, `youtrack` |  |  | Tracker product |
| `sources.trackers[].base_url` | string (uri) |  |  | Base URL of the tracker |
| `sources.trackers[].projects` | list of string |  |  | Tracker projects to read |
| `sources.trackers[].credentials_ref` | string |  |  | Secret reference for the tracker's credentials, never the value |
| `sources.ci` | list of objects |  |  | CI systems beyond GitHub checks, referenced by ci_check rules through their id. |
| `sources.ci[].id` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | CI source identifier, referenced by the source field of ci_check rules |
| `sources.ci[].provider` | `github_checks`, `commit_status`, `external_api` |  |  | commit_status covers Jenkins, Buildkite and anything posting legacy statuses |
| `sources.ci[].context_glob` | string | `"*"` |  | Glob over the check or status names read from this source |
| `sources.ci[].credentials_ref` | string |  |  | Secret reference for the CI system's credentials, never the value |
| `sources.test_reports.enabled` | boolean | `false` | later | Read test reports |
| `sources.test_reports.format` | `junit_xml` | `"junit_xml"` | later | Test report file format |
| `sources.test_reports.artifact_glob` | string | `"**/TEST-*.xml"` | later | Glob matching test report files |
| `sources.outcome_import.enabled` | boolean | `false` |  | Accept outcomes at POST /v1/outcomes |
| `sources.outcome_import.secret_ref` | string |  |  | Secret reference for the secret that signs POST /v1/outcomes requests, never the value |

### `labels` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `labels.target` | `union`, `weighted` | `"union"` |  | union: label_bad = any target source fires (binary). weighted: each source's label_source weight becomes a sample weight. Per-repo layer only |
| `labels.szz_in_target` | boolean | `false` |  | SZZ stays a separate label unless true |
| `labels.maturity_days` | integer 7–180 | `30` |  | Rows younger than this are dropped before training (label_mature) |
| `labels.szz_maturity_days` | integer 30–365 | `90` |  | Days before an SZZ label is mature, used for SZZ instead of maturity_days |
| `labels.unobserved` | `mask`, `negative`, `downweight` | `"downweight"` |  | What to do when a label source had no data for a mature PR (e.g. CI results expired). Any observed source that fired still makes the row positive. Otherwise: mask drops the row; negative counts it as fully clean; downweight counts it as clean with sample weight unobserved_weight |
| `labels.unobserved_weight` | number 0.05–1 | `0.5` |  | downweight only. Multiplies the row's sample weight for each unobserved source (0.5 with one missing, 0.25 with two) |
| `labels.target_sources` | list of `revert`, `ci_fail`, `hotfix`, `szz`, `follow_up_fix`, `route_override`, `incident_link`, `external_import` | `["revert", "ci_fail", "hotfix"]` |  | Sources that can make a row positive. Others still get mined and reported as ablations, but do not enter the target |
| `labels.merge_outcome.enabled` | boolean | `true` |  | Mine closed-unmerged PRs and the merge-outcome labels: label_merged, label_rework, label_duplicate, label_unwanted, label_abandoned, label_ci_technical, close_reason |
| `labels.merge_outcome.rework_grace_seconds` | integer 0–3600 | `300` |  | Commits and force-pushes this soon after open are part of opening the PR, not rework. Also the cut for the at-open snapshot the features are computed from (docs/adr/0010-features-at-open.md) |
| `labels.merge_outcome.abandon_days` | integer 14–365 | `60` |  | A PR still open this many days after opening with no human non-author response counts as unmerged and abandoned |
| `labels.merge_outcome.close_reason_classifier` | `llm`, `rules` | `"llm"` |  | llm: an LLM reads each unmerged PR's comments, reviews and labels up to close and picks close_reason, falling back to rules when the LLM is off or fails. Training data only, never the scoring path |
| `labels.merge_outcome.stale_bot_logins` | list of string | `["stale[bot]", "stale-bot", "stalebot"]` | union | A PR closed by one of these accounts is abandoned |
| `labels.merge_outcome.unwanted_labels` | list of string | `["wontfix", "won't fix", "invalid", "not planned", "out of scope", "declined", "rejected", "spam"]` | union | Labels present at close that mark the PR unwanted. Matched case-insensitively as substrings, so `rejected AI` counts |

### `mining` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `mining.similar_prs.k` | integer 3–20 | `5` |  | Number of nearest past PRs similar_pr_bad_rate is computed over |
| `mining.similar_prs.min_file_overlap` | number 0.05–1 | `0.2` |  | Jaccard over touched files |
| `mining.similar_prs.lookback_days` | integer 30–1095 | `365` |  | Days of past PRs searched for neighbours |
| `mining.similar_prs.min_neighbours` | integer 1–10 | `3` |  | Fewer than this and the feature is null |
| `mining.scrutiny.lookback_days` | integer 30–1095 | `180` |  | Days of prior PR reviews counted |
| `mining.scrutiny.exclude_bot_reviews` | boolean | `true` |  | Leave bot reviews out of revealed_path_scrutiny |
| `mining.scrutiny.min_prior_prs` | integer 1–50 | `5` |  | Fewer prior PRs than this and the feature is null |
| `mining.failure_mode.lookback_days` | integer 90–1095 | `365` |  | Days of past bad changes on the touched paths counted |
| `mining.failure_mode.min_bad_changes` | integer 1–20 | `2` |  | Fewer past bad changes than this and the features are null |
| `mining.path_size.lookback_days` | integer 30–1095 | `365` |  | Days of past changes to the same files compared against |
| `mining.path_size.min_prior_changes` | integer 3–100 | `10` |  | Fewer past changes to the same files than this and the feature is null |
| `mining.release_cycle.tag_pattern` | string (regex) | `"^v?\\d+\\.\\d+(\\.\\d+)?$"` |  | Which tags count as releases, e.g. ^\d+\.\d+\.0$ for minor releases only |
| `mining.release_cycle.min_releases` | integer 2–20 | `3` |  | Fewer release tags than this and the feature is null |
| `mining.release_cycle.interval_stat` | `median`, `mean` | `"median"` |  | Statistic over past release intervals used as the repo's typical release interval |
| `mining.flaky_detection.enabled` | boolean | `true` |  | false turns off flaky detection from re-runs |
| `mining.flaky_detection.min_rerun_passes` | integer 1–20 | `3` |  | Failed-then-passed re-runs needed before a check is marked flaky |
| `mining.flaky_detection.lookback_days` | integer 14–365 | `90` |  | Days of check history searched for failed-then-passed re-runs |

### `model` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `model.per_repo_layer.enabled` | boolean | `true` |  | false serves the global model only (layer_weight 0) |
| `model.per_repo_layer.max_layer_weight` | number 0–1 | `1.0` |  | Upper limit on layer_weight, the per-repo layer's share of the score (0 to 1) |
| `model.disabled_features` | list of string | `[]` | union | Feature names forced to null. Width of the vector never changes. Repos may only add. Example: agent_authored |
| `model.backfill.lookback_days` | integer 90–3650 | `1095` |  | Days of history mined on install |
| `model.backfill.recent_first_days` | integer 30–365 | `180` |  | Most recent days of history mined first; older history follows in the background |

### Declared model columns

Every rule compiles to these fixed columns. `null` when undeclared.

| Column | Meaning |
|---|---|
| `max_criticality` | 0-3 from path_tier and component tier |
| `frac_lines_in_critical` | share of changed lines at tier critical |
| `n_components_touched` | declared components spanned |
| `n_sensitivity_tags` | distinct sensitivity values touched |
| `max_exposure` | 0 internal, 1 partner, 2 external |
| `max_data_class` | 0 public, 1 internal, 2 confidential, 3 restricted |
| `regulated_scope_count` | distinct regimes touched |
| `log_max_blast_radius` | log1p of declared dependents |
| `touches_deprecated` | any touched component has lifecycle deprecated |
| `policy_violations` | count of policy rules broken by the diff |
| `required_tests_missing` | a required_tests policy fired |
| `in_freeze_window` | PR opened inside a freeze window |
| `targets_release_branch` | base branch class is release or hotfix |
| `author_is_owner` | resolved author owns any touched path |
| `agent_in_critical` | agent-authored and max_criticality = 3 |
| `label_priority` | 0-3 from pr_label priority, null if unlabeled |
| `is_safe_change_only` | every changed line falls in a declared safe_change kind |

## Settings (`org_config.schema.json`)

**Version:** 5.0

How the app behaves. Settings never change what the model sees or learns; those live in contracts/org_rules.schema.json and are pinned into model_version. Files: .github/riffle.yml in the org's .github repo (org defaults) and in each repo. Loading: built-in defaults, then org file, then repo file (repo wins), then organization.locked_keys restored from the org file. Files are read from the default branch only, so a PR cannot change the settings it is judged by. An invalid file never stops Riffle: the last valid settings stay in force and one neutral notice is posted. Arrays replace unless marked x-riffle-merge: union. Keys marked x-riffle-org-only are ignored in repo files. Every key has a default, so a file with only schema_version is valid.

### `schema_version`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `schema_version` | string matching `^5\.[0-9]+$` |  |  | major.minor. Minor adds optional keys; major renames or removes, with deprecated keys accepted for one minor version |

### `mode`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `mode` | `active`, `shadow`, `off` | `"active"` |  | shadow scores, learns and logs but posts nothing |

### `organization`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `organization.outcome_sharing.enabled` | boolean | `false` | org only | Anonymised feature vectors and outcomes only. Never code, diffs or identities |
| `organization.data_retention.store_diffs` | boolean | `false` | org only | Keep full diffs after features are computed |
| `organization.data_retention.retention_days` | integer 30–730 | `365` | org only | Days stored data is kept |
| `organization.label_prefix` | string | `"riffle"` | org only | Prefix of every label Riffle applies |
| `organization.locked_keys` | list of string matching `^/` | `["/organization", "/llm/provider", "/llm/send"]` | org only | JSON pointers a repo file may not change |

### `scope`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `scope.base_branches` | list of string (regex) | `[]` |  | Extra target branches besides the default branch |
| `scope.include_drafts` | boolean | `false` |  | Post on draft PRs too |
| `scope.quiet_authors` | list of string | `["dependabot[bot]", "renovate[bot]", "github-actions[bot]"]` | union | Scored and shown on the dashboard, but no comment or label. Nothing is left unscored |
| `scope.quiet_title_keywords` | list of string | `["WIP", "DO NOT MERGE"]` | union | Same as quiet_authors, matched on the PR title |

### `rank_bands`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `rank_bands.mode` | `percentile`, `absolute` | `"percentile"` |  | percentile cuts bands from the repo's score distribution; absolute cuts on fixed scores |
| `rank_bands.percentile.senior_recommended_top_fraction` | number 0–1 | `0.05` |  | Share of the highest-scoring PRs placed in senior_recommended |
| `rank_bands.percentile.review_first_top_fraction` | number 0–1 | `0.2` |  | Matches the top-20% effort-aware recall metric |
| `rank_bands.percentile.window` | `repo`, `component` | `"repo"` |  | component ranks each monorepo package against itself |
| `rank_bands.absolute.senior_recommended_min_score` | number 0–1 | `0.8` |  | Lowest risk_score placed in senior_recommended |
| `rank_bands.absolute.review_first_min_score` | number 0–1 | `0.5` |  | Lowest risk_score placed in review_first |
| `rank_bands.holdout_fraction` | number 0.02–0.1 | `0.05` |  | Share of PRs left unranked (FIFO) to measure lift and limit feedback-loop bias |
| `rank_bands.tie_break` | list of `similar_pr_bad_rate`, `path_detection_lag_days`, `revealed_path_scrutiny`, `path_size_pctile`, `release_cycle_position` | `["similar_pr_bad_rate", "path_detection_lag_days"]` |  | Only for identical rounded scores; PR age is always the last key |

### `floors`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `floors.enabled` | boolean | `true` | decision pending | false turns off every floor |
| `floors.paths` | list of objects | `[]` | decision pending, union | Minimum band for PRs that touch a path |
| `floors.paths[].pattern` | string |  | decision pending, union | Path glob |
| `floors.paths[].min_band` | `review_first`, `senior_recommended` |  | decision pending, union | standard is the lowest band, so it is not a floor |
| `floors.ai_agents` | `review_first`, `senior_recommended` or null | `null` | decision pending | Minimum band for PRs by AI agents; null for none |
| `floors.first_time_contributors` | `review_first`, `senior_recommended` or null | `null` | decision pending | Minimum band for PRs by first-time contributors; null for none |

### `size`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `size.thresholds.xs` | integer 0– | `0` |  | Lower bound of xs |
| `size.thresholds.s` | integer 0– | `10` |  | Lower bound of s |
| `size.thresholds.m` | integer 0– | `100` |  | Lower bound of m |
| `size.thresholds.l` | integer 0– | `500` |  | Lower bound of l |
| `size.thresholds.xl` | integer 0– | `1000` |  | Lower bound of xl |
| `size.count_files` | boolean | `false` |  | Whether changed files count toward the size label |
| `size.ignore_paths` | list of string | `["**/*.lock", "**/package-lock.json", "**/go.sum"]` |  | Excluded from the size label only; still counted for risk |
| `size.apply_label` | boolean | `true` |  | Apply the size label |
| `size.large_pr_note` | string or null | `null` |  | Note shown on large PRs; null for none |

### `authors`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `authors.agents` | `full`, `label_only`, `silent` | `"full"` |  | silent still scores and shows on the dashboard |
| `authors.external` | `full`, `label_only`, `silent` | `"full"` |  | silent still scores and shows on the dashboard |
| `authors.ai_agent_label` | string or null | `"ai-authored"` |  | Transparency label. Detection itself is configured in rules (author_class) |
| `authors.path_experience_note` | boolean | `true` |  | Mention when the author rarely touches these files |

### `reviewer_routing`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `reviewer_routing.mode` | `off`, `suggest`, `request` | `"suggest"` |  | off, suggest reviewers, or request them on the PR |
| `reviewer_routing.use_codeowners` | boolean | `true` |  | Use CODEOWNERS when choosing reviewers |
| `reviewer_routing.senior_reviewers` | list of string matching `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$` | `[]` |  | Reviewers for senior_recommended PRs |
| `reviewer_routing.groups` | list of objects | `[]` |  | Reviewers by path |
| `reviewer_routing.groups[].name` | string |  |  | Group name |
| `reviewer_routing.groups[].paths` | list of string |  |  | Path globs the group reviews |
| `reviewer_routing.groups[].reviewers` | list of string matching `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$` |  |  | Reviewers in the group |
| `reviewer_routing.reviewers_per_pr` | integer 1–5 | `1` |  | Reviewers chosen per PR |
| `reviewer_routing.algorithm` | `load_balance`, `round_robin`, `random` | `"load_balance"` |  | How reviewers are picked |
| `reviewer_routing.max_open_reviews_per_reviewer` | integer or null 1– | `null` |  | Most open reviews per reviewer; null for no limit |
| `reviewer_routing.skip_title_keywords` | list of string | `["wip"]` |  | PR title keywords that skip reviewer routing |

### `availability`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `availability.users_unavailable` | list of string matching `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$` | `[]` |  | Users or teams currently unavailable to review |
| `availability.timezone` | string | `"UTC"` |  | IANA name |
| `availability.working_days` | list of `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` | `["mon", "tue", "wed", "thu", "fri"]` |  | Days that count as working days |
| `availability.working_hours` | string matching `^([01][0-9]\|2[0-3]):[0-5][0-9]-([01][0-9]\|2[0-3]):[0-5][0-9]$` | `"09:00-17:00"` |  | Working hours on a working day, HH:MM-HH:MM in timezone |

### `review_targets`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `review_targets.senior_recommended_hours` | integer 1–720 | `4` |  | Target for senior_recommended PRs, in working hours |
| `review_targets.review_first_hours` | integer 1–720 | `4` |  | Target for review_first PRs, in working hours |
| `review_targets.standard_hours` | integer 1–720 | `8` |  | Target for standard PRs, in working hours |

### `reminders`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `reminders.enabled` | boolean | `false` |  | Send reminders |
| `reminders.min_age_hours` | integer 1–720 | `24` |  | Hours a PR must be open before a reminder |
| `reminders.min_staleness_hours` | integer 1–720 | `8` |  | Hours without activity before a reminder |
| `reminders.ignore_drafts` | boolean | `true` |  | No reminders on draft PRs |
| `reminders.ignore_approved_with` | integer 0–10 | `1` |  | No reminders on PRs with at least this many approvals |
| `reminders.ignored_labels` | list of string | `[]` |  | PR labels that stop reminders |

### `rescore`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `rescore.on` | list of `push`, `ready_for_review`, `ci_completed`, `reopened`, `edited`, `labeled` | `["push", "ready_for_review", "ci_completed", "reopened"]` |  | PR events that trigger a rescore |
| `rescore.max_rescores_per_pr` | integer 1–100 | `20` |  | Most rescores one PR gets |
| `rescore.min_minutes_between_edits` | integer 0–60 | `2` |  | Minutes between rescores triggered by edits |

### `commands`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `commands.enabled` | boolean | `true` |  | Accept /riffle commands |
| `commands.prefix` | string matching `^/[a-z-]+$` | `"/riffle"` |  | Command prefix |
| `commands.override.allowed` | `write_access`, `maintainers`, `codeowners` | `"write_access"` | org only | Who may override a band |
| `commands.override.require_reason` | boolean | `true` | org only | The reason is stored with the route_override training label |
| `commands.override.allow_downgrade` | boolean | `true` | org only | Floors still apply |
| `commands.disable_label` | string | `"riffle:disabled"` |  | Stops comments on that PR; it is still scored |

### `pr_output`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `pr_output.post_comment` | boolean | `true` |  | Post a comment on the PR |
| `pr_output.update_existing_comment` | boolean | `true` |  | Edit the existing comment rather than posting a new one |
| `pr_output.apply_labels` | boolean | `true` |  | Apply band labels |
| `pr_output.labels.review_first` | string | `"review first"` |  | Label for review_first |
| `pr_output.labels.standard` | string | `"standard"` |  | Label for standard |
| `pr_output.labels.senior_recommended` | string | `"senior recommended"` |  | Label for senior_recommended |
| `pr_output.labels.override` | string | `"wrong route"` |  | Label for a PR whose band was overridden |
| `pr_output.component_label_prefix` | string or null | `null` |  | Prefix of component labels; null for none |
| `pr_output.bands_with_comment` | list of `review_first`, `standard`, `senior_recommended` | `["review_first", "senior_recommended"]` |  | Bands whose PRs get a comment |
| `pr_output.show_top_factors` | integer 0–5 | `3` |  | From SHAP; explains the rank even with the LLM off |
| `pr_output.show_score` | boolean | `false` |  | Show risk_score in the comment |
| `pr_output.show_component` | boolean | `true` |  | Show the component in the comment |
| `pr_output.collapse` | boolean | `true` |  | Collapse the comment |
| `pr_output.evidence.cite_similar_prs` | integer 0–5 | `3` |  | Most similar past PRs cited |
| `pr_output.evidence.failure_mode` | boolean | `true` |  | Cite failure_mode_top |
| `pr_output.evidence.scrutiny` | boolean | `true` |  | Cite scrutiny_top_path |
| `pr_output.evidence.path_size` | boolean | `true` |  | Cite size_context |
| `pr_output.evidence.release_cycle` | boolean | `true` |  | Cite release_context |

### `explanation`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `explanation.enabled` | boolean | `true` |  | Turning off never stops scoring |
| `explanation.language` | string matching `^[a-z]{2}(-[A-Z]{2})?$` | `"en-US"` |  | Language of the explanation, e.g. en-US |
| `explanation.max_bullets` | integer 1–7 | `3` |  | Most bullets in the explanation |
| `explanation.show_review_effort` | boolean | `true` |  | Mention review effort |
| `explanation.mention_missing_tests` | boolean | `true` |  | Mention missing tests |
| `explanation.mention_missing_issue` | boolean | `false` |  | Mention a missing linked issue |
| `explanation.show_effective_config` | boolean | `false` |  | Show the effective settings |
| `explanation.known_fragile` | list of string | `[]` |  | Paths the narrator names explicitly |
| `explanation.house_terms` | object | `{}` |  | Up to 50 organisation terms for the narrator, as term to text |

### `llm`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `llm.provider` | `none`, `hosted`, `self_hosted` | `"none"` |  | none = SHAP template explanations only |
| `llm.endpoint` | string or null (uri) | `null` |  | URL of the LLM endpoint |
| `llm.send` | `features_only`, `diff_hunks`, `full_files` | `"features_only"` |  | Repos may only make this stricter |
| `llm.excluded_paths` | list of string | `["**/.env*", "**/*.pem", "**/*.key", "**/*secret*"]` | union | Paths never sent to the LLM |
| `llm.max_calls_per_hour` | integer 0–10000 | `60` |  | Most LLM calls per hour |

### `queue`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `queue.epic_priority` | boolean | `false` |  | Linked issue priority breaks ties inside a band |
| `queue.manual_boosts.enabled` | boolean | `false` |  | Apply manual boosts |
| `queue.manual_boosts.areas` | list of objects | `[]` |  | Paths whose PRs move up the queue |
| `queue.manual_boosts.areas[].paths` | list of string |  |  | Path globs the boost applies to |
| `queue.manual_boosts.areas[].boost` | number 0–0.2 | `0.05` |  | Moves queue position, never risk_score |

### `stacks`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `stacks.score_as` | `each_pr`, `whole_stack` | `"each_pr"` |  | Score each PR in a stack on its own, or the stack as a whole |
| `stacks.comment_on` | `each_pr`, `stack_top` | `"each_pr"` |  | Comment on each PR in a stack, or only the top one |

### `limits`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `limits.max_comments_per_hour` | integer 1–1000 | `120` |  | Most comments Riffle posts per hour |

### `overrides`

Per component or path, inside a monorepo. Most specific match wins.

Each entry has `match` (`component` or `paths`) and may set `rank_bands`, `pr_output`, `reviewer_routing`, `review_targets` with the same keys as the top-level blocks.

### `dashboard`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `dashboard.visibility` | `org_members`, `repo_writers`, `admins` | `"repo_writers"` | org only | Who can see the dashboard |
| `dashboard.show_individual_metrics` | boolean | `false` | org only | Per-person statistics stay hidden unless on |
| `dashboard.group_by` | `band`, `component`, `owner`, `author_type` | `"band"` | org only | How the dashboard groups PRs |

### `audit`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `audit.enabled` | boolean | `true` | org only | Keep the audit log |
| `audit.retention_days` | integer 30–1095 | `365` | org only | Days audit entries are kept |

### `notifications`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `notifications.slack_webhook_secret_ref` | string or null | `null` | later | Secret reference for a Slack webhook, never the value; null for none |
| `notifications.digest` | `off`, `daily`, `weekly` | `"off"` | later | How often a digest is sent |

### Code checks

Cross-field rules the schema cannot express:

- rank_bands.percentile.senior_recommended_top_fraction <= review_first_top_fraction
- rank_bands.absolute.review_first_min_score <= senior_recommended_min_score
- size.thresholds strictly increase xs < s < m < l < xl
- scope.base_branches entries compile as regex
- repo files cannot loosen locked or privacy keys (e.g. llm.send may only move toward features_only)
- floors never lower a band; a floor that equals the model band is not shown

## Mined features (`mined_features.schema.json`)

**Version:** 1.0

Nine repo-local columns mined from git, CI and review history that capture how one repository behaves. Computed at the PR's base commit by the scorer's extractor at scoring time and by the repo miner (pipelines/ingestion/repo_miner) for training rows, appended to the fixed feature vector, and emitted alongside evidence the narrator may cite. Point-in-time rule: only past PRs merged before this PR opened AND whose labels were mature at that moment may contribute. Only reviews completed before open count. null means not enough history (thresholds in rules.mining), never 0.

### `columns`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `columns.similar_pr_bad_rate` | number or null 0–1 |  |  | Share of the k nearest past PRs (Jaccard over touched files, >= min_file_overlap) with label_bad. Smoothed toward repo_revert_base_rate when neighbours are few |
| `columns.similar_pr_count` | integer 0– |  |  | Neighbours actually found; lets the model discount a rate built on 3 PRs |
| `columns.revealed_path_scrutiny` | number or null 0– |  |  | Mean over touched files of (review comments + 2 x changes_requested + rounds) on prior PRs, divided by the repo mean. 1.0 = typical for this repo |
| `columns.path_failure_mode_revert` | number or null 0–1 |  |  | Share of past bad changes on these paths that were reverts |
| `columns.path_failure_mode_ci` | number or null 0–1 |  |  | Share that were post-merge CI failures |
| `columns.path_failure_mode_hotfix` | number or null 0–1 |  |  | Share that were hotfixes. The three shares sum to 1 when not null. Encoded as shares, not a category, so trees can split on them |
| `columns.path_detection_lag_days` | number or null 0– |  |  | Median days from merge to the outcome event for past bad changes on these paths. High = failures here surface slowly |
| `columns.path_size_pctile` | number or null 0–1 |  |  | Percentile of this PR's LA+LD among past changes to the same files (weighted by lines per file). Complements the repo-wide pr_size_pctile_repo |
| `columns.release_cycle_position` | number or null 0– |  |  | Days since last release tag divided by the repo's typical release interval. Above 1 means a release is overdue |

### `evidence`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `evidence.similar_prs` | list of objects |  |  | Past PRs behind similar_pr_bad_rate |
| `evidence.similar_prs[].pr_number` | integer |  |  | Past PR number |
| `evidence.similar_prs[].overlap` | number 0–1 |  |  | Jaccard over touched files with this PR |
| `evidence.similar_prs[].outcome` | `clean`, `revert`, `ci_fail`, `hotfix` |  |  | How the past PR turned out |
| `evidence.similar_prs[].merged_at` | string (date-time) |  |  | When the past PR merged |
| `evidence.scrutiny_top_path` | object or null |  |  | Touched path with the highest scrutiny ratio, for revealed_path_scrutiny. |
| `evidence.failure_mode_top` | object or null |  |  | Most common failure mode on these paths, for the path_failure_mode_* columns. |
| `evidence.size_context` | object or null |  |  | Comparison behind path_size_pctile. |
| `evidence.release_context` | object or null |  |  | Release history behind release_cycle_position. |

## Outcome import (`outcome_import.schema.json`)

**Version:** 1.0

Body of POST /v1/outcomes. Lets an org report outcomes Riffle cannot see (internal reverts, incidents, internal CI). Signed with the secret named in rules sources.outcome_import. Imported outcomes become the external_import label source, weighted by its label_source rule. Idempotent on event_id. Never carries code or diffs.

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `event_id` | string |  |  | Sender's unique id; resends are ignored |
| `repo` | string matching `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$` |  |  | Repository as owner/name |
| `target.commit_sha` | string matching `^[0-9a-f]{40}$` |  |  | Commit the outcome is about |
| `target.pr_number` | integer 1– |  |  | PR the outcome is about |
| `target.external_change_id` | string |  |  | Matched through link rules, e.g. D12345678 |
| `outcome` | `revert`, `incident`, `ci_fail`, `hotfix`, `clean` |  |  | What happened to the target |
| `severity` | integer 1–4 |  |  | 1 is worst |
| `occurred_at` | string (date-time) |  |  | When the outcome happened |
| `source` | string |  |  | Free label for audit, e.g. internal-ci |

## Riffle head commit features (`mined_commit_features.schema.json`)

**Version:** 1.0

Code features of each head commit a PR had while open, captured during mining (snapshot_capture.py) for the PRs snapshots are built for: up to snapshot_max_heads per PR. Feature columns are null on the at-open head, whose features are in the mined row, and on a head that failed (error).

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `repo` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `pr_number` | integer |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_sha` | string |  |  | A head commit the PR had while it was open |
| `base_sha` | string or null |  |  | Its fork point from the base branch; the code features are base...head |
| `first_seen` | string (date-time) |  |  | When this commit became the PR's head (commit time standing in for push time) |
| `at_open` | boolean |  |  | The at-open head; its features are the mined row's, not repeated here |
| `error` | string or null |  |  | Why the features couldn't be computed, if they couldn't |
| `Entropy` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `LA` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `LD` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `ND` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `NF` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `NS` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `api_contract_change` | `additive`, `breaking`, `None` |  |  | Source 5 API contract diff. Computed in `pipelines/ingestion/repo_miner/features/api_contract.py`. |
| `bound_or_comparison_edit` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `ccn_delta` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `dependency_major_bump_count` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `docs_only` | boolean or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `frac_generated_lines` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `frac_test_lines` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `ghsa_malware_dep` | boolean or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `ghsa_max_severity_introduced` | `LOW`, `MODERATE`, `HIGH`, `CRITICAL`, `None` |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `hardcoded_secret_added` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `lang_mix` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `max_ccn_changed_func` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `migration_reversibility` | `destructive`, `reversible`, `None` |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `n_deps_added` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_deps_downgraded` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_deps_upgraded` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_files_added` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_files_removed` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_files_renamed` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_funcs_modified` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `n_major_bumps` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `new_env_or_config_key` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `observability_delta` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `osv_ghsa_disagreement` | any |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. Type not pinned yet: null in every sampled row. |
| `osv_vulns_fixed` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `osv_vulns_introduced` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `patch_missing_files` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `semgrep_cwe_top25_new` | boolean or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `semgrep_findings_new` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `semgrep_high_conf_security_new` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `test_only` | boolean or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `touches_auth_path` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_ci_config` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_codeowners_or_riffle_yml` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_dep_manifest` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_infra` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_lockfile` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_migration` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `unused_import_or_dead_code_added` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `vuln_has_fix_available` | boolean or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |

## Riffle repo miner training row (`mined_dataset.schema.json`)

**Version:** 1.1

One row of the training dataset written by the repo miner (pipelines/ingestion/repo_miner, .parquet or .jsonl): one closed PR, merged or unmerged (ADR 0009), with every feature computed from the PR as it opened (ADR 0010) and its outcome labels. Labels and the counts behind them are outcomes and never model inputs. The nine repo-local columns are defined in mined_features.schema.json. null means not observed or not enough history, never 0. Every column is present on every row; a stage that is turned off leaves its columns null. Since 1.1 rows also carry provenance (miner_version, miner_commit, llm_model, llm_prompt_version) and domain; these are optional in the schema so rows mined before miner 4.0.0 still validate, and the miner writes them on every row.

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `AGE` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `EXP` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `Entropy` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `FIX` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `LA` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `LD` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `LT` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `ND` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `NF` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `NS` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `REXP` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `SEXP` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `agent_boilerplate` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `agent_name` | `claude_code`, `codex`, `copilot`, `cursor`, `devin`, `jules`, `sweep`, `None` |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `agent_recent_closed_count` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `agent_recent_merge_rate` | any |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. Type not pinned yet: null in every sampled row. |
| `ai_agent_authored` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `api_contract_change` | `additive`, `breaking`, `None` |  |  | Source 5 API contract diff. Computed in `pipelines/ingestion/repo_miner/features/api_contract.py`. |
| `author_account_age_days` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_association` | `OWNER`, `MEMBER`, `COLLABORATOR`, `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, `FIRST_TIMER`, `MANNEQUIN`, `NONE`, `None` |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_days_since_last_change` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `author_email` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_file_familiarity` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `author_is_bot` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_post_merge_ci_fail_rate` | any |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. Type not pinned yet: null in every sampled row. |
| `author_prs_merged_repo` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_revert_rate` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `authz_check_change` | `none`, `added`, `removed`, `weakened`, `moved`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `base_behind_by` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `base_sha` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `body_checklist_items` | number or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `body_has_test_plan` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `body_headings` | number or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `body_len` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `bound_or_comparison_edit` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `ccn_delta` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `change_intent` | `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `build_ci`, `chore`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `changes_public_api_signature` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `changes_requested_reviews` | number or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `ci_observed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `close_reason` | `duplicate`, `unwanted`, `ci_technical`, `abandoned`, `other`, `None` |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `close_reason_source` | `llm`, `rules`, `None` |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `closed_at` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `cochange_degree` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `cochange_partners_missing` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `concurrent_open_prs_same_files` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `concurrent_open_prs_same_hunks` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `config_or_feature_flag_change` | `none`, `config`, `feature_flag`, `both`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `created_at` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `days_to_hotfix` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `days_to_revert` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `days_to_szz_fix` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `db_schema_or_query_change` | `none`, `schema`, `query`, `both`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `declared_hotfix` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `dependency_major_bump_count` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `docs_only` | boolean or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `entropy_pctile_repo` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `error_handling_change` | `none`, `added`, `removed`, `modified`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `exception_swallowed` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `expected_test_cochange_missing` | boolean or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `feature_flag_guarding` | `n_a`, `guarded`, `partial`, `unguarded`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `frac_generated_lines` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `frac_test_lines` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `ghsa_malware_dep` | boolean or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `ghsa_max_severity_introduced` | `LOW`, `MODERATE`, `HIGH`, `CRITICAL`, `None` |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `hardcoded_secret_added` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `has_agents_md` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `has_contributing` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `has_linked_issue` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `has_milestone` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_branch_agent_prefix` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_from_fork` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_sha` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `hotfix_window_closed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `input_validation_change` | `none`, `added`, `weakened`, `modified`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `la_norm` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `label_abandoned` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_bad` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_bad_observed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_ci_fail` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `label_ci_fail_optional` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `label_ci_technical` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_duplicate` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_hotfix` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_merged` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_revert` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_rework` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_szz_bug` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_unwanted` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `lang_mix` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `ld_norm` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `llm_touches_authn_authz` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `max_NDEV` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_NUC` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_abandoned_lines_share` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_ccn_changed_func` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `max_file_churn_365d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_churn_90d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_complexity_ccn` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_fix_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_hotspot_score` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_minor_contributor_count` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_post_merge_ci_fail_rate` | any |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. Type not pinned yet: null in every sampled row. |
| `max_file_revert_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_top_author_share` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_fix_decay_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_hotfix_count_365d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_hunk_fix_proximity` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `max_mod_lines_distinct_authors` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `max_mod_lines_prior_fix_share` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `max_recent_merge_burst_7d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_revert_decay_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_sim_open_prs` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `max_sim_recent_merged` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `max_test_cochange_ratio` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_file_age_days` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_file_fix_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_file_revert_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_hunk_fix_proximity` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `mean_mod_lines_self_authored_share` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `median_mod_line_age_days_median` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `merge_via` | `github`, `landed_commit`, `None` |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `merged_at` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `migration_reversibility` | `destructive`, `reversible`, `None` |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `min_days_since_last_fix` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `min_days_since_last_revert` | any |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. Type not pinned yet: null in every sampled row. |
| `min_file_hotspot_rank` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `min_hotspot_rank_touched` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `min_mod_line_age_days_min` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `mined_at` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `n_deps_added` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_deps_downgraded` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_deps_upgraded` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_files_added` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_files_removed` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_files_renamed` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_funcs_modified` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `n_major_bumps` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_topk_hotspots_touched` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `new_env_or_config_key` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `nf_pctile_repo` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `null_handling_change` | `none`, `added`, `removed`, `new_nullable_deref`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `numeric_money_precision` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `observability_delta` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `observed_through` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `open_snapshot` | `exact`, `recovered`, `approx`, `final`, `None` |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `osv_ghsa_disagreement` | any |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. Type not pinned yet: null in every sampled row. |
| `osv_vulns_fixed` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `osv_vulns_introduced` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `patch_missing_files` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `path_detection_lag_days` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_failure_mode_ci` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_failure_mode_hotfix` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_failure_mode_revert` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_size_pctile` | number or null |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. |
| `post_open_commits` | number or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `post_open_force_pushes` | number or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `pr_number` | integer |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `pr_size_pctile_repo` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `refactor_vs_behavior` | `pure_refactor`, `behavior_change`, `mixed`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `release_cycle_position` | number or null |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. |
| `repo` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `repo_agent_pr_load_30d` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `repo_median_first_response_hours` | any |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. Type not pinned yet: null in every sampled row. |
| `repo_outside_closed_count` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `repo_outside_merge_rate` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `repo_revert_base_rate` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `resource_lifecycle_change` | `none`, `acquire_no_release`, `release_removed`, `modified`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `retry_timeout_idempotency_change` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `revealed_path_scrutiny` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `revert_window_closed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `satd_added` | integer or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `security_sensitive_ops` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `semantic_risk_1to5` | integer or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `semgrep_cwe_top25_new` | boolean or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `semgrep_findings_new` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `semgrep_high_conf_security_new` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `similar_pr_bad_rate` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `similar_pr_count` | number or null |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. |
| `sum_file_complexity_ccn` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `sum_hotfix_count_365d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `targets_default_branch` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `test_code_alignment` | `none`, `unrelated`, `partial`, `aligned`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `test_only` | boolean or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `tests_added_for_behavior_change` | `yes`, `no`, `n_a`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `time_handling_change` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `title_len` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `touches_auth_path` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_ci_config` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_codeowners_or_riffle_yml` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_dep_manifest` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_infra` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_lockfile` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_migration` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_topk_hotspot` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `transaction_boundary_change` | `none`, `added`, `removed`, `widened`, `narrowed`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `unrelated_change_share` | integer or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `unused_import_or_dead_code_added` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `vuln_has_fix_available` | boolean or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `miner_version` | string |  |  | Repo miner version that wrote the row (version.py). Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `miner_commit` | string |  |  | The miner's git commit (12 characters), with -dirty when its files had uncommitted changes, or unknown. Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `llm_model` | string or null |  |  | LLM model the row's LLM columns were asked of; null when the LLM was off. Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `llm_prompt_version` | string or null |  |  | Hash of the LLM prompts and schemas (flags and close-reason classifier); null when the LLM was off. Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `domain` | string or null |  |  | Dataset domain of the repository (ADR 0011), from its [domain: NAME] tag in the repos file; null when untagged. Computed in `pipelines/ingestion/repo_miner/build_dataset.py`. |

## Riffle mined history manifest (`mined_history.schema.json`)

**Version:** 1.0

Manifest written by the Go history miner (services/miner) for one repository and one run, next to the Parquet tables it lists. pipelines/ and scorer read the tables only through this manifest. Tables are raw history, not model features: every feature is computed in Python from these tables, in training and serving alike. Data stays inside the installation; author emails are stored only as salted hashes. A run is usable only when status is complete; partial runs carry a checkpoint and are resumed, never read.

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `run_id` | string |  |  | Unique per run; reruns with the same run_id overwrite the same files (idempotent) |
| `tenant_id` | string |  |  | Installation the run belongs to |
| `repo` | string matching `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$` |  |  | Repository as owner/name |
| `miner_version` | string |  |  | Pinned into model_version via the feature extractor version |
| `status` | `running`, `partial`, `complete`, `failed` |  |  | Run state; only complete runs are read |
| `window.from` | string (date-time) |  |  | Start of the history covered |
| `window.to` | string (date-time) |  |  | End of the history covered |
| `window.head_sha` | string matching `^[0-9a-f]{40}$` |  |  | Default branch head the run mined up to |
| `started_at` | string (date-time) |  |  | When the run started |
| `finished_at` | string or null (date-time) |  |  | When the run finished; null if it has not |
| `checkpoint` | object or null |  |  | Where a partial or failed run resumes. Opaque to readers. |
| `rate_limit.requests` | integer 0– |  |  | GitHub API requests made |
| `rate_limit.conditional_hits` | integer 0– |  |  | 304 responses, which do not count against the limit |
| `rate_limit.secondary_limit_waits` | integer 0– |  |  | Times the run waited on a GitHub secondary rate limit |
| `coverage.check_runs_from` | string or null (date-time) |  |  | Earliest check result still retrievable; ci_fail is unobserved before this |
| `coverage.reviews_from` | string or null (date-time) |  |  | Earliest review still retrievable |
| `tables` | list of objects |  |  | Parquet tables the run wrote. |
| `tables[].name` | `commits`, `file_changes`, `pull_requests`, `reviews`, `check_runs`, `tags` |  |  | Table name; columns are listed in x-riffle-tables |
| `tables[].uri` | string |  |  | gs:// or file:// path to the Parquet file |
| `tables[].rows` | integer 0– |  |  | Row count |
| `tables[].sha256` | string matching `^[0-9a-f]{64}$` |  |  | SHA-256 of the Parquet file |

### Table `commits`

| Column | Type and meaning |
|---|---|
| `sha` | string, primary key |
| `parent_shas` | list<string> |
| `author_login` | string or null, resolved through identity rules |
| `author_email_hash` | string, salted per installation |
| `committed_at` | timestamp |
| `message` | string, used by pattern and link rules |
| `is_merge` | bool |

### Table `file_changes`

| Column | Type and meaning |
|---|---|
| `sha` | string, commits.sha |
| `path` | string |
| `old_path` | string or null, set on rename |
| `status` | added \| modified \| removed \| renamed |
| `lines_added` | int |
| `lines_deleted` | int |
| `is_binary` | bool |

### Table `pull_requests`

| Column | Type and meaning |
|---|---|
| `number` | int, primary key |
| `author_login` | string |
| `created_at` | timestamp |
| `merged_at` | timestamp or null |
| `closed_at` | timestamp or null |
| `base_branch` | string |
| `head_branch` | string |
| `merge_commit_sha` | string or null |
| `labels_at_open` | list<string> |
| `is_draft_at_open` | bool |
| `title` | string |

### Table `reviews`

| Column | Type and meaning |
|---|---|
| `pr_number` | int, pull_requests.number |
| `reviewer_login` | string |
| `state` | approved \| changes_requested \| commented \| dismissed |
| `submitted_at` | timestamp |
| `comment_count` | int |

### Table `check_runs`

| Column | Type and meaning |
|---|---|
| `head_sha` | string |
| `name` | string |
| `conclusion` | success \| failure \| neutral \| cancelled \| skipped \| timed_out \| action_required \| null |
| `run_attempt` | int, for flaky detection |
| `app_id` | int |
| `completed_at` | timestamp or null |

### Table `tags`

| Column | Type and meaning |
|---|---|
| `name` | string |
| `sha` | string |
| `created_at` | timestamp |

## Riffle PR snapshot row (`mined_snapshot.schema.json`)

**Version:** 1.1

One PR at one moment while it was open (pipelines/ingestion/repo_miner/snapshots.py), labelled with how the PR finally ended. Every column of mined_dataset.schema.json, with three groups taken as of snapshot_at instead of the PR's open: the code features of the head commit then (diff sizes, path flags, deterministic, scanner, dependency and API-contract columns), the merge signals (repo_outside_merge_rate and the rest), and the snapshot state columns. Git-history, blame, LLM and PR-metadata columns stay as at open. Labels are outcomes and never model inputs; so is near_decided. Bots count for nothing in the state.

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `AGE` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `EXP` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `Entropy` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `FIX` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `LA` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `LD` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `LT` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `ND` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `NF` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `NS` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `REXP` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `SEXP` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `agent_boilerplate` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `agent_name` | `claude_code`, `codex`, `copilot`, `cursor`, `devin`, `jules`, `sweep`, `None` |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `agent_recent_closed_count` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `agent_recent_merge_rate` | any |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. Type not pinned yet: null in every sampled row. |
| `ai_agent_authored` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `api_contract_change` | `additive`, `breaking`, `None` |  |  | Source 5 API contract diff. Computed in `pipelines/ingestion/repo_miner/features/api_contract.py`. |
| `author_account_age_days` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_association` | `OWNER`, `MEMBER`, `COLLABORATOR`, `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, `FIRST_TIMER`, `MANNEQUIN`, `NONE`, `None` |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_days_since_last_change` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `author_email` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_file_familiarity` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `author_is_bot` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_post_merge_ci_fail_rate` | any |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. Type not pinned yet: null in every sampled row. |
| `author_prs_merged_repo` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `author_revert_rate` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `authz_check_change` | `none`, `added`, `removed`, `weakened`, `moved`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `base_behind_by` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `base_sha` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `body_checklist_items` | number or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `body_has_test_plan` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `body_headings` | number or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `body_len` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `bound_or_comparison_edit` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `ccn_delta` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `change_intent` | `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `build_ci`, `chore`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `changes_public_api_signature` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `changes_requested_reviews` | number or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `ci_observed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `close_reason` | `duplicate`, `unwanted`, `ci_technical`, `abandoned`, `other`, `None` |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `close_reason_source` | `llm`, `rules`, `None` |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `closed_at` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `cochange_degree` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `cochange_partners_missing` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `concurrent_open_prs_same_files` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `concurrent_open_prs_same_hunks` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `config_or_feature_flag_change` | `none`, `config`, `feature_flag`, `both`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `created_at` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `days_to_hotfix` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `days_to_revert` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `days_to_szz_fix` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `db_schema_or_query_change` | `none`, `schema`, `query`, `both`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `declared_hotfix` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `dependency_major_bump_count` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `docs_only` | boolean or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `entropy_pctile_repo` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `error_handling_change` | `none`, `added`, `removed`, `modified`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `exception_swallowed` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `expected_test_cochange_missing` | boolean or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `feature_flag_guarding` | `n_a`, `guarded`, `partial`, `unguarded`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `frac_generated_lines` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `frac_test_lines` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `ghsa_malware_dep` | boolean or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `ghsa_max_severity_introduced` | `LOW`, `MODERATE`, `HIGH`, `CRITICAL`, `None` |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `hardcoded_secret_added` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `has_agents_md` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `has_contributing` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `has_linked_issue` | boolean or null |  |  | Merge-model signal. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `has_milestone` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_branch_agent_prefix` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_from_fork` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `head_sha` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `hotfix_window_closed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `input_validation_change` | `none`, `added`, `weakened`, `modified`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `la_norm` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `label_abandoned` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_bad` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_bad_observed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_ci_fail` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `label_ci_fail_optional` | any |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. Type not pinned yet: null in every sampled row. |
| `label_ci_technical` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_duplicate` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_hotfix` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_merged` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_revert` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_rework` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `label_szz_bug` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `label_unwanted` | boolean or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `lang_mix` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `ld_norm` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `llm_touches_authn_authz` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `max_NDEV` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_NUC` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_abandoned_lines_share` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_ccn_changed_func` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `max_file_churn_365d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_churn_90d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_complexity_ccn` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_fix_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_hotspot_score` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_minor_contributor_count` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_post_merge_ci_fail_rate` | any |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. Type not pinned yet: null in every sampled row. |
| `max_file_revert_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_file_top_author_share` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_fix_decay_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_hotfix_count_365d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_hunk_fix_proximity` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `max_mod_lines_distinct_authors` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `max_mod_lines_prior_fix_share` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `max_recent_merge_burst_7d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_revert_decay_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `max_sim_open_prs` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `max_sim_recent_merged` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `max_test_cochange_ratio` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_file_age_days` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_file_fix_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_file_revert_density` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `mean_hunk_fix_proximity` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `mean_mod_lines_self_authored_share` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `median_mod_line_age_days_median` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `merge_via` | `github`, `landed_commit`, `None` |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `merged_at` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `migration_reversibility` | `destructive`, `reversible`, `None` |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `min_days_since_last_fix` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `min_days_since_last_revert` | any |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. Type not pinned yet: null in every sampled row. |
| `min_file_hotspot_rank` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `min_hotspot_rank_touched` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `min_mod_line_age_days_min` | number or null |  |  | Source 3 line-level blame. Computed in `pipelines/ingestion/repo_miner/features/line_history.py`. |
| `mined_at` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `n_deps_added` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_deps_downgraded` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_deps_upgraded` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_files_added` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_files_removed` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_files_renamed` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `n_funcs_modified` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `n_major_bumps` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `n_topk_hotspots_touched` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `new_env_or_config_key` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `nf_pctile_repo` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `null_handling_change` | `none`, `added`, `removed`, `new_nullable_deref`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `numeric_money_precision` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `observability_delta` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `observed_through` | string or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `open_snapshot` | `exact`, `recovered`, `approx`, `final`, `None` |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `osv_ghsa_disagreement` | any |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. Type not pinned yet: null in every sampled row. |
| `osv_vulns_fixed` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `osv_vulns_introduced` | number or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `patch_missing_files` | number or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `path_detection_lag_days` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_failure_mode_ci` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_failure_mode_hotfix` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_failure_mode_revert` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `path_size_pctile` | number or null |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. |
| `post_open_commits` | number or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `post_open_force_pushes` | number or null |  |  | Merge-outcome label or its counts (ADR 0009), an outcome, never a model input. Computed in `pipelines/ingestion/repo_miner/merge_labels.py`. |
| `pr_number` | integer |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `pr_size_pctile_repo` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `refactor_vs_behavior` | `pure_refactor`, `behavior_change`, `mixed`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `release_cycle_position` | number or null |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. |
| `repo` | string |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `repo_agent_pr_load_30d` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `repo_median_first_response_hours` | any |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. Type not pinned yet: null in every sampled row. |
| `repo_outside_closed_count` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `repo_outside_merge_rate` | number or null |  |  | Merge-model signal, prior PRs only. Computed in `pipelines/ingestion/repo_miner/features/merge_signals.py`. |
| `repo_revert_base_rate` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `resource_lifecycle_change` | `none`, `acquire_no_release`, `release_removed`, `modified`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `retry_timeout_idempotency_change` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `revealed_path_scrutiny` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `revert_window_closed` | boolean or null |  |  | Post-merge label or outcome detail (ADR 0002), never a model input; null on unmerged rows. Computed in `pipelines/ingestion/repo_miner/labels.py`. |
| `satd_added` | integer or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `security_sensitive_ops` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `semantic_risk_1to5` | integer or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `semgrep_cwe_top25_new` | boolean or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `semgrep_findings_new` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `semgrep_high_conf_security_new` | number or null |  |  | Source 4d scanners. Computed in `pipelines/ingestion/repo_miner/features/scanners.py`. |
| `similar_pr_bad_rate` | any |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. Type not pinned yet: null in every sampled row. |
| `similar_pr_count` | number or null |  |  | Repo-local mined feature (mined_features.schema.json). Computed in `pipelines/ingestion/repo_miner/features/repo_local.py`. |
| `sum_file_complexity_ccn` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `sum_hotfix_count_365d` | number or null |  |  | Source 2 repo history. Computed in `pipelines/ingestion/repo_miner/features/history.py`. |
| `targets_default_branch` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `test_code_alignment` | `none`, `unrelated`, `partial`, `aligned`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `test_only` | boolean or null |  |  | Source 4a/4b diff and paths. Computed in `pipelines/ingestion/repo_miner/features/diff_stats.py`. |
| `tests_added_for_behavior_change` | `yes`, `no`, `n_a`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `time_handling_change` | boolean or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `title_len` | number or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `touches_auth_path` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_ci_config` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_codeowners_or_riffle_yml` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_dep_manifest` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_infra` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_lockfile` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_migration` | boolean or null |  |  | Source 4b path flag. Computed in `pipelines/ingestion/repo_miner/config.py`. |
| `touches_topk_hotspot` | boolean or null |  |  | Row metadata or Source 4c PR metadata. Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `transaction_boundary_change` | `none`, `added`, `removed`, `widened`, `narrowed`, `None` |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `unrelated_change_share` | integer or null |  |  | Source 6 LLM flag (diff only). Computed in `pipelines/ingestion/repo_miner/features/llm_flags.py`. |
| `unused_import_or_dead_code_added` | number or null |  |  | Source 5 deterministic checks. Computed in `pipelines/ingestion/repo_miner/features/deterministic.py`. |
| `vuln_has_fix_available` | boolean or null |  |  | Source 4e dependencies and CVEs. Computed in `pipelines/ingestion/repo_miner/features/deps_cve.py`. |
| `snapshot_id` | string |  |  | Stable id: hash of repo, PR number, trigger and time, so re-mining gives the same rows |
| `snapshot_at` | string (date-time) |  |  | The moment the snapshot describes (UTC). Everything in the row is as of this moment, except labels |
| `snapshot_index` | integer 0– |  |  | Order of this snapshot among the PR's kept snapshots |
| `trigger` | `approval`, `author_fix_push`, `back_to_draft`, `changes_requested`, `clock`, `first_maintainer_reply`, `idle`, `marked_duplicate`, `no_maintainer_reply`, `open`, `outcome_label` |  |  | What made this moment a snapshot: an event, or a clock (time since start, idle, no maintainer reply) |
| `trigger_role` | `author`, `maintainer`, `other`, `clock` |  |  | Who caused the trigger; clock for time-based snapshots |
| `snapshot_head_sha` | string or null |  |  | The PR's head commit at snapshot_at (or the nearest earlier captured one, see head_found) |
| `snapshot_base_sha` | string or null |  |  | Fork point the head's code features were computed from |
| `head_found` | `open`, `exact`, `approx`, `capped` |  |  | open: the at-open head; exact: from the head line with force-push history known; approx: force-push history unreadable; capped: the true head wasn't captured, so the nearest earlier captured one is used |
| `near_decided` | boolean |  |  | Last hour before close, or after the PR entered a merge queue or got auto-merge. For filtering in training; never a model input |
| `time_since_start_h` | number 0– |  |  | Snapshot state. Hours since the clock started (open, ready-for-review, or the last reopen) |
| `time_since_last_human_activity_h` | number 0– |  |  | Snapshot state. Hours since the last non-bot event |
| `maintainer_replied` | boolean |  |  | Snapshot state. A maintainer has commented or reviewed |
| `first_maintainer_reply_h` | number or null |  |  | Snapshot state. Hours from start to the first maintainer reply; null if none yet |
| `review_rounds` | integer 0– |  |  | Snapshot state. Reviews by humans other than the author |
| `changes_requested_count` | integer 0– |  |  | Snapshot state. Changes-requested reviews so far |
| `approvals_count` | integer 0– |  |  | Snapshot state. Approving reviews so far |
| `commits_since_start` | integer 0– |  |  | Snapshot state. Commits pushed after the start (merges from the base not counted) |
| `force_pushes_since_start` | integer 0– |  |  | Snapshot state. Force-pushes after the start |
| `author_pushed_after_last_review` | boolean |  |  | Snapshot state. The author pushed after the latest review |
| `maintainer_comment_count` | integer 0– |  |  | Snapshot state. Comments by maintainers |
| `author_comment_count` | integer 0– |  |  | Snapshot state. Comments by the PR author |
| `other_comment_count` | integer 0– |  |  | Snapshot state. Comments by other humans |
| `has_outcome_label` | boolean |  |  | Snapshot state. An outcome label (duplicate, wontfix, stale, needs-info, ...) is on the PR now |
| `marked_duplicate_so_far` | boolean |  |  | Snapshot state. Someone marked the PR as a duplicate |
| `is_draft_now` | boolean |  |  | Snapshot state. The PR is back in draft |
| `reopen_count` | integer 0– |  |  | Snapshot state. Times the PR was reopened so far |
| `new_commits_since_prev` | integer or null |  |  | Snapshot state. Commits since the previous kept snapshot; null on the first |
| `new_comments_since_prev` | integer or null |  |  | Snapshot state. Human comments since the previous kept snapshot; null on the first |
| `diff_size_change_since_prev` | number or null |  |  | Snapshot state. Change in LA + LD since the previous kept snapshot; null on the first |
| `miner_version` | string |  |  | Repo miner version that wrote the row (version.py). Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `miner_commit` | string |  |  | The miner's git commit (12 characters), with -dirty when its files had uncommitted changes, or unknown. Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `llm_model` | string or null |  |  | LLM model the row's LLM columns were asked of; null when the LLM was off. Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `llm_prompt_version` | string or null |  |  | Hash of the LLM prompts and schemas (flags and close-reason classifier); null when the LLM was off. Provenance (proposal 1.7, 1.8). Computed in `pipelines/ingestion/repo_miner/mine_repo.py`. |
| `domain` | string or null |  |  | Dataset domain of the repository (ADR 0011), from its [domain: NAME] tag in the repos file; null when untagged. Computed in `pipelines/ingestion/repo_miner/build_dataset.py`. |
