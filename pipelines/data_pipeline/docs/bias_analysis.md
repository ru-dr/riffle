# Bias analysis: does a model work equally well for every group of pull requests?

The tables behind every number here are in `reports/bias_report.md` (readable) and
`reports/slices_metrics.json` (a DVC metrics file). Both are written by the `slices` stage and
checked into git, so this page can be compared with them line by line.

## What "bias" means for this data

AIDev has no personal attributes: no names, ages, genders or locations about people. So there is
no demographic fairness question to ask, and this analysis does not pretend otherwise. The question
it can ask is whether a model's quality depends on **which group a pull request falls in**:

| Slice | Why it could matter |
| --- | --- |
| `agent` (Claude Code, Copilot, OpenAI Codex, Cursor, Devin, Google Jules) | a tool that ranks pull requests should not quietly rank one agent's work worse |
| `language_group` (top 10 languages, rest as `other`) | a model trained mostly on TypeScript and Python may fail on C++ or HTML |
| `stars_bucket` (100-199, 200-999, 1k-4.9k, 5k+) | small and large projects review differently |
| `license_family` (permissive, copyleft, other, unknown) | licence is a proxy for how a project is run |

## Process

1. **Data:** the 332,404 closed pull requests from the `features` stage. The label is `merged`
   (74 % of them). Only things known when the pull request was opened are features, so nothing
   leaks the answer.
2. **Split by time, not at random.** The model fits on the oldest 226,034 rows, thresholds are
   chosen on the next 39,889, and everything is scored on the newest 66,481, which is later than all
   of it. A random split would hide the drift described below.
3. **Baseline:** logistic regression, trained with class-balanced weights. Without them the model
   says "merged" for 97 % of pull requests and scores 77.9 %, exactly what always answering "merged"
   scores, while telling you nothing.
4. **Measure per group** with Fairlearn's `MetricFrame`: AUC, accuracy, true-positive rate (merged
   pull requests the model got right), false-positive rate (unmerged ones it called merged).
   The headline number for each slice is the **gap**: best group minus worst group. Groups with
   fewer than 500 test rows are shown but left out of the gaps, because their rates are noise.
5. **Mitigate and measure again,** on `agent` (see below).

## What we found

Overall the baseline is a weak model: AUC 0.672, accuracy 67.4 % against 77.9 % for always saying
"merged". Nearly all its signal comes from repository features (AUC 0.66); the agent alone is at
chance (0.505), for the reason below.

**1. Its yes/no answers differ enormously by agent.** The baseline finds 85.4 % of Claude Code's
merged pull requests but only 19.6 % of Copilot's and 9.1 % of Cursor's: a 76-point gap in
true-positive rate. Copilot and Cursor pull requests are mostly predicted "not merged".

**2. The cause is drift, not the agents.** Merge rates changed between the training and test
periods. Copilot went from 64.5 % to 74.5 %, Cursor from 62.6 % to 71.7 %, Devin from 61.4 % to
71.8 %; OpenAI Codex went the other way, from 81.1 % to 73.2 %. The model learned the old rates
and applies them to a period in which they no longer hold. That is why a time split matters.

**3. Ranking quality differs too, and nothing here fixes it.** AUC is 0.72 for OpenAI Codex, 0.69
for Claude Code and 0.57 for Copilot and Cursor (Devin, 0.48 on 464 rows, and Google Jules, 0.80
on 77 rows, are too small to trust). Reweighing leaves the 0.15 AUC gap where it was (0.152), and
thresholding cannot change it: a threshold only moves the cut-off within one agent, never the order
of that agent's pull requests. The fixes change where the line is drawn, not how much the model
knows about each group.

**4. Language and size gaps are large as well:** 46 points of true-positive rate across languages
(C++ at 50 %, HTML at 97 %) and 57 across star buckets (5k+ at 34 %, 100-199 at 91 %). Part of
this is real: 5k+ projects merged 65.9 % of the test pull requests and 100-199 projects 86.8 %, so
a model that follows the base rate will say "merged" less often for big projects. A gap in
true-positive rate is a reason to look, not proof of unfairness.

## Mitigation

Two standard methods, both on `agent`, compared with the baseline on the same test period:

| | Baseline | Reweighed | Thresholded |
| --- | --- | --- | --- |
| What it does | nothing | trains with weights that remove the link between agent and outcome (pre-processing) | one decision threshold per agent, chosen to equalise error rates (post-processing, `ThresholdOptimizer`) |
| Overall accuracy | 67.4 % | 61.4 % | 59.7 % |
| Agent gap, true-positive rate | 76.3 pts | 35.6 pts | **4.2 pts** |
| Agent gap, false-positive rate | 53.3 pts | 42.1 pts | **4.5 pts** |
| Agent gap, accuracy | 43.5 pts | 18.6 pts | **4.0 pts** |
| Language gap, true-positive rate | 46.2 pts | 64.4 pts (worse) | 26.1 pts |
| Stars gap, true-positive rate | 56.7 pts | 87.3 pts (worse) | 30.9 pts |
| Licence gap, true-positive rate | 12.5 pts | 13.0 pts | 0.0 pts |

**Thresholding works, at a price.** It shrinks the agent gaps to about 4 points and, as a side
effect, narrows the language and size gaps too, but overall accuracy falls by 7.7 points, and the
false-positive rate rises from 47.2 % to 54.5 %. That price is the honest cost of treating agents
alike when the data has drifted.

**Reweighing is not worth it here.** It halves the agent gap, costs 6 points of accuracy, and makes
the language and size gaps worse. It also leaves ranking quality untouched.

## Limits, and what we would do next

- **One split, one run, no confidence intervals.** The agent gaps are far larger than sampling
  noise. Small differences (a point or two, or any figure for Devin and Google Jules) are not.
- **Thresholds were chosen on a window in which 74.7 % of pull requests were merged, and the test
  period had 77.9 %.** They still held up (gaps near 4 points), which suggests they carry across
  the drift, but that is one test period, not a guarantee.
- **The better fix is upstream.** The real problem is a model trained on a period that no longer
  matches. Retraining on recent data, or weighting recent pull requests more, addresses the cause;
  per-agent thresholds only patch the symptom, and need re-fitting every time rates move.
- **Which gap to close depends on use.** If the model only ranks pull requests (as Riffle does),
  AUC per group is the metric to protect, and no method here improves it. If it ever makes yes/no
  calls, equalised error rates matter, and the threshold method is the one to use. Riffle must not
  hide or auto-reject a pull request in either case.
- **Not tried:** fairness-constrained training (Fairlearn `ExponentiatedGradient`), more features,
  a stronger model class. A weak model leaves little for any mitigation to work with.

## Reproduce or change it

```bash
cd pipelines/data_pipeline
dvc repro slices          # reruns only what changed
```

The settings are in `params.yaml` under `slices`: `min_group_size` (500), `validation_fraction`
(0.15), `top_languages` (10) and `mitigate_on` (`agent`, or any slice above). Changing one reruns
the stage and rewrites both reports.
