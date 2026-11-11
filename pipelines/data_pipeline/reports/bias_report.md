# Bias report (generated)

Written by `python -m scripts.slices`; do not edit by hand. The reading of these numbers is in `docs/bias_analysis.md`.

Rows: fit 226,034, validation 39,889, test 66,481. Test merge rate 77.9%, so a model that always says merged scores 77.9%. Mitigation is on `agent`; groups under 500 test rows are shown but left out of the gaps.

## Overall

| Model | AUC | Accuracy | True-positive rate | False-positive rate | Predicted merged |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.672 | 67.4% | 71.5% | 47.2% | 66.1% |
| reweighed | 0.669 | 61.4% | 61.1% | 37.4% | 55.8% |
| thresholded | n/a | 59.7% | 63.7% | 54.5% | 61.7% |

## Largest gap between groups, by model

Gap = best group minus worst group. Smaller is fairer. The group named is the worst one.

| Attribute | Metric | baseline | reweighed | thresholded |
| --- | --- | --- | --- | --- |
| agent | auc | 0.150 (Copilot) | 0.152 (Copilot) | n/a |
| agent | accuracy | 43.5 pts (Cursor) | 18.6 pts (OpenAI_Codex) | 4.0 pts (OpenAI_Codex) |
| agent | tpr | 76.3 pts (Cursor) | 35.6 pts (OpenAI_Codex) | 4.2 pts (OpenAI_Codex) |
| agent | fpr | 53.3 pts (Claude_Code) | 42.1 pts (Copilot) | 4.5 pts (OpenAI_Codex) |
| language_group | auc | 0.222 (HTML) | 0.225 (HTML) | n/a |
| language_group | accuracy | 28.4 pts (C++) | 36.9 pts (C++) | 15.8 pts (C++) |
| language_group | tpr | 46.2 pts (C++) | 64.4 pts (C++) | 26.1 pts (C++) |
| language_group | fpr | 57.5 pts (HTML) | 72.0 pts (HTML) | 31.4 pts (HTML) |
| stars_bucket | auc | 0.044 (100-199) | 0.099 (100-199) | n/a |
| stars_bucket | accuracy | 31.1 pts (5k+) | 45.0 pts (5k+) | 18.0 pts (5k+) |
| stars_bucket | tpr | 56.7 pts (5k+) | 87.3 pts (5k+) | 30.9 pts (5k+) |
| stars_bucket | fpr | 64.1 pts (100-199) | 89.8 pts (100-199) | 36.9 pts (100-199) |
| license_family | auc | 0.052 (copyleft) | 0.084 (copyleft) | n/a |
| license_family | accuracy | 9.1 pts (copyleft) | 9.5 pts (copyleft) | 0.8 pts (copyleft) |
| license_family | tpr | 12.5 pts (copyleft) | 13.0 pts (copyleft) | 0.0 pts (unknown) |
| license_family | fpr | 6.8 pts (unknown) | 6.5 pts (unknown) | 0.8 pts (permissive) |

## By agent

| Group | Test rows | Merge rate | AUC (baseline / reweighed) | Accuracy (baseline / reweighed / thresholded) | TPR (baseline / thresholded) | FPR (baseline / thresholded) |
| --- | --- | --- | --- | --- | --- | --- |
| Claude_Code | 45,079 | 79.9% | 0.687 / 0.689 | 76.0% / 65.3% / 60.1% | 85.4% / 63.5% | 61.6% / 53.6% |
| Copilot | 10,745 | 74.5% | 0.571 / 0.569 | 36.7% / 58.4% / 59.9% | 19.6% / 65.1% | 13.1% / 55.3% |
| OpenAI_Codex | 8,371 | 73.2% | 0.721 / 0.721 | 70.0% / 46.7% / 56.9% | 74.3% / 62.3% | 42.0% / 57.7% |
| Cursor | 1,745 | 71.7% | 0.576 / 0.577 | 32.5% / 53.9% / 60.9% | 9.1% / 66.5% | 8.3% / 53.2% |
| Devin *(too small)* | 464 | 71.8% | 0.484 / 0.491 | 31.7% / 39.2% / 60.1% | 5.4% / 65.8% | 1.5% / 54.2% |
| Google_Jules *(too small)* | 77 | 87.0% | 0.797 / 0.797 | 15.6% / 84.4% / 62.3% | 3.0% / 62.7% | 0.0% / 40.0% |

## By language_group

