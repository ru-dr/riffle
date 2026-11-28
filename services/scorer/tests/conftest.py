import copy
import json
import os
import sys
from concurrent.futures import Future
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))                       # scorer
sys.path.insert(0, str(ROOT / "pipelines" / "training"))   # outcome_features, as in the image
sys.path.insert(0, str(HERE))

FIXTURE = ROOT / "fixtures" / "pr_features" / "pull_request.opened.json"


@pytest.fixture(scope="session")
def tiny_model_dir(tmp_path_factory):
    import tiny_bundle
    return tiny_bundle.make(str(tmp_path_factory.mktemp("model") / "new_repos"))


@pytest.fixture
def message():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def history(message):
    """Three earlier PRs of the repo by the same author, closed before the fixture PR opened."""
    rows = []
    opened = pd.Timestamp(message["row"]["created_at"])
    for i, outcome in enumerate(("lands_clean", "lands_after_rework", "goes_nowhere")):
        r = copy.deepcopy(message["row"])
        r.update({"pr_number": 10 + i, "head_sha": f"{i + 1:040x}", "outcome_3class": outcome,
                  "created_at": (opened - pd.Timedelta(days=30 - i)).isoformat(),
                  "closed_at": (opened - pd.Timedelta(days=20 - i)).isoformat()})
        rows.append(r)
    return pd.DataFrame(rows)


class FakePublisher:
    def __init__(self, fail=False, fail_topics=()):
        self.sent, self.fail, self.fail_topics = [], fail, set(fail_topics)

    def publish(self, topic, message, attributes):
        f = Future()
        if self.fail or topic in self.fail_topics:
            f.set_exception(RuntimeError("pubsub down"))
        else:
            self.sent.append((topic, message, attributes))
            f.set_result("id")
        return f


class FakeStore:
    def __init__(self, recent=()):
        self.rows, self.recent = {}, list(recent)

    def get(self, tenant_id, delivery_id):
        r = self.rows.get(delivery_id)
        return copy.deepcopy(r) if r and r["tenant_id"] == tenant_id else None

    def insert(self, record):
        if record["delivery_id"] in self.rows:
            return False
        self.rows[record["delivery_id"]] = json.loads(json.dumps(record))    # as stored: plain JSON values
        return True

    def recent_rank_scores(self, *a):
        return self.recent


class FakeHistory:
    def __init__(self, df=None, fail=False):
        self.df, self.fail = (df if df is not None else pd.DataFrame()), fail

    def rows(self, tenant_id, repo):
        if self.fail:
            raise RuntimeError("bucket unreachable")
        return self.df


@pytest.fixture
def fakes():
    return {"publisher": FakePublisher(), "store": FakeStore(), "history": FakeHistory()}


def contract(name):
    return json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text(encoding="utf-8"))


os.environ.setdefault("CONTRACTS_DIR", str(ROOT / "contracts"))
