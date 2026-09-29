# Contracts reference

Generated from the schemas by `gen_reference.py`. Do not edit by hand.

- [Rules](#rules-org_rulesschemajson)
- [Settings](#settings-org_configschemajson)
- [Mined features](#mined-features-mined_featuresschemajson)
- [Outcome import](#outcome-import-outcome_importschemajson)

## Rules (`org_rules.schema.json`)

How an organisation manages its code, as flat typed rules (kind + match + value). Orgs may write any number of rules, but the compiler always reduces them to the fixed column set in x-riffle-model-columns. Undeclared means null, never 0. Adding a column is a schema version bump, a retrain, and a decision record. Rules are read point-in-time from git history of the rules file, and the compiled hash is pinned into model_version. Read from the default branch only; an invalid file keeps the last valid compiled rules and posts a notice. Org file: riffle/rules.yml in the org's .github repo. Repo file: .github/riffle/rules.yml, appended after org rules and limited to that repo.

### Fields every rule accepts

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `id` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  |  |
| `kind` | string |  |  |  |
| `applies_to.repos` | list of string | `["*"]` |  |  |
| `applies_to.paths` | list of string |  |  | Limit to a monorepo package, e.g. packages/react-native/** |
| `applies_to.branches` | list of string |  |  |  |
| `priority` | integer 0–100 | `50` |  | Conflict resolution: higher wins, then the more specific glob, then the later rule. |
| `enabled` | boolean | `true` |  |  |
| `note` | string |  |  |  |

### Rule kinds

#### `path_tier`

**Sink:** feature  
**Compiles to:** `max_criticality`, `frac_lines_in_critical`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required |  |
| `value` | `low`, `normal`, `elevated`, `critical` |  | required |  |

#### `path_class`

**Sink:** feature | filter  
**Compiles to:** `effective_lines_changed`, `test_lines_ratio`, `docs_only`, `config_only`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required |  |
| `value` | `excluded`, `vendored`, `generated`, `lockfile`, `test`, `test_fixture`, `docs`, `config`, `build_config`, `dependency_manifest`, `i18n` |  | required |  |

#### `sensitivity`

What the code handles. Split from change_signal so authn and authz stay distinct.

**Sink:** feature  
**Compiles to:** `n_sensitivity_tags`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required |  |
| `value` | `authn`, `authz`, `payments`, `pii`, `secrets`, `crypto`, `infra`, `public_api` |  | required |  |

#### `change_signal`

Risky change types. Handled by the extractor's own path rules too; these declarations override its defaults per repo.

**Sink:** feature  
**Compiles to:** `sharpens extractor touches_* features; no new columns`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required |  |
| `value` | `schema_migration`, `wire_protocol`, `infra_as_code`, `build_system`, `dependency_bump`, `feature_flag`, `concurrency`, `native_interop`, `release_config` |  | required |  |

#### `component`

Names a monorepo package or module. The name is free text, but the model only sees generic stats computed per component from history.

**Sink:** feature  
**Compiles to:** `n_components_touched`, `max_criticality`, `max_exposure`, `max_data_class`, `regulated_scope_count`, `log_max_blast_radius`, `touches_deprecated`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | e.g. streams/** or packages/virtualized-lists/** |
| `value` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  | required |  |
| `tier` | integer 1–4 |  |  | Availability tier, 1 is most critical |
| `exposure` | `internal`, `partner`, `external` |  |  |  |
| `data_class` | `public`, `internal`, `confidential`, `restricted` |  |  |  |
| `regimes` | list of `pci`, `gdpr`, `hipaa`, `sox`, `soc2`, `fedramp` |  |  |  |
| `lifecycle` | `experimental`, `active`, `stable`, `deprecated` |  |  |  |
| `blast_radius` | integer 0– |  |  | Declared downstream dependents |

#### `owner`

Path ownership. import_codeowners reads CODEOWNERS instead of listing paths.

**Sink:** feature  
**Compiles to:** `author_is_owner`, `owning_teams_touched (extractor, not a declared column)`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob, or the CODEOWNERS path when import_codeowners is true |
| `value` | string |  | required | Team or login, e.g. @apache/kafka-streams |
| `import_codeowners` | boolean | `false` |  |  |

#### `policy`

Team policies checked against the diff. Each firing policy adds one to policy_violations; the specific policy is shown in the comment, not given its own column.

**Sink:** feature  
**Compiles to:** `policy_violations`, `required_tests_missing`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Path glob the policy covers |
| `value` | `required_tests`, `high_scrutiny`, `perf_critical`, `no_import_from`, `flag_required`, `migration_reversible`, `migration_no_destructive`, `dependency_allowlist`, `dependency_denylist`, `regen_pair` |  | required |  |
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
| `start` | string (date-time) |  | required |  |
| `end` | string (date-time) |  | required |  |
| `match` | string | `"*"` |  | Branch glob |

#### `pr_label`

Maps PR labels at open time. Different from issue_label, which is about linked issues.

**Sink:** feature  
**Compiles to:** `label_priority`, `label_is_hotfix`, `label_security`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Exact PR label name |
| `value` | `priority`, `hotfix`, `security`, `safe` |  | required |  |
| `level` | integer 0–3 |  |  | priority only: 3 is highest (P0) |

#### `safe_change`

Which change kinds this team treats as safe. No match; the extractor classifies lines.

**Sink:** feature  
**Compiles to:** `is_safe_change_only`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `value` | `docs`, `tests`, `formatting`, `comments`, `generated`, `i18n` |  | required |  |

#### `pattern`

**Sink:** label | feature  
**Compiles to:** `task_type`, `is_backport`, `labels: revert, bugfix`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string (regex) |  | required |  |
| `value` | `revert`, `bugfix`, `hotfix`, `backport`, `conventional_type`, `security_fix` |  | required |  |
| `field` | `commit_message`, `commit_trailer`, `pr_title`, `pr_body`, `branch_name` | `"commit_message"` |  |  |

#### `link`

Connects objects Riffle would otherwise treat as unrelated. match must contain a named group (?<ref>...).

**Sink:** link  
**Compiles to:** `commit-PR-issue graph used by every label source; cherry picks dedupe labels across branches`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string (regex) |  | required |  |
| `field` | `commit_message`, `commit_trailer`, `pr_title`, `pr_body` | `"commit_trailer"` |  |  |
| `value` | `pr_ref`, `revert_target`, `cherry_pick_source`, `issue_key`, `external_change_id` |  | required |  |
| `tracker` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | For issue_key: which sources.trackers entry |

#### `identity`

Recovers the real person when a bot lands the commit or email differs from login. match needs a named group (?<login>...) or (?<email>...).

**Sink:** feature  
**Compiles to:** `author_* features use the resolved author`, `reviewer_count`, `reviewer_path_experience`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string (regex) |  | required |  |
| `on` | `commit_trailer`, `commit_message`, `pr_body`, `co_authored_by` | `"commit_trailer"` |  |  |
| `value` | `author`, `co_author`, `reviewer`, `lander` |  | required |  |

#### `merge_signal`

What counts as merged. Needed when PRs are closed and the change lands another way.

**Sink:** label  
**Compiles to:** `merged_at, merge_commit_sha for every outcome window`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `value` | `github_merge`, `linked_commit_on_default`, `bot_close_with_commit`, `direct_push` |  | required |  |
| `match` | string | `"*"` |  | Actor login for bot_close_with_commit, else '*' |

#### `issue_field`

Tracker fields beyond labels, e.g. Jira issuetype and priority.

**Sink:** label

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `tracker` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  | required |  |
| `field` | string |  | required | e.g. issuetype, priority, customfield_10020 |
| `match` | string |  | required |  |
| `value` | `bug`, `incident`, `regression`, `security`, `not_a_bug` |  | required |  |
| `severity` | integer 1–4 |  |  | 1 is worst; scales label weight |

#### `issue_label`

**Sink:** label

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `tracker` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` | `"github"` |  |  |
| `match` | string |  | required |  |
| `value` | `bug`, `incident`, `regression`, `security`, `not_a_bug` |  | required |  |
| `severity` | integer 1–4 |  |  |  |

#### `author_class`

**Sink:** feature  
**Compiles to:** `agent_in_critical`, `author_is_maintainer, author_is_external, author_is_new (extractor)`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required |  |
| `on` | `login`, `email_domain`, `branch_prefix`, `commit_trailer` | `"login"` |  |  |
| `value` | `bot`, `agent`, `maintainer`, `employee`, `external` |  | required |  |
| `new_contributor_days` | integer 1–730 |  |  | Optional; overrides the default tenure cut for authors this rule matches |

#### `ci_check`

**Sink:** feature | label  
**Compiles to:** `ci_required_failed`, `ci_failures_non_flaky`, `label: post_merge_ci`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `source` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  | sources.ci entry; default is GitHub checks |
| `match` | string |  | required |  |
| `value` | `required`, `flaky`, `informational`, `auxiliary` |  | required | auxiliary (CodeQL, Scorecards, coverage) never triggers ci_fail |
| `stage` | `pre_merge`, `post_merge`, `nightly`, `release` | `"pre_merge"` |  |  |

#### `test_class`

Test-level flakiness from sources.test_reports, for repos with one giant CI check.

**Sink:** feature | label  
**Phase:** later

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required | Test id glob, e.g. org.apache.kafka.streams.integration.* |
| `value` | `flaky`, `quarantined`, `slow` |  | required |  |

#### `label_source`

Weight of an outcome source as a sample weight in this org's per-repo layer. Never applied to the global model. Windows: revert 30d, ci_fail on required checks only, hotfix is a hotfix-labelled PR on the same lines within 7d, follow_up_fix is any PR on the same lines within 14d.

**Sink:** label

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | `revert`, `ci_fail`, `hotfix`, `szz`, `follow_up_fix`, `route_override`, `incident_link`, `external_import` |  | required |  |
| `value` | number 0–2 |  | required |  |
| `window_days` | integer 1–90 |  |  |  |

#### `branch_class`

**Sink:** feature | label  
**Compiles to:** `targets_release_branch`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `match` | string |  | required |  |
| `value` | `default`, `release`, `hotfix`, `backport`, `long_lived`, `experimental` |  | required |  |

#### `stack`

Stacked PR tooling, so a stack is understood as related changes.

**Sink:** feature  
**Compiles to:** `stack_depth`, `stack_position`

| Field | Allowed | Default | Required | Meaning |
|---|---|---|---|---|
| `value` | `ghstack`, `graphite`, `sapling`, `spr`, `branch_chain` |  | required |  |
| `match` | string |  | required | Branch pattern or body marker the tool leaves |

### `sources` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `sources.trackers` | list of objects |  |  |  |
| `sources.trackers[].id` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  |  |
| `sources.trackers[].provider` | `github`, `jira`, `linear`, `bugzilla`, `youtrack` |  |  |  |
| `sources.trackers[].base_url` | string (uri) |  |  |  |
| `sources.trackers[].projects` | list of string |  |  |  |
| `sources.trackers[].credentials_ref` | string |  |  |  |
| `sources.ci` | list of objects |  |  |  |
| `sources.ci[].id` | string matching `^[a-z0-9][a-z0-9-]{0,63}$` |  |  |  |
| `sources.ci[].provider` | `github_checks`, `commit_status`, `external_api` |  |  | commit_status covers Jenkins, Buildkite and anything posting legacy statuses |
| `sources.ci[].context_glob` | string | `"*"` |  |  |
| `sources.ci[].credentials_ref` | string |  |  |  |
| `sources.test_reports.enabled` | boolean | `false` | later |  |
| `sources.test_reports.format` | `junit_xml` | `"junit_xml"` | later |  |
| `sources.test_reports.artifact_glob` | string | `"**/TEST-*.xml"` | later |  |
| `sources.outcome_import.enabled` | boolean | `false` |  |  |
| `sources.outcome_import.secret_ref` | string |  |  |  |

### `labels` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `labels.target` | `union`, `weighted` | `"union"` |  | union: label_bad = any target source fires (binary). weighted: each source's label_source weight becomes a sample weight. Per-repo layer only |
| `labels.szz_in_target` | boolean | `false` |  | SZZ stays a separate label unless true |
| `labels.maturity_days` | integer 7–180 | `30` |  | Rows younger than this are dropped before training (label_mature) |
| `labels.szz_maturity_days` | integer 30–365 | `90` |  |  |
| `labels.unobserved` | `mask`, `negative`, `downweight` | `"downweight"` |  | What to do when a label source had no data for a mature PR (e.g. CI results expired). Any observed source that fired still makes the row positive. Otherwise: mask drops the row; negative counts it as fully clean; downweight counts it as clean with sample weight unobserved_weight |
| `labels.unobserved_weight` | number 0.05–1 | `0.5` |  | downweight only. Multiplies the row's sample weight for each unobserved source (0.5 with one missing, 0.25 with two) |
| `labels.target_sources` | list of `revert`, `ci_fail`, `hotfix`, `szz`, `follow_up_fix`, `route_override`, `incident_link`, `external_import` | `["revert", "ci_fail", "hotfix"]` |  | Sources that can make a row positive. Others still get mined and reported as ablations, but do not enter the target |

### `mining` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `mining.similar_prs.k` | integer 3–20 | `5` |  |  |
| `mining.similar_prs.min_file_overlap` | number 0.05–1 | `0.2` |  | Jaccard over touched files |
| `mining.similar_prs.lookback_days` | integer 30–1095 | `365` |  |  |
| `mining.similar_prs.min_neighbours` | integer 1–10 | `3` |  | Fewer than this and the feature is null |
| `mining.scrutiny.lookback_days` | integer 30–1095 | `180` |  |  |
| `mining.scrutiny.exclude_bot_reviews` | boolean | `true` |  |  |
| `mining.scrutiny.min_prior_prs` | integer 1–50 | `5` |  |  |
| `mining.failure_mode.lookback_days` | integer 90–1095 | `365` |  |  |
| `mining.failure_mode.min_bad_changes` | integer 1–20 | `2` |  |  |
| `mining.path_size.lookback_days` | integer 30–1095 | `365` |  |  |
| `mining.path_size.min_prior_changes` | integer 3–100 | `10` |  |  |
| `mining.release_cycle.tag_pattern` | string (regex) | `"^v?\\d+\\.\\d+(\\.\\d+)?$"` |  | Which tags count as releases, e.g. ^\d+\.\d+\.0$ for minor releases only |
| `mining.release_cycle.min_releases` | integer 2–20 | `3` |  |  |
| `mining.release_cycle.interval_stat` | `median`, `mean` | `"median"` |  |  |
| `mining.flaky_detection.enabled` | boolean | `true` |  |  |
| `mining.flaky_detection.min_rerun_passes` | integer 1–20 | `3` |  |  |
| `mining.flaky_detection.lookback_days` | integer 14–365 | `90` |  |  |

### `model` block

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `model.per_repo_layer.enabled` | boolean | `true` |  | false serves the global model only (layer_weight 0) |
| `model.per_repo_layer.max_layer_weight` | number 0–1 | `1.0` |  |  |
| `model.disabled_features` | list of string | `[]` | union | Feature names forced to null. Width of the vector never changes. Repos may only add. Example: agent_authored |
| `model.backfill.lookback_days` | integer 90–3650 | `1095` |  |  |
| `model.backfill.recent_first_days` | integer 30–365 | `180` |  |  |

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
| `organization.data_retention.store_diffs` | boolean | `false` | org only |  |
| `organization.data_retention.retention_days` | integer 30–730 | `365` | org only |  |
| `organization.label_prefix` | string | `"riffle"` | org only |  |
| `organization.locked_keys` | list of string matching `^/` | `["/organization", "/llm/provider", "/llm/send"]` | org only | JSON pointers a repo file may not change |

### `scope`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `scope.base_branches` | list of string (regex) | `[]` |  | Extra target branches besides the default branch |
| `scope.include_drafts` | boolean | `false` |  |  |
| `scope.quiet_authors` | list of string | `["dependabot[bot]", "renovate[bot]", "github-actions[bot]"]` | union | Scored and shown on the dashboard, but no comment or label. Nothing is left unscored |
| `scope.quiet_title_keywords` | list of string | `["WIP", "DO NOT MERGE"]` | union | Same as quiet_authors, matched on the PR title |

### `rank_bands`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `rank_bands.mode` | `percentile`, `absolute` | `"percentile"` |  |  |
| `rank_bands.percentile.senior_recommended_top_fraction` | number 0–1 | `0.05` |  |  |
| `rank_bands.percentile.review_first_top_fraction` | number 0–1 | `0.2` |  | Matches the top-20% effort-aware recall metric |
| `rank_bands.percentile.window` | `repo`, `component` | `"repo"` |  | component ranks each monorepo package against itself |
| `rank_bands.absolute.senior_recommended_min_score` | number 0–1 | `0.8` |  |  |
| `rank_bands.absolute.review_first_min_score` | number 0–1 | `0.5` |  |  |
| `rank_bands.holdout_fraction` | number 0.02–0.1 | `0.05` |  | Share of PRs left unranked (FIFO) to measure lift and limit feedback-loop bias |
| `rank_bands.tie_break` | list of `similar_pr_bad_rate`, `path_detection_lag_days`, `revealed_path_scrutiny`, `path_size_pctile`, `release_cycle_position` | `["similar_pr_bad_rate", "path_detection_lag_days"]` |  | Only for identical rounded scores; PR age is always the last key |

### `floors`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `floors.enabled` | boolean | `true` | decision pending |  |
| `floors.paths` | list of objects | `[]` | decision pending, union |  |
| `floors.paths[].pattern` | string |  | decision pending, union |  |
| `floors.paths[].min_band` | `review_first`, `senior_recommended` |  | decision pending, union | standard is the lowest band, so it is not a floor |
| `floors.ai_agents` | `review_first`, `senior_recommended` or null | `null` | decision pending |  |
| `floors.first_time_contributors` | `review_first`, `senior_recommended` or null | `null` | decision pending |  |

### `size`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `size.thresholds.xs` | integer 0– | `0` |  |  |
| `size.thresholds.s` | integer 0– | `10` |  |  |
| `size.thresholds.m` | integer 0– | `100` |  |  |
| `size.thresholds.l` | integer 0– | `500` |  |  |
| `size.thresholds.xl` | integer 0– | `1000` |  |  |
| `size.count_files` | boolean | `false` |  |  |
| `size.ignore_paths` | list of string | `["**/*.lock", "**/package-lock.json", "**/go.sum"]` |  | Excluded from the size label only; still counted for risk |
| `size.apply_label` | boolean | `true` |  |  |
| `size.large_pr_note` | string or null | `null` |  |  |

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
| `reviewer_routing.mode` | `off`, `suggest`, `request` | `"suggest"` |  |  |
| `reviewer_routing.use_codeowners` | boolean | `true` |  |  |
| `reviewer_routing.senior_reviewers` | list of string matching `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$` | `[]` |  |  |
| `reviewer_routing.groups` | list of objects | `[]` |  |  |
| `reviewer_routing.groups[].name` | string |  |  |  |
| `reviewer_routing.groups[].paths` | list of string |  |  |  |
| `reviewer_routing.groups[].reviewers` | list of string matching `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$` |  |  |  |
| `reviewer_routing.reviewers_per_pr` | integer 1–5 | `1` |  |  |
| `reviewer_routing.algorithm` | `load_balance`, `round_robin`, `random` | `"load_balance"` |  |  |
| `reviewer_routing.max_open_reviews_per_reviewer` | integer or null 1– | `null` |  |  |
| `reviewer_routing.skip_title_keywords` | list of string | `["wip"]` |  |  |

### `availability`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `availability.users_unavailable` | list of string matching `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$` | `[]` |  |  |
| `availability.timezone` | string | `"UTC"` |  | IANA name |
| `availability.working_days` | list of `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` | `["mon", "tue", "wed", "thu", "fri"]` |  |  |
| `availability.working_hours` | string matching `^([01][0-9]\|2[0-3]):[0-5][0-9]-([01][0-9]\|2[0-3]):[0-5][0-9]$` | `"09:00-17:00"` |  |  |

### `review_targets`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `review_targets.senior_recommended_hours` | integer 1–720 | `4` |  |  |
| `review_targets.review_first_hours` | integer 1–720 | `4` |  |  |
| `review_targets.standard_hours` | integer 1–720 | `8` |  |  |

### `reminders`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `reminders.enabled` | boolean | `false` |  |  |
| `reminders.min_age_hours` | integer 1–720 | `24` |  |  |
| `reminders.min_staleness_hours` | integer 1–720 | `8` |  |  |
| `reminders.ignore_drafts` | boolean | `true` |  |  |
| `reminders.ignore_approved_with` | integer 0–10 | `1` |  |  |
| `reminders.ignored_labels` | list of string | `[]` |  |  |

### `rescore`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `rescore.on` | list of `push`, `ready_for_review`, `ci_completed`, `reopened`, `edited`, `labeled` | `["push", "ready_for_review", "ci_completed", "reopened"]` |  |  |
| `rescore.max_rescores_per_pr` | integer 1–100 | `20` |  |  |
| `rescore.min_minutes_between_edits` | integer 0–60 | `2` |  |  |

### `commands`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `commands.enabled` | boolean | `true` |  |  |
| `commands.prefix` | string matching `^/[a-z-]+$` | `"/riffle"` |  |  |
| `commands.override.allowed` | `write_access`, `maintainers`, `codeowners` | `"write_access"` | org only |  |
| `commands.override.require_reason` | boolean | `true` | org only | The reason is stored with the route_override training label |
| `commands.override.allow_downgrade` | boolean | `true` | org only | Floors still apply |
| `commands.disable_label` | string | `"riffle:disabled"` |  | Stops comments on that PR; it is still scored |

### `pr_output`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `pr_output.post_comment` | boolean | `true` |  |  |
| `pr_output.update_existing_comment` | boolean | `true` |  |  |
| `pr_output.apply_labels` | boolean | `true` |  |  |
| `pr_output.labels.review_first` | string | `"review first"` |  |  |
| `pr_output.labels.standard` | string | `"standard"` |  |  |
| `pr_output.labels.senior_recommended` | string | `"senior recommended"` |  |  |
| `pr_output.labels.override` | string | `"wrong route"` |  |  |
| `pr_output.component_label_prefix` | string or null | `null` |  |  |
| `pr_output.bands_with_comment` | list of `review_first`, `standard`, `senior_recommended` | `["review_first", "senior_recommended"]` |  |  |
| `pr_output.show_top_factors` | integer 0–5 | `3` |  | From SHAP; explains the rank even with the LLM off |
| `pr_output.show_score` | boolean | `false` |  |  |
| `pr_output.show_component` | boolean | `true` |  |  |
| `pr_output.collapse` | boolean | `true` |  |  |
| `pr_output.evidence.cite_similar_prs` | integer 0–5 | `3` |  |  |
| `pr_output.evidence.failure_mode` | boolean | `true` |  |  |
| `pr_output.evidence.scrutiny` | boolean | `true` |  |  |
| `pr_output.evidence.path_size` | boolean | `true` |  |  |
| `pr_output.evidence.release_cycle` | boolean | `true` |  |  |

### `explanation`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `explanation.enabled` | boolean | `true` |  | Turning off never stops scoring |
| `explanation.language` | string matching `^[a-z]{2}(-[A-Z]{2})?$` | `"en-US"` |  |  |
| `explanation.max_bullets` | integer 1–7 | `3` |  |  |
| `explanation.show_review_effort` | boolean | `true` |  |  |
| `explanation.mention_missing_tests` | boolean | `true` |  |  |
| `explanation.mention_missing_issue` | boolean | `false` |  |  |
| `explanation.show_effective_config` | boolean | `false` |  |  |
| `explanation.known_fragile` | list of string | `[]` |  | Paths the narrator names explicitly |
| `explanation.house_terms` | object | `{}` |  |  |

### `llm`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `llm.provider` | `none`, `hosted`, `self_hosted` | `"none"` |  | none = SHAP template explanations only |
| `llm.endpoint` | string or null (uri) | `null` |  |  |
| `llm.send` | `features_only`, `diff_hunks`, `full_files` | `"features_only"` |  | Repos may only make this stricter |
| `llm.excluded_paths` | list of string | `["**/.env*", "**/*.pem", "**/*.key", "**/*secret*"]` | union |  |
| `llm.max_calls_per_hour` | integer 0–10000 | `60` |  |  |

### `queue`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `queue.epic_priority` | boolean | `false` |  | Linked issue priority breaks ties inside a band |
| `queue.manual_boosts.enabled` | boolean | `false` |  |  |
| `queue.manual_boosts.areas` | list of objects | `[]` |  |  |
| `queue.manual_boosts.areas[].paths` | list of string |  |  |  |
| `queue.manual_boosts.areas[].boost` | number 0–0.2 | `0.05` |  | Moves queue position, never risk_score |

### `stacks`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `stacks.score_as` | `each_pr`, `whole_stack` | `"each_pr"` |  |  |
| `stacks.comment_on` | `each_pr`, `stack_top` | `"each_pr"` |  |  |

### `limits`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `limits.max_comments_per_hour` | integer 1–1000 | `120` |  |  |

### `overrides`

Per component or path, inside a monorepo. Most specific match wins.

Each entry has `match` (`component` or `paths`) and may set `rank_bands`, `pr_output`, `reviewer_routing`, `review_targets` with the same keys as the top-level blocks.

### `dashboard`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `dashboard.visibility` | `org_members`, `repo_writers`, `admins` | `"repo_writers"` | org only |  |
| `dashboard.show_individual_metrics` | boolean | `false` | org only | Per-person statistics stay hidden unless on |
| `dashboard.group_by` | `band`, `component`, `owner`, `author_type` | `"band"` | org only |  |

### `audit`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `audit.enabled` | boolean | `true` | org only |  |
| `audit.retention_days` | integer 30–1095 | `365` | org only |  |

### `notifications`

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `notifications.slack_webhook_secret_ref` | string or null | `null` | later |  |
| `notifications.digest` | `off`, `daily`, `weekly` | `"off"` | later |  |

### Code checks

Cross-field rules the schema cannot express:

- rank_bands.percentile.senior_recommended_top_fraction <= review_first_top_fraction
- rank_bands.absolute.review_first_min_score <= senior_recommended_min_score
- size.thresholds strictly increase xs < s < m < l < xl
- scope.base_branches entries compile as regex
- repo files cannot loosen locked or privacy keys (e.g. llm.send may only move toward features_only)
- floors never lower a band; a floor that equals the model band is not shown

## Mined features (`mined_features.schema.json`)

Five features mined from git, CI and review history that capture how one repository behaves. Produced by the scorer's extractor at the PR's base commit, appended to the fixed feature vector, and emitted alongside evidence the narrator may cite. Point-in-time rule: only past PRs merged before this PR opened AND whose labels were mature at that moment may contribute. Only reviews completed before open count. null means not enough history (thresholds in rules.mining), never 0.

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
| `evidence.similar_prs` | list of objects |  |  |  |
| `evidence.similar_prs[].pr_number` | integer |  |  |  |
| `evidence.similar_prs[].overlap` | number 0–1 |  |  |  |
| `evidence.similar_prs[].outcome` | `clean`, `revert`, `ci_fail`, `hotfix` |  |  |  |
| `evidence.similar_prs[].merged_at` | string (date-time) |  |  |  |
| `evidence.scrutiny_top_path` | object or null |  |  |  |
| `evidence.failure_mode_top` | object or null |  |  |  |
| `evidence.size_context` | object or null |  |  |  |
| `evidence.release_context` | object or null |  |  |  |

## Outcome import (`outcome_import.schema.json`)

Body of POST /v1/outcomes. Lets an org report outcomes Riffle cannot see (internal reverts, incidents, internal CI). Signed with the secret named in rules sources.outcome_import. Imported outcomes become the external_import label source, weighted by its label_source rule. Idempotent on event_id. Never carries code or diffs.

| Key | Allowed | Default | Flags | Meaning |
|---|---|---|---|---|
| `event_id` | string |  |  | Sender's unique id; resends are ignored |
| `repo` | string matching `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$` |  |  |  |
| `target.commit_sha` | string matching `^[0-9a-f]{40}$` |  |  |  |
| `target.pr_number` | integer 1– |  |  |  |
| `target.external_change_id` | string |  |  | Matched through link rules, e.g. D12345678 |
| `outcome` | `revert`, `incident`, `ci_fail`, `hotfix`, `clean` |  |  |  |
| `severity` | integer 1–4 |  |  | 1 is worst |
| `occurred_at` | string (date-time) |  |  |  |
| `source` | string |  |  | Free label for audit, e.g. internal-ci |
