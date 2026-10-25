"""The admin form's list of shared settings comes from the miner's own parser: it cannot offer a flag the miner lacks
or miss one it shares."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import join  # noqa: E402
import settings_schema  # noqa: E402


def test_the_committed_schema_is_what_the_miner_builds():
    with open(settings_schema.SCHEMA_PATH, encoding="utf-8") as fh:
        assert fh.read() == settings_schema.render(), "run: python settings_schema.py --write"


def test_every_shared_setting_has_a_form_entry_and_nothing_else_does():
    got = {s["dest"] for s in settings_schema.build()["settings"]}
    assert got == join.SHARED_DESTS


def test_every_flag_in_the_schema_is_one_the_parser_accepts():
    import build_dataset
    flags = set(build_dataset.build_parser()._option_string_actions)
    for s in settings_schema.build()["settings"]:
        assert s["flag"] in flags and (not s.get("off_flag") or s["off_flag"] in flags), s["dest"]
        assert s["group"] in {g["id"] for g in settings_schema.GROUPS}


def test_the_split_fixing_settings_are_locked():
    locked = {s["dest"] for s in settings_schema.build()["settings"] if s.get("locked")}
    assert locked == {"repos_file", "only_domain", "split_by", "out", "out_prefix", "out_postfix"}


def test_no_help_text_is_flag_typing_jargon():
    for s in settings_schema.build()["settings"]:
        assert "passed through" not in s["help"] and "reaches the miner" not in s["help"], s["dest"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_form_model_round_trips_the_flags_in_the_browser_code(tmp_path):
    js = (MINER.parent.parent.parent / "infra" / "docker" / "riffle-data" / "server" / "static" / "app.js").read_text(encoding="utf-8")
    a, b = js.index("  var SCHEMA = null;"), js.index("  function admin()")
    flags = ["--min-age-days", "90", "--llm", "--no-szz", "--no-scanners", "--llm-model", "haiku", "--weird", "x"]
    prog = (js[a:b] + "\nvar schema=" + json.dumps(settings_schema.build()) + ";\nvar f=" + json.dumps(flags) + ";\n"
            "var p=parseModel(f,schema);var out=modelToFlags(p.model,p.extra,schema);"
            "var q=parseModel(out,schema);\n"
            "console.log(JSON.stringify({model:p.model,extra:p.extra,out:out,same:JSON.stringify(Object.keys(q.model).sort())===JSON.stringify(Object.keys(p.model).sort())}));")
    f = tmp_path / "m.js"
    f.write_text(prog, encoding="utf-8")
    r = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout)
    assert r["model"]["llm"] is True and r["model"]["szz"] is False and r["model"]["no_scanners"] is True
    assert r["extra"] == ["--weird", "x"] and r["same"] is True
    assert set(r["out"]) == set(flags)