| Group | Test rows | Merge rate | AUC (baseline / reweighed) | Accuracy (baseline / reweighed / thresholded) | TPR (baseline / thresholded) | FPR (baseline / thresholded) |
| --- | --- | --- | --- | --- | --- | --- |
| TypeScript | 22,395 | 77.4% | 0.724 / 0.716 | 68.7% / 60.4% / 59.3% | 70.3% / 61.9% | 36.8% / 49.5% |
| Python | 11,549 | 75.6% | 0.664 / 0.665 | 69.5% / 63.1% / 59.1% | 75.8% / 63.5% | 50.1% / 54.5% |
| other | 8,896 | 80.4% | 0.644 / 0.621 | 66.9% / 53.0% / 59.6% | 70.7% / 62.5% | 48.8% / 52.2% |
| Go | 7,044 | 78.2% | 0.643 / 0.685 | 64.8% / 67.0% / 61.3% | 71.8% / 68.7% | 60.3% / 65.1% |
| Rust | 5,747 | 79.3% | 0.649 / 0.646 | 66.9% / 63.6% / 58.5% | 73.1% / 63.4% | 56.8% / 60.2% |
| C# | 2,545 | 84.4% | 0.653 / 0.591 | 64.6% / 71.0% / 66.3% | 66.2% / 73.0% | 44.0% / 69.8% |
| JavaScript | 2,494 | 76.3% | 0.623 / 0.623 | 61.9% / 58.9% / 57.7% | 64.5% / 61.3% | 46.5% / 53.9% |
| C++ | 2,161 | 71.4% | 0.603 / 0.596 | 54.5% / 47.3% / 50.5% | 50.3% / 46.9% | 35.2% / 40.4% |
| Java | 1,757 | 80.0% | 0.602 / 0.570 | 66.1% / 62.3% / 63.4% | 72.2% / 70.9% | 58.2% / 66.5% |
| HTML | 1,445 | 84.8% | 0.502 / 0.491 | 82.9% / 84.2% / 65.4% | 96.5% / 72.1% | 92.7% / 71.8% |
| unknown *(too small)* | 446 | 67.7% | 0.587 / 0.724 | 67.3% / 70.9% / 66.1% | 91.4% / 82.1% | 83.3% / 67.4% |
| Scheme *(too small)* | 2 | 100.0% | n/a / n/a | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% | n/a / n/a |

## By stars_bucket

| Group | Test rows | Merge rate | AUC (baseline / reweighed) | Accuracy (baseline / reweighed / thresholded) | TPR (baseline / thresholded) | FPR (baseline / thresholded) |
| --- | --- | --- | --- | --- | --- | --- |
| 200-999 | 20,275 | 82.4% | 0.626 / 0.596 | 75.8% / 75.4% / 64.6% | 86.5% / 72.3% | 74.6% / 71.5% |
| 5k+ | 19,438 | 65.9% | 0.618 / 0.649 | 49.9% / 37.5% / 49.2% | 34.2% / 42.8% | 19.8% / 38.5% |
| 1k-4.9k | 15,326 | 80.7% | 0.642 / 0.639 | 68.2% / 57.5% / 60.9% | 74.3% / 65.8% | 57.1% / 59.4% |
| 100-199 | 11,442 | 86.8% | 0.598 / 0.549 | 81.0% / 82.5% / 67.2% | 90.9% / 73.7% | 83.8% / 75.4% |

## By license_family

| Group | Test rows | Merge rate | AUC (baseline / reweighed) | Accuracy (baseline / reweighed / thresholded) | TPR (baseline / thresholded) | FPR (baseline / thresholded) |
| --- | --- | --- | --- | --- | --- | --- |
| permissive | 44,644 | 77.3% | 0.679 / 0.681 | 67.1% / 61.7% / 59.5% | 70.9% / 63.6% | 45.7% / 54.6% |
| unknown | 15,183 | 80.2% | 0.671 / 0.657 | 70.6% / 63.6% / 60.2% | 76.2% / 63.6% | 52.5% / 53.8% |
| copyleft | 6,467 | 76.4% | 0.627 / 0.598 | 61.4% / 54.1% / 59.4% | 63.7% / 63.6% | 46.1% / 54.5% |
| other *(too small)* | 187 | 80.2% | 0.433 / 0.537 | 69.0% / 74.9% / 73.3% | 82.0% / 87.3% | 83.8% / 83.8% |
