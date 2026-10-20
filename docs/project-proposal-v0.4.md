DADS 7305 Machine Learning Operations, final project proposal

# Riffle

Spend review time on the pull requests that will land.

Named after a riffle: the shallow, fast stretch of a stream where gravel sorts itself as the water passes through.

**Version** 0.4, draft for team review **Replaces** v0.3 (risk-ranking for engineering teams) **Date** October 2026

Contents

1. [Summary](#summary)
2. [1. Problem](#problem)
3. [2. Competitive position](#competition)
4. [3. Product](#product)
5. [4. Model design](#model)
6. [5. Data](#data)
7. [6. Drift and retraining](#drift)
8. [7. Architecture and stack](#stack)
9. [8. Success metrics](#metrics)
10. [9. Week-two go/no-go](#gate)
11. [10. Work breakdown](#work)
12. [11. Timeline](#timeline)
13. [12. Risks](#risks)
14. [13. Open questions](#open)
15. [References](#refs)

## Summary

Riffle is an open-source GitHub App for open-source maintainers. When an AI coding agent opens a pull request, Riffle predicts how that PR is likely to end: merged, merged after rework, closed as a duplicate, closed as unwanted, or abandoned. Maintainers get a private triage view of where their attention will pay off. The agent gets a short, constructive brief on what this repository expects.

The model learns from what maintainers actually did with past agent PRs, not from hand-written rules. Coding agents change constantly, so the project's MLOps centrepiece is detecting when the stream of agent PRs shifts and retraining automatically.

The project covers the full MLOps lifecycle: data ingestion and versioning, training and experiment tracking, containerised deployment on GCP, drift monitoring and automated retraining. The deliverable is a working app a maintainer can install, backed by a time-ordered backtest on public data.

**What changed from v0.3.** v0.3 ranked PRs by defect risk for engineering teams. CodeRabbit Triage launched an almost identical product in September 2026, and research found merged agent PRs are not measurably buggier than human ones. v0.4 targets the gap that remains: wasted maintainer attention on agent PRs that never merge.

## 1Problem

Coding agents made opening a pull request nearly free for the person who launches them. Reviewing one still costs a maintainer real time. On popular open-source projects this imbalance has produced a flood of agent PRs, and most of the cost lands on unpaid maintainers.

The striking finding from the research is *why* these PRs fail. It is rarely bad code. It is mostly social and workflow failure: nobody wanted the change, it duplicates another PR, or it sits unreviewed until it goes stale.

### 1.1 Evidence

| Source | Finding |
| --- | --- |
| Ehsani et al., *Where Do AI Coding Agents Fail?*, MSR 2026 \[1\] | Of 600 hand-coded rejected agent PRs, 38% were abandoned or never reviewed and 23% were duplicates. Agent-level failures such as misalignment were about 2%. |
| Peralta et al., MSR 2026 \[2\] | Of rejected agent PRs in repos with 500+ stars, only 35.7% were clear agent failures; 31.2% were workflow-driven (duplicates, superseded, inactivity) and 33.1% had no visible rationale. |
| arXiv 2605.02273 \[3\] | 61% of agent PRs in AIDev's popular-repo subset have no recorded review activity. |
| METR, March 2026 \[4\] | Roughly half of test-passing SWE-bench patches would not be merged by real maintainers; merge decisions ran about 24 percentage points below automated grading. Green CI is a weak proxy for merge-readiness. |
| *AI Slop is DDoSing Open Source*, arXiv 2607.04003 \[5\] | Across 294 repos and 2M+ PRs and issues, merge rates for one-time contributors fell 18% against the counterfactual in 2025. |
| GitHub, 2026 \[6\] | GitHub publicly acknowledged the volume of low-quality contributions and shipped controls to disable PRs or cap outside contributors' PRs. Maintainers of Godot and Coolify have described agent PRs as draining; Coolify reported 120+ slop PRs a month. |

### 1.2 Who has this problem

Our user is a maintainer of a mid-to-large open-source repository (roughly 100+ stars) that receives agent PRs from people outside the project. They are often unpaid and short on time. They want to spend less time on PRs that go nowhere, avoid being rude to contributors, and avoid adding another noisy tool.

Companies face a related problem as they assign more issues to agents internally, but the evidence and the public data are strongest in open source. We prove the approach there and treat enterprise use as a future direction, not a claim.

### 1.3 Non-goals

- Riffle does not review code. It predicts how a PR will end and explains why.
- Riffle never closes, blocks or merges anything. Maintainers decide.
- Riffle does not publicly judge contributors. Predictions are visible only to maintainers.
- Riffle does not detect whether code is AI-written. It works on PRs already identified as agent-authored.

## 2Competitive position

The "AI looks at your PR" layer is crowded. What is missing is a model that learns from real maintainer outcomes which agent PRs will go nowhere, and why.

| Tool | What it does | Learns from outcomes? | Built for OSS maintainers? |
| --- | --- | --- | --- |
| CodeRabbit Triage (Sept 2026) \[7\] | Ranks the PR queue P0–P3 with risk, reviewer suggestions and rule-based "close candidates" | No: base score is deterministic (review severity, linked-issue priority, blocked PRs) | No: Team plan and above |
| GitHub maintainer controls \[6\] | Disable PRs, restrict to collaborators, cap outside PRs | No: on/off throttles | Yes, but blunt |
| anti-slop GitHub Action \[8\] | Auto-closes PRs matching 34 heuristic rules | No: fixed rules | Yes |
| Copilot review, Claude Code Review, Cursor Bugbot | Review the diff and comment on code | No | Partly; they review, they don't triage |
| Greptile | Tunes which comments it makes from reactions | From feedback, for comment relevance | No |
| **Riffle** | Predicts outcome class with calibrated probability; private maintainer triage plus public rework brief | Yes, from real merge, close and abandonment outcomes | Yes, free and open source |

### 2.1 Why incumbents are unlikely to fill this soon

CodeRabbit sells to paying teams and deliberately keeps its score rule-based and explainable. GitHub, Cursor and Anthropic all sell coding agents, so scoring agent PRs as likely waste is awkward for them. A free, agent-neutral tool built for maintainers is not their business.

This is a narrow position, and we say so. Our defensible part is the outcome-labelling pipeline and the drift-aware retraining loop, which are the hardest pieces to copy well.

## 3Product

### 3.1 How it works

1. **Detect.** A PR opens. Riffle checks whether it is agent-authored (bot account, branch prefix, body watermark or commit trailer) and from an outside contributor.
2. **Extract.** It builds features known at submission time only: size and files, tests touched, linked issue, similarity to open PRs, author history, repo norms, agent identity.
3. **Predict.** A gradient-boosted model, calibrated per repository, returns probabilities for each outcome class.
4. **Explain.** For PRs likely to need rework or go nowhere, a small language model drafts a brief using the repository's own conventions.
5. **Act.** The prediction goes to the maintainer's private triage view. Only the constructive brief is posted publicly.
6. **Observe.** Real outcomes flow back as labels. Drift monitors watch the incoming PR stream; sustained drift or decay triggers retraining.

### 3.2 What is public and what is private

PR comments on open-source projects are visible to everyone, including the contributor. A bot publicly labelling a PR "likely waste (78%)" would offend people and get uninstalled. So the output is split:

| Surface | Audience | Shows |
| --- | --- | --- |
| Maintainer triage view (web) | Maintainers only | Queue sorted by likely outcome, probabilities, reasons, suspected duplicates |
| Public PR comment | Contributor and agent | A constructive brief only: repo expectations, missing pieces, related PRs. No score. |
| Model health (Grafana) | Project team | Calibration by agent, drift alarms, retraining history |

riffle-bot commented

Thanks for the contribution. A few things this repository usually expects before review:

- A linked issue. Changes here are normally discussed in an issue first.
- Tests under `tests/unit/`. Nine of the last ten merged PRs included them.
- PR #412 touches the same files and may overlap with this change.

Maintainers will take it from here.

## 4Model design

### 4.1 Outcome classes

| Class | Meaning | Three-class fallback |
| --- | --- | --- |
| Merged unchanged | Merged with no changes requested | Likely to land |
| Merged after rework | Merged after requested changes or follow-up commits | Needs rework |
| Closed: duplicate | Closed as a duplicate of, or superseded by, another PR | Likely to go nowhere |
| Closed: unwanted | Closed as out of scope or not wanted | Likely to go nowhere |
| Abandoned | Closed or stale with no maintainer response | Likely to go nowhere |
| Closed: CI or technical | Closed with failing checks and no other stated reason | Needs rework |

If any class has fewer than about 200 labelled examples after filtering, we collapse to the three-class version. About a third of rejections have no visible reason \[2\], so silent closures fold into "abandoned".

### 4.2 Features (submission time only)

| Group | Examples |
| --- | --- |
| Change shape | Lines added and removed, files touched, file types, tests touched, docs-only flag |
| Description | Linked issue present, description length, description–diff consistency |
| Duplication | Embedding similarity of title and diff to open and recently merged PRs; file overlap |
| Author | Prior PRs to this repo, prior merges, account age, from a fork |
| Repository norms | Historical outside-PR merge rate, test expectations, CONTRIBUTING/AGENTS.md present (computed only from earlier PRs) |
| Agent | Agent identity, detection method, time since agent's last known release |
| Optional second stage | CI result once checks complete (a "t+CI" model, reported separately) |

### 4.3 Global first, per-repo calibration

Most repositories see too few agent PRs to train their own model. One global model serves everyone, with a lightweight per-repository calibration layer whose weight grows as that repository's outcomes accumulate. If a layer makes a repository's predictions worse than the global model on its own holdout, its weight drops to zero automatically.

### 4.4 The rework brief

A small open-weight language model (MiniCPM5-2B in v0.3, to be re-confirmed) writes the brief from the diff, the predicted reasons and the repository's contributing files. It ships prompted. A LoRA fine-tune on real maintainer review comments from AIDev is an ablation, served only if it beats the prompted version. To keep a free tool affordable, the brief is generated only for PRs predicted to need rework or go nowhere.

### 4.5 Baselines and ablations

- Baselines: first-in-first-out order, diff size, and the anti-slop Action's rules.
- Global model alone against global plus per-repo calibration.
- With and without agent-identity features.
- Filtered (outside contributors only) against unfiltered training, to show the self-merge shortcut.
- Submission-time model against the t+CI model.
- Prompted brief against LoRA fine-tuned brief.

## 5Data

### 5.1 Sources

| Source | Scale | Role |
| --- | --- | --- |
| AIDev v4 \[9\] | 2,743,854 agent PRs, six agents, cutoff Nov 2025 | Volume and agent coverage (incl. Google Jules) |
| AIDev-pop (v3 tag) \[9\] | 33,596 PRs from 2,807 repos with 100+ stars; reviews, comments, timelines, diffs | Primary training base |
| MOSAIC-agentic-3m \[10\] | 111,969 agent and human PRs, June–Aug 2025 | Extra Claude Code and Jules depth; same-window human baseline |
| Hand-coded rejection sets \[1\]\[2\] | About 600 and 717 PRs | Validating our labeller; seeding the brief's taxonomy |
| DupPR \[11\] | 2,323 duplicate PR pairs | Pre-training the duplicate-similarity feature |
| AgenticFlict \[12\] | 107,026 simulated PRs, 27.7% with merge conflicts | Optional conflict feature |
| Self-collected 2026 stream | Continuous, from the GitHub API | Drift detection, retraining, live demo |

### 5.2 Outside-contributor filter

Most agent PRs are developers merging their own agent's work, which makes "merged or not" trivially predictable. Published models report F1 above 0.95 on unfiltered data \[13\]. We train and evaluate only on PRs from people without merge rights, using three GitHub fields fetched in one GraphQL call per PR:

- `authorAssociation` is `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, `FIRST_TIMER` or `NONE`.
- `isCrossRepository` is true, meaning the PR came from a fork.
- If merged, `mergedBy` is not the author.

We report a looser variant (association and merger only) alongside the strict one. Because `authorAssociation` may reflect the author's status today rather than at PR time, we cross-check it against history: had the author merged, closed or labelled other PRs in that repository before this PR opened? Bot-authored PRs (Copilot coding agent, Devin) are attributed to the human who launched them, who is usually a repo insider, so most drop out. That is expected.

### 5.3 Deriving labels

| Label | Rule |
| --- | --- |
| Merged unchanged | Merged; no changes requested; no commits after opening |
| Merged after rework | Merged after a "changes requested" review, or with commits or force-pushes after opening |
| Duplicate | "Marked as duplicate" timeline event, or maintainer comments matching "duplicate of", "superseded by", "already fixed in" |
| Unwanted | Maintainer closing comment classified as out of scope, seeded from the hand-coded taxonomies |
| Abandoned | Closed with no non-author human response, closed by a stale bot, or open over 60 days with no response |
| CI or technical | A required check failing at close, with no substantive maintainer objection |

Two team members hand-label 300–500 PRs using the Ehsani taxonomy. We report agreement (Cohen's κ) and per-class precision of the automatic labeller against it.

### 5.4 Leakage rules

- Features must be knowable at the moment the PR opens. Review counts, comments, later commits, close times and later labels are excluded.
- Repository-level statistics use only PRs closed before the target PR opened.
- All splits are time-ordered: train on PRs created before a cut-off, test on PRs created after it. No random splits.
- A cross-repository split (unseen repositories in test) is reported as well.

### 5.5 Collecting 2026 data

AIDev ends in November 2025, so drift monitoring needs fresh data. Since GitHub trimmed its public event payloads in October 2025 \[14\], GH Archive on BigQuery is useful only to discover candidate PRs. Details come from the GitHub GraphQL API. We fork PRarena's open-source search queries \[15\], add signals for more agents, and run collection as a scheduled Airflow DAG from week three, slicing searches by date to stay under the 1,000-result and rate limits. PRs are labelled once closed for 14 days or open for 60.

**Licence note.** AIDev is CC-BY-4.0, but each source repository keeps its own licence. We store diffs only as derived features, do not redistribute raw patches, and mask patches from repositories without an OSI licence.

## 6Drift monitoring and retraining

Coding agents change faster than almost any other data source. New agents appear, existing ones switch underlying models, and their PRs change shape. AIDev already shows merge rates ranging from 54% (Cursor) to 87% (Codex). This makes drift real rather than simulated, and it is the centre of the MLOps story.

| Monitor | Signal | Action |
| --- | --- | --- |
| Input drift | Population stability of key features, per agent; embedding drift of titles and diffs | Alert; flag affected agent |
| New agent or release | Unseen agent signature, or a jump in detection-method mix; known release dates | Alert; route new agent to global model with wide uncertainty |
| Calibration decay | Expected calibration error per agent on newly labelled PRs | Trigger retraining past threshold |
| Performance decay | Wasted-attention recall on a rolling window of labelled PRs | Trigger retraining; roll back if the candidate model is worse |

Retraining runs as an Airflow DAG: snapshot new labels with DVC, retrain, evaluate against the current champion on the latest time window, and promote in the MLflow registry only if it wins. Every model version stays rollback-ready.

Exact model versions are not recorded in any public dataset. We monitor drift by agent, detection method and time, and use model names only where agents expose them (for example, some Claude Code commit trailers).

## 7Architecture and stack

| Layer | Technology |
| --- | --- |
| Data pipeline | Airflow (collection, hydration, labelling, retraining), DVC (versioned snapshots), Great Expectations (schema, label distribution, null outcomes) |
| Training | LightGBM or XGBoost, scikit-learn calibration, PyTorch + PEFT for the LoRA ablation, MLflow tracking and registry |
| Serving | FastAPI GitHub App, Docker, Kubernetes on GKE; vLLM optional, since the 2B explainer can run on a small GPU or quantised on CPU |
| Monitoring | Prometheus, Grafana, custom drift detectors (Evidently or equivalent) |
| Frontend | Next.js maintainer triage view on GCP |
| CI/CD | GitHub Actions |

### 7.1 Latency and cost

The tree model scores a PR in milliseconds and updates the triage view immediately. The brief, when needed, follows as a comment within seconds. Running the language model only on flagged PRs keeps GPU cost bounded, which is the focus of the week-12 cost work.

## 8Success metrics

| Metric | Why it matters |
| --- | --- |
| **Wasted-attention recall at 20%** | Share of PRs that eventually went nowhere (abandoned, duplicate, unwanted) that Riffle flags within a 20% attention budget. The headline number. |
| Lift over baselines | Against FIFO, diff size and anti-slop rules on the same PRs |
| Per-class precision and macro-F1 | Whether failure reasons are right, not just the merge/no-merge split |
| Calibration (ECE) per agent | Probabilities maintainers can trust, and the drift trigger |
| Shortcut gap | F1 on unfiltered versus filtered data, showing why the outside-contributor filter matters |
| Brief usefulness | Team ratings on 100 held-out PRs; pilot maintainer feedback where available |
| Drift detection time | How quickly monitors flag a new agent in the replayed stream |
| Latency | Sub-second for the prediction, seconds for the brief |

### 8.1 Backtest

1. Replay filtered agent PRs from AIDev, MOSAIC and our 2026 stream in date order.
2. For each PR, use only models and statistics built from data before it opened.
3. Place it in a simulated maintainer queue and record where eventually-wasted PRs landed.
4. Compare against all three baselines; report by agent and by label type.
5. Replay the moment a new agent or model enters the stream, and show the drift alarm and retraining recovering calibration.

**Kill criterion.** If the model does not beat the anti-slop rules and the diff-size baseline under a time-ordered, cross-repository split, the ranking claim is unproven. Riffle then repositions as an agent release radar: a drift observatory showing how each agent's PRs change after a release.

## 9Week-two go/no-go

Nobody has published how many outside-contributor agent PRs AIDev contains. This decides which version of the project we build, so we measure it before committing.

Take a random 1,000 AIDev-pop PRs, fetch the three filter fields via GraphQL, and count how many pass the strict and loose rules, by agent. In the same pass, check that v4 includes timelines, sample model-version strings, and test whether a one-feature "author equals merger" model reproduces F1 above 0.9 on unfiltered data.

| Projected outside-contributor PRs | Plan |
| --- | --- |
| More than about 5,000 | Full plan, six classes |
| About 1,500–5,000 | Three classes; lean harder on the 2026 stream |
| Fewer than about 1,500 | Use the loose filter and state the limitation; make drift monitoring the lead result |

All three versions remain a complete MLOps project. They differ in the strength of the headline claim, not in whether there is something to show.

## 10Work breakdown

Twelve workstreams, each with one owner and one named reviewer. Pick two or three each. Only workstreams 1–4 need ML background. API contracts and data schemas are agreed in week one so no workstream blocks another.

| # | Workstream | Covers | ML |
| --- | --- | --- | --- |
| 1 | Labels and features | Labeller, hand-labelled set, submission-time features, leakage audit | Yes |
| 2 | Model training and tracking | Global model, calibration, ablations, MLflow registry | Yes |
| 3 | Rework brief | Prompting, repo-context retrieval, LoRA ablation, brief evaluation | Yes |
| 4 | Evaluation and backtest | Time-ordered replay, baselines, the Expo number | Yes |
| 5 | Data ingestion | AIDev and MOSAIC loading, GraphQL hydration, 2026 collector |  |
| 6 | Versioning and validation | DVC snapshots, Great Expectations checks |  |
| 7 | Pipeline orchestration | Airflow DAGs for collection, labelling and retraining |  |
| 8 | GitHub App | Webhooks, agent and outsider detection, comments, install flow |  |
| 9 | Inference service | Serving API, latency, brief generation path |  |
| 10 | Deployment and CI/CD | Docker, GKE, environments, GitHub Actions |  |
| 11 | Monitoring and drift | Prometheus, Grafana, drift detectors, retraining triggers |  |
| 12 | Maintainer triage view | Next.js queue, reasons, suspected duplicates |  |

Natural pairings: 5+6 (data in and trusted), 1+4 (labels and proof), 2+3 (the two models), 8+9 (the app and its service), 11+12 (measure it and show it). Pilot outreach to maintainers, brand and Expo material are shared.

### 10.1 Working agreement

- Every workstream has an owner and a named reviewer from a different area, assigned in week one.
- If a named reviewer hasn't responded within 24 hours, any approval merges and they catch up after.
- A 20-minute weekly demo: each owner shows their piece running, not a status update.
- Specs live in the repo. Label definitions, schemas and API contracts are reviewed like code.
- Nobody owns more than three workstreams. Unowned workstreams at the end of week one are cut or scoped down.

## 11Timeline

| Weeks | Phase | Deliverable |
| --- | --- | --- |
| 1–2 | Scoping | Proposal, schemas, roles; **go/no-go data check at end of week 2** |
| 3–5 | Data | Hydrated and filtered base set, labeller, hand-labelled set, DVC and validation; 2026 collector running from week 3 |
| 6–7 | Model | Global model and calibration, baselines, ablations, first backtest, prompted brief; all in MLflow |
| 8–9 | Deployment | App live on GKE and a test repo; outreach to 10–20 candidate maintainers |
| 10–11 | Monitoring | Drift detectors, alerts, automated retraining and rollback; LoRA ablation if time allows |
| 12 | Scale | Load testing, cost optimisation, latency |
| 13 | Hardening | Security, secrets, permissions, privacy review of public comments |
| 14 | Expo | Backtest result, drift replay demo, live install, recorded fallback |

## 12Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Too few outside-contributor PRs | Week-two gate with three pre-agreed plans; 2026 collection from week 3 |
| Noisy derived labels | Hand-labelled validation set, reported agreement, collapse to three classes if needed |
| Self-merge shortcut inflates results | Outside-contributor filter; report the shortcut gap explicitly |
| Author status reflects today, not PR time | Reconstruct maintainer status from repository history before each PR |
| Contributors offended by public judgement | Scores private to maintainers; public output is a constructive brief only; never auto-close |
| GitHub API limits slow collection | Start early; GraphQL batching; date-sliced searches; GH Archive for discovery |
| Agent detection misses some agents | Combine several signals; monitor detection method as a drift feature; state coverage limits |
| Modest accuracy | Promise ordering, not certainty; lead with lift over baselines; kill criterion decides the claim |
| GPU cost | Brief only for flagged PRs; quantised serving; LoRA off the critical path |
| Incumbents add learned triage | Stay free, agent-neutral and maintainer-first; invest in the labelling pipeline and drift loop |
| Patents on PR risk scoring | v0.3 was close to IBM US11645188B1; predicting acceptance outcomes moves further from it. Review before any release beyond the course. |

## 13Open questions to verify in week one

- Does AIDev v4 include timelines, issues and task types, or only the v3 tag?
- What is the MOSAIC dataset's licence?
- Is `authorAssociation` computed at query time? Check on known examples.
- Do Copilot coding agent and Devin PRs always come from repository insiders?
- Do current agents expose model names in PR bodies or commit trailers?
- Does GH Archive in 2026 still carry the head branch name in PR events?
- Is MiniCPM5-2B still the best small open explainer for our budget?

## References

1. Ehsani et al., *Where Do AI Coding Agents Fail? An Empirical Study of Failed Agentic Pull Requests in GitHub*, MSR 2026. [arXiv 2601.15195](https://arxiv.org/html/2601.15195)
2. Peralta et al., *Why Are Agentic PRs Merged or Rejected?*, MSR 2026. [arXiv 2605.22534](https://arxiv.org/pdf/2605.22534)
3. Study of review activity on agent PRs in AIDev-pop, arXiv 2605.02273.
4. METR, *Many SWE-bench-Passing PRs Would Not Be Merged into Main*, March 2026.
5. *"AI Slop is DDoSing Open Source": Understanding the Impact of AI-Generated Contributions on Open Source Sustainability*. [arXiv 2607.04003](https://www.alphaxiv.org/abs/2607.04003)
6. GitHub maintainer controls for pull requests, 2026; GitHub Community Discussion #185387. [coverage](https://www.coderabbit.ai/blog/github-gives-maintainers-a-throttle-for-the-ai-pull-request)
7. CodeRabbit, *CodeRabbit Triage: Know Which Pull Request to Review Next*, September 2026. [blog](https://www.coderabbit.ai/blog/coderabbit-triage) and [prioritisation docs](https://docs.coderabbit.ai/triage/prioritization)
8. peakoss, anti-slop GitHub Action. [github.com/peakoss/anti-slop](https://github.com/peakoss/anti-slop)
9. Li et al., *AIDev: Studying AI Coding Agents on GitHub*, MSR 2026. [arXiv 2602.09185](https://arxiv.org/html/2602.09185v1); dataset [hao-li/AIDev](https://huggingface.co/datasets/hao-li/AIDev)
10. Popescu et al., *Investigating Autonomous Agent Contributions in the Wild*, MSR 2026. [arXiv 2604.00917](https://arxiv.org/abs/2604.00917); dataset [MOSAIC-agentic-3m](https://huggingface.co/datasets/AISE-TUDelft/MOSAIC-agentic-3m)
11. Yu et al., DupPR duplicate pull request dataset, MSR 2018.
12. Ogenrwot and Businge, *AgenticFlict*. [arXiv 2604.03551](https://arxiv.org/html/2604.03551v2)
13. Pansuriya et al., *Predicting Acceptance and Review Effort in Human and Agent Pull Requests*. [arXiv 2607.12057](https://arxiv.org/abs/2607.12057)
14. CodePulse, *The GitHub Archive Data Cliff*. [codepulsehq.com](https://codepulsehq.com/research/github-archive-payload-cliff)
15. aavetis, PRarena. [github.com/aavetis/PRarena](https://github.com/aavetis/PRarena)

Riffle v0.4, draft for team review. Figures come from the cited sources; items in section 13 are unverified and should be checked before they appear on Expo slides.