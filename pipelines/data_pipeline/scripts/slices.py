"""Stage 5, slices: does a baseline model work equally well for every group of pull requests?

Run from pipelines/data_pipeline:  python -m scripts.slices

AIDev has no personal attributes, so "bias" here means unequal performance across groups of
pull requests: which AI agent opened it, the repository's language, its size (stars) and its
licence. A model that is right 80 % of the time overall but 60 % for one agent would quietly
rank that agent's pull requests worse.

Three models share the same data, so their numbers compare. All are trained with class-balanced
weights: three in four pull requests are merged, and without that the model learns to say "merged"
for nearly everything, which scores as well as it does while telling you nothing.
  baseline      logistic regression on the creation-time features
  reweighed     the same, with extra weights that remove the link between agent and outcome in the
                training data (pre-processing)
  thresholded   the baseline with a separate decision threshold per agent, chosen to equalise
                true- and false-positive rates (post-processing, Fairlearn ThresholdOptimizer)

Time order is kept: the baseline and reweighed models fit on the older part of the training
period, the thresholds are chosen on the newest part of it, and everything is scored on the
test period, which is later than all of it.

Outputs:
  reports/slices_metrics.json  every metric, per group, per model (a DVC metrics file)
  reports/bias_report.md       the same as readable tables
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from fairlearn.metrics import MetricFrame, count, false_positive_rate, selection_rate, true_positive_rate
from fairlearn.postprocessing import ThresholdOptimizer
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from scripts.logging_setup import ROOT, setup_logging

log = logging.getLogger("slices")

CATEGORICAL = ["agent", "language_group", "license_family"]
NUMERIC = ["log_stars", "log_forks", "log_pr_number", "log_title_chars", "log_body_chars", "title_words",
           "has_body", "hour_utc", "is_weekend", "is_forked"]
SLICE_ATTRIBUTES = ["agent", "language_group", "stars_bucket", "license_family"]
VARIANTS = ["baseline", "reweighed", "thresholded"]
GAP_METRICS = ["auc", "accuracy", "tpr", "fpr"]
DEFAULTS = {"min_group_size": 500, "validation_fraction": 0.15, "top_languages": 10, "mitigate_on": "agent"}


def prepare(features: pd.DataFrame, top_languages: int, train_languages: pd.Series | None = None) -> pd.DataFrame:
    """Add the model's columns. Languages outside the most common are grouped as 'other'."""
    out = features.copy()
    source = out["language"] if train_languages is None else train_languages
    keep = set(source.value_counts().head(top_languages).index) | {"unknown"}
    out["language_group"] = out["language"].where(out["language"].isin(keep), "other")
    out["log_pr_number"] = np.log1p(out["pr_number"])
    out["log_title_chars"] = np.log1p(out["title_chars"])
    out["log_body_chars"] = np.log1p(out["body_chars"])
    return out


def split_by_time(features: pd.DataFrame, validation_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(fit, validation, test). Validation is the newest part of the training period."""
    train = features[features["split"] == "train"].sort_values("created_at")
    test = features[features["split"] == "test"]
    cut = int(len(train) * (1 - validation_fraction))
    return train.iloc[:cut], train.iloc[cut:], test


def make_model() -> Pipeline:
    encode = ColumnTransformer(
        [("categories", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL), ("numbers", StandardScaler(), NUMERIC)]
    )
    return Pipeline([("encode", encode), ("model", LogisticRegression(max_iter=1000))])


def balanced_weights(label: pd.Series) -> np.ndarray:
    """Each class counts for half of the total weight, whatever its share of the rows."""
    share = label.map(label.value_counts() / len(label))
    return (0.5 / share).to_numpy()


def reweighing_weights(group: pd.Series, label: pd.Series) -> np.ndarray:
    """Weight each (group, label) cell by P(group) * P(label) / P(group, label).

    After weighting, the label is statistically independent of the group in the training data
    (Kamiran and Calders, 2012), so the model cannot learn 'this agent merges more often' from it.
    """
    frame = pd.DataFrame({"g": group.to_numpy(), "y": label.to_numpy()})
    n = len(frame)
    p_group = frame["g"].map(frame["g"].value_counts() / n)
    p_label = frame["y"].map(frame["y"].value_counts() / n)
    p_joint = frame.groupby(["g", "y"])["y"].transform("size") / n
    return (p_group * p_label / p_joint).to_numpy()


METRICS = {
    "count": count,
    "merge_rate": lambda y_true, y_pred: float(np.mean(y_true)),
    "accuracy": accuracy_score,
    "tpr": true_positive_rate,
    "fpr": false_positive_rate,
    "selection_rate": selection_rate,
}


def _clean(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), 4)


def group_auc(y_true: pd.Series, score: np.ndarray, sensitive: pd.Series) -> dict[str, float | None]:
    """ROC AUC per group: the chance a merged pull request is scored above an unmerged one.

    It does not depend on a threshold, so it shows whether the model ranks a group well even when
    its yes/no answers look the same. None when a group has only one outcome.
    """
    frame = pd.DataFrame({"y": y_true.to_numpy(), "s": score, "g": sensitive.to_numpy()})
    return {
        str(name): (_clean(roc_auc_score(part["y"], part["s"])) if part["y"].nunique() == 2 else None)
        for name, part in frame.groupby("g")
    }


def evaluate(
    y_true: pd.Series, y_pred: np.ndarray, sensitive: pd.Series, min_group_size: int, score: np.ndarray | None = None
) -> dict[str, Any]:
    """Per-group metrics, plus the largest gap between groups that are big enough to trust.

    `score` is the model's probability of 'merged'; without it (the thresholded model has none)
    the AUC columns are empty.
    """
    frame = MetricFrame(metrics=METRICS, y_true=y_true.to_numpy(), y_pred=y_pred, sensitive_features=sensitive.to_numpy())
    by_group = frame.by_group.copy()
    # Fairlearn reports 0.0 for a rate with nothing to divide (a group with no negatives has no false-positive
    # rate). Undefined is not measured-and-zero, so those become empty.
    outcomes = pd.Series(y_true.to_numpy()).groupby(sensitive.to_numpy())
    positives = outcomes.sum().reindex(by_group.index)
    negatives = (outcomes.count() - outcomes.sum()).reindex(by_group.index)
    by_group.loc[positives == 0, "tpr"] = np.nan
    by_group.loc[negatives == 0, "fpr"] = np.nan
    aucs =group_auc(y_true, score, sensitive) if score is not None else {}
    by_group["auc"] = pd.to_numeric(pd.Series([aucs.get(str(name)) for name in by_group.index], index=by_group.index), errors="coerce")
    groups = {
        str(name): {
            metric: (int(row[metric]) if metric == "count" else _clean(row[metric])) for metric in [*METRICS, "auc"]
        }
        for name, row in by_group.iterrows()
    }
    big = by_group[by_group["count"] >= min_group_size]
    gaps: dict[str, Any] = {}
    for metric in GAP_METRICS:
        values = big[metric].dropna()
        if len(values) < 2:
            gaps[metric] = {"gap": None, "best": None, "worst": None}
            continue
        # "worst" is the group furthest from the best one: lowest accuracy and TPR, highest FPR.
        worst = values.idxmax() if metric == "fpr" else values.idxmin()
        best = values.idxmin() if metric == "fpr" else values.idxmax()
        gaps[metric] = {"gap": _clean(values.max() - values.min()), "best": str(best), "worst": str(worst)}
    return {"groups": groups, "gaps": gaps, "groups_too_small": sorted(str(g) for g in by_group.index[by_group["count"] < min_group_size])}


def overall(y_true: pd.Series, y_pred: np.ndarray, score: np.ndarray | None = None) -> dict[str, float | None]:
    return {
        "auc": None if score is None else _clean(roc_auc_score(y_true, score)),
        "accuracy": _clean(accuracy_score(y_true, y_pred)),
        "tpr": _clean(true_positive_rate(y_true, y_pred)),
        "fpr": _clean(false_positive_rate(y_true, y_pred)),
        "selection_rate": _clean(selection_rate(y_true, y_pred)),
    }


def run_analysis(features: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    """Fit the three models and score them on the test period. Returns the full result."""
    s = {**DEFAULTS, **settings}
    fit_raw, val_raw, test_raw = split_by_time(features, s["validation_fraction"])
    if min(len(fit_raw), len(val_raw), len(test_raw)) == 0:
        raise ValueError("need rows in the fit, validation and test periods")
    languages = fit_raw["language"]
    fit, val, test = (prepare(part, s["top_languages"], languages) for part in (fit_raw, val_raw, test_raw))
    columns = CATEGORICAL + NUMERIC
    mitigate = s["mitigate_on"]
    if mitigate not in SLICE_ATTRIBUTES:
        raise ValueError(f"mitigate_on must be one of {SLICE_ATTRIBUTES}")

    balanced = balanced_weights(fit["merged"])
    baseline = make_model().fit(fit[columns], fit["merged"], model__sample_weight=balanced)
    reweighed = make_model().fit(
        fit[columns], fit["merged"], model__sample_weight=balanced * reweighing_weights(fit[mitigate], fit["merged"])
    )
    thresholds = ThresholdOptimizer(
        estimator=baseline, constraints="equalized_odds", objective="balanced_accuracy_score", prefit=True,
        predict_method="predict_proba",
    ).fit(val[columns], val["merged"], sensitive_features=val[mitigate])

    scores = {"baseline": baseline.predict_proba(test[columns])[:, 1], "reweighed": reweighed.predict_proba(test[columns])[:, 1]}
    predictions = {
        "baseline": baseline.predict(test[columns]),
        "reweighed": reweighed.predict(test[columns]),
        "thresholded": thresholds.predict(test[columns], sensitive_features=test[mitigate], random_state=0),
    }
    y = test["merged"]
    result: dict[str, Any] = {
        "settings": {**s, "features": columns},
        "rows": {"fit": len(fit), "validation": len(val), "test": len(test)},
        "test_merge_rate": _clean(y.mean()),
        "always_merged_accuracy": _clean(y.mean()),  # what a model that always says "merged" scores
        "overall": {name: overall(y, predicted, scores.get(name)) for name, predicted in predictions.items()},
        "slices": {},
    }
    for attribute in SLICE_ATTRIBUTES:
        result["slices"][attribute] = {
            name: evaluate(y, predicted, test[attribute], s["min_group_size"], scores.get(name))
            for name, predicted in predictions.items()
        }
    return result


def render_report(result: dict[str, Any]) -> str:
    """The result as Markdown tables. Numbers only; the interpretation lives in docs/bias_analysis.md."""
    s, rows = result["settings"], result["rows"]
    pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    out = ["# Bias report (generated)", "",
           "Written by `python -m scripts.slices`; do not edit by hand. The reading of these numbers is in `docs/bias_analysis.md`.", "",
           f"Rows: fit {rows['fit']:,}, validation {rows['validation']:,}, test {rows['test']:,}. "
           f"Test merge rate {pct(result['test_merge_rate'])}, so a model that always says merged scores {pct(result['always_merged_accuracy'])}. "
           f"Mitigation is on `{s['mitigate_on']}`; groups under {s['min_group_size']} test rows are shown but left out of the gaps.", "",
           "## Overall", "", "| Model | AUC | Accuracy | True-positive rate | False-positive rate | Predicted merged |", "| --- | --- | --- | --- | --- | --- |"]
    for name in VARIANTS:
        o = result["overall"][name]
        auc = "n/a" if o["auc"] is None else f"{o['auc']:.3f}"
        out.append(f"| {name} | {auc} | {pct(o['accuracy'])} | {pct(o['tpr'])} | {pct(o['fpr'])} | {pct(o['selection_rate'])} |")

    out += ["", "## Largest gap between groups, by model", "",
            "Gap = best group minus worst group. Smaller is fairer. The group named is the worst one.", "",
            "| Attribute | Metric | baseline | reweighed | thresholded |", "| --- | --- | --- | --- | --- |"]
    for attribute in SLICE_ATTRIBUTES:
        for metric in GAP_METRICS:
            cells = []
            for name in VARIANTS:
                g = result["slices"][attribute][name]["gaps"][metric]
                scale, unit, places = (1, "", 3) if metric == "auc" else (100, " pts", 1)
                cells.append("n/a" if g["gap"] is None else f"{g['gap'] * scale:.{places}f}{unit} ({g['worst']})")
            out.append(f"| {attribute} | {metric} | " + " | ".join(cells) + " |")

    for attribute in SLICE_ATTRIBUTES:
        out += ["", f"## By {attribute}", ""]
        base, fixed, thresh = (result["slices"][attribute][v] for v in VARIANTS)
        out += ["| Group | Test rows | Merge rate | AUC (baseline / reweighed) | Accuracy (baseline / reweighed / thresholded) | TPR (baseline / thresholded) | FPR (baseline / thresholded) |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        for group, m in sorted(base["groups"].items(), key=lambda item: -item[1]["count"]):
            small = " *(too small)*" if group in base["groups_too_small"] else ""
            auc = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
            out.append(
                f"| {group}{small} | {m['count']:,} | {pct(m['merge_rate'])} | {auc(m['auc'])} / {auc(fixed['groups'][group]['auc'])} | "
                f"{pct(m['accuracy'])} / {pct(fixed['groups'][group]['accuracy'])} / "
                f"{pct(thresh['groups'][group]['accuracy'])} | {pct(m['tpr'])} / {pct(thresh['groups'][group]['tpr'])} | "
                f"{pct(m['fpr'])} / {pct(thresh['groups'][group]['fpr'])} |"
            )
    return "\n".join(out) + "\n"


def load_settings(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = dict(document.get("slices") or {})
    unknown = set(settings) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown slices setting: {', '.join(sorted(unknown))}")
    if not 0 < settings.get("validation_fraction", DEFAULTS["validation_fraction"]) < 0.5:
        raise ValueError("slices.validation_fraction must be between 0 and 0.5")
    if int(settings.get("min_group_size", DEFAULTS["min_group_size"])) < 1:
        raise ValueError("slices.min_group_size must be at least 1")
    return settings


def run(processed: Path, reports: Path, settings: dict[str, Any]) -> dict[str, Any]:
    path = processed / "features.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist; run the features stage first")
    result = run_analysis(pd.read_parquet(path), settings)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "slices_metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (reports / "bias_report.md").write_text(render_report(result), encoding="utf-8", newline="\n")
    for name in VARIANTS:
        o = result["overall"][name]
        log.info("%-12s accuracy %s  tpr %s  fpr %s", name, o["accuracy"], o["tpr"], o["fpr"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.slices", description=__doc__.splitlines()[0])
    parser.add_argument("--params", type=Path, default=ROOT / "params.yaml")
    parser.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)

    log_path = setup_logging("slices")
    log.info("logging to %s", log_path)
    try:
        run(args.processed, args.reports, load_settings(args.params))
        return 0
    except (FileNotFoundError, ValueError, KeyError, OSError, yaml.YAMLError) as error:
        log.error("slices failed: %s", error)
        return 1
    except Exception:
        log.exception("slices failed with an unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
