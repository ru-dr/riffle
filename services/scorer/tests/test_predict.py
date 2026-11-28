"""A live PR (the PrFeatures fixture, a real mined row) through the model."""
import os

import numpy as np
import pandas as pd
import pytest

import outcome_features as OF
from scorer import predict


@pytest.fixture(scope="module")
def variant(tiny_model_dir):
    return predict.Variant(tiny_model_dir)


def test_a_live_row_gives_valid_probabilities(variant, message):
    p = predict.predict(variant, pd.DataFrame(), message["row"])
    assert abs(p.p_lands_clean + p.p_lands_after_rework + p.p_goes_nowhere - 1) < 1e-9
    assert abs(sum(p.reasons.values()) - 1) < 1e-9
    assert all(0 <= v <= 1 for v in [*p.reasons.values(), *p.labels.values()])
    assert list(p.features) == list(OF.FEATURES)        # the 161 inputs, in the training order


def test_scoring_one_row_is_scoring_it_in_a_batch(variant, message, history):
    """Scoring the PR alone with its history gives what training's batch path (whole table at once) gives."""
    one = predict.predict(variant, history, message["row"])
    table = pd.concat([history, pd.DataFrame([message["row"]])], ignore_index=True)
    X = variant.bundle["encoder"].transform(OF.build_features(table))
    batch = predict.prediction_columns(*predict.predict_bundle(variant.bundle, X)).iloc[-1]
    assert np.isclose(one.p_lands_clean, batch["p_clean"], atol=1e-12)
    assert np.isclose(one.p_goes_nowhere, batch["p_nowhere"], atol=1e-12)


def test_history_counts_only_prs_closed_before_this_one_opened(variant, message, history):
    p = predict.predict(variant, history, message["row"])
    assert p.features["ah_n"] == 3 and p.features["ah_merged"] == 2
    later = history.copy()
    later["closed_at"] = (pd.Timestamp(message["row"]["created_at"]) + pd.Timedelta(days=1)).isoformat()
    assert predict.predict(variant, later, message["row"]).features["ah_n"] == 0


def test_an_earlier_row_of_the_same_pr_is_not_its_own_history(variant, message, history):
    same = history.copy()
    same["pr_number"] = message["row"]["pr_number"]
    assert predict.predict(variant, same, message["row"]).features["ah_n"] == 0


@pytest.mark.parametrize("bot, maintainer, outside", [
    (False, False, True), (True, False, False), (False, True, False), (None, None, True), ("true", None, False)])
def test_outside_contributor_is_build_features_rule(message, bot, maintainer, outside):
    row = {**message["row"], "author_is_bot": bot, "author_confirmed_maintainer": maintainer}
    assert predict.is_outside_contributor(row) is outside
    assert bool(OF.build_features(pd.DataFrame([row]))["outside"].iloc[0]) is outside


def test_a_model_reading_unknown_features_is_refused(tiny_model_dir, tmp_path):
    import json
    import shutil
    d = tmp_path / "bad"
    shutil.copytree(tiny_model_dir, d)
    meta = json.loads((d / "meta.json").read_text())
    meta["features"] = meta["features"] + ["not_a_feature"]
    (d / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="not_a_feature"):
        predict.Variant(str(d))


@pytest.mark.skipif(not os.environ.get("RIFFLE_MODEL_DIR"), reason="set RIFFLE_MODEL_DIR to the trained models/ folder")
def test_the_trained_model_scores_the_fixture(message):
    v = predict.Variant(os.path.join(os.environ["RIFFLE_MODEL_DIR"], "new_repos"))
    p = predict.predict(v, pd.DataFrame(), message["row"])
    assert abs(p.p_lands_clean + p.p_lands_after_rework + p.p_goes_nowhere - 1) < 1e-9
    print(f"trained model on the fixture: clean {p.p_lands_clean:.3f}, rework {p.p_lands_after_rework:.3f}, "
          f"nowhere {p.p_goes_nowhere:.3f}")
