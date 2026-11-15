# setup_G vs nishit_v3 vs preeyal_v2 vs rudra_v3

- 64,788 PRs in every file are compared; left out: 2,762 of setup_G's PRs are missing from another file.
- Truth columns (labels, reasons, dates, groups) come from nishit_v3 where its file has them, so every model is scored against the same answers.
- Truth columns that differ between the files: observed_through in setup_G's file (0.0% agree); ai_agent_authored in preeyal_v2's file (0.0% agree); close_reason in setup_G's file (96.6% agree); close_reason in rudra_v3's file (96.6% agree); nowhere_reason in setup_G's file (96.6% agree); nowhere_reason in rudra_v3's file (96.6% agree). Each model was trained on its own file's version of these.
- Base rate: taken from the compared PRs themselves, the same for all (no --train).

## Paired differences (same PRs)

| pair | metric | first | second | first − second | 95% range | better |
|---|---|---|---|---|---|---|
| setup_G vs nishit_v3 | brier | 0.5130 | 0.5206 | -0.0076 | -0.0144 to -0.0014 | setup_G |
| setup_G vs nishit_v3 | log_loss | 0.8601 | 0.8709 | -0.0108 | -0.0223 to -0.0009 | setup_G |
| setup_G vs nishit_v3 | auroc_clean | 0.7998 | 0.7933 | 0.0065 | -0.0006 to 0.0152 | setup_G (within noise) |
| setup_G vs nishit_v3 | auroc_rework | 0.7477 | 0.7447 | 0.0029 | -0.0023 to 0.0081 | setup_G (within noise) |
| setup_G vs nishit_v3 | auroc_nowhere | 0.8179 | 0.8045 | 0.0134 | 0.0019 to 0.0252 | setup_G |
| setup_G vs preeyal_v2 | brier | 0.5130 | 0.5455 | -0.0325 | -0.0422 to -0.0233 | setup_G |
| setup_G vs preeyal_v2 | log_loss | 0.8601 | 0.9100 | -0.0499 | -0.0646 to -0.0361 | setup_G |
| setup_G vs preeyal_v2 | auroc_clean | 0.7998 | 0.7821 | 0.0177 | 0.0063 to 0.0257 | setup_G |
| setup_G vs preeyal_v2 | auroc_rework | 0.7477 | 0.7340 | 0.0137 | 0.0014 to 0.0249 | setup_G |
| setup_G vs preeyal_v2 | auroc_nowhere | 0.8179 | 0.7876 | 0.0303 | 0.0109 to 0.0461 | setup_G |
| setup_G vs rudra_v3 | brier | 0.5130 | 0.5659 | -0.0529 | -0.0704 to -0.0383 | setup_G |
| setup_G vs rudra_v3 | log_loss | 0.8601 | 0.9447 | -0.0846 | -0.1117 to -0.0610 | setup_G |
| setup_G vs rudra_v3 | auroc_clean | 0.7998 | 0.7501 | 0.0497 | 0.0313 to 0.0702 | setup_G |
| setup_G vs rudra_v3 | auroc_rework | 0.7477 | 0.7049 | 0.0427 | 0.0303 to 0.0550 | setup_G |
| setup_G vs rudra_v3 | auroc_nowhere | 0.8179 | 0.7352 | 0.0827 | 0.0507 to 0.1135 | setup_G |
| nishit_v3 vs preeyal_v2 | brier | 0.5206 | 0.5455 | -0.0249 | -0.0327 to -0.0176 | nishit_v3 |
| nishit_v3 vs preeyal_v2 | log_loss | 0.8709 | 0.9100 | -0.0391 | -0.0516 to -0.0280 | nishit_v3 |
| nishit_v3 vs preeyal_v2 | auroc_clean | 0.7933 | 0.7821 | 0.0112 | 0.0027 to 0.0182 | nishit_v3 |
| nishit_v3 vs preeyal_v2 | auroc_rework | 0.7447 | 0.7340 | 0.0107 | 0.0015 to 0.0195 | nishit_v3 |
| nishit_v3 vs preeyal_v2 | auroc_nowhere | 0.8045 | 0.7876 | 0.0169 | 0.0048 to 0.0260 | nishit_v3 |
| nishit_v3 vs rudra_v3 | brier | 0.5206 | 0.5659 | -0.0453 | -0.0562 to -0.0354 | nishit_v3 |
| nishit_v3 vs rudra_v3 | log_loss | 0.8709 | 0.9447 | -0.0738 | -0.0908 to -0.0578 | nishit_v3 |
| nishit_v3 vs rudra_v3 | auroc_clean | 0.7933 | 0.7501 | 0.0432 | 0.0294 to 0.0557 | nishit_v3 |
| nishit_v3 vs rudra_v3 | auroc_rework | 0.7447 | 0.7049 | 0.0398 | 0.0312 to 0.0489 | nishit_v3 |
| nishit_v3 vs rudra_v3 | auroc_nowhere | 0.8045 | 0.7352 | 0.0693 | 0.0466 to 0.0891 | nishit_v3 |
| preeyal_v2 vs rudra_v3 | brier | 0.5455 | 0.5659 | -0.0204 | -0.0337 to -0.0086 | preeyal_v2 |
| preeyal_v2 vs rudra_v3 | log_loss | 0.9100 | 0.9447 | -0.0347 | -0.0550 to -0.0162 | preeyal_v2 |
| preeyal_v2 vs rudra_v3 | auroc_clean | 0.7821 | 0.7501 | 0.0320 | 0.0170 to 0.0493 | preeyal_v2 |
| preeyal_v2 vs rudra_v3 | auroc_rework | 0.7340 | 0.7049 | 0.0291 | 0.0196 to 0.0407 | preeyal_v2 |
| preeyal_v2 vs rudra_v3 | auroc_nowhere | 0.7876 | 0.7352 | 0.0524 | 0.0355 to 0.0708 | preeyal_v2 |

