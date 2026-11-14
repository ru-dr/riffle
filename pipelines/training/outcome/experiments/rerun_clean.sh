#!/bin/bash
# Rerun passes 1-4 on leak-free features (stale-open rows fixed). Logs to logs/rerun_*.log; backs up after each pass.
set -euo pipefail
cd "$(dirname "$0")"
PY=~/riffle/.venv/bin/python
BK=~/riffle-data/backups
snap() { d=$BK/outcome_$(date +%m%d_%H%M)_$1; mkdir -p $d; cp *.py *.md *.sh *.jsonl *.json $d/ 2>/dev/null || true; cp -r fe_preds $d/; echo "backup $d"; }
$PY fe_run.py --pass 1 2>&1 | tee logs/rerun_p1.log
$PY fe_run.py --pass 2 2>&1 | tee logs/rerun_p2.log
snap after_pass2
$PY fe_models.py 2>&1 | tee logs/rerun_p3_one_seed.log
$PY fe_seq.py 2>&1 | tee logs/rerun_p3_seq_one_seed.log
$PY fe_models.py --only lgb_default lgb_strong_reg xgb logreg mlp --seeds 0 1 2 2>&1 | tee logs/rerun_p3_seeds.log
$PY fe_seq.py --seeds 0 1 2 2>&1 | tee logs/rerun_p3_seq_seeds.log
snap after_pass3
$PY fe_setups.py 2>&1 | tee logs/rerun_p4.log
snap after_pass4
echo DONE
