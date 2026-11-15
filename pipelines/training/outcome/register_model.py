"""
register_model.py - push a trained outcome model to the model registry (MLflow), but only if it passes the gates
and is not worse than the model currently in service (guidelines 2.6, 4, 7.4, 7.6, 8.7).

    python register_model.py                       # after the notebook and bias_report.py
    python register_model.py --data riffle_mined_v0.1.zip --note "open-now fix"
    python register_model.py --rollback            # point 'champion' back at the previous version

What it does
  1. Runs model_gates.py on eval/ (validation and bias). Any failure: nothing is registered.
  2. Compares with the registered 'champion' version: refuses if by-repo skill drops by more than MAX_SKILL_DROP or
     calibration error rises by more than MAX_CALIB_RISE (model_gates' limits), unless --force.
  3. Logs one MLflow run in experiment EXPERIMENT: parameters from models/*/meta.json (data file, row counts,
     seeds, feature count, build time), the git commit, the data file's hash when --data is given; every metric of
     both splits from eval/*/summary.json; the bias numbers; and as artifacts the model files (models/seen_repos,
     models/new_repos), the eval reports and the bias reports.
  4. Registers the run's model folder as a new version of MODEL_NAME, tags it with its scores, moves the alias
     'champion' to it and the alias 'previous' to the version it replaces.
--rollback moves 'champion' back to 'previous' (and 'previous' to the one before it, if tagged).

Where: $MLFLOW_TRACKING_URI if set (a team server, or one backed by the team's GCS bucket); otherwise a local
SQLite store at mlruns/mlflow.db in this folder, with artifacts in mlruns/artifacts (gitignored). Needs mlflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import model_gates as G  # noqa: E402

EXPERIMENT = "riffle-outcome"
MODEL_NAME = "riffle-outcome-setup-g"
MODELS = ("seen_repos", "new_repos")


def _sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def setup(mlflow, tracking_uri):
    uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        store = HERE / "mlruns"
        (store / "artifacts").mkdir(parents=True, exist_ok=True)
        uri = "sqlite:///" + (store / "mlflow.db").as_posix()
        mlflow.set_tracking_uri(uri)
        if mlflow.get_experiment_by_name(EXPERIMENT) is None:
            mlflow.create_experiment(EXPERIMENT, artifact_location=(store / "artifacts").as_uri())
    else:
        mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(EXPERIMENT)
    return uri


def champion(client, name, alias="champion"):
    try:
        return client.get_model_version_by_alias(name, alias)
    except Exception:  # noqa: BLE001  no such model or alias yet
        return None


def metrics_of(cur):
    out = {}
    for split in G.SPLITS:
        s = cur[split] or {}
        for k, v in (s.get("metrics") or {}).items():
            if isinstance(v, (int, float)):
                out[f"{split}_{k}"] = float(v)
        p95 = (s.get("latency_ms") or {}).get("p95")
        if p95 is not None:
            out[f"{split}_latency_p95_ms"] = float(p95)
    new, ret = G.author_rows(cur["bias"])
    if new is not None and ret is not None:
        out["bias_ff_newcomer"] = float(new["false_flag_rate"])
        out["bias_ff_returning"] = float(ret["false_flag_rate"])
        out["bias_acc_newcomer"] = float(new["accuracy"])
        out["bias_acc_returning"] = float(ret["accuracy"])
    return out


def params_of(models_dir, data):
    p = {"git_commit": git_commit()}
    for m in MODELS:
        meta_path = models_dir / m / "meta.json"
        if not meta_path.is_file():
            raise SystemExit(f"no {meta_path}: run the notebook first")
        meta = json.load(open(meta_path, encoding="utf-8"))
        for k in ("trained_on", "train_prs", "validation_prs", "built", "validation"):
            if k in meta:
                p[f"{m}_{k}"] = str(meta[k])[:250]
        p[f"{m}_features"] = len(meta.get("features", []))
        p[f"{m}_seeds"] = len(meta.get("seeds", {}))
        p[f"{m}_members"] = ",".join(meta.get("members", []))
    if data:
        p["data_file"] = os.path.basename(data)
        p["data_sha256"] = _sha256(data)
    return p


def register(args):
    import mlflow
    from mlflow.tracking import MlflowClient

    rows = G.run_gates(str(args.eval))
    failed = [r for r in rows if r["result"] == "FAIL"]
    if failed:
        print(G.as_markdown(rows, False))
        raise SystemExit("gates failed: nothing registered")
    uri = setup(mlflow, args.tracking_uri)
    client = MlflowClient()
    cur = G.load(str(args.eval))
    mets = metrics_of(cur)
    champ = champion(client, MODEL_NAME)
    if champ is not None and not args.force:
        tags = champ.tags or {}
        old_skill = float(tags.get("by_repo_skill", "nan"))
        old_cal = float(tags.get("by_repo_calibration_error", "nan"))
        new_skill, new_cal = mets["by_repo_skill"], mets["by_repo_calibration_error"]
        if new_skill < old_skill - G.MAX_SKILL_DROP or new_cal > old_cal + G.MAX_CALIB_RISE:
            raise SystemExit(f"worse than champion version {champ.version} (by-repo skill {new_skill:.4f} vs "
                             f"{old_skill:.4f}, calibration error {new_cal:.4f} vs {old_cal:.4f}): not registered; "
                             "--force to register anyway")
    with mlflow.start_run(run_name=args.note or "setup G") as run:
        mlflow.log_params(params_of(args.models, args.data))
        mlflow.log_metrics(mets)
        mlflow.set_tags({"gates": "passed", "note": args.note or ""})
        for m in MODELS:
            mlflow.log_artifacts(str(args.models / m), artifact_path=f"model/{m}")
        for split in G.SPLITS:
            if (args.eval / split).is_dir():
                mlflow.log_artifacts(str(args.eval / split), artifact_path=f"eval/{split}")
        run_id, artifact_uri = run.info.run_id, run.info.artifact_uri
    try:
        client.create_registered_model(MODEL_NAME, description="Riffle at-open outcome model (setup G): "
                                       "seen_repos and new_repos bundles, 56-column scorer output")
    except Exception:  # noqa: BLE001  already exists
        pass
    keep = ("by_repo_skill", "by_repo_calibration_error", "by_repo_brier", "by_pr_skill", "bias_ff_newcomer")
    mv = client.create_model_version(MODEL_NAME, source=f"{artifact_uri}/model", run_id=run_id,
                                     tags={k: f"{mets[k]:.6f}" for k in keep if k in mets})
    if champ is not None:
        client.set_registered_model_alias(MODEL_NAME, "previous", champ.version)
        client.set_model_version_tag(MODEL_NAME, mv.version, "replaces", str(champ.version))
    client.set_registered_model_alias(MODEL_NAME, "champion", mv.version)
    print(f"registered {MODEL_NAME} version {mv.version} as champion (run {run_id}) in {uri}"
          + (f"; version {champ.version} is now 'previous'" if champ is not None else ""))
    return mv.version


def rollback(args):
    import mlflow
    from mlflow.tracking import MlflowClient

    uri = setup(mlflow, args.tracking_uri)
    client = MlflowClient()
    champ, prev = champion(client, MODEL_NAME), champion(client, MODEL_NAME, "previous")
    if champ is None or prev is None:
        raise SystemExit("nothing to roll back to: no 'previous' version")
    client.set_registered_model_alias(MODEL_NAME, "champion", prev.version)
    before = (prev.tags or {}).get("replaces")
    if before:
        client.set_registered_model_alias(MODEL_NAME, "previous", before)
    else:
        client.delete_registered_model_alias(MODEL_NAME, "previous")
    print(f"champion rolled back from version {champ.version} to {prev.version} in {uri}")
    return prev.version


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", type=Path, default=HERE / "models")
    ap.add_argument("--eval", type=Path, default=HERE / "eval")
    ap.add_argument("--data", default=os.environ.get("RIFFLE_DATA"), help="the data file, to record its hash")
    ap.add_argument("--tracking-uri", help="MLflow tracking URI (default $MLFLOW_TRACKING_URI, else a local store)")
    ap.add_argument("--note", help="a short name for this run")
    ap.add_argument("--force", action="store_true", help="register even if worse than the champion")
    ap.add_argument("--rollback", action="store_true", help="move 'champion' back to the previous version")
    args = ap.parse_args(argv)
    if args.data and not os.path.isfile(args.data):
        args.data = None
    rollback(args) if args.rollback else register(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
