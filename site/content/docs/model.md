Riffle does not wait for a repository to accumulate history before it can rank.
One global model serves every repository from the first pull request, and each
repository earns its own adjustment as outcomes arrive.

> **Accepted.** This design is recorded and accepted.

## Two layers

| Layer | Trained on | Serves | Fits in |
| --- | --- | --- | --- |
| Global model | Published datasets, self-mined history, opt-in shared outcomes | Every repository, from day one | A scheduled training run |
| Repository layer | That repository's own mature outcomes | That repository only | Seconds, on CPU, with dozens of labels |

The global model is gradient-boosted. The repository layer is deliberately
light, so it can be refitted often and cheaply.

## How the repository layer earns its weight

```mermaid
%% caption: Illustrative shape only. How layer_weight grows is still open.
xychart-beta
  x-axis "mature labels" [none, ·, ·, ·, ·, ·, ·, ·, many]
  y-axis "layer_weight" 0 --> 1
  line [0, 0.02, 0.07, 0.2, 0.45, 0.72, 0.88, 0.96, 0.99]
```

- `layer_weight` starts at `0.0` and grows with the number of mature labels.
  There is no cutover and no threshold to argue about.
- Each layer is checked against its own holdout. If it ranks worse than the
  global model alone, its weight drops to `0.0` automatically.
- Path history is computed from git on install, so repository-specific signal
  exists before the first label matures.

## Version pinning

A score records exactly what produced it. `model_version` covers the global
model, the repository layer, the feature extractor and the compiled rules
hash. A request never mixes a new model with an old feature extractor.

## Training corpus

> **Planned.** The corpus is chosen and its size measured; the model is not trained yet.

The first global model trains on 50 public repositories across five domains:
**1,899,996 pull requests**, open and closed, measured on 48 of them from each
repository's Pull Requests tab between July and September 2026.
`rust-lang/rust` and `envoyproxy/envoy` are not measured yet.

| Domain | Repositories | Pull requests | Share |
| --- | --- | --- | --- |
| ML, science, media and end-user software | 10 | 505,272 | 27% |
| Systems, languages and developer tools | 10 | 437,114 | 23% |
| Databases and data systems | 10 | 425,940 | 22% |
| Cloud, infrastructure and observability | 10 | 272,176 | 14% |
| Web and application frameworks | 10 | 259,494 | 14% |
| **Total** | **50** | **1,899,996** | |

Forty repositories train the model; ten are held out entirely, to measure how
it ranks repositories it has never seen. The target there is a ROC-AUC of
0.65 or better, following
[Kamei et al., 2016](https://link.springer.com/article/10.1007/s10664-015-9400-x).
Closed counts mix merged, rejected, auto-closed and bot pull requests; bot,
backport and sync PRs are filtered before training.

### The 50 repositories

Every repository in the first training corpus, by domain, with its measured
pull request counts.

#### Systems, languages and developer tools

| # | Repository | Open | Closed | Total | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) | 946 | 89,333 | 90,279 | Stale PRs auto-closed after 90 days |
| 2 | [rust-lang/cargo](https://github.com/rust-lang/cargo) | 82 | 8,340 | 8,422 | Replaces golang/go (review on Gerrit) |
| 3 | [python/cpython](https://github.com/python/cpython) | 2,717 | 74,687 | 77,404 | Many bot backport PRs |
| 4 | [rust-lang/rust](https://github.com/rust-lang/rust) | — | — | — | Not measured yet; rollup PRs bundle many PRs |
| 5 | [llvm/llvm-project](https://github.com/llvm/llvm-project) | 10,033 | 115,156 | 125,189 | GitHub review only since September 2023 |
| 6 | [rust-lang/rust-clippy](https://github.com/rust-lang/rust-clippy) | 201 | 7,655 | 7,856 | Replaces git/git (review on the mailing list) |
| 7 | [curl/curl](https://github.com/curl/curl) | 53 | 15,544 | 15,597 |  |
| 8 | [neovim/neovim](https://github.com/neovim/neovim) | 298 | 25,476 | 25,774 |  |
| 9 | [microsoft/vscode](https://github.com/microsoft/vscode) | 2,731 | 64,406 | 67,137 | Mostly internal-team PRs |
| 10 | [microsoft/TypeScript](https://github.com/microsoft/TypeScript) | 130 | 19,326 | 19,456 |  |
| | **Subtotal** | | | **437,114** | |

#### Web and application frameworks

| # | Repository | Open | Closed | Total | Notes |
| --- | --- | --- | --- | --- | --- |
| 11 | [facebook/react](https://github.com/facebook/react) | 482 | 18,977 | 19,459 | Now redirects to react/react |
| 12 | [nodejs/node](https://github.com/nodejs/node) | 584 | 42,044 | 42,628 |  |
| 13 | [denoland/deno](https://github.com/denoland/deno) | 271 | 18,223 | 18,494 |  |
| 14 | [django/django](https://github.com/django/django) | 453 | 21,083 | 21,536 | Tickets in Trac, review on GitHub |
| 15 | [pallets/flask](https://github.com/pallets/flask) | 3 | 2,846 | 2,849 | Small; low weight |
| 16 | [rails/rails](https://github.com/rails/rails) | 1,120 | 38,155 | 39,275 |  |
| 17 | [laravel/framework](https://github.com/laravel/framework) | 44 | 35,893 | 35,937 | Fast-close culture; low merge ratio |
| 18 | [spring-projects/spring-boot](https://github.com/spring-projects/spring-boot) | 11 | 7,840 | 7,851 | Manual merges; needs a merge heuristic |
| 19 | [electron/electron](https://github.com/electron/electron) | 142 | 30,810 | 30,952 | Many bot backport PRs |
| 20 | [vercel/next.js](https://github.com/vercel/next.js) | 2,486 | 38,027 | 40,513 | Large open backlog |
| | **Subtotal** | | | **259,494** | |

#### Databases and data systems

| # | Repository | Open | Closed | Total | Notes |
| --- | --- | --- | --- | --- | --- |
| 21 | [ClickHouse/ClickHouse](https://github.com/ClickHouse/ClickHouse) | 1,445 | 77,586 | 79,031 |  |
| 22 | [redis/redis](https://github.com/redis/redis) | 679 | 7,526 | 8,205 |  |
| 23 | [pingcap/tidb](https://github.com/pingcap/tidb) | 1,926 | 45,120 | 47,046 | Replaces mongodb/mongo (internal development) |
| 24 | [apache/airflow](https://github.com/apache/airflow) | 810 | 45,723 | 46,533 | Replaces apache/cassandra (review in Jira) |
| 25 | [elastic/elasticsearch](https://github.com/elastic/elasticsearch) | 1,241 | 105,914 | 107,155 |  |
| 26 | [apache/kafka](https://github.com/apache/kafka) | 498 | 22,608 | 23,106 |  |
| 27 | [apache/spark](https://github.com/apache/spark) | 409 | 56,512 | 56,921 | Merged by script; needs a merge heuristic |
| 28 | [apache/flink](https://github.com/apache/flink) | 362 | 28,234 | 28,596 | Merged by committers; needs a merge heuristic |
| 29 | [apache/druid](https://github.com/apache/druid) | 119 | 14,891 | 15,010 |  |
| 30 | [duckdb/duckdb](https://github.com/duckdb/duckdb) | 338 | 13,999 | 14,337 |  |
| | **Subtotal** | | | **425,940** | |

#### Cloud, infrastructure and observability

| # | Repository | Open | Closed | Total | Notes |
| --- | --- | --- | --- | --- | --- |
| 31 | [docker/compose](https://github.com/docker/compose) | 45 | 5,569 | 5,614 | Small |
| 32 | [moby/moby](https://github.com/moby/moby) | 594 | 28,004 | 28,598 |  |
| 33 | [hashicorp/terraform](https://github.com/hashicorp/terraform) | 167 | 16,301 | 16,468 | BSL licence since 2023 |
| 34 | [hashicorp/vault](https://github.com/hashicorp/vault) | 280 | 24,982 | 25,262 | Some synced enterprise PRs |
| 35 | [ansible/ansible](https://github.com/ansible/ansible) | 342 | 53,017 | 53,359 | 2020 collections split in history |
| 36 | [prometheus/prometheus](https://github.com/prometheus/prometheus) | 374 | 11,685 | 12,059 |  |
| 37 | [grafana/grafana](https://github.com/grafana/grafana) | 632 | 81,555 | 82,187 | Many bot PRs |
| 38 | [open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector) | 77 | 11,056 | 11,133 | Short history (2019 on) |
| 39 | [istio/istio](https://github.com/istio/istio) | 71 | 37,425 | 37,496 |  |
| 40 | [envoyproxy/envoy](https://github.com/envoyproxy/envoy) | — | — | — | Not measured yet |
| | **Subtotal** | | | **272,176** | |

#### ML, science, media and end-user software

| # | Repository | Open | Closed | Total | Notes |
| --- | --- | --- | --- | --- | --- |
| 41 | [pytorch/pytorch](https://github.com/pytorch/pytorch) | 3,514 | 133,276 | 136,790 | Largest; ghstack merges need a heuristic |
| 42 | [tensorflow/tensorflow](https://github.com/tensorflow/tensorflow) | 1,963 | 76,531 | 78,494 | Mostly Copybara sync PRs; filter by author |
| 43 | [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) | 587 | 20,631 | 21,218 |  |
| 44 | [huggingface/transformers](https://github.com/huggingface/transformers) | 1,497 | 26,435 | 27,932 |  |
| 45 | [jupyter/notebook](https://github.com/jupyter/notebook) | 48 | 2,593 | 2,641 | Small; low weight |
| 46 | [obsproject/obs-studio](https://github.com/obsproject/obs-studio) | 330 | 7,693 | 8,023 |  |
| 47 | [godotengine/godot](https://github.com/godotengine/godot) | 5,304 | 51,459 | 56,763 | Large open backlog |
| 48 | [home-assistant/core](https://github.com/home-assistant/core) | 1,063 | 106,968 | 108,031 | Many dependency-bump PRs |
| 49 | [nextcloud/server](https://github.com/nextcloud/server) | 1,012 | 39,241 | 40,253 | Many bot and backport PRs |
| 50 | [bitcoin/bitcoin](https://github.com/bitcoin/bitcoin) | 388 | 24,739 | 25,127 | Merged by maintainer script |
| | **Subtotal** | | | **505,272** | |

### The next model: 100 repositories

> **Proposed.** The next version of the global model is planned on 100
> public repositories: these 50 and 50 new ones.

| | Repositories | Pull requests | Commits (approx.) |
| --- | --- | --- | --- |
| Current 50 | 50 | 2,045,996 | 3,482,259 |
| New 50 | 50 | 837,766 | 1,526,983 |
| **All 100** | **100** | **2,883,762** | **5,009,242** |

About 66% of the pull request total is measured; the rest, including
`rust-lang/rust`, `envoyproxy/envoy` and most of the new 50, is estimated and
will be measured before extraction. The plan also replaces the 40 / 10 split
with 70 training, 15 validation and 15 final-test repositories. Every
repository, figure and caveat is on
[Dataset plan: 100 repositories](/docs/reference/dataset-plan).
