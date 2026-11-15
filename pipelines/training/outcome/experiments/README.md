# Experiments behind passes 1 to 5

The code, results and logs that `DECISIONS.md` and `FEATURES_LOG.md` cite. The model itself is in the parent
folder's `pipeline.ipynb`, which re-implements the frozen features and needs none of this.

Run them from this folder. `fe_run.py` imports `pipelines/training/frames.py` (two folders up). They read the
unzipped miner data from `RIFFLE_DATA_DIR` (default `~/riffle-data/v0.1/`, which holds `dataset_merged*.parquet`
from `riffle_mined_v0.1.zip`) and cache the prepared table next to it. `rerun_clean.sh` reruns passes 1 to 4 in
order (about 1.5 hours on 16 cores and a GPU).

Requirements beyond numpy and pandas: LightGBM, XGBoost, CatBoost, scikit-learn, SciPy, and torch for the MLP and
the sequence model. `python -m pytest test_fe_features.py` needs only numpy and pandas; the two sequence-model tests
skip without torch.

| File | Pass | What it does |
|---|---|---|
| `fe_features.py` | 1 onwards | Feature engineering: point-in-time history, consolidated columns, context |
| `test_fe_features.py` | | Cut test, stale-open test, counting and timestamp checks (`python -m pytest test_fe_features.py`) |
| `fe_run.py` | 1, 2 | Harness: rows, splits, weights, LightGBM, calibration, metrics; `--pass 1` / `--pass 2` |
| `fe_fingerprint.py` | 2 | Can a model tell a recovered at-open diff from an exact one? |
| `fe_calib.py` | 2 | Weighted calibration error from saved predictions |
| `fe_models.py` | 3 | LightGBM settings, XGBoost, CatBoost, logistic regression, MLP on one feature set |
| `fe_seq.py` | 3 | GRU over the author's and the repo's previous PRs |
| `fe_blend.py` | 3 | Cross-fitted linear and geometric blends |
| `fe_setups.py` | 4 | Setups A to H over 3 seeds |
| `fe_profile.py` | 4 | Training and serving cost of each blend member |
| `rerun_clean.sh` | | Passes 1 to 4 again on leak-free features |
| `fe_results.jsonl`, `fe_models_results.jsonl`, `fe_setups.json`, `fe_profile.json`, `fe_fingerprint.json` | | Results of passes 1 and 2, 3, 4 (quality and cost) and the fingerprint test, where the scripts write them |
| `logs/` | | Logs of the clean rerun |
| `notebook_build/` | 5 | `pipeline_src.py` (the notebook as a script), `build_notebook.py` (writes the outcome folder's `pipeline.ipynb`), `frozen_features.json` (the 161 inputs) |
| `eda.ipynb` | before 1 | The exploratory analysis the leaks and features came from |

The saved test predictions (`fe_preds/`), profiled models (`fe_profile/`) and feature importances are not in git;
the scripts regenerate them.
