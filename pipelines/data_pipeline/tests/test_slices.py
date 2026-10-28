import json

import numpy as np
import pandas as pd
import pytest
import yaml
from frames import synthetic_features

from scripts import slices


# weights


def test_balanced_weights_give_each_class_half_the_total():
    y = pd.Series([1] * 90 + [0] * 10)
    w = slices.balanced_weights(y)
    assert w[y == 1].sum() == pytest.approx(50) and w[y == 0].sum() == pytest.approx(50)


def test_reweighing_removes_the_link_between_group_and_label():
    group = pd.Series(["a"] * 60 + ["b"] * 40)
    label = pd.Series([1] * 54 + [0] * 6 + [1] * 20 + [0] * 20)  # a merges 90 %, b merges 50 %
    w = slices.reweighing_weights(group, label)
    frame = pd.DataFrame({"g": group, "y": label, "w": w})
    weighted_rate = frame.groupby("g").apply(lambda part: np.average(part["y"], weights=part["w"]), include_groups=False)
    assert weighted_rate["a"] == pytest.approx(weighted_rate["b"], abs=1e-9)
    assert w.sum() == pytest.approx(len(frame))  # the total weight is unchanged


# per-group metrics


def test_group_auc_is_none_for_a_group_with_one_outcome():
    y = pd.Series([1, 0, 1, 0, 1, 1])
    score = np.array([0.9, 0.1, 0.8, 0.2, 0.7, 0.6])
    group = pd.Series(["a", "a", "a", "a", "b", "b"])  # b only ever merged
    result = slices.group_auc(y, score, group)
    assert result == {"a": 1.0, "b": None}


def test_evaluate_leaves_small_groups_out_of_the_gaps_but_still_lists_them():
    y = pd.Series([1, 0] * 600 + [1, 0] * 5)
    pred = np.array([1, 0] * 600 + [0, 1] * 5)  # the tiny group is wrong every time
    group = pd.Series(["big"] * 1200 + ["tiny"] * 10)
    result = slices.evaluate(y, pred, group, min_group_size=500)
    assert result["groups_too_small"] == ["tiny"]
    assert result["groups"]["tiny"]["accuracy"] == 0.0
    assert result["gaps"]["accuracy"]["gap"] is None  # only one group is big enough to compare


def test_evaluate_names_the_worst_group_and_the_gap():
    rng = np.random.default_rng(0)
    y = pd.Series(rng.integers(0, 2, 2000))
    group = pd.Series(["good"] * 1000 + ["bad"] * 1000)
    pred = y.to_numpy().copy()
    pred[1000:] = 1 - pred[1000:]  # always wrong for "bad"
    result = slices.evaluate(y, pred, group, min_group_size=500)
    assert result["gaps"]["accuracy"] == {"gap": 1.0, "best": "good", "worst": "bad"}


def test_a_group_with_no_negatives_has_an_empty_false_positive_rate_not_a_crash():
    y = pd.Series([1] * 600 + [1, 0] * 300)
    pred = np.ones(len(y), dtype=int)
    group = pd.Series(["all_merged"] * 600 + ["mixed"] * 600)
    result = slices.evaluate(y, pred, group, min_group_size=500)
    assert result["groups"]["all_merged"]["fpr"] is None
    assert result["gaps"]["fpr"]["gap"] is None


# preparing and splitting


def test_languages_outside_the_most_common_are_grouped_as_other_using_the_fit_period_only():
    f = synthetic_features(400)
    f["language"] = ["Python"] * 200 + ["Go"] * 150 + ["Zig"] * 50
    fit_languages = pd.Series(["Python"] * 100 + ["Go"] * 50)
    out = slices.prepare(f, top_languages=1, train_languages=fit_languages)
    assert set(out["language_group"]) == {"Python", "other"}  # Go is not in the fit period's top 1


def test_split_keeps_time_order_fit_then_validation_then_test():
    f = synthetic_features(1000)
    fit, val, test = slices.split_by_time(f, 0.2)
    assert fit["created_at"].max() <= val["created_at"].min() <= val["created_at"].max() <= test["created_at"].min()
    assert len(fit) + len(val) == (f["split"] == "train").sum() and len(val) == 160


# the whole analysis


@pytest.fixture(scope="module")
def result():
    return slices.run_analysis(synthetic_features(6000), {"min_group_size": 100})


def test_every_model_and_attribute_is_reported(result):
    assert set(result["overall"]) == set(slices.VARIANTS)
    for attribute in slices.SLICE_ATTRIBUTES:
        assert set(result["slices"][attribute]) == set(slices.VARIANTS)
    assert result["rows"]["test"] > 0 and result["rows"]["fit"] > result["rows"]["validation"]


def test_models_make_both_kinds_of_prediction_rather_than_always_merged(result):
    for name in slices.VARIANTS:
        assert 0.05 < result["overall"][name]["selection_rate"] < 0.95


def test_the_baseline_ranks_better_than_chance_and_the_thresholded_model_has_no_auc(result):
    assert result["overall"]["baseline"]["auc"] > 0.6
    assert result["overall"]["thresholded"]["auc"] is None


def test_threshold_adjustment_narrows_the_agent_gap_that_the_baseline_has(result):
    base = result["slices"]["agent"]["baseline"]["gaps"]["tpr"]["gap"]
    fixed = result["slices"]["agent"]["thresholded"]["gaps"]["tpr"]["gap"]
    assert fixed < base


def test_the_result_is_json_serialisable_and_the_report_renders(result):
    json.dumps(result)
    text = slices.render_report(result)
    assert text.startswith("# Bias report") and "## By agent" in text and "## By stars_bucket" in text
    assert "| agent | auc |" in text and "n/a" in text  # the thresholded model has no AUC


def test_running_twice_gives_the_same_numbers():
    f = synthetic_features(3000)
    assert slices.run_analysis(f, {"min_group_size": 100}) == slices.run_analysis(f, {"min_group_size": 100})


def test_a_split_with_no_test_rows_is_refused():
    f = synthetic_features(500)
    f["split"] = "train"
    with pytest.raises(ValueError, match="need rows"):
        slices.run_analysis(f, {})


def test_mitigating_on_an_unknown_attribute_is_refused():
    with pytest.raises(ValueError, match="mitigate_on"):
        slices.run_analysis(synthetic_features(2000), {"mitigate_on": "shoe_size"})


# settings, files and main


def test_settings_are_checked(tmp_path):
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump({"slices": {"min_group_size": 50}}), encoding="utf-8")
    assert slices.load_settings(path) == {"min_group_size": 50}
    for bad in ({"validation_fraction": 0.9}, {"validation_fraction": 0}, {"min_group_size": 0}, {"surprise": 1}):
        path.write_text(yaml.safe_dump({"slices": bad}), encoding="utf-8")
        with pytest.raises(ValueError):
            slices.load_settings(path)


def test_main_writes_the_metrics_and_the_report_and_needs_features_first(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    params = tmp_path / "params.yaml"
    params.write_text(yaml.safe_dump({"slices": {"min_group_size": 100}}), encoding="utf-8")
    args = ["--params", str(params), "--processed", str(tmp_path / "p"), "--reports", str(tmp_path / "r")]
    assert slices.main(args) == 1  # the features stage has not run
    (tmp_path / "p").mkdir()
    synthetic_features(3000).to_parquet(tmp_path / "p" / "features.parquet")
    assert slices.main(args) == 0
    assert json.loads((tmp_path / "r" / "slices_metrics.json").read_text(encoding="utf-8"))["rows"]["test"] > 0
    assert (tmp_path / "r" / "bias_report.md").read_text(encoding="utf-8").startswith("# Bias report")
