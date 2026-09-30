Plan for expanding Riffle's training dataset from 50 to 100 public GitHub
repositories.

> **Proposed.** The corpus and split are proposed. Pull request counts for
> repositories 1–50 were measured from each repository's Pull Requests tab
> between July and September 2026; for repositories 51–100, with the GitHub
> search API on 30 September 2026. Commit counts marked `~` are estimates, the
> rest come from each repository's GitHub page; estimates are replaced with
> `git rev-list --count HEAD` after cloning.

## At a glance

| Scope | Repositories | Total PRs | Approx. commits | Status |
| --- | ---: | ---: | ---: | --- |
| Current 50 | 50 | 1,899,996 | ~3,482,259 | rust and envoy PRs pending |
| New 50 | 50 | 1,019,194 | ~1,526,983 | All measured |
| **All 100** | **100** | **2,919,190** | **~5,009,242** | 98 repositories measured |

## Current 50 repositories (v1)

### Systems, languages, and developer tools

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 1 | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) | 946 | 89,333 | 90,279 | 141,606 | Train | Stale PRs auto-closed after 90 days |
| 2 | [rust-lang/cargo](https://github.com/rust-lang/cargo) | 82 | 8,340 | 8,422 | ~20,000 | Train | Replaces golang/go |
| 3 | [python/cpython](https://github.com/python/cpython) | 2,717 | 74,687 | 77,404 | ~128,000 | Train | Many bot backport PRs |
| 4 | [rust-lang/rust](https://github.com/rust-lang/rust) | — | — | — | 341,866 | Train | PRs not measured; bors rollups |
| 5 | [llvm/llvm-project](https://github.com/llvm/llvm-project) | 10,033 | 115,156 | 125,189 | 599,783 | Train | GitHub review only since Sep 2023 |
| 6 | [rust-lang/rust-clippy](https://github.com/rust-lang/rust-clippy) | 201 | 7,655 | 7,856 | ~19,000 | Train | Replaces git/git |
| 7 | [curl/curl](https://github.com/curl/curl) | 53 | 15,544 | 15,597 | ~35,000 | Val | |
| 8 | [neovim/neovim](https://github.com/neovim/neovim) | 298 | 25,476 | 25,774 | ~34,000 | Test | vim-patch port PRs |
| 9 | [microsoft/vscode](https://github.com/microsoft/vscode) | 2,731 | 64,406 | 67,137 | ~140,000 | Train | Mostly internal-team PRs |
| 10 | [microsoft/TypeScript](https://github.com/microsoft/TypeScript) | 130 | 19,326 | 19,456 | ~37,000 | Train | |
| | **Subtotal** | | | **437,114** | **~1,496,255** | | |

### Web and application frameworks

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 11 | [facebook/react](https://github.com/facebook/react) | 482 | 18,977 | 19,459 | ~21,000 | Train | Now redirects to react/react |
| 12 | [nodejs/node](https://github.com/nodejs/node) | 584 | 42,044 | 42,628 | ~47,000 | Train | Lands via commit-queue bot |
| 13 | [denoland/deno](https://github.com/denoland/deno) | 271 | 18,223 | 18,494 | ~14,500 | Train | |
| 14 | [django/django](https://github.com/django/django) | 453 | 21,083 | 21,536 | ~34,500 | Train | Mergers push manually |
| 15 | [pallets/flask](https://github.com/pallets/flask) | 3 | 2,846 | 2,849 | ~5,500 | Val | Small |
| 16 | [rails/rails](https://github.com/rails/rails) | 1,120 | 38,155 | 39,275 | ~95,000 | Train | |
| 17 | [laravel/framework](https://github.com/laravel/framework) | 44 | 35,893 | 35,937 | ~40,000 | Test | Fast-close culture |
| 18 | [spring-projects/spring-boot](https://github.com/spring-projects/spring-boot) | 11 | 7,840 | 7,851 | ~58,000 | Train | Manual merges |
| 19 | [electron/electron](https://github.com/electron/electron) | 142 | 30,810 | 30,952 | ~31,000 | Train | Backport bot PRs |
| 20 | [vercel/next.js](https://github.com/vercel/next.js) | 2,486 | 38,027 | 40,513 | ~32,000 | Train | Large open backlog |
| | **Subtotal** | | | **259,494** | **~378,500** | | |

### Databases and data systems

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 21 | [ClickHouse/ClickHouse](https://github.com/ClickHouse/ClickHouse) | 1,445 | 77,586 | 79,031 | ~240,000 | Train | Backport bot PRs |
| 22 | [redis/redis](https://github.com/redis/redis) | 679 | 7,526 | 8,205 | ~13,000 | Test | Licence changes 2024/2025 |
| 23 | [pingcap/tidb](https://github.com/pingcap/tidb) | 1,926 | 45,120 | 47,046 | ~26,000 | Train | Replaces mongodb/mongo |
| 24 | [apache/airflow](https://github.com/apache/airflow) | 810 | 45,723 | 46,533 | ~30,000 | Train | Replaces apache/cassandra |
| 25 | [elastic/elasticsearch](https://github.com/elastic/elasticsearch) | 1,241 | 105,914 | 107,155 | 106,421 | Test | |
| 26 | [apache/kafka](https://github.com/apache/kafka) | 498 | 22,608 | 23,106 | ~17,500 | Train | |
| 27 | [apache/spark](https://github.com/apache/spark) | 409 | 56,512 | 56,921 | ~50,000 | Train | Merged by script |
| 28 | [apache/flink](https://github.com/apache/flink) | 362 | 28,234 | 28,596 | ~37,000 | Train | Merged by committers |
| 29 | [apache/druid](https://github.com/apache/druid) | 119 | 14,891 | 15,010 | ~14,500 | Val | |
| 30 | [duckdb/duckdb](https://github.com/duckdb/duckdb) | 338 | 13,999 | 14,337 | ~55,000 | Train | |
| | **Subtotal** | | | **425,940** | **~589,421** | | |

### Cloud, infrastructure, and observability

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 31 | [docker/compose](https://github.com/docker/compose) | 45 | 5,569 | 5,614 | ~7,000 | Train | v1 to v2 rewrite |
| 32 | [moby/moby](https://github.com/moby/moby) | 594 | 28,004 | 28,598 | ~52,000 | Train | |
| 33 | [hashicorp/terraform](https://github.com/hashicorp/terraform) | 167 | 16,301 | 16,468 | ~35,000 | Train | BSL licence since 2023 |
| 34 | [hashicorp/vault](https://github.com/hashicorp/vault) | 280 | 24,982 | 25,262 | ~23,000 | Test | Enterprise sync commits |
| 35 | [ansible/ansible](https://github.com/ansible/ansible) | 342 | 53,017 | 53,359 | ~55,000 | Train | 2020 collections split |
| 36 | [prometheus/prometheus](https://github.com/prometheus/prometheus) | 374 | 11,685 | 12,059 | ~14,000 | Val | |
| 37 | [grafana/grafana](https://github.com/grafana/grafana) | 632 | 81,555 | 82,187 | ~62,000 | Train | Backport bot PRs |
| 38 | [open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector) | 77 | 11,056 | 11,133 | ~8,000 | Train | Short history (2019 on) |
| 39 | [istio/istio](https://github.com/istio/istio) | 71 | 37,425 | 37,496 | ~24,000 | Train | |
| 40 | [envoyproxy/envoy](https://github.com/envoyproxy/envoy) | — | — | — | 28,474 | Train | PRs not measured |
| | **Subtotal** | | | **272,176** | **~308,474** | | |

### ML, science, media, and end-user software

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 41 | [pytorch/pytorch](https://github.com/pytorch/pytorch) | 3,514 | 133,276 | 136,790 | 111,609 | Train | Lands via merge bot; ghstack |
| 42 | [tensorflow/tensorflow](https://github.com/tensorflow/tensorflow) | 1,963 | 76,531 | 78,494 | ~195,000 | Train | Mostly Copybara sync PRs |
| 43 | [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) | 587 | 20,631 | 21,218 | ~33,000 | Train | |
| 44 | [huggingface/transformers](https://github.com/huggingface/transformers) | 1,497 | 26,435 | 27,932 | ~21,000 | Train | |
| 45 | [jupyter/notebook](https://github.com/jupyter/notebook) | 48 | 2,593 | 2,641 | ~10,500 | Test | Small; v7 rewrite |
| 46 | [obsproject/obs-studio](https://github.com/obsproject/obs-studio) | 330 | 7,693 | 8,023 | ~14,500 | Val | |
| 47 | [godotengine/godot](https://github.com/godotengine/godot) | 5,304 | 51,459 | 56,763 | ~80,000 | Val | Large open backlog |
| 48 | [home-assistant/core](https://github.com/home-assistant/core) | 1,063 | 106,968 | 108,031 | ~118,000 | Train | Many dependency bumps |
| 49 | [nextcloud/server](https://github.com/nextcloud/server) | 1,012 | 39,241 | 40,253 | ~80,000 | Train | Backport bot PRs |
| 50 | [bitcoin/bitcoin](https://github.com/bitcoin/bitcoin) | 388 | 24,739 | 25,127 | ~46,000 | Train | Merged by maintainer script |
| | **Subtotal** | | | **505,272** | **~709,609** | | |

## New 50 repositories (v2)

### Languages and compilers

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 51 | [swiftlang/swift](https://github.com/swiftlang/swift) | 1,729 | 68,765 | 70,494 | 211,250 | Train | Moved from apple/swift (2024) |
| 52 | [dotnet/roslyn](https://github.com/dotnet/roslyn) | 748 | 46,001 | 46,749 | 146,826 | Train | Maestro bot PRs |
| 53 | [JuliaLang/julia](https://github.com/JuliaLang/julia) | 1,066 | 34,314 | 35,380 | 63,162 | Test | Backport labels |
| 54 | [scala/scala3](https://github.com/scala/scala3) | 128 | 15,699 | 15,827 | ~30,000 | Val | Renamed from lampepfl/dotty |
| 55 | [elixir-lang/elixir](https://github.com/elixir-lang/elixir) | 10 | 9,756 | 9,766 | ~22,000 | Test | Core team pushes directly |
| 56 | [haskell/cabal](https://github.com/haskell/cabal) | 150 | 5,746 | 5,896 | ~17,000 | Val | Mergify merges |
| 57 | [php/php-src](https://github.com/php/php-src) | 1,120 | 17,133 | 18,253 | ~140,000 | Train | GitHub canonical only since 2021 |
| 58 | [oven-sh/bun](https://github.com/oven-sh/bun) | 5,812 | 18,681 | 24,493 | ~13,000 | Train | Many bot-authored PRs |
| | **Subtotal** | | | **226,858** | **~643,238** | | |

### Frontend and mobile

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 59 | [angular/angular](https://github.com/angular/angular) | 184 | 37,761 | 37,945 | ~33,000 | Train | Merged by merge tool |
| 60 | [vuejs/core](https://github.com/vuejs/core) | 360 | 6,450 | 6,810 | 7,198 | Train | Vue 2 history in vuejs/vue |
| 61 | [sveltejs/svelte](https://github.com/sveltejs/svelte) | 164 | 8,413 | 8,577 | ~11,000 | Test | Changesets release PRs |
| 62 | [vitejs/vite](https://github.com/vitejs/vite) | 277 | 8,881 | 9,158 | ~9,500 | Val | Renovate bot |
| 63 | [withastro/astro](https://github.com/withastro/astro) | 77 | 10,983 | 11,060 | ~12,000 | Train | Changesets release PRs |
| 64 | [flutter/flutter](https://github.com/flutter/flutter) | 598 | 75,411 | 76,009 | 91,756 | Train | Engine merged in Dec 2024 |
| 65 | [expo/expo](https://github.com/expo/expo) | 541 | 28,840 | 29,381 | ~30,000 | Train | Monorepo |
| 66 | [thunderbird/thunderbird-android](https://github.com/thunderbird/thunderbird-android) | 27 | 5,326 | 5,353 | ~12,000 | Train | Formerly K-9 Mail |
| 67 | [AntennaPod/AntennaPod](https://github.com/AntennaPod/AntennaPod) | 27 | 3,850 | 3,877 | ~6,500 | Val | Small |
| 68 | [wordpress-mobile/WordPress-iOS](https://github.com/wordpress-mobile/WordPress-iOS) | 81 | 16,413 | 16,494 | ~50,000 | Test | Release-branch merges |
| | **Subtotal** | | | **204,664** | **~262,954** | | |

### Security and networking

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 69 | [openssl/openssl](https://github.com/openssl/openssl) | 556 | 18,740 | 19,296 | ~36,000 | Train | Manual pushes after approval |
| 70 | [keycloak/keycloak](https://github.com/keycloak/keycloak) | 504 | 25,691 | 26,195 | ~28,000 | Train | Dependabot heavy |
| 71 | [aquasecurity/trivy](https://github.com/aquasecurity/trivy) | 83 | 4,909 | 4,992 | ~3,500 | Train | Security incident Mar 2026 |
| 72 | [sigstore/cosign](https://github.com/sigstore/cosign) | 46 | 3,798 | 3,844 | ~2,800 | Val | Many dependency bumps |
| 73 | [bitwarden/server](https://github.com/bitwarden/server) | 206 | 6,725 | 6,931 | ~5,500 | Test | Mixed licence |
| 74 | [cilium/cilium](https://github.com/cilium/cilium) | 262 | 36,110 | 36,372 | ~40,000 | Train | Backport PRs |
| 75 | [caddyserver/caddy](https://github.com/caddyserver/caddy) | 92 | 3,042 | 3,134 | ~5,000 | Train | v2 rewrite 2019 |
| 76 | [tailscale/tailscale](https://github.com/tailscale/tailscale) | 549 | 10,093 | 10,642 | ~11,000 | Train | |
| | **Subtotal** | | | **111,406** | **~131,800** | | |

### Data engineering and ML infrastructure

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 77 | [apache/arrow](https://github.com/apache/arrow) | 388 | 21,634 | 22,022 | ~17,000 | Train | Jira to GitHub issues 2023 |
| 78 | [trinodb/trino](https://github.com/trinodb/trino) | 274 | 22,131 | 22,405 | ~25,000 | Train | Renamed from prestosql |
| 79 | [pola-rs/polars](https://github.com/pola-rs/polars) | 376 | 15,827 | 16,203 | ~12,000 | Val | |
| 80 | [dbt-labs/dbt](https://github.com/dbt-labs/dbt) | 306 | 6,443 | 6,749 | ~8,000 | Train | Renamed from dbt-labs/dbt-core |
| 81 | [ray-project/ray](https://github.com/ray-project/ray) | 699 | 42,357 | 43,056 | 31,787 | Train | External Buildkite CI |
| 82 | [vllm-project/vllm](https://github.com/vllm-project/vllm) | 5,852 | 34,177 | 40,029 | 20,901 | Train | Short history (2023 on) |
| 83 | [numpy/numpy](https://github.com/numpy/numpy) | 303 | 18,156 | 18,459 | 42,003 | Train | SVN-era history |
| 84 | [delta-io/delta](https://github.com/delta-io/delta) | 585 | 5,172 | 5,757 | ~4,500 | Test | Check internal-sync commits |
| | **Subtotal** | | | **174,680** | **~161,191** | | |

### Developer tools, package managers, and editors

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 85 | [Homebrew/brew](https://github.com/Homebrew/brew) | 8 | 17,809 | 17,817 | ~30,000 | Train | Dependabot |
| 86 | [astral-sh/uv](https://github.com/astral-sh/uv) | 570 | 12,044 | 12,614 | ~7,000 | Train | Short history (2023 on) |
| 87 | [pnpm/pnpm](https://github.com/pnpm/pnpm) | 232 | 7,445 | 7,677 | ~9,000 | Train | Changesets |
| 88 | [cli/cli](https://github.com/cli/cli) | 62 | 4,577 | 4,639 | ~10,000 | Val | |
| 89 | [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | 76 | 1,088 | 1,164 | ~2,300 | Train | Very small |
| 90 | [helix-editor/helix](https://github.com/helix-editor/helix) | 580 | 6,413 | 6,993 | ~6,500 | Test | |
| 91 | [zed-industries/zed](https://github.com/zed-industries/zed) | 790 | 32,159 | 32,949 | ~35,000 | Train | Private before Jan 2024 |
| | **Subtotal** | | | **83,853** | **~99,800** | | |

### Embedded, games, fintech, CMS, and social

| # | Repository | Open | Closed | Total PRs | Approx. commits | Split | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 92 | [zephyrproject-rtos/zephyr](https://github.com/zephyrproject-rtos/zephyr) | 1,904 | 85,949 | 87,853 | ~110,000 | Train | Backport bot PRs |
| 93 | [esphome/esphome](https://github.com/esphome/esphome) | 465 | 16,504 | 16,969 | ~9,000 | Val | |
| 94 | [bevyengine/bevy](https://github.com/bevyengine/bevy) | 574 | 14,533 | 15,107 | ~9,000 | Train | Merge queue |
| 95 | [luanti-org/luanti](https://github.com/luanti-org/luanti) | 123 | 8,356 | 8,479 | ~17,000 | Train | Renamed from minetest (2024) |
| 96 | [tigerbeetle/tigerbeetle](https://github.com/tigerbeetle/tigerbeetle) | 29 | 3,237 | 3,266 | ~11,000 | Test | Small |
| 97 | [actualbudget/actual](https://github.com/actualbudget/actual) | 86 | 5,224 | 5,310 | ~4,000 | Test | Small |
| 98 | [lightningnetwork/lnd](https://github.com/lightningnetwork/lnd) | 287 | 5,876 | 6,163 | ~18,000 | Val | |
| 99 | [WordPress/gutenberg](https://github.com/WordPress/gutenberg) | 2,678 | 47,040 | 49,718 | ~30,000 | Train | Synced into WP core |
| 100 | [mastodon/mastodon](https://github.com/mastodon/mastodon) | 187 | 24,681 | 24,868 | ~20,000 | Train | Renovate/Crowdin bots |
| | **Subtotal** | | | **217,733** | **~228,000** | | |

## Totals

| Scope | Repositories | Total PRs | Approx. commits | Status |
| --- | ---: | ---: | ---: | --- |
| Systems, languages, and developer tools | 10 | 437,114 | ~1,496,255 | Measured PRs |
| Web and application frameworks | 10 | 259,494 | ~378,500 | Measured PRs |
| Databases and data systems | 10 | 425,940 | ~589,421 | Measured PRs |
| Cloud, infrastructure, and observability | 10 | 272,176 | ~308,474 | Measured PRs |
| ML, science, media, and end-user software | 10 | 505,272 | ~709,609 | Measured PRs |
| Languages and compilers | 8 | 226,858 | ~643,238 | Measured PRs |
| Frontend and mobile | 10 | 204,664 | ~262,954 | Measured PRs |
| Security and networking | 8 | 111,406 | ~131,800 | Measured PRs |
| Data engineering and ML infrastructure | 8 | 174,680 | ~161,191 | Measured PRs |
| Developer tools, package managers, and editors | 7 | 83,853 | ~99,800 | Measured PRs |
| Embedded, games, fintech, CMS, and social | 9 | 217,733 | ~228,000 | Measured PRs |
| **Current 50** | 50 | **1,899,996** | **~3,482,259** | rust and envoy PRs pending |
| **New 50** | 50 | **1,019,194** | **~1,526,983** | All measured |
| **All 100** | 100 | **2,919,190** | **~5,009,242** | 98 repositories measured |

## Data split

| Purpose | Value | Status |
| --- | --- | --- |
| Training | 70 repositories | Decision |
| Unseen-repository validation | 15 repositories | Decision |
| Final unseen test | 15 repositories | Decision |

Each repository's split is in the tables above. Validation tunes the
per-repository layer and thresholds; the final test is touched once.
Held-out sets cover every category, most languages, at least four
repositories under 6,000 PRs, and one giant each (godot in validation,
elasticsearch in test). Splits within each training repository are
time-ordered.

## Dataset estimates

| Item | Value | Status |
| --- | --- | --- |
| Total PRs, 98 measured repositories | 2,919,190 | Measured |
| Closed PRs, 98 measured repositories | 2,837,457 | Measured |
| rust-lang/rust and envoyproxy/envoy PRs | Not measured yet | Pending |
| Approx. commits, all 100 repositories | ~5,009,242 | Estimate |
| Positive labels at 2% of closed PRs (upper bound) | 56,700 | Derived |
| Positive labels at 3% of closed PRs (upper bound) | 85,100 | Derived |
| Positive labels at 5% of closed PRs (upper bound) | 141,900 | Derived |
| Revert rate in open source | 1–5% of commits | Sourced [1] |
| SZZ bug-inducing rate, if used as a label | About 26% | Sourced [2] |
| R-SZZ precision | 57–73% | Sourced [3] |
| Permanent structured data | 15–61 GB without diffs or CI logs | Derived |
| Temporary clone and SZZ scratch space | 1–1.5 TB across machines | Estimate |

Positive-label counts are upper bounds before bot and backport filtering.
Roughly one SZZ label in three is likely wrong, so SZZ is weighted below
reverts and follow-up fixes.

## Machine allocation

| Item | Value | Status |
| --- | --- | --- |
| Machines | 8 | Estimate |
| Repositories per machine | 3–22, balanced to ~360,000 PRs each | Estimate |
| Parallel workers per machine | 3–4 | Estimate |
| Recommended disk per machine | 250–500 GB | Estimate |

The largest repositories (llvm-project, pytorch, swift, flutter) go on the
machines with the most disk.

## Extraction timeline

| Task | Expected time | Status |
| --- | --- | --- |
| Clone repositories and count commits | 1–2 days | Estimate |
| List-level PR metadata (GraphQL) | 2–3 days | Derived |
| Full extraction with reviews and checks | 2–3 weeks | Derived |
| Labelling (reverts, R-SZZ, follow-up fixes) and audit | 5–7 days | Estimate |
| Global model training | Minutes to several hours | Estimate |
| Backtesting | Hours to several days | Estimate |

## Caveats

- From 1 October 2026, GitHub keeps check runs, workflow runs, and statuses
  on public repositories for at most 90 days [4]. CI-failure labels for the
  new 50 will only cover a rolling window from the day archiving starts.
  Missing CI data is treated as missing, not negative.
- The 10 largest repositories hold about a third of all PRs. Each repository
  is capped (for example 20,000 PRs, sampled across time) or reweighted, and
  metrics are reported per repository.
- Many repositories merge outside the GitHub button (rust, pytorch, angular,
  openssl, php-src, spark, bitcoin, node). Merges are detected by commit SHA
  or the PR number in commit messages.
- Bot, backport, sync, and dependency-bump PRs are filtered, or modelled
  separately.
- History breaks: renames (scala3, trino, luanti, thunderbird-android, dbt),
  repository mergers (the flutter engine), private-then-public history (zed),
  and tracker moves (arrow).
- GH Archive under-captured PR and issue events from mid-2025 [6]. It is
  checked against the API before any backfill.
- Cross-project models are weaker than within-project ones, which is why
  unseen repositories are judged on their own target [5].

## Sources

1. [Shimagaki et al., Why are commits being reverted? (ICSME 2016)](https://rebels.cs.uwaterloo.ca/confpaper/2016/10/04/why-are-commits-being-reverted.html)
2. [Keshavarz and Nagappan, ApacheJIT (MSR 2022)](https://arxiv.org/abs/2203.00101)
3. [Rosa et al., Evaluating SZZ Implementations Through a Developer-informed Oracle (ICSE 2021)](https://arxiv.org/pdf/2102.03300)
4. [GitHub Changelog, Actions retention will cover checks, workflow runs, and statuses (27 Aug 2026)](https://github.blog/changelog/2026-08-27-actions-retention-will-cover-checks-workflow-runs-and-statuses/)
5. [Kamei et al., Studying just-in-time defect prediction using cross-project models (EMSE 2016)](https://link.springer.com/article/10.1007/s10664-015-9400-x)
6. [OSSInsight, GitHub events feed under-capture notice](https://ossinsight.io/)
