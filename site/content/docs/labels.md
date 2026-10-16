A model can only learn what it is shown. The labels decide what "a bad change"
and "a change that won't land" mean, and maturity decides when an outcome is
old enough to count.

> **Accepted.** Both targets are recorded and accepted.

Riffle learns two things from the same mined history, and never mixes them
into one label:

| Target | Question | Trained on |
| --- | --- | --- |
| `label_bad` | Will this merged change break? | Merged pull requests |
| `label_merged` | Will this pull request merge at all? | Every closed pull request, plus ones abandoned while open |

A combined risk, when wanted, is `P(merge) × P(bad | merged)`. Keeping them
apart matters for triage: "won't merge" means don't spend a senior reviewer
on it; "will merge but break" means do.

## Breakage: `label_bad`

```text
label_bad = revert OR ci_fail OR hotfix
```

| Signal | Fires when | Window |
| --- | --- | --- |
| `revert` | A later commit reverts the pull request | 30 days |
| `ci_fail` | Required checks on the merge commit fail; auxiliary and flaky checks excluded | at merge |
| `hotfix` | A hotfix-labelled pull request touches the same lines | 7 days |

SZZ bug-inducing links and follow-up fixes are mined as well, but are not part
of the target; both are reported as ablations.

## Merge outcome: `label_merged`

`label_merged` is whether `merged_at` is set. The state field is not used,
because "closed" covers merged pull requests too. Around it, the miner records
why a pull request did not land, and whether one that did needed rework:

| Label | Rows | Fires when |
| --- | --- | --- |
| `label_rework` | merged | A human reviewer requested changes, or commits or force-pushes came after opening (merges from the base branch don't count) |
| `label_duplicate` | not merged | Marked as a duplicate, or closed as superseded or already fixed |
| `label_unwanted` | not merged | Maintainers declined the change itself: out of scope, against policy, rejected outright |
| `label_abandoned` | not merged | No human response before closing, closed by a stale bot, or still open after 60 days with no response |
| `label_ci_technical` | not merged | Required checks were failing at close, with no maintainer objection |

`close_reason` names one of these per pull request. With an LLM available, a
classifier reads the title, comments, reviews, labels and who closed it, which
catches cases rules miss, such as a maintainer silently closing a pull request
and retitling it "AI spam". Without one, phrase and label rules decide. The
classifier only labels training data; it is never on the scoring path.

## Maturity

An outcome is only usable once the window for it to happen has closed.

| Label source | Matures after |
| --- | --- |
| `revert`, `ci_fail`, `hotfix` | 30 days |
| SZZ | 90 days |
| `label_merged` and close reasons | At close |
| Abandoned while open | 60 days without a response |

Pull requests younger than their window are dropped from training, never
counted as good.

## What the signal looks like

| Measure | Rate | Source |
| --- | --- | --- |
| Revert rate in open source | 1–5% of commits | [Shimagaki et al., ICSME 2016](https://rebels.cs.uwaterloo.ca/confpaper/2016/10/04/why-are-commits-being-reverted.html) |
| SZZ bug-inducing rate | about 26% | [ApacheJIT, MSR 2022](https://arxiv.org/abs/2203.00101) |

## Point in time

Features and labels are point-in-time: a pull request only sees data from
before it opened, and only labels that were already mature at that moment.

That includes the pull request itself. It is scored when it opens, so its
features come from its first push, not from the version that eventually
merged: commits pushed under review would otherwise teach the model about
rework it cannot see yet. A force-push after opening is undone from GitHub's
record of the replaced commit. The title and milestone are taken as they were
at opening too, since maintainers retitle rejected pull requests and
milestone accepted ones.
`null` means not declared or not enough history; `0` means measured and found
none. The two are never coerced into each other.
