"""Every contract's data has a flat form that round-trips (ADR 0012).

Run: python -m pytest contracts/tests
"""
import json
import pathlib
import re
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import flatten as F  # noqa: E402

SCHEMAS = sorted(p.name for p in HERE.glob("*.schema.json"))
# free-form keys that break naive dotted paths
TRICKY_KEYS = ["plain", "a/b", "c~d", "0", "e.f", ""]


def load(name):
    return json.loads((HERE / name).read_text())


def instances(schema):
    """Documents that touch every property, every oneOf / anyOf branch and every
    free-form map of the schema. Values are structural, not necessarily valid:
    the flat form must round-trip any shape the schema allows."""
    def resolve(node, depth=0):
        """Like F._resolve, but leaves oneOf / anyOf unexpanded so each branch
        can become its own instance."""
        if not isinstance(node, dict) or depth > 32:
            return []
        out = [node]
        if "$ref" in node:
            target = schema
            for part in node["$ref"][2:].split("/"):
                target = target[F.unescape(part)]
            out += resolve(target, depth + 1)
        for key in ("allOf", "if", "then", "else"):
            for sub in (node[key] if key == "allOf" else [node[key]]) if key in node else []:
                out += resolve(sub, depth + 1)
        return out

    def gen(nodes, depth):
        if depth > 12:
            return [None]
        branches = [b for n in nodes for key in ("oneOf", "anyOf") for b in n.get(key, [])]
        if branches:
            base = [{k: v for k, v in n.items() if k not in ("oneOf", "anyOf")} for n in nodes]
            return [doc for b in branches for doc in gen(base + resolve(b), depth + 1)[:1]]
        ts = F._types(nodes)
        if "object" in ts:
            doc = {}
            for n in nodes:
                for k, sub in n.get("properties", {}).items():
                    if k not in doc:
                        doc[k] = gen(resolve(sub), depth + 1)[0]
                free = list(n.get("patternProperties", {}).values())
                if isinstance(n.get("additionalProperties"), dict):
                    free.append(n["additionalProperties"])
                for sub in free:
                    for k in TRICKY_KEYS:
                        doc.setdefault(k, gen(resolve(sub), depth + 1)[0])
            return [doc]
        if "array" in ts:
            items = [s for n in nodes if isinstance(n.get("items"), dict) for s in resolve(n["items"])]
            if not items:
                return [["a", "b"]]
            vals = gen(items, depth + 1)
            return [(vals * 2)[:max(2, len(vals))]]
        for n in nodes:
            if "const" in n:
                return [n["const"]]
            if "enum" in n:
                return [n["enum"][0]]
        if "integer" in ts or "number" in ts:
            return [1]
        if "boolean" in ts:
            return [True]
        if "string" in ts:
            return ["x/y~z"]
        return [None]

    return gen(resolve(schema), 0)


def matches(pattern, key):
    rx = "^" + "/".join(
        "[0-9]+" if s == F._INDEX else "[^/]*" if s == F._ANY_KEY else re.escape(s)
        for s in pattern.split("/")) + "$"
    return re.match(rx, key) is not None


@pytest.mark.parametrize("name", SCHEMAS)
def test_round_trip_every_shape(name):
    schema = load(name)
    for doc in instances(schema):
        flat = F.flatten(doc)
        assert all(not isinstance(v, dict) or v == {} for v in flat.values())
        assert all(not isinstance(v, list) or not any(F._is_container(x) for x in v) for v in flat.values())
        assert F.unflatten(flat, schema) == doc


@pytest.mark.parametrize("name", SCHEMAS)
def test_every_flat_key_is_in_the_catalog(name):
    schema = load(name)
    cat = F.catalog(schema)
    for doc in instances(schema):
        for key in F.flatten(doc):
            assert any(matches(p, key) for p in cat), f"{name}: {key} not in catalog"


def test_fixtures_round_trip():
    yaml = pytest.importorskip("yaml")
    schema = load("org_config.schema.json")
    for fx in sorted((HERE.parent / "fixtures" / "config").glob("*.yml")):
        doc = yaml.safe_load(fx.read_text())
        assert F.unflatten(F.flatten(doc), schema) == doc, fx.name


def test_pointer_escaping():
    doc = {"house_terms": {"a/b": "1", "c~d": "2", "0": "3"}}
    flat = F.flatten(doc)
    assert flat == {"/house_terms/a~1b": "1", "/house_terms/c~0d": "2", "/house_terms/0": "3"}
    # house_terms is an object in the schema, so "0" stays a key, not a list index
    assert F.unflatten(flat, load("org_config.schema.json")) == doc


def test_list_of_objects_is_indexed_and_list_of_scalars_is_kept():
    flat = F.flatten({"scope": {"quiet_authors": ["a", "b"]}, "rules": [{"id": "r", "kind": "pr_label"}]})
    assert flat == {"/scope/quiet_authors": ["a", "b"], "/rules/0/id": "r", "/rules/0/kind": "pr_label"}


def test_mined_dataset_row_is_already_flat():
    schema = load("mined_dataset.schema.json")
    row = {c: None for c in schema["required"]}
    row.update(repo="o/r", pr_number=1, base_sha="a" * 40, head_sha="b" * 40, mined_at="2026-01-01T00:00:00Z")
    assert F.flatten(row) == {"/" + F.escape(k): v for k, v in row.items()}


def test_overlapping_keys_fail():
    with pytest.raises(ValueError):
        F.unflatten({"/a": 1, "/a/b": 2}, {})
