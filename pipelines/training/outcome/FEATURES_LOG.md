# Feature engineering log

Every feature we build, every config we test, and the result, pass by pass. Decisions that follow from a pass go into
DECISIONS.md as well. Code: `fe_features.py` (features, numpy + pandas only), `fe_run.py` (experiments),
`fe_fingerprint.py` (recovered-diff test), `test_fe_features.py` (unit and cut tests). Raw results: `fe_results.jsonl`;
gain importances: `fe_importance/`; predictions: `fe_preds/`.

## How every pass is measured

- **Model:** one LightGBM multiclass recipe for every config: 31 leaves, at least 50 rows per leaf, feature fraction 0.7,
  bagging 0.8, L2 5, early stopping on validation, then vector-scaling calibration on validation outside PRs. Rows
  weighted 1/sqrt(PRs in repo). Learning rate 0.1 for ablations; finalists are retrained at 0.05.
- **Rows:** PRs with a known outcome, mature (opened 90+ days before the data cutoff), at-open diff not `final`.
  Scored rows: outside contributors (not a bot, not a confirmed maintainer).
- **Splits:**
  - *By PR*: 20% of PRs by a hash of the head commit; every repo is seen in training.
  - *By repo*: 5 folds by repo hash; each repo is scored by a model that never saw it. Validation inside a fold uses
    whole held-out training repos.
- **Metric:** `eval_script.py`'s skill, 1 − Brier / base-rate Brier averaged over repos (base rate = the training
  outside PRs' outcome mix). Also log loss, accuracy, macro AUROC, calibration error (10 bins, averaged over classes).
- **Two views of the same test set:**
  - *all*: every scored test row.
  - *exact* (`x_` columns): only rows whose at-open diff is exact, each weighted by (test rows of its repo and class) /
    (exact ones among them), so every repo keeps its full class mix. Production diffs are always exact, and recovered
    rows carry a fingerprint (below), so **the exact view is the fair one**.

## Feature catalogue

