A model can only learn what it is shown. The label decides what "a bad change"
means, and maturity decides when an outcome is old enough to count.

> **Proposed.** The label target below is proposed and awaiting sign-off from
> the training owner.

## The target

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

## Maturity

An outcome is only usable once the window for it to happen has closed.

| Label source | Matures after |
| --- | --- |
| `revert`, `ci_fail`, `hotfix` | 30 days |
| SZZ | 90 days |

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
`null` means not declared or not enough history; `0` means measured and found
none. The two are never coerced into each other.
