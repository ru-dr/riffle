"""api_contract.py against the real oasdiff / buf / graphql-inspector.

Each test skips itself when its tool is not on PATH (CI installs none of them),
so these only run where the doctor's tools are installed.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from features import api_contract  # noqa: E402

ST = config.Settings() if hasattr(config, "Settings") else None

OAS_BASE = """openapi: 3.0.0
info: {title: t, version: '1'}
paths:
  /pets:
    get:
      responses: {'200': {description: ok}}
  /owners:
    get:
      responses: {'200': {description: ok}}
"""
OAS_ADD = OAS_BASE.replace("  /owners:", "  /vets:\n    get:\n      responses: {'200': {description: ok}}\n  /owners:")
OAS_BREAK = OAS_BASE.replace("  /owners:\n    get:\n      responses: {'200': {description: ok}}\n", "")

PROTO_BASE = 'syntax = "proto3";\npackage p.v1;\nmessage M { string a = 1; string b = 2; }\n'
PROTO_ADD = PROTO_BASE.replace("string b = 2;", "string b = 2; string c = 3;")
PROTO_BREAK = PROTO_BASE.replace("string b = 2;", "int32 b = 2;")

GQL_BASE = "type Query { a: String b: String }\n"
GQL_ADD = "type Query { a: String b: String c: String }\n"
GQL_BREAK = "type Query { a: String }\n"


def _need(tool):
    if not shutil.which(tool):
        pytest.skip(f"{tool} not installed")


def test_oasdiff_labels():
    _need("oasdiff")
    assert api_contract._diff_one("openapi", OAS_BASE, OAS_ADD, "api.yaml", ST) == "additive"
    assert api_contract._diff_one("openapi", OAS_BASE, OAS_BREAK, "api.yaml", ST) == "breaking"


def test_buf_labels():
    _need("buf")
    assert api_contract._diff_one("proto", PROTO_BASE, PROTO_ADD, "a/m.proto", ST) == "additive"
    assert api_contract._diff_one("proto", PROTO_BASE, PROTO_BREAK, "a/m.proto", ST) == "breaking"


def test_graphql_inspector_labels():
    _need("graphql-inspector")
    assert api_contract._diff_one("graphql", GQL_BASE, GQL_ADD, "s.graphql", ST) == "additive"
    assert api_contract._diff_one("graphql", GQL_BASE, GQL_BREAK, "s.graphql", ST) == "breaking"


def test_graphql_inspector_failure_has_no_verdict():
    _need("graphql-inspector")
    assert api_contract._diff_one("graphql", GQL_BASE, "type Query {{{ \n", "s.graphql", ST) is None
