The plan for the next version of the global model: 100 public repositories,
the current 50 and 50 new ones. It is a proposal, and most of the new
figures are estimates.

> **Proposed.** About 66% of the pull request total is measured. Every
> Estimate below is to be measured - the Pull Requests tab's open and closed
> counts, and `git rev-list --count HEAD` on a clone - before extraction.

## At a glance

| Scope | Repositories | Pull requests | Commits (approx.) | Status |
| --- | --- | --- | --- | --- |
| Current 50 | 50 | 2,045,996 | 3,482,259 | Measured, sourced and estimated |
| New 50 | 50 | 837,766 | 1,526,983 | Mostly estimated |
| **All 100** | **100** | **2,883,762** | **5,009,242** | Derived |
| Measured so far | 48 | 1,899,996 (about 66%) | | Measured |
| Largest 10 | 10 | 997,560 (about 35%) | | Derived |

The new 50 add six categories and more than 17 languages. Every repository
reviews on GitHub pull requests; mirrors and projects reviewed on Gerrit,
Phabricator or mailing lists are excluded, and no organisation has more than
two of the new slots.

**Status key:** **M** measured from the Pull Requests tab, July to September
2026. **S** sourced from the repository's GitHub page. **E** estimated from
pull request number ranges; to be measured.

## Split: 70 / 15 / 15

The 40 / 10 split of the first model had no untouched test set. The plan uses
70 repositories to train, 15 unseen repositories to validate (calibration and
tuning) and 15 unseen repositories as a final test, used once. Inside the
training repositories, splits are time-ordered: oldest data to train, newest
to stop early.

| Split | Repositories |
| --- | --- |
| Validation (15) | curl/curl, pallets/flask, apache/druid, prometheus/prometheus, obsproject/obs-studio, godotengine/godot, scala/scala3, haskell/cabal, vitejs/vite, AntennaPod/AntennaPod, sigstore/cosign, pola-rs/polars, cli/cli, esphome/esphome, lightningnetwork/lnd |
| Final test (15) | neovim/neovim, redis/redis, laravel/framework, hashicorp/vault, jupyter/notebook, elastic/elasticsearch, JuliaLang/julia, elixir-lang/elixir, sveltejs/svelte, wordpress-mobile/WordPress-iOS, bitwarden/server, delta-io/delta, helix-editor/helix, tigerbeetle/tigerbeetle, actualbudget/actual |
| Train (70) | All other repositories |

Each of the 11 categories has one or two repositories in both held-out sets,
each set has at least four repositories under 6,000 pull requests and one
giant (godot in validation, elasticsearch in test), and the held-out sets
cover C, C++, Go, Rust, Java, Kotlin, Scala, Haskell, Julia, Elixir, PHP,
Swift, C#, Zig, TypeScript and Python. The split is re-balanced once the
estimates are measured.

## The current 50

### Systems, languages and developer tools

