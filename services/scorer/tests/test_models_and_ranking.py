"""Loading the champion from MLflow, and ranking. MLflow is faked: its client answers which version the alias
points to, and the download copies a local folder."""
import shutil
from types import SimpleNamespace

import pytest

from conftest import contract
from scorer import models, ranking


class FakeClient:
    def __init__(self):
        self.version, self.fail = "1", False

    def get_model_version_by_alias(self, name, alias):
        if self.fail:
            raise ConnectionError("mlflow down")
        return SimpleNamespace(version=self.version, source=f"mlflow-artifacts:/1/run/artifacts/model",
                               tags={"miner_version": "7.2.2"})


@pytest.fixture
def store(tiny_model_dir, tmp_path):
    calls = []

    def download(uri, dst):
        calls.append(uri)
        out = f"{dst}/{uri.rsplit('/', 1)[1]}"
        shutil.copytree(tiny_model_dir, out)
        return out

    s = models.ModelStore("http://mlflow", client=FakeClient(), download=download, cache_dir=str(tmp_path))
    s.calls = calls
    return s


def test_the_champion_is_loaded_into_memory(store, tmp_path):
    assert store.current is None and store.refresh()
    assert store.current.model_version == "riffle-outcome-setup-g/1/new_repos"
    assert store.current.miner_version == "7.2.2"
    assert store.calls == ["mlflow-artifacts:/1/run/artifacts/model/new_repos"]
    assert list(tmp_path.iterdir()) == []                   # files removed once loaded
    assert store.attempted.is_set()


def test_the_same_version_is_not_downloaded_again(store):
    store.refresh()
    assert not store.refresh() and len(store.calls) == 1


def test_a_new_champion_replaces_the_model(store):
    store.refresh()
    store.client().version = "2"
    assert store.refresh() and store.current.version == "2"


def test_mlflow_down_keeps_the_model_in_service(store):
    store.refresh()
    before = store.current
    store.client().fail = True
    assert not store.refresh() and store.current is before


def test_no_champion_yet_leaves_no_model_but_marks_the_check_done():
    s = models.ModelStore("http://mlflow", client=SimpleNamespace(
        get_model_version_by_alias=lambda *a: (_ for _ in ()).throw(LookupError("no alias"))))
    assert not s.refresh() and s.current is None and s.attempted.is_set()


def test_rank_score_is_merge_first():
    assert ranking.rank_score(0.5, 0.5) == pytest.approx(0.8)
    assert ranking.rank_score(0.0, 0.0) == 0.0 and ranking.rank_score(1.0, 0.0) == 1.0


@pytest.mark.parametrize("above, band", [(0, "senior_recommended"), (4, "senior_recommended"),
                                         (5, "review_first"), (19, "review_first"), (20, "standard")])
def test_bands_are_percentiles_of_the_repo(above, band):
    recent = [0.9] * above + [0.1] * (100 - above)
    assert ranking.band(0.5, recent) == band


def test_too_few_scores_give_standard():
    assert ranking.band(0.99, [0.1] * 19) == "standard"


def test_band_defaults_are_org_configs():
    p = contract("org_config")["properties"]["rank_bands"]["properties"]["percentile"]["properties"]
    assert ranking.SENIOR_RECOMMENDED_TOP_FRACTION == p["senior_recommended_top_fraction"]["default"]
    assert ranking.REVIEW_FIRST_TOP_FRACTION == p["review_first_top_fraction"]["default"]


def test_fair_share_caps_one_tenant():
    from scorer.__main__ import FairShare
    f = FairShare(2)
    assert f.try_acquire("a") and f.try_acquire("a") and not f.try_acquire("a")
    assert f.try_acquire("b")
    f.release("a")
    assert f.try_acquire("a")


class Msg:
    def __init__(self, data=b"{}", tenant="48213", attempt=1):
        self.data, self.attributes, self.delivery_attempt, self.done = data, {"tenant_id": tenant}, attempt, None

    def ack(self):
        self.done = "ack"

    def nack(self):
        self.done = "nack"


@pytest.mark.parametrize("behaviour, done", [(lambda *a: "ranked", "ack"), (lambda *a: "dropped", "ack"),
                                             ("retry", "nack"), ("boom", "nack")])
def test_the_consumer_acks_only_what_is_done(behaviour, done):
    from scorer import scoring
    from scorer.__main__ import FairShare, make_callback

    def handle(data, attempt):
        if behaviour == "retry":
            raise scoring.Retry("later")
        if behaviour == "boom":
            raise RuntimeError("postgres down")
        return behaviour(data, attempt)

    fair = FairShare(1)
    m = Msg()
    make_callback(SimpleNamespace(handle=handle), fair)(m)
    assert m.done == done and fair.try_acquire("48213")     # the tenant's slot is given back either way


def test_a_tenant_over_its_share_is_deferred():
    from scorer.__main__ import FairShare, make_callback
    fair = FairShare(1)
    fair.try_acquire("48213")
    m = Msg()
    make_callback(SimpleNamespace(handle=lambda *a: "ranked"), fair)(m)
    assert m.done == "nack"


def test_ready_only_after_the_first_model_check_and_while_consuming():
    import urllib.error
    import urllib.request
    from scorer.__main__ import Health
    s = models.ModelStore("http://mlflow")
    h = Health(s)
    srv = h.serve(0)
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    def code(p):
        try:
            return urllib.request.urlopen(url + p).status
        except urllib.error.HTTPError as e:
            return e.code
    assert code("/healthz") == 200 and code("/readyz") == 503
    s.attempted.set()
    h.consuming.set()
    assert code("/readyz") == 200
    h.stopping.set()
    assert code("/readyz") == 503 and code("/healthz") == 503
    srv.shutdown()
