import copy
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))           # scorelog_writer
sys.path.insert(0, str(ROOT / "contracts"))    # flatten, as in the image

ROW = {
    "delivery_id": "72d3162e-cc78-11e3-81ab-4c9367dc0958", "tenant_id": "48213", "repo": "acme-corp/payments",
    "pr_number": 42, "head_sha": "a3f1c9e2" + "0" * 32, "trigger": "opened",
    "scored_at": "2026-11-27T12:00:00.000Z", "model_version": "riffle-outcome-setup-g/7/new_repos",
    "miner_version": "7.2.2", "rank_score": 0.61, "rank_band": "standard", "layer_weight": 0.0,
    "unranked_reason": None, "p_lands_clean": 0.4, "p_lands_after_rework": 0.35, "p_goes_nowhere": 0.25,
    "features": {"LA": 120.0, "NF": 4.0, "EXP": None, "change_intent": "bugfix", "ah_n": 3.0},
}


def row(**kw):
    r = copy.deepcopy(ROW)
    r.update(kw)
    return r


class Msg:
    def __init__(self, data, attributes=None):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.attributes = attributes or {}
        self.done = None

    def ack(self):
        self.done = "ack"

    def nack(self):
        self.done = "nack"


class Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if self.bucket.fail:
            raise ConnectionError("bucket unreachable")
        if if_generation_match == 0 and self.name in self.bucket.objects:
            raise FileExistsError(self.name)
        self.bucket.objects[self.name] = data


class Bucket:
    def __init__(self, fail=False):
        self.objects, self.fail = {}, fail

    def blob(self, name):
        return Blob(self, name)


class FakeKms:
    """Wraps by XOR with a per-tenant pad and checks the context, like KMS's associated data."""

    kek_name = "projects/p/locations/us-east4/keyRings/riffle-staging/cryptoKeys/tenant-kek"

    def __init__(self):
        self.wraps = 0

    def wrap(self, tenant_id, key):
        self.wraps += 1
        return tenant_id.encode() + b":" + bytes(b ^ 0x5A for b in key)

    def unwrap(self, tenant_id, kek_name, wrapped):
        t, _, body = wrapped.partition(b":")
        if t.decode() != tenant_id:
            raise PermissionError("wrapped for another tenant")
        return bytes(b ^ 0x5A for b in body)


class FakeDb:
    def __init__(self, statuses=None):
        self.statuses = statuses if statuses is not None else {"48213": "active"}
        self.keys = {}

    def get(self, tenant_id):
        return self.keys.get(tenant_id)

    def status(self, tenant_id):
        return self.statuses.get(tenant_id)

    def insert(self, tenant_id, wrapped, kek_name):
        self.keys.setdefault(tenant_id, (wrapped, kek_name))


@pytest.fixture
def validator():
    import jsonschema
    schema = json.loads((ROOT / "contracts" / "score_log.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