## eval_script.py, side by side

| | setup_G | nishit_v3 | preeyal_v2 | rudra_v3 | best |
|---|---|---|---|---|---|
| Brier, repo-averaged | 0.5130 | 0.5206 | 0.5455 | 0.5659 | setup_G |
| Skill vs base rate | 0.2059 | 0.1941 | 0.1556 | 0.1240 | setup_G |
| Log loss | 0.8520 | 0.8662 | 0.9170 | 0.9479 | setup_G |
| RPS | 0.1635 | 0.1670 | 0.1759 | 0.1839 | setup_G |
| Calibration error | 0.0145 | 0.0123 | 0.0667 | 0.0189 | nishit_v3 |
| Calibration slope, clean | 0.9895 | 1.0767 | 0.8690 | 0.8984 | setup_G |
| Calibration slope, rework | 1.0021 | 1.0440 | 0.8665 | 0.8721 | setup_G |
| Calibration slope, nowhere | 1.0560 | 1.0770 | 0.8957 | 0.8373 | setup_G |
| AUROC clean | 0.7998 | 0.7933 | 0.7821 | 0.7501 | setup_G |
| AUROC rework | 0.7477 | 0.7447 | 0.7340 | 0.7049 | setup_G |
| AUROC goes nowhere | 0.8179 | 0.8045 | 0.7876 | 0.7352 | setup_G |
| AUROC average | 0.7885 | 0.7809 | 0.7679 | 0.7301 | setup_G |
| AUPRC goes nowhere | 0.7094 | 0.6767 | 0.6490 | 0.5790 | setup_G |
| Macro F1 | 0.6005 | 0.5917 | 0.5535 | 0.5386 | setup_G |
| Balanced accuracy | 0.6016 | 0.5922 | 0.5646 | 0.5395 | setup_G |
| Accuracy | 0.6038 | 0.5940 | 0.5651 | 0.5437 | setup_G |
| Review next: top-5 hit rate | 0.8615 | 0.8554 | 0.8620 | 0.8331 | preeyal_v2 |
| Review next: top-10 hit rate | 0.7944 | 0.7902 | 0.7975 | 0.7742 | preeyal_v2 |
| Review next: top-10 quality | 0.6751 | 0.6700 | 0.6754 | 0.6379 | preeyal_v2 |
| Effort-aware score | 0.5229 | 0.5202 | 0.5445 | 0.4831 | preeyal_v2 |
| Nowhere list: top-5, new PRs | 0.4872 | 0.4904 | 0.4877 | 0.4743 | nishit_v3 |
| Nowhere list: top-10 quality, new PRs | 0.8350 | 0.8307 | 0.8155 | 0.7990 | setup_G |
| Reason F1, averaged | 0.1716 | 0.4253 | 0.3854 | 0.3372 | nishit_v3 |
| Reason F1, unknown counts as wrong | 0.1711 | 0.3918 | 0.3439 | 0.3301 | nishit_v3 |
| Outcome and reason Brier | 0.6386 | 0.6322 | 0.6523 | 0.6792 | nishit_v3 |
| Model says unknown | 0.0098 | 0.2333 | 0.2726 | 0.0549 |  |
| Really unknown | 0.2440 | 0.2440 | 0.2440 | 0.2440 |  |
| label_merged: skill | 0.2729 | 0.2446 | 0.1773 | 0.1512 | setup_G |
| label_merged: AUROC | 0.8179 | 0.8045 | 0.7876 | 0.7352 | setup_G |
| label_merged: calibration error | 0.0138 | 0.0096 | 0.0987 | 0.0260 | nishit_v3 |
| label_merged: Brier | 0.1512 | 0.1571 | 0.1711 | 0.1765 | setup_G |
| label_rework: skill | 0.2375 | 0.2478 | 0.2400 | 0.1961 | nishit_v3 |
| label_rework: AUROC | 0.7799 | 0.7850 | 0.7796 | 0.7552 | nishit_v3 |
| label_rework: calibration error | 0.0176 | 0.0196 | 0.0192 | 0.0143 | rudra_v3 |
| label_rework: Brier | 0.1900 | 0.1874 | 0.1894 | 0.2003 | nishit_v3 |
| label_abandoned: skill | -0.0043 | -0.0387 | -0.0143 | 0.0081 | rudra_v3 |
| label_abandoned: AUROC | 0.6248 | 0.6426 | 0.6375 | 0.6229 | nishit_v3 |
| label_abandoned: calibration error | 0.0653 | 0.1359 | 0.1138 | 0.0518 | rudra_v3 |
| label_abandoned: Brier | 0.2343 | 0.2424 | 0.2367 | 0.2314 | rudra_v3 |
| label_duplicate: skill | 0.0310 | 0.0634 | 0.0571 | 0.0150 | nishit_v3 |
| label_duplicate: AUROC | 0.6354 | 0.6939 | 0.6865 | 0.6475 | nishit_v3 |
| label_duplicate: calibration error | 0.0222 | 0.0178 | 0.0223 | 0.0420 | nishit_v3 |
| label_duplicate: Brier | 0.1606 | 0.1552 | 0.1562 | 0.1632 | nishit_v3 |
| label_unwanted: skill | 0.0170 | 0.0554 | 0.0493 | 0.0024 | nishit_v3 |
| label_unwanted: AUROC | 0.6143 | 0.6673 | 0.6563 | 0.6221 | nishit_v3 |
| label_unwanted: calibration error | 0.0386 | 0.0115 | 0.0254 | 0.0507 | nishit_v3 |
| label_unwanted: Brier | 0.1699 | 0.1633 | 0.1643 | 0.1724 | nishit_v3 |
| label_ci_technical: skill | -0.5666 | -0.5001 | -0.5143 | -0.5838 | nishit_v3 |
| label_ci_technical: AUROC | 0.4096 | 0.4621 | 0.4450 | 0.4203 | nishit_v3 |
| label_ci_technical: calibration error | 0.3372 | 0.2719 | 0.2939 | 0.3461 | nishit_v3 |
| label_ci_technical: Brier | 0.3902 | 0.3737 | 0.3772 | 0.3945 | nishit_v3 |
| label_bad: skill | -0.0001 | — | -0.0385 | -0.0008 | setup_G |
| label_bad: AUROC | 0.5247 | — | 0.5472 | 0.6191 | rudra_v3 |
| label_bad: calibration error | 0.0046 | — | 0.0135 | 0.0038 | rudra_v3 |
| label_bad: Brier | 0.0056 | — | 0.0058 | 0.0056 | setup_G |
| label_revert: skill | -0.0006 | — | -0.0009 | -0.0013 | setup_G |
| label_revert: AUROC | 0.4402 | — | 0.3909 | 0.5844 | rudra_v3 |
| label_revert: calibration error | 0.0048 | — | 0.0047 | 0.0039 | rudra_v3 |
| label_revert: Brier | 0.0045 | — | 0.0045 | 0.0045 | setup_G |
| label_hotfix: skill | -0.0013 | — | — | — |  |
| label_hotfix: AUROC | 0.2652 | — | — | — |  |
| label_hotfix: calibration error | 0.0013 | — | — | — |  |
| label_hotfix: Brier | 0.0011 | — | — | — |  |
| label_szz_bug: skill | 0.1431 | — | 0.1501 | 0.1425 | preeyal_v2 |
| label_szz_bug: AUROC | 0.7677 | — | 0.7636 | 0.7520 | setup_G |
| label_szz_bug: calibration error | 0.0166 | — | 0.0145 | 0.0183 | preeyal_v2 |
| label_szz_bug: Brier | 0.1316 | — | 0.1306 | 0.1317 | preeyal_v2 |

Best Brier per repo: setup_G 13, nishit_v3 8, preeyal_v2 2, rudra_v3 0.
