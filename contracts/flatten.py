#!/usr/bin/env python3
"""Flat form of contract data: one key per value, keyed by JSON Pointer (RFC 6901).

    flatten({"labels": {"maturity_days": 30}, "rules": [{"id": "a", "kind": "pr_label"}]})
    -> {"/labels/maturity_days": 30, "/rules/0/id": "a", "/rules/0/kind": "pr_label"}

Rules (ADR 0012):
- A key is the JSON Pointer of the value, the same notation as
  organization.locked_keys. "~" is written "~0" and "/" is written "~1", so
  any object key round-trips.
- A value is a scalar, a list of scalars (kept whole, e.g. quiet_authors), or
  an empty object or list.
- A list that holds objects or lists is split by index: /rules/0/kind.
- unflatten(flat, schema) rebuilds the document. The schema decides whether
  /x/0 is a list item or an object key "0", so it must be the schema the data
  was written against.

Standard library only, so every service and pipeline can vendor it.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any

_INDEX = "*"          # list index in a key pattern
_NONE = "__no_default__"
_NONE_JSON = json.dumps(_NONE)
_ANY_KEY = "{key}"    # free-form object key in a key pattern


def escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def unescape(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")


def _is_container(v: Any) -> bool:
    return isinstance(v, (dict, list))


def flatten(doc: Any, prefix: str = "") -> dict[str, Any]:
    """Document -> {JSON Pointer: value}. The root pointer is ""."""
    out: dict[str, Any] = {}
    if isinstance(doc, dict) and doc:
        for k, v in doc.items():
            out.update(flatten(v, f"{prefix}/{escape(str(k))}"))
    elif isinstance(doc, list) and any(_is_container(x) for x in doc):
        for i, v in enumerate(doc):
            out.update(flatten(v, f"{prefix}/{i}"))
    else:
        out[prefix] = doc
    return out


def split(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"not a JSON Pointer: {pointer!r}")
    return [unescape(s) for s in pointer[1:].split("/")]


# ---- schema walking -------------------------------------------------------

def _resolve(root: dict, node: Any, depth: int = 0) -> list[dict]:
    """Every subschema that can describe the same value: follows $ref and
    expands allOf / anyOf / oneOf / if-then-else."""
    if not isinstance(node, dict) or depth > 32:
        return []
    out = [node]
    if "$ref" in node and node["$ref"].startswith("#/"):
        target: Any = root
        for part in node["$ref"][2:].split("/"):
            target = target[unescape(part)]
        out += _resolve(root, target, depth + 1)
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(key, []):
            out += _resolve(root, sub, depth + 1)
    for key in ("if", "then", "else"):
        if key in node:
            out += _resolve(root, node[key], depth + 1)
    return out


def _types(nodes: list[dict]) -> set[str]:
    ts: set[str] = set()
    for n in nodes:
        t = n.get("type")
        ts |= set(t) if isinstance(t, list) else ({t} if t else set())
        if "items" in n or "prefixItems" in n:
            ts.add("array")
        if "properties" in n or "patternProperties" in n or isinstance(n.get("additionalProperties"), dict):
            ts.add("object")
        if "enum" in n:
            ts |= {_json_type(v) for v in n["enum"]}
        if "const" in n:
            ts.add(_json_type(n["const"]))
    return ts


def _json_type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    return "array" if isinstance(v, list) else "object"


def _child(root: dict, nodes: list[dict], segment: str, as_index: bool) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        if as_index:
            prefix = n.get("prefixItems")
            if isinstance(prefix, list) and segment.isdigit() and int(segment) < len(prefix):
                out += _resolve(root, prefix[int(segment)])
            elif isinstance(n.get("items"), dict):
                out += _resolve(root, n["items"])
            continue
        if segment in n.get("properties", {}):
            out += _resolve(root, n["properties"][segment])
            continue
        hit = False
        for pat, sub in n.get("patternProperties", {}).items():
            if re.search(pat, segment):
                out += _resolve(root, sub)
                hit = True
        if not hit and isinstance(n.get("additionalProperties"), dict):
            out += _resolve(root, n["additionalProperties"])
    return out


class _Leaf(dict):
    """Marks a value set by a pointer, so it is never rebuilt as a container."""


def _rebuild(tree: Any, root: dict, nodes: list[dict]) -> Any:
    if isinstance(tree, _Leaf):
        return tree["value"]
    keys = list(tree)
    as_list = ("array" in _types(nodes) and bool(keys)
               and sorted(keys, key=lambda k: int(k) if k.isdigit() else -1) == [str(i) for i in range(len(keys))])
    if as_list:
        return [_rebuild(tree[str(i)], root, _child(root, nodes, str(i), True)) for i in range(len(keys))]
    return {k: _rebuild(v, root, _child(root, nodes, k, False)) for k, v in tree.items()}


def unflatten(flat: dict[str, Any], schema: dict) -> Any:
    """{JSON Pointer: value} -> document, using schema for list vs object."""
    if "" in flat:
        if len(flat) != 1:
            raise ValueError("root pointer \"\" can't sit beside other keys")
        return flat[""]
    tree: dict = {}
    for pointer, value in flat.items():
        segs = split(pointer)
        node = tree
        for s in segs[:-1]:
            nxt = node.setdefault(s, {})
            if isinstance(nxt, _Leaf):
                raise ValueError(f"{pointer!r} goes inside a value set by a shorter key")
            node = nxt
        if segs[-1] in node:
            raise ValueError(f"{pointer!r} is set twice or overlaps a longer key")
        node[segs[-1]] = _Leaf(value=value)
    return _rebuild(tree, schema, _resolve(schema, schema))


# ---- key catalog ----------------------------------------------------------

def catalog(schema: dict) -> dict[str, dict]:
    """Every key pattern the flat form of this schema's data can contain.
    "*" is a list index, "{key}" a free-form object key. Each entry gives the
    value's JSON types and, where set, its default."""
    out: dict[str, dict] = {}

    def walk(groups: list[list[dict]], pattern: str, depth: int) -> None:
        """groups: one resolved node list per subschema that describes this
        key (one per rule kind for /rules/*/...)."""
        if depth > 24:
            return
        nodes = [n for g in groups for n in g]
        ts = _types(nodes)
        props: dict[str, list[list[dict]]] = {}
        free: list[dict] = []
        items: list[dict] = []
        for n in nodes:
            for k, sub in n.get("properties", {}).items():
                props.setdefault(k, []).append(_resolve(schema, sub))
            for sub in n.get("patternProperties", {}).values():
                free += _resolve(schema, sub)
            if isinstance(n.get("additionalProperties"), dict):
                free += _resolve(schema, n["additionalProperties"])
            if isinstance(n.get("items"), dict):
                items += _resolve(schema, n["items"])
        container_items = "array" in ts and items and _types(items) & {"object", "array"}
        leaf_types = sorted(ts - ({"object"} if (props or free) else set())
                            - ({"array"} if container_items else set()))
        if leaf_types or not (props or free or container_items):
            entry: dict[str, Any] = {"type": leaf_types or ["any"]}
            # a default is shown only when every subschema for this key has
            # the same one; otherwise it depends on the branch (the rule kind)
            per_group = {json.dumps(next((n["default"] for n in g if "default" in n), _NONE),
                                    sort_keys=True) for g in groups}
            if len(per_group) == 1 and _NONE_JSON not in per_group:
                entry["default"] = json.loads(per_group.pop())
            elif per_group - {_NONE_JSON}:
                entry["default_varies"] = True
            out[pattern] = entry
        for k, sub in props.items():
            walk(sub, f"{pattern}/{escape(k)}", depth + 1)
        if free:
            walk([free], f"{pattern}/{_ANY_KEY}", depth + 1)
        if container_items:
            walk([items], f"{pattern}/{_INDEX}", depth + 1)

    walk([_resolve(schema, schema)], "", 0)
    return out


if __name__ == "__main__":
    # python contracts/flatten.py data.json  -> prints the flat form
    print(json.dumps(flatten(json.load(open(sys.argv[1]))), indent=2, ensure_ascii=False))
