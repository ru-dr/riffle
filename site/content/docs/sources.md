Every figure, dataset and design reference these docs rely on, with what it
is used for. Each link was checked to resolve.

## Industry research

The case for Riffle: review, not authoring, is now the bottleneck.

| Source | Used for |
| --- | --- |
| [Faros AI, *The AI Engineering Report 2026: The Acceleration Whiplash*](https://www.faros.ai/research/ai-acceleration-whiplash) | Median review time up 441.5%; bugs per developer up 54%; 31% more PRs merged with no review |
| [LinearB, *2026 Software Engineering Benchmarks*](https://linearb.io/resources/software-engineering-benchmarks-report) | 8.1M PRs: AI PRs wait 4.6× longer for review; 32.7% vs 84.4% acceptance |
| [GitHub, *Agent pull requests are everywhere*](https://github.blog/ai-and-ml/generative-ai/agent-pull-requests-are-everywhere-heres-how-to-review-them/) | More than 1 in 5 code reviews now involve an agent |

## Research: defect prediction

The literature the model, labels and planning targets rest on.

| Source | Used for |
| --- | --- |
| [Kamei et al., *A large-scale empirical study of just-in-time quality assurance*, TSE 2013](https://posl.ait.kyushu-u.ac.jp/~kamei/publications/Kamei_TSE2013.pdf) | Within-project ROC-AUC and effort-aware recall targets |
| [Kamei et al., *Studying just-in-time defect prediction using cross-project models*, EMSE 2016](https://link.springer.com/article/10.1007/s10664-015-9400-x) | The 0.65+ ROC-AUC target on repositories the model never trained on |
| [Yan et al., *Characterizing and identifying reverted commits*, EMSE 2019](https://ink.library.smu.edu.sg/cgi/viewcontent.cgi?article=5360&context=sis_research) | Lift, recall at the top 20% of effort, and the `revert` label |
| [Shimagaki et al., *Why are commits being reverted?*, ICSME 2016](https://rebels.cs.uwaterloo.ca/confpaper/2016/10/04/why-are-commits-being-reverted.html) | Revert rate in open source: 1–5% of commits |
| [Zeng et al., *Deep just-in-time defect prediction: how far are we?*, ISSTA 2021](https://conf.researchr.org/details/issta-2021/issta-2021-technical-papers/33/Deep-Just-in-Time-Defect-Prediction-How-Far-Are-We) | Why full diffs are not kept: diff-reading deep models did not reliably beat metric-based ones |

## Datasets

| Source | Used for |
| --- | --- |
| [Keshavarz and Nagappan, *ApacheJIT*, MSR 2022](https://arxiv.org/abs/2203.00101) | 106,674 commits, 28,239 labelled bug-inducing; the ~26% SZZ rate |
| [Zhang, Rastogi and Yu, *On the Shoulders of Giants*, MSR 2020](https://yuyue.github.io/res/paper/newPR_MSR2020.pdf) | Engineered features for pull request outcome prediction |
| [Gousios and Zaidman, *A dataset for pull request research*, MSR 2014](https://azaidman.github.io/publications/gousiosMSR2014a.pdf) | Pull request dataset methodology |
| [AIDev](https://arxiv.org/abs/2601.15195) | Agent-authored pull requests with merge outcomes, CI results and reviewer interactions |

## Platform documentation

Constraints the data pipeline is designed around.

| Source | Used for |
| --- | --- |
| [GitHub Docs, *Rate limits for the REST API*](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) | 5,000 requests per hour, secondary limits, and the extraction timeline |
| [GitHub Changelog, *Actions retention will cover checks, workflow runs and statuses*](https://github.blog/changelog/2026-08-27-actions-retention-will-cover-checks-workflow-runs-and-statuses/) | From 1 October 2026, check runs and statuses on public repositories are kept for at most 90 days |
| [Grunert, *My exciting journey into Kubernetes' history*, Kubernetes Blog 2020](https://v1-33.docs.kubernetes.io/blog/2020/05/my-exciting-journey-into-kubernetes-history) | A size anchor for structured pull request history |