| Group | Columns | How it is computed | Point-in-time |
|---|---|---|---|
| Allowed inputs | 164 miner columns minus `x-riffle-non-input` ones | As mined | Miner's guarantee (ARCHITECTURE.md) |
| Leaks (dropped) | `author_association`, body columns, `has_linked_issue`, `agent_boilerplate`, `max_sim_open_prs`, `max_sim_recent_merged`, `ai_agent_authored`, `agent_name`, `author_prs_merged_repo` | — | No (DECISIONS.md) |
| Repair | `head_from_fork` | Blank (fork deleted later) read as a fork | Yes |
| C. Consolidated | `c_exp_zero` (first commit ever), `c_exp_specialisation` (SEXP / (EXP+1)), `c_churn` (LA+LD), `c_delete_share` (LD / churn), `c_files_per_dir` (NF / ND), `c_llm_ran`, `c_llm_flags` (count of the 18 LLM detail answers that are not "none") | From the row | Yes |
| C. Dropped as redundant | `REXP` (0.99 with EXP), `la_norm`, `ld_norm`, the 18 LLM detail columns (kept: risk 1-5, change intent, refactor vs behaviour, tests added, unrelated share) | — | — |
| D1. Author in this repo | `ah_n`, `ah_merged`, `ah_nowhere`, `ah_{clean,rework,nowhere}_rate` (shrunk to the repo's 365-day mix, strength 3), `ah_open_now`, `ah_first_pr`, `ah_days_since_prev_pr`, `ah_tenure_days` | Author = lower-cased email; counts of PRs **closed strictly before** this PR opened | Yes; cut test |
| D2. Repo recent behaviour | `rh90_n`, `rh90_{clean,rework,nowhere}_rate` (need 5+), `rh_open_now`, `rh30_opened`, `rh90_mean_log_hours_to_close`, `rh180_ft_{n,clean_rate,nowhere_rate}` (outside first-timers), `rh180_out_*` (outside authors) | Same rule; windows in days before open | Yes; cut test |
| D3. Author across repos | `xh_n_other_repos`, `xh_merged_rate`, `xh_nowhere_rate` | Same rule, all repos in the data | Yes, but needs an org-wide view |
| E. Context | `e_hour_utc`, `e_dow_utc` | Open time in UTC | Yes |

History counts use every known PR (bots, maintainers and `final` rows too: their outcomes are real). PRs still open at
mining time are not in the table, so `*_open_now` undercounts near the cutoff (the same in train and test).

Checks on the history code: `test_fe_features.py` (a cut test: features of PRs opened before a cut are unchanged when
everything after it is unknown; toy counts; seconds from any timestamp unit), and agreement with the earlier
`history.py` on `ah_n`, `ah_open_now`, `rh90_n`, `rh_open_now` (100% of rows) before both units bugs were fixed.

## Pass 1

**Question:** which groups help, under which split; do the three tests change the row or label choices?

Configs (each under both splits, seed 0 unless stated):

| Config | Columns | Rows / option |
|---|---|---|
| `p1_00_all_allowed_with_leaks` | every allowed input | reference: how much the leaks inflate |
| `p1_01_leaks_dropped` | minus the leaks | |
| `p1_02_consolidated` | C applied | |
| `p1_03_plus_author_history` | + D1 | |
| `p1_04_plus_repo_history` | + D2 | |
| `p1_05_plus_context` | + E (the "full" set) | |
| `p1_06_plus_cross_repo_author` | + D3 | |
| `p1_t1_drop_recovered_rows` | full | train on exact diffs only |
| `p1_t2_train_outside_only` | full | train on outside PRs only |
| `p1_t3_repo_as_feature` | full + `repo` | by PR only (specialisation inside one model) |
| `p1_t3_per_repo_models` | full | by PR only; one model per repo |
| `p1_t4_seed_noise` | full | seeds 0, 1, 2: how big a difference is noise |

**Fingerprint test** (`fe_fingerprint.py`, rows of one outcome, repo-grouped CV, AUROC of "not exact"):

| Outcome | Diff values | Diff blanks only | All clean inputs | Share not exact |
|---|---|---|---|---|
| lands_after_rework | 0.671 | 0.645 | 0.653 | 42.3% |
| goes_nowhere | 0.657 | 0.635 | 0.657 | 13.1% |

Blanks alone identify recovered diffs. Largest blank-rate gaps on rework PRs: `min_days_since_last_revert` 22 points,
`ghsa_malware_dep` / `osv_*` / `vuln_has_fix_available` 19, `path_size_pctile` 15, `path_failure_mode_*` 13.

### Pass 1 results

Skill (higher is better). *all* = every scored test row; *exact* = exact diffs reweighted to each repo's class mix
(the fair view). Calibration = weighted calibration error on the exact view (recomputed from `fe_preds/`; the
`x_calib` field in `fe_results.jsonl` is unweighted and should not be used). Seed noise (sd of skill over 3 seeds):
**0.0027 by PR, 0.0009 by repo**, so a by-PR difference under about 0.005 is noise.

| Config | Features | By PR all | By PR exact | By PR calib | By repo all | By repo exact | By repo calib |
|---|---|---|---|---|---|---|---|
| 00 all allowed, leaks in | 164 | 0.386 | 0.379 | 0.009 | 0.328 | 0.323 | 0.017 |
| 01 leaks dropped | 152 | 0.253 | 0.244 | 0.011 | 0.177 | 0.167 | 0.014 |
| 02 consolidated | 138 | 0.254 | 0.246 | 0.010 | 0.180 | 0.169 | 0.014 |
| 03 + author history | 148 | 0.281 | 0.273 | 0.008 | 0.209 | 0.199 | 0.009 |
| 04 + repo history | 161 | 0.284 | 0.275 | 0.011 | 0.235 | 0.227 | 0.014 |
| 05 + context | 163 | 0.283 | 0.274 | 0.011 | 0.232 | 0.224 | 0.013 |
| 06 + cross-repo author | 166 | 0.286 | 0.278 | 0.009 | 0.232 | 0.224 | 0.011 |
| t1 train on exact diffs only | 163 | 0.270 | 0.275 | **0.034** | 0.230 | 0.224 | **0.040** |
| t2 train on outside PRs only | 163 | **0.290** | 0.279 | 0.011 | **0.239** | **0.230** | 0.012 |
| t3 repo as a feature | 164 | **0.290** | 0.280 | 0.010 | n/a | | |
| t3 one model per repo | 163 | 0.272 | 0.263 | 0.015 | n/a | | |

(t1 to t3 use config 05's features.)

Gain importance, config 05 by PR, top 10: `ah_nowhere_rate`, `ah_rework_rate`, `ah_clean_rate`, `LA`, `ah_merged`,
`rh180_ft_nowhere_rate`, `base_behind_by`, `c_churn`, `author_account_age_days`, `ah_n`.

### What pass 1 says

1. **The leaks were worth 0.13 to 0.15 of skill** (00 vs 01). Every earlier number that kept any of them was inflated.
   Pass 2 splits that between the leaks, for the writeup.
2. **Consolidation costs nothing**: 14 fewer columns, skill +0.001 to +0.003. Kept.
3. **Author history is the biggest gain under both splits** (+0.028 by PR, +0.030 by repo), and its rates are the top
   three features.
4. **Repo history matters for new repos only**: +0.003 by PR (noise), **+0.027 by repo**. This is the "online
   correction" working: a repo the model never saw is still described by its own recent past. The outside
   first-timer nowhere rate is the strongest of these columns.
5. **Context (open hour, weekday) adds nothing** (−0.001, −0.003). Dropped.
6. **Cross-repo author history**: +0.003 by PR (about one seed sd), 0.000 by repo, and it needs an org-wide view of
   authors. Dropped for now.
7. **Recovered rows**: the fingerprint inflates the *all* view (by PR 0.283 all vs 0.274 exact), but a model trained
   with them scores the same on exact diffs as one trained without (0.274 vs 0.275). Dropping them as in t1 breaks
   calibration, because 42% of rework PRs go and the model under-predicts rework. Pass 2: drop them and **reweight**
   the exact rows to each repo's class mix.
8. **Training on outside PRs only helps** (+0.005 to +0.007, above noise under both splits) and trains 4x faster.
   Adopted.
9. **Specialisation vs generalisation**: one model per repo is worst (0.272); one shared model is better (0.283); a
   shared model that knows the repo is best (0.290). With 23 repos, **sharing already beats specialising**; knowing
   the repo adds a little on repos the model has seen. A repo the model never saw cannot use the repo column, so only
   the by-PR model may take it.

### Pass 2 plan

- Base: config 05's features minus context, trained on outside PRs.
- Recovered rows: kept vs dropped-and-reweighted.
- Leak decomposition: add each leak back alone.
- Prune: drop the weakest groups by importance (file-history block, scanners) and see if skill holds.
- New features from pass 1's importance: the author's last outcome and recent streak, author rates over the last
  90/365 days (recent behaviour vs lifetime), 90-day first-timer rates, size relative to the repo's recent merged PRs.
- `repo` as a feature for the by-PR model.

## Pass 2

**Question:** keep or drop recovered rows (with reweighting); what each leak was worth; can groups be pruned; do the
new history features help?

Base = pass 1's config 05 minus context (161 columns), trained on outside PRs. Calibration is the weighted
calibration error on the exact view (`fe_calib.py`). Seed noise on this base: **0.0018 by PR, 0.0005 by repo**.

New features built for this pass (all point-in-time, covered by the cut test):

| Group | Columns | How |
|---|---|---|
| D4. Author's recent behaviour | `ar_last_outcome`, `ar_last{3,5}_{nowhere,clean}_rate`, `ar{90,365}_n`, `ar{90,365}_{nowhere,clean}_rate` | The author's last 1/3/5 PRs in this repo closed before open, and the last 90/365 days; rates shrunk to the repo mix |
| D5. First-timers, 90 days | `rf90_ft_n`, `rf90_ft_{clean,nowhere}_rate` | As `rh180_ft_*` over 90 days |
| D6. Relative size | `s_rel_size_z`, `s_rel_size_vs_merged` | log churn against the repo's PRs merged in the last 90 days (10+ needed) |

### Pass 2a: with recovered rows in training (as pass 1)

| Config | Features | By PR all | By PR exact | By repo all | By repo exact |
|---|---|---|---|---|---|
| base | 161 | 0.289 | 0.279 | 0.237 | 0.229 |
| **drop recovered rows, reweighted** | 161 | 0.281 | **0.287** | 0.242 | **0.236** |
| + `author_association` (leak) | 162 | 0.402 | 0.396 | 0.357 | 0.351 |
| + body columns (leak) | 167 | 0.293 | 0.283 | 0.241 | 0.233 |
| + body-text similarity (leak) | 163 | 0.288 | 0.279 | 0.238 | 0.229 |
| + agent flags (leak) | 163 | 0.289 | 0.280 | 0.238 | 0.228 |
| + `author_prs_merged_repo` (leak) | 162 | 0.292 | 0.282 | 0.238 | 0.229 |
| without file history | 123 | 0.289 | 0.280 | 0.234 | 0.225 |
| without scanners | 144 | 0.280 | 0.272 | 0.229 | 0.222 |
| without repo-local (path, similar PRs, release) | 152 | 0.288 | 0.279 | 0.234 | 0.226 |
| without LLM | 154 | 0.278 | 0.270 | 0.226 | 0.217 |
| + D4 author recent | 172 | 0.289 | 0.280 | 0.238 | 0.229 |
| + D5 first-timers 90 days | 164 | 0.290 | 0.282 | 0.236 | 0.228 |
| + D6 relative size | 163 | 0.288 | 0.279 | 0.237 | 0.228 |
| + D4 + D5 + D6 | 177 | 0.292 | 0.283 | 0.235 | 0.226 |

Calibration stayed between 0.009 and 0.017 for every config (by repo, drop-and-reweight: 0.017).

### Pass 2b: the deciding configs again with recovered rows dropped and reweighted (the new default)

Scanner blanks were part of the recovered fingerprint, so the pruning result had to be checked without those rows.
Seed noise: 0.0014 by PR, 0.0005 by repo.

| Config | Features | By PR exact | By repo exact | Calib PR / repo |
|---|---|---|---|---|
| **base (3 seeds)** | 161 | **0.290** | **0.236** | 0.011 / 0.016 |
| without scanners | 144 | 0.284 | 0.231 | 0.013 / 0.013 |
| without file history | 123 | 0.287 | 0.234 | 0.013 / 0.018 |
| without repo-local | 152 | 0.286 | 0.234 | 0.013 / 0.021 |
| + D4 + D5 + D6 | 177 | 0.289 | 0.236 | 0.010 / 0.017 |
| + `repo` as a feature | 162 | 0.293 | n/a | 0.011 |
| + D4 + D5 + D6 + `repo` | 178 | 0.294 | n/a | 0.013 |

### What pass 2 says

1. **Drop recovered rows and reweight.** +0.008 by PR and +0.007 by repo on the exact view, calibration within
   limits. The *all* view falls by PR (0.289 to 0.281) because the model no longer reads the fingerprint: that drop is
   the inflation going away.
2. **`author_association` was nearly the whole leak:** +0.11 by PR, +0.12 by repo on its own. The body columns add
   +0.004 on both splits (a small leak, or a real signal we cannot use until the miner reads the body as at open);
   `author_prs_merged_repo` +0.003 by PR only; similarity and agent flags nothing.
3. **Nothing can be pruned.** On the new default, removing scanners costs 0.006 / 0.005, file history 0.003 / 0.003,
   repo-local 0.004 / 0.003, LLM (pass 2a) 0.009 / 0.012. The scanner loss is real signal, not only the fingerprint.
4. **The new history features (D4 to D6) add nothing** beyond pass 1's history. The summary rates already hold what a
   tree can use from the past. Dropped (the sequence model in pass 3 tries the history another way).
5. **`repo` as a feature:** +0.003 by PR (about 2 seed sd). Kept as an option for the seen-repos model.

**Frozen for pass 3:** the 161-column base; outside PRs; recovered rows dropped with reweighting.

## Pass 3: architectures

**Question:** on the frozen feature set (161 columns, outside PRs, recovered rows dropped and reweighted), does any
other model beat LightGBM, and do blends help? Code: `fe_models.py`, `fe_seq.py`, `fe_blend.py`. Seed 0; exact view;
calibration error weighted. Latency = ms per PR for the model step, batches of 256 (features excluded).

Learners:
- LightGBM at learning rate 0.05, four settings: default (31 leaves), small leaves (15, 100 per leaf, L2 10), big
  leaves (63), strong regularisation (200 per leaf, L2 30, feature fraction 0.5).
- XGBoost (GPU, depth 6, eta 0.05) and CatBoost (GPU, depth 6, rate 0.08).
- Logistic regression (signed log1p, standardised, missing flags, one-hot categories; C 0.1).
- MLP (same inputs; 512-256-128, LayerNorm, SiLU, dropout 0.2, AdamW; early stopping on validation).
- **Sequence model** (`seq_gru`): the MLP's static input, plus a GRU over the author's last 16 PRs in this repo
  opened before this one (size, files, fork, outside, first-timer, LLM risk, test and docs shares, time since open,
  and the outcome and time to close **only if it closed before this PR opened**), plus a GRU over the repo's last 32
  PRs closed before this one opened. Cut test in `test_fe_features.py`.

| Model | By PR skill | log loss | calib | By repo skill | log loss | calib | ms / PR |
|---|---|---|---|---|---|---|---|
| LightGBM default | **0.294** | 0.778 | 0.010 | 0.240 | 0.860 | 0.018 | 0.03-0.05 |
| LightGBM small leaves | 0.293 | 0.779 | 0.011 | 0.239 | 0.860 | 0.020 | 0.03-0.06 |
| LightGBM big leaves | 0.292 | 0.777 | 0.014 | 0.237 | 0.863 | 0.018 | 0.03-0.05 |
| LightGBM strong regularisation | 0.292 | 0.780 | 0.012 | **0.240** | 0.859 | 0.018 | 0.03-0.07 |
| XGBoost | 0.293 | 0.778 | 0.012 | 0.239 | 0.860 | 0.016 | 0.12-0.26 |
| CatBoost | 0.287 | 0.787 | 0.010 | 0.229 | 0.873 | 0.016 | 0.06 |
| Logistic regression | 0.243 | 0.836 | 0.015 | 0.219 | 0.880 | 0.015 | 0.04 |
| MLP | 0.265 | 0.809 | 0.013 | 0.223 | 0.879 | 0.008 | 0.07 |
| Sequence (GRU) | 0.271 | 0.801 | 0.010 | 0.224 | 0.877 | 0.008 | 0.11 |

Blends (`fe_blend.py`): weights and recalibration cross-fitted on halves of the test repos, so the single model is
also shown through the same procedure (it costs it about 0.002 against the table above). Geometric = weighted
average of log probabilities; it was equal to or slightly better than linear in every case.

| Blend (geometric) | By PR | By repo |
|---|---|---|
| LightGBM alone (same procedure) | 0.292 | 0.238 |
| + XGBoost | 0.295 | 0.239 |
| + sequence | 0.295 | 0.241 |
| + MLP | 0.295 | 0.241 |
| + logistic regression | 0.292 | 0.242 |
| + sequence + MLP | 0.295 | 0.241 |
| **+ XGBoost + sequence + MLP** | **0.298** | **0.242** |

Weights in the four-model blend: about LightGBM 0.3, XGBoost 0.4, sequence 0.2, MLP 0.1 (by PR); LightGBM 0.3,
XGBoost 0.3, sequence 0.3, MLP 0.1 (by repo, varying between halves).

### What pass 3 says

1. **Boosting still wins as a single model**, now on clean features: LightGBM 0.294 / 0.240, XGBoost level with it,
   CatBoost 0.007 to 0.011 behind. The early choice of LightGBM holds, for the right reason this time.
2. **Settings barely matter** (0.292 to 0.294; 0.237 to 0.240). Heavier regularisation is slightly better for new
   repos, as expected.
3. **Networks trail by 0.02 to 0.03.** Reading history as a sequence helps a network (sequence 0.271 vs MLP 0.265 by
   PR) but does not catch the trees' summary rates.
4. **Blends add about 0.005** (about 3 seed sd by PR, about 9 by repo). On new repos any different partner helps
   about equally (logistic regression as much as the sequence model): the gain there comes from smoothing the
   trees' confident predictions, not from new information.
5. The whole spread between architectures (except logistic regression) is about 0.03; the leaks were worth 0.12 and
   history 0.06. **Features and rows decide this problem much more than the model does.**

## Pass 4: the cost and benefit of each setup

**Question:** for each candidate blend, what does it buy in quality, and what does it cost to train, ship and serve?
Code: `fe_setups.py` (quality, from saved predictions), `fe_profile.py` (each member trained and served in a fresh
process). Machine: 16 logical cores, 15 GB RAM visible to WSL, RTX 4070 Laptop 8 GB.

### Quality over 3 seeds (exact view, cross-fitted geometric blends)

| Setup | By PR skill | sd | By repo skill | sd | Calib (worst class) PR / repo | Weakest repo (by repo split) |
|---|---|---|---|---|---|---|
| A LightGBM | 0.292 | 0.0018 | 0.238 | 0.0004 | 0.012 / 0.017 | nestjs/nest 0.070 |
| A' LightGBM, strong regularisation | 0.290 | 0.0005 | 0.238 | 0.0001 | 0.012 / 0.017 | nestjs/nest 0.060 |
| B LightGBM + XGBoost | 0.295 | 0.0005 | 0.239 | 0.0003 | 0.012 / 0.017 | nestjs/nest 0.065 |
| C LightGBM + sequence | 0.295 | 0.0015 | 0.242 | 0.0004 | 0.012 / 0.014 | nestjs/nest 0.057 |
| D LightGBM + logistic regression | 0.292 | 0.0017 | 0.242 | 0.0003 | 0.012 / 0.015 | nestjs/nest 0.066 |
| E LightGBM + sequence + MLP | 0.295 | 0.0017 | 0.242 | 0.0005 | 0.013 / 0.014 | nestjs/nest 0.058 |
| F LightGBM + XGBoost + sequence + MLP | **0.297** | 0.0008 | 0.242 | 0.0006 | 0.012 / 0.014 | nestjs/nest 0.055 |
| G LightGBM + XGBoost + logistic regression | 0.294 | 0.0005 | **0.242** | 0.0001 | 0.012 / 0.015 | nestjs/nest 0.063 |
| H LightGBM + XGBoost + sequence | **0.297** | 0.0004 | **0.242** | 0.0003 | 0.012 / 0.014 | nestjs/nest 0.055 |

No setup has a repo below the base rate. nestjs/nest is the weakest repo under every setup.

### Cost of each member (by-PR training set, about 70,000 PRs; serving on the CPU with CUDA hidden)

| Member | Train (GPU there) | Train, CPU only | Train peak RAM | Train peak GPU | Same seed, same model? | Model on disk | Libraries | 1 PR (median / p95) | Per PR, batch 256 |
|---|---|---|---|---|---|---|---|---|---|
| LightGBM | 23 s | 23 s | 0.1 GB | none | yes | 4.3 MB | lightgbm 10 MB | 6.2 / 8.1 ms | 0.05 ms |
| LightGBM, strong reg. | 22 s | 22 s | 0.1 GB | none | yes | 5.5 MB | (same) | 6.3 / 7.5 ms | 0.06 ms |
| XGBoost | 28 s | 29-39 s | 0.3 GB | 0.3 GB | **GPU: no** (probabilities differ by up to 0.17); CPU: yes | 7.8 MB | xgboost 84 MB | 14.8 / 24.1 ms | 0.09 ms |
| Logistic regression | 14 s | 14 s | 0.2 GB | none | yes | 0.02 MB | scikit-learn + scipy 177 MB | 9.2 / 14.2 ms | 0.05 ms |
| MLP | 8 s | about 38 s | 0.6 GB | 0.3 GB | yes (this run) | 1.1 MB | torch | 11.1 / 15.3 ms | 0.06 ms |
| Sequence (GRU) | 12 s | about 150 s | 1.7 GB | 2.2 GB | yes (this run) | 0.6 MB | torch | 12.5 / 15.0 ms | 0.11 ms (+0.01 ms gathering tokens) |

Libraries: torch with its CUDA libraries is **6.2 GB** in our venv (a CPU-only build is far smaller, or the network
can be exported to ONNX and served with onnxruntime). numpy and pandas (139 MB) are needed by every setup. Loading
any model takes 0.01 to 0.3 s; importing its library 1.3 to 2.6 s; the RAM a loaded model adds is under 15 MB for
all of them. One-PR latency is mostly pandas overhead per call, not the model.

### Setup totals and qualities that are not numbers

| Setup | Train, CPU only | Libraries beyond numpy/pandas | 1 PR, members in turn | Per-PR explanation | Moving parts |
|---|---|---|---|---|---|
| A LightGBM | 23 s | 10 MB | 6 ms | exact (TreeSHAP built in) | 1 model |
| B + XGBoost | 60 s | 94 MB | 21 ms | exact for both | 2 models, 2 libraries |
| C + sequence | 175 s | 10 MB + torch | 19 ms | trees only; the network needs an approximation | 2 models; the service must also serve raw recent PRs per author and repo |
| D + logistic regression | 37 s | 187 MB | 15 ms | exact (coefficients are additive) | 2 models |
| G LightGBM + XGBoost + logistic regression | 75 s | 271 MB | 30 ms | exact for all three | 3 models, 3 libraries |
| H LightGBM + XGBoost + sequence | 210 s | 94 MB + torch | 34 ms | trees only | 3 models; raw recent PRs |
| F all four | 250 s | 94 MB + torch | 45 ms | trees only | 4 models; raw recent PRs |

Every setup is far inside the 200 ms latency limit, and every one trains on a CPU in under 5 minutes on this
training set (the full corpus will be about 5x larger).

### Pros and cons

- **A (LightGBM alone):** simplest and smallest, deterministic, exact explanations; the baseline. Gives up 0.005 by
  PR and 0.004 by repo against the best.
- **B (+ XGBoost):** +0.003 on seen repos, nothing on new repos. Torch-free and explainable. Must train XGBoost on
  the CPU to be reproducible.
- **C (+ sequence):** +0.004 on new repos, +0.003 on seen. Costs torch, the most training RAM (1.7 GB) and GPU (2.2 GB),
  and a serving path that fetches the author's last 16 and the repo's last 32 PRs (a history store the history
  features need in some form anyway).
- **D (+ logistic regression):** the cheapest way to the best new-repo score (0.242): torch-free, deterministic,
  explainable, 37 s to train. No gain on seen repos.
- **G (LightGBM + XGBoost + logistic regression):** best new-repo score with no torch, 0.294 on seen repos.
- **H (LightGBM + XGBoost + sequence):** best on both splits (0.297 / 0.242) and the most stable over seeds by PR
  (sd 0.0004). Costs torch and the raw-history serving path; explanations cover the trees only.
- **F (all four):** no better than H; the MLP adds nothing once the sequence model is there. Dropped.
- **E:** no better than C. Dropped.

### What pass 4 says

1. Quality differences between the serious candidates are small: 0.292 to 0.297 by PR, 0.238 to 0.242 by repo.
2. On **new repos**, any smoothing partner gets the gain; the cheapest is logistic regression (D or G).
3. On **seen repos**, the sequence model plus XGBoost (H) is the only setup at 0.297; without torch, the best is
   0.295 (B) or 0.294 (G).
4. The real cost of the best setup is not time or memory (all small) but **torch** and the **raw-history serving
   path**, plus losing exact explanations for part of the score.
5. XGBoost must train on the CPU: on the GPU the same seed gives a different model.

## Pass 5: the deliverable

The notebook (`pipeline.ipynb`) re-implements the frozen features without importing this folder's code.
Checked against `fe_features.py` on all 192,963 PRs: every engineered column matches, after one fix found by the
comparison. 1,339 goes-nowhere PRs have no `closed_at`; the experiment code treated them as closed when they
opened, so their outcome leaked into later PRs' history. Both now leave such PRs out of every outcome and open/closed
count. The remaining differences (0.001% to 0.45% of rows, in `ah_first_pr`, `ah_days_since_prev_pr`, `ah_tenure_days`
and `rh30_opened`) come from 35 PRs with no known outcome: the notebook keeps them as "opened", the experiment code
dropped them.

## Rerun of passes 1 to 4 on leak-free features

**Why:** passes 1 to 4 ran before the stale-open fix (item 8 of DECISIONS.md), so their numbers carried the leak.
A new test (`test_stale_open_outcome_never_used`: scramble the outcomes of rows with no `closed_at`; no feature or
sequence token may change) found two more places that read those rows:

- `fe_seq.py`: the sequence model still closed a stale-open row at its open, so its outcome showed in later PRs'
  author and repo sequences. Now such a row closes after mining (`close_secs`): it stays "still open" in author
  sequences and never enters a repo sequence. Stale-open rows really were open at every earlier moment.
- `fe_features.py`, `s_rel_*` (relative size against recently merged PRs): read every row's outcome. No real effect,
  because stale-open rows are all goes-nowhere and never counted as merged; masked anyway. Not in the deliverable.

The notebook's point-in-time cell now runs the same stale-open check. Everything below is from
`rerun_clean.sh` (logs in `logs/rerun_*.log`), on the same rows, splits, seeds and settings as the first runs.

### Passes 1 and 2

Every config moved by at most 0.005. By PR the changes go both ways (within seed noise); by repo almost every config
lost 0.001 to 0.004, as expected from a leak that helped on repos the model never saw. No decision changes:

- the leaks are still worth what they were (`author_association` back in: 0.397 / 0.351)
- author history still adds about +0.03; repo history still helps new repos
- one model that knows the repo (0.284 by PR) still beats separate models per repo (0.264)
- dropping recovered rows and reweighting the exact ones (0.290 / 0.234) still beats keeping them (0.280 / 0.226)
- removing any feature group still costs skill
- the pass-2 new features still do not help; on clean features all of them together are clearly worse
  (0.285 / 0.232 against 0.291 / 0.235 for the base set)

### Pass 3

The order is unchanged. By PR / by repo, one seed: LightGBM 0.293 / 0.237, XGBoost 0.294 / 0.234, CatBoost
0.287 / 0.229, sequence model 0.270 / 0.220, MLP 0.264 / 0.219, logistic regression 0.242 / 0.215.

### Pass 4 (3 seeds, exact view, cross-fitted geometric blends)

| Setup | By PR skill (before) | By PR skill (clean) | sd | By repo skill (before) | By repo skill (clean) | sd | Calib (worst class) PR / repo | Weakest repo (by repo split) |
|---|---|---|---|---|---|---|---|---|
| A LightGBM | 0.2921 | 0.2926 | 0.0008 | 0.2381 | 0.2348 | 0.0004 | 0.012 / 0.017 | nestjs/nest 0.057 |
| A' LightGBM, strong regularisation | 0.2902 | 0.2898 | 0.0012 | 0.2383 | 0.2353 | 0.0008 | 0.012 / 0.018 | nestjs/nest 0.061 |
| B LightGBM + XGBoost | 0.2949 | 0.2949 | 0.0001 | 0.2391 | 0.2357 | 0.0001 | 0.012 / 0.018 | nestjs/nest 0.057 |
| C LightGBM + sequence | 0.2951 | 0.2955 | 0.0008 | 0.2419 | 0.2386 | 0.0006 | 0.012 / 0.015 | nestjs/nest 0.048 |
| D LightGBM + logistic regression | 0.2916 | 0.2923 | 0.0009 | 0.2418 | 0.2387 | 0.0003 | 0.013 / 0.015 | nestjs/nest 0.056 |
| E LightGBM + sequence + MLP | 0.2951 | 0.2955 | 0.0003 | 0.2416 | 0.2381 | 0.0008 | 0.012 / 0.014 | nestjs/nest 0.048 |
| F LightGBM + XGBoost + sequence + MLP | 0.2969 | 0.2970 | 0.0004 | 0.2421 | 0.2386 | 0.0006 | 0.012 / 0.014 | nestjs/nest 0.049 |
| G LightGBM + XGBoost + logistic regression | 0.2943 | 0.2945 | 0.0001 | 0.2424 | 0.2390 | 0.0000 | 0.012 / 0.015 | nestjs/nest 0.057 |
| H LightGBM + XGBoost + sequence | 0.2970 | 0.2969 | 0.0001 | 0.2424 | 0.2390 | 0.0003 | 0.012 / 0.015 | nestjs/nest 0.049 |

- By repo, every setup lost about 0.003. By PR nothing moved by more than 0.001.
- The sequence model's own fix made no visible difference (H by PR 0.2970 before, 0.2969 after).
- G and H still tie on new repos (0.2390). H still leads on seen repos by 0.0024 (0.0027 before). The reasons for
  choosing G (no torch, deterministic on the CPU, every part explainable) stand.
- Costs (`fe_profile.json`) were not rerun; the leak cannot change training time, memory or latency.