| Repository | PRs | | Commits | | Since | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) | 90,279 | M | 141,606 | S | 2014 | Prow/tide bot merges via the API; cherry-pick PRs |
| [rust-lang/cargo](https://github.com/rust-lang/cargo) | 8,422 | M | ~20,000 | E | 2014 | Merge queue; a submodule of rust |
| [python/cpython](https://github.com/python/cpython) | 77,404 | M | ~128,000 | E | 1990 | Backport bot (miss-islington) PRs |
| [rust-lang/rust](https://github.com/rust-lang/rust) | ~113,000 | E | 341,866 | S | 2010 | bors rollups: one merge can cover up to 20 PRs; rebase-only |
| [llvm/llvm-project](https://github.com/llvm/llvm-project) | 125,189 | M | 599,783 | S | 2001 | Squash-merge; direct pushes to main still allowed |
| [rust-lang/rust-clippy](https://github.com/rust-lang/rust-clippy) | 7,856 | M | ~19,000 | E | 2014 | Subtree syncs with rust inflate commits |
| [curl/curl](https://github.com/curl/curl) | 15,597 | M | ~35,000 | E | 1999 | Maintainer often rebases and pushes by hand |
| [neovim/neovim](https://github.com/neovim/neovim) | 25,774 | M | ~34,000 | E | 2014 | vim-patch port PRs (semi-automatic) |
| [microsoft/vscode](https://github.com/microsoft/vscode) | 67,137 | M | ~140,000 | E | 2015 | Many direct team commits |
| [microsoft/TypeScript](https://github.com/microsoft/TypeScript) | 19,456 | M | ~37,000 | E | 2014 | typescript-bot PRs |

### Web and application frameworks

| Repository | PRs | | Commits | | Since | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| [facebook/react](https://github.com/facebook/react) | 19,459 | M | ~21,000 | E | 2013 | Some Meta-internal sync |
| [nodejs/node](https://github.com/nodejs/node) | 42,628 | M | ~47,000 | E | 2009 | Lands via the commit-queue bot; PRs often closed, not merged |
| [denoland/deno](https://github.com/denoland/deno) | 18,494 | M | ~14,500 | E | 2018 | Squash-merge |
| [django/django](https://github.com/django/django) | 21,536 | M | ~34,500 | E | 2005 | Mergers push by hand; backports |
| [pallets/flask](https://github.com/pallets/flask) | 2,849 | M | ~5,500 | E | 2010 | Small |
| [rails/rails](https://github.com/rails/rails) | 39,275 | M | ~95,000 | E | 2004 | Merge commits; backports |
| [laravel/framework](https://github.com/laravel/framework) | 35,937 | M | ~40,000 | E | 2013 | Branch merge-ups |
| [spring-projects/spring-boot](https://github.com/spring-projects/spring-boot) | 7,851 | M | ~58,000 | E | 2013 | Most commits pushed directly; low PR-to-commit ratio |
| [electron/electron](https://github.com/electron/electron) | 30,952 | M | ~31,000 | E | 2013 | Backport bot PRs; Chromium roll PRs |
| [vercel/next.js](https://github.com/vercel/next.js) | 40,513 | M | ~32,000 | E | 2016 | Canary release commits |

### Databases and data systems

| Repository | PRs | | Commits | | Since | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| [ClickHouse/ClickHouse](https://github.com/ClickHouse/ClickHouse) | 79,031 | M | ~240,000 | E | 2008 | Merge commits; backport bot PRs |
| [redis/redis](https://github.com/redis/redis) | 8,205 | M | ~13,000 | E | 2009 | Licence changes 2024 and 2025 |
| [pingcap/tidb](https://github.com/pingcap/tidb) | 47,046 | M | ~26,000 | E | 2015 | Bot merges and cherry-pick bot PRs |
| [apache/airflow](https://github.com/apache/airflow) | 46,533 | M | ~30,000 | E | 2014 | Backport PRs; provider releases |
| [elastic/elasticsearch](https://github.com/elastic/elasticsearch) | 107,155 | M | 106,421 | S | 2010 | Bot mute-test commits; backport PRs; licence changes |
| [apache/kafka](https://github.com/apache/kafka) | 23,106 | M | ~17,500 | E | 2011 | Issues in Jira |
| [apache/spark](https://github.com/apache/spark) | 56,921 | M | ~50,000 | E | 2010 | Merged by script; PRs closed, not merged |
| [apache/flink](https://github.com/apache/flink) | 28,596 | M | ~37,000 | E | 2010 | Committers push by hand; Jira |
| [apache/druid](https://github.com/apache/druid) | 15,010 | M | ~14,500 | E | 2012 | Squash-merge |
| [duckdb/duckdb](https://github.com/duckdb/duckdb) | 14,337 | M | ~55,000 | E | 2018 | Merge commits keep PR branch commits |

### Cloud, infrastructure and observability

| Repository | PRs | | Commits | | Since | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| [docker/compose](https://github.com/docker/compose) | 5,614 | M | ~7,000 | E | 2013 | v1 to v2 rewrite discontinuity |
| [moby/moby](https://github.com/moby/moby) | 28,598 | M | ~52,000 | E | 2013 | Merge commits; vendoring PRs |
| [hashicorp/terraform](https://github.com/hashicorp/terraform) | 16,468 | M | ~35,000 | E | 2014 | BSL licence since 2023 |
| [hashicorp/vault](https://github.com/hashicorp/vault) | 25,262 | M | ~23,000 | E | 2015 | Enterprise-to-OSS sync commits; BSL since 2023 |
| [ansible/ansible](https://github.com/ansible/ansible) | 53,359 | M | ~55,000 | E | 2012 | 2020 collections split in history |
| [prometheus/prometheus](https://github.com/prometheus/prometheus) | 12,059 | M | ~14,000 | E | 2012 | Merge commits |
| [grafana/grafana](https://github.com/grafana/grafana) | 82,187 | M | ~62,000 | E | 2013 | Backport bot PRs; AGPL since 2021 |
| [open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector) | 11,133 | M | ~8,000 | E | 2019 | Heavy on dependency-bot PRs |
| [istio/istio](https://github.com/istio/istio) | 37,496 | M | ~24,000 | E | 2016 | Prow bot merges; automator PRs |
| [envoyproxy/envoy](https://github.com/envoyproxy/envoy) | ~33,000 | E | 28,474 | S | 2016 | Dependency bot PRs; release backports |

### ML, science, media and end-user software

| Repository | PRs | | Commits | | Since | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| [pytorch/pytorch](https://github.com/pytorch/pytorch) | 136,790 | M | 111,609 | S | 2012 | Lands via a merge bot and ghstack; PRs show as closed |
| [tensorflow/tensorflow](https://github.com/tensorflow/tensorflow) | 78,494 | M | ~195,000 | E | 2015 | Heavy internal Copybara sync; most commits not from GitHub PRs |
| [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) | 21,218 | M | ~33,000 | E | 2010 | Squash-merge |
| [huggingface/transformers](https://github.com/huggingface/transformers) | 27,932 | M | ~21,000 | E | 2018 | Squash-merge |
| [jupyter/notebook](https://github.com/jupyter/notebook) | 2,641 | M | ~10,500 | E | 2015 | v7 rewrite discontinuity |
| [obsproject/obs-studio](https://github.com/obsproject/obs-studio) | 8,023 | M | ~14,500 | E | 2013 | Maintainers rebase-merge |
| [godotengine/godot](https://github.com/godotengine/godot) | 56,763 | M | ~80,000 | E | 2014 | Cherry-pick PRs to 4.x branches |
| [home-assistant/core](https://github.com/home-assistant/core) | 108,031 | M | ~118,000 | E | 2013 | Default branch dev; many small integration PRs |
| [nextcloud/server](https://github.com/nextcloud/server) | 40,253 | M | ~80,000 | E | 2016 | Backport bot PRs; ownCloud history from 2010 |
| [bitcoin/bitcoin](https://github.com/bitcoin/bitcoin) | 25,127 | M | ~46,000 | E | 2009 | Maintainers merge locally with signed merge commits; PRs closed, not merged |

| Category | PRs | Commits (approx.) |
| --- | --- | --- |
| Systems, languages and developer tools | 550,114 | 1,496,255 |
| Web and application frameworks | 259,494 | 378,500 |
| Databases and data systems | 425,940 | 589,421 |
| Cloud, infrastructure and observability | 305,176 | 308,474 |
| ML, science, media and end-user software | 505,272 | 709,609 |
| **Current 50** | **2,045,996** | **3,482,259** |

## The new 50

### Languages and compilers

| Repository | PRs | Commits | Language | Since | Notes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [swiftlang/swift](https://github.com/swiftlang/swift) | 65,547 | 211,250 | C++, Swift | 2010 | CI triggered by a bot; release-branch cherry-pick PRs | PRs S (Jan 2026 snapshot), commits S |
| [dotnet/roslyn](https://github.com/dotnet/roslyn) | ~42,000 | 146,826 | C# | 2014 | Dependency and merge-flow bot PRs | PRs E, commits S |
| [JuliaLang/julia](https://github.com/JuliaLang/julia) | ~28,000 | 63,162 | Julia, C | 2009 | Buildkite CI; backport labels | PRs E, commits S |
| [scala/scala3](https://github.com/scala/scala3) | ~12,000 | ~30,000 | Scala | 2012 | Renamed from lampepfl/dotty | E |
| [elixir-lang/elixir](https://github.com/elixir-lang/elixir) | ~5,500 | ~22,000 | Elixir | 2011 | Core team pushes much directly to main | E |
| [haskell/cabal](https://github.com/haskell/cabal) | ~5,000 | ~17,000 | Haskell | 2004 | Mergify merges and backports | E |
| [php/php-src](https://github.com/php/php-src) | ~11,000 | ~140,000 | C | 1999 | Many PRs merged by hand and closed; branch merge-ups | E |
| [oven-sh/bun](https://github.com/oven-sh/bun) | ~12,000 | ~13,000 | Zig, C++, TS | 2021 | Many bot- and AI-authored PRs | E |
| **Subtotal** | **181,047** | **643,238** | | | | |

### Frontend and mobile

| Repository | PRs | Commits | Language | Since | Notes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [angular/angular](https://github.com/angular/angular) | ~32,000 | ~33,000 | TypeScript | 2014 | Merged by a merge tool; PRs show as closed | E |
| [vuejs/core](https://github.com/vuejs/core) | ~6,500 | 7,198 | TypeScript | 2018 | Renovate bot | PRs E, commits S |
| [sveltejs/svelte](https://github.com/sveltejs/svelte) | ~8,500 | ~11,000 | JS, TS | 2016 | Changesets release PRs | E |
| [vitejs/vite](https://github.com/vitejs/vite) | ~11,000 | ~9,500 | TypeScript | 2020 | Renovate bot | E |
| [withastro/astro](https://github.com/withastro/astro) | ~9,500 | ~12,000 | TypeScript | 2021 | Changesets release PRs | E |
| [flutter/flutter](https://github.com/flutter/flutter) | 75,919 | 91,756 | Dart, C++ | 2015 | Engine merged in 2024; autoroller and autosubmit bots | PRs S (Sep 2026), commits S |
| [expo/expo](https://github.com/expo/expo) | ~20,000 | ~30,000 | TS, Kotlin, Swift | 2016 | Monorepo; release commits | E |
| [thunderbird/thunderbird-android](https://github.com/thunderbird/thunderbird-android) | ~4,500 | ~12,000 | Kotlin, Java | 2008 | Formerly K-9 Mail; translation bot PRs | E |
| [AntennaPod/AntennaPod](https://github.com/AntennaPod/AntennaPod) | ~3,500 | ~6,500 | Java, Kotlin | 2012 | Small; useful for calibration | E |
| [wordpress-mobile/WordPress-iOS](https://github.com/wordpress-mobile/WordPress-iOS) | ~17,000 | ~50,000 | Swift, ObjC | 2008 | Release-branch merges; Buildkite CI | E |
| **Subtotal** | **188,419** | **262,954** | | | | |

### Security and networking

| Repository | PRs | Commits | Language | Since | Notes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [openssl/openssl](https://github.com/openssl/openssl) | ~20,000 | ~36,000 | C | 1998 | Pushed by hand after approval; PRs closed, not merged | E |
| [keycloak/keycloak](https://github.com/keycloak/keycloak) | ~25,000 | ~28,000 | Java | 2013 | Heavy on Dependabot | E |
| [aquasecurity/trivy](https://github.com/aquasecurity/trivy) | ~5,000 | ~3,500 | Go | 2019 | Dependabot | E |
| [sigstore/cosign](https://github.com/sigstore/cosign) | ~3,000 | ~2,800 | Go | 2021 | Small; many dependency bumps | E |
| [bitwarden/server](https://github.com/bitwarden/server) | ~5,000 | ~5,500 | C# | 2015 | Mixed licence; Renovate | E |
| [cilium/cilium](https://github.com/cilium/cilium) | ~32,000 | ~40,000 | Go, C (eBPF) | 2015 | Many backport PRs; Renovate | E |
| [caddyserver/caddy](https://github.com/caddyserver/caddy) | ~3,000 | ~5,000 | Go | 2015 | v2 rewrite in 2019 | E |
| [tailscale/tailscale](https://github.com/tailscale/tailscale) | ~9,000 | ~11,000 | Go | 2020 | Check share of commits without PRs | E |
| **Subtotal** | **102,000** | **131,800** | | | | |

### Data engineering and ML infrastructure

| Repository | PRs | Commits | Language | Since | Notes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [apache/arrow](https://github.com/apache/arrow) | ~25,000 | ~17,000 | C++, multi | 2016 | Moved from Jira to GitHub issues in 2023 | E |
| [trinodb/trino](https://github.com/trinodb/trino) | ~15,000 | ~25,000 | Java | 2012 | Renamed from Presto in 2020 | E |
| [pola-rs/polars](https://github.com/pola-rs/polars) | ~13,000 | ~12,000 | Rust, Python | 2020 | Squash-merge | E |
| [dbt-labs/dbt-core](https://github.com/dbt-labs/dbt-core) | ~5,500 | ~8,000 | Python | 2016 | Changelog and backport bots | E |
| [ray-project/ray](https://github.com/ray-project/ray) | ~38,000 | 31,787 | Python, C++ | 2016 | External Buildkite CI; flaky-test PRs | PRs E, commits S |
| [vllm-project/vllm](https://github.com/vllm-project/vllm) | ~28,000 | 20,901 | Python, CUDA | 2023 | Over 5,000 open PRs; short history | PRs E, commits S |
| [numpy/numpy](https://github.com/numpy/numpy) | ~17,000 | 42,003 | Python, C | 2002 | SVN-era history | PRs E, commits S |
| [delta-io/delta](https://github.com/delta-io/delta) | ~3,500 | ~4,500 | Scala, Java | 2019 | Check for internal-sync commits | E |
| **Subtotal** | **145,000** | **161,191** | | | | |

### Developer tools, package managers and editors

| Repository | PRs | Commits | Language | Since | Notes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [Homebrew/brew](https://github.com/Homebrew/brew) | ~14,000 | ~30,000 | Ruby | 2009 | Dependabot; split from legacy-homebrew in 2016 | E |
| [astral-sh/uv](https://github.com/astral-sh/uv) | ~9,000 | ~7,000 | Rust | 2023 | Squash-merge; short history | E |
| [pnpm/pnpm](https://github.com/pnpm/pnpm) | ~5,000 | ~9,000 | TypeScript | 2015 | Changesets | E |
| [cli/cli](https://github.com/cli/cli) | ~5,500 | ~10,000 | Go | 2019 | Squash-merge | E |
| [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | ~800 | ~2,300 | Rust | 2016 | Very small; maintainer often re-commits PRs by hand | E |
| [helix-editor/helix](https://github.com/helix-editor/helix) | ~6,500 | ~6,500 | Rust | 2020 |  | E |
| [zed-industries/zed](https://github.com/zed-industries/zed) | ~25,000 | ~35,000 | Rust | 2021 | Private before January 2024, so PR data from 2024 only | E |
| **Subtotal** | **65,800** | **99,800** | | | | |

### Embedded and IoT, games, fintech, CMS and social

| Repository | PRs | Commits | Language | Since | Notes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [zephyrproject-rtos/zephyr](https://github.com/zephyrproject-rtos/zephyr) | ~60,000 | ~110,000 | C | 2014 | Backport bot PRs; release-team merges | E |
| [esphome/esphome](https://github.com/esphome/esphome) | ~8,000 | ~9,000 | C++, Python | 2018 |  | E |
| [bevyengine/bevy](https://github.com/bevyengine/bevy) | ~12,000 | ~9,000 | Rust | 2019 | Merge queue | E |
| [luanti-org/luanti](https://github.com/luanti-org/luanti) | ~8,000 | ~17,000 | C++, Lua | 2010 | Renamed from minetest in 2024; some direct pushes | E |
| [tigerbeetle/tigerbeetle](https://github.com/tigerbeetle/tigerbeetle) | ~2,500 | ~11,000 | Zig | 2020 | Merge queue | E |
| [actualbudget/actual](https://github.com/actualbudget/actual) | ~3,500 | ~4,000 | TypeScript | 2022 | Small | E |
| [lightningnetwork/lnd](https://github.com/lightningnetwork/lnd) | ~5,500 | ~18,000 | Go | 2015 | Merge commits; multi-commit PRs | E |
| [WordPress/gutenberg](https://github.com/WordPress/gutenberg) | ~40,000 | ~30,000 | JS, TS, PHP | 2017 | Synced downstream into WordPress core | E |
| [mastodon/mastodon](https://github.com/mastodon/mastodon) | ~16,000 | ~20,000 | Ruby, TS | 2016 | Renovate and Crowdin bots | E |
| **Subtotal** | **155,500** | **228,000** | | | | |

## Labels

Planning figures, derived from published rates; not results.

| Label basis | Base | Rate | Positives |
| --- | --- | --- | --- |
| Revert- or hotfix-grade, on merged PRs (about 65% of 2.88M) | ~1.87M | 1–5% | ~18,700–93,700 |
| SZZ-grade, on merged PRs | ~1.87M | ~26% | ~487,000 |
| SZZ positives likely correct | ~487,000 | 57–73% precision | ~278,000–356,000 |

Open-source projects sit at the low end of the revert range: about 1% of
commits, per [Shimagaki et al., ICSME 2016](https://posl.ait.kyushu-u.ac.jp/~kamei/publications/Shimagaki_ICSME2016.pdf).
The SZZ rate follows [ApacheJIT](https://arxiv.org/abs/2203.00101), and R-SZZ's
precision [Rosa et al., ICSE 2021](https://www.inf.usi.ch/faculty/bavota/papers/ICSE-2021-szz.pdf)
means about one SZZ positive in three is wrong. SZZ is therefore a weak label:
weighted below reverts and follow-up fixes, never used alone to evaluate the
unseen-repository test, and hand-audited on about 200 sampled positives per
category.

From 1 October 2026 GitHub keeps check runs, workflow runs and statuses on
public repositories for at most 90 days
([changelog](https://github.blog/changelog/2026-08-27-actions-retention-will-cover-checks-workflow-runs-and-statuses/)).
CI-failure labels exist only where Riffle's own archive covers them; outside
that window the label is *missing*, never *negative*.

## Risks

- **Giants dominate.** The 10 largest repositories hold about 35% of pull
  requests; ripgrep has about 800. Training caps each repository at about
  20,000 PRs sampled evenly over time, or weights each PR by 1/√(repo PRs),
  and metrics are macro-averaged per repository.
- **Merges outside the button.** rust's bors rollups, pytorch's merge bot,
  node's commit queue, spark's merge script, bitcoin, openssl, php-src,
  angular and django merge in ways GitHub reports as *closed*. Merges are
  resolved by commit SHA or the `(#N)` pattern in commit messages, and rust
  rollups are split into their member PRs before labelling.
- **Bot PRs.** Dependency bots, autorollers, backport bots and sync bots are
  excluded or modelled separately; a backport inherits its original PR's
  label.
- **History breaks.** Renames (dotty to scala3, Presto to Trino, minetest to
  luanti, K-9 Mail to thunderbird-android), merged repositories (the flutter
  engine in 2024), private-then-public history (zed), tracker moves (arrow)
  and licence changes are handled per repository.
- **GH Archive after mid-2025.** [OSSInsight](https://ossinsight.io/) reports
  that pull request and issue events since mid-2025 were badly under-captured;
  any backfill from it is checked against the API first.
