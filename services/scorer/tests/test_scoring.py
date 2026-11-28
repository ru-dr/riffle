"""One message through the scorer, with fakes for MLflow, the bucket, Postgres and Pub/Sub. Every message the scorer
publishes is checked against its contract."""
import json

import jsonschema
import pytest

from conftest import FakeHistory, FakePublisher, FakeStore, contract
from scorer import models, predict, scoring


@pytest.fixture(scope="module")
def loaded(tiny_model_dir):
    return models.Loaded(model_version="riffle-outcome-setup-g/7/new_repos", version="7",
                         variant=predict.Variant(tiny_model_dir), miner_version="7.2.2")


class Models:
    def __init__(self, current):
        self.current = current


def make(fakes, current, **kw):
    return scoring.Scorer(models=Models(current), history=fakes["history"], store=fakes["store"],
                          publisher=fakes["publisher"], contracts=scoring.Contracts(),
                          now=lambda: "2026-11-27T12:00:00.000Z", **kw)


def check_published(publisher, delivery_id, logged=True):
    assert [t for t, _, _ in publisher.sent] == (["score-results", "score-log"] if logged else ["score-results"])
    for topic, msg, attrs in publisher.sent:
        name = topic.replace("-", "_").rstrip("s") if topic == "score-results" else "score_log"
        v = jsonschema.Draft202012Validator(contract(name), format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
        assert [e.message for e in v.iter_errors(msg)] == []
        assert attrs["delivery_id"] == delivery_id and attrs["schema"].startswith(f"{name}/")
    result = publisher.sent[0][1]
    ranked = result["rank_score"] is not None
    assert ranked == (result["unranked_reason"] is None) == (result["rank_band"] is not None)
    if ranked:
        assert result["model_version"] and abs(result["p_merge"] - result["p_lands_clean"] -
                                               result["p_lands_after_rework"]) < 1e-9
    return result, (publisher.sent[1][1] if logged else None)


def test_an_outside_contributors_pr_is_ranked(fakes, loaded, message, history):
    fakes["history"] = FakeHistory(history)
    out = make(fakes, loaded).handle(json.dumps(message).encode())
    assert out == "ranked"
    result, log = check_published(fakes["publisher"], message["delivery_id"])
    assert result["model_version"] == "riffle-outcome-setup-g/7/new_repos" and result["rank_mode"] == "merge_first"
    assert result["rank_band"] == "standard"            # fewer than 20 recent scores in the repo
    assert abs(result["rank_score"] - (result["p_lands_clean"] + 0.6 * result["p_lands_after_rework"])) < 1e-9
    assert result["explanation"] is None and result["layer_weight"] == 0
    assert result["miner_version"] == "7.2.2" and log["features"]["ah_n"] == 3 and len(log["features"]) == 161
    stored = fakes["store"].rows[message["delivery_id"]]
    assert stored["rank_score"] == result["rank_score"]
    assert "features" not in stored                     # the inputs go to the score log only (ADR 0024)


def test_the_band_comes_from_the_repos_recent_scores(fakes, loaded, message):
    fakes["store"] = FakeStore(recent=[0.0] * 99)      # every other PR is below: this one is in the top 5%
    make(fakes, loaded).handle(json.dumps(message).encode())
    assert fakes["publisher"].sent[0][1]["rank_band"] == "senior_recommended"


def test_a_redelivered_message_is_republished_not_rescored(fakes, loaded, message):
    s = make(fakes, loaded)
    s.handle(json.dumps(message).encode())
    first = fakes["publisher"].sent[0][1]
    fakes["publisher"].sent.clear()
    before = scoring.SCORE_LOG_DROPPED._value.get()
    assert s.handle(json.dumps(message).encode()) == "duplicate"
    # The same ScoreResult again; no score log row, as its inputs are not stored, and the loss is counted.
    assert check_published(fakes["publisher"], message["delivery_id"], logged=False)[0] == first
    assert scoring.SCORE_LOG_DROPPED._value.get() == before + 1


@pytest.mark.parametrize("change, reason", [
    (lambda m: m["row"].update(author_is_bot=True), "not_outside_contributor"),
    (lambda m: m["row"].update(author_confirmed_maintainer=True), "not_outside_contributor"),
    (lambda m: m["row"].update(pr_number=43), "invalid_input"),           # row and envelope disagree
    (lambda m: m.update(miner_version="8.0.0"), "invalid_input"),          # a miner MAJOR the model was not trained on
    (lambda m: m.pop("mined_at"), "invalid_input"),
])
def test_unranked_with_a_reason(fakes, loaded, message, change, reason):
    change(message)
    assert make(fakes, loaded).handle(json.dumps(message).encode()) == reason
    result, log = check_published(fakes["publisher"], message["delivery_id"])
    assert result["unranked_reason"] == reason and result["p_lands_clean"] is None
    assert message["delivery_id"] in fakes["store"].rows        # fail open: stored and shown, unranked


def test_no_model_yet_is_unranked_not_lost(fakes, message):
    assert make(fakes, None).handle(json.dumps(message).encode()) == "model_unavailable"
    check_published(fakes["publisher"], message["delivery_id"])


def test_a_failure_is_retried_then_stored_unranked(fakes, loaded, message):
    fakes["history"] = FakeHistory(fail=True)
    s = make(fakes, loaded)
    for attempt in (1, 2):
        with pytest.raises(scoring.Retry):
            s.handle(json.dumps(message).encode(), delivery_attempt=attempt)
        assert fakes["publisher"].sent == [] and fakes["store"].rows == {}
    assert s.handle(json.dumps(message).encode(), delivery_attempt=3) == "scoring_failed"
    check_published(fakes["publisher"], message["delivery_id"])


def test_pubsub_down_is_redelivered_and_then_republished(fakes, loaded, message):
    fakes["publisher"] = FakePublisher(fail=True)
    with pytest.raises(RuntimeError):
        make(fakes, loaded).handle(json.dumps(message).encode())
    assert message["delivery_id"] in fakes["store"].rows       # stored before publishing
    fakes["publisher"] = FakePublisher()
    assert make(fakes, loaded).handle(json.dumps(message).encode()) == "duplicate"
    check_published(fakes["publisher"], message["delivery_id"], logged=False)


def test_the_score_log_failing_never_stops_the_score(fakes, loaded, message):
    fakes["publisher"] = FakePublisher(fail_topics={"score-log"})
    before = scoring.SCORE_LOG_DROPPED._value.get()
    assert make(fakes, loaded).handle(json.dumps(message).encode()) == "ranked"
    assert [t for t, _, _ in fakes["publisher"].sent] == ["score-results"]
    assert scoring.SCORE_LOG_DROPPED._value.get() == before + 1


@pytest.mark.parametrize("data", [b"not json", b"[]", json.dumps({"delivery_id": "x"}).encode()])
def test_a_message_without_its_ids_is_dropped(fakes, loaded, data):
    assert make(fakes, loaded).handle(data) == "dropped"
    assert fakes["publisher"].sent == []
