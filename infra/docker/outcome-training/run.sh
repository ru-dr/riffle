#!/usr/bin/env bash
# The outcome model's training run inside the container: notebook, bias, mitigation, SHAP, gates; results to $OUT.
set -uo pipefail
OUT="${OUT:-/out}"
: "${RIFFLE_DATA:?mount the data release and set RIFFLE_DATA, e.g. -e RIFFLE_DATA=/data/dataset_merged.parquet}"
[ -f "$RIFFLE_DATA" ] || { echo "RIFFLE_DATA=$RIFFLE_DATA does not exist in the container (is /data mounted?)"; exit 2; }

status=0
copy_out() {
    mkdir -p "$OUT"
    cp pipeline.ipynb "$OUT"/ 2>/dev/null || true
    for d in eval models; do [ -d "$d" ] && cp -r "$d" "$OUT"/; done
    echo "results copied to $OUT"
}
trap copy_out EXIT

step() {
    echo "==== $1"
    shift
    "$@" || { echo "==== failed: $*"; status=1; }
}

step "notebook (about 20 minutes; RIFFLE_FAST=1 for a quick check)" \
    python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=-1 pipeline.ipynb
if [ "$status" -eq 0 ]; then
    step "bias report" python bias_report.py --data "$RIFFLE_DATA"
    step "bias mitigation experiment" python bias_mitigation.py --data "$RIFFLE_DATA"
    step "SHAP report" python shap_report.py --data "$RIFFLE_DATA"
    step "validation and bias gates" python model_gates.py --json eval/gates.json
fi
exit "$status"
