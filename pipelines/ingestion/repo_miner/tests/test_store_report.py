"""store_report.py: charts from the store's /api/runs reply."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store_report  # noqa: E402


def _run(n, repos, **status):
    return {"pc": f"pc{n}of6-x", "prefix": "7.0.1/x", "updated": 1.0,
            "status": {"pc": f"{n}/6", "host": f"h{n}", "miner_version": "7.0.1", "repos": repos, **status}}


RUNS = [
    _run(2, {"a/b": {"state": "done", "rows": 10}, "c/d": {"state": "pending"}}, prs_done=10, prs_remaining=5, synced_at="t"),
    _run(1, {"a/b": {"state": "stopped", "rows": 4}, "e/f": {"state": "mining", "rows": 1}}, prs_done=5, prs_remaining=0),
]


def test_summarise_sums_rows_per_repo_across_pcs_and_sorts_pcs():
    s = store_report.summarise(RUNS)
    assert [p["pc"] for p in s["pcs"]] == ["1/6", "2/6"]
    assert s["repos"][0]["repo"] == "a/b" and s["repos"][0]["rows"] == 14 and len(s["repos"][0]["pcs"]) == 2
    assert s["rows"] == 15 and s["left_started"] == 0 and s["est_not_started"] == 0 and s["kept"] is None
    assert s["pcs"][1]["states"]["pending"] == 1 and s["pcs"][0]["states"]["stopped"] == 1


def test_left_counts_the_started_repo_exactly_and_the_rest_from_the_plan(tmp_path):
    plan = {"a/b": 20, "c/d": 100, "e/f": 50, "g/h": 40}
    runs = [_run(1, {"a/b": {"state": "done", "rows": 10}, "e/f": {"state": "stopped", "selected": 30, "rows": 12},
                     "c/d": {"state": "pending"}}),
            _run(2, {"g/h": {"state": "pending"}})]
    s = store_report.summarise(runs, plan)
    p1, p2 = s["pcs"]
    assert s["kept"] == 0.5 and p1["rows"] == 22 and p1["left_started"] == 18
    assert p1["pending_plan"] == 100 and p1["est_not_started"] == 50 and p2["est_not_started"] == 20
    assert s["plan_total"] == 210 and s["unreported"] == {"plan": 0, "est": 0}


def test_plan_is_parsed_from_the_repos_file(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text("# header [9 PRs]\nhttps://github.com/O/R  # [~5 commits] [1,234 PRs] [domain: x]\nfoo/bar  # no count\n")
    assert store_report.parse_plan(str(f)) == {"o/r": 1234}
    assert store_report.parse_plan(str(tmp_path / "missing.txt")) == {}


def test_missing_fields_do_not_crash_and_unknown_state_is_pending():
    s = store_report.summarise([{"pc": "pc9", "status": {"repos": {"x/y": {"state": "weird"}}}}, {"pc": "pc8"}])
    assert s["rows"] == 0 and s["pcs"][0]["states"]["pending"] in (0, 1)
    html_out = store_report.render([{"pc": "pc9"}, {"pc": "pc8", "status": None}])
    assert "<svg" in html_out and "Riffle store report" in html_out


def test_render_escapes_repo_names():
    runs = [_run(1, {"<script>x</script>": {"state": "done", "rows": 3}}, prs_done=1, prs_remaining=0)]
    assert "<script>x" not in store_report.render(runs)


def test_main_from_json_and_missing_credentials(tmp_path, monkeypatch):
    src, out = tmp_path / "r.json", tmp_path / "o.html"
    src.write_text(json.dumps(RUNS))
    assert store_report.main(["--from-json", str(src), "--out", str(out)]) == 0
    assert "a/b" in out.read_text(encoding="utf-8")
    monkeypatch.setattr(store_report, "_env", lambda name: "")
    assert store_report.main(["--out", str(out)]) == 2


def test_store_unreachable_is_exit_1_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(store_report, "_env", lambda name: "http://127.0.0.1:9" if name.endswith("URL") else "t")
    assert store_report.main(["--out", str(tmp_path / "o.html")]) == 1


def test_admin_overview_adds_a_pc_that_never_uploaded_and_pending_is_planned_minus_mined():
    plan = {"a/b": 60, "c/d": 40, "e/f": 100}
    runs = [_run(1, {"a/b": {"state": "done", "rows": 50}, "c/d": {"state": "pending"}}, prs_total=100, repos_total=2)]
    overview = {"pcs": [{"pc": "pc1of6-x", "state": "mining"}, {"pc": "pc2of6-x", "state": "never"}], "splits": [{"n_repos": 3}]}
    s = store_report.summarise(store_report.apply_admin(runs, overview, plan), plan)
    p1, p2 = s["pcs"]
    assert p1["admin_state"] == "mining" and p1["planned"] == 100 and p1["pending"] == 50
    assert p2["planned"] == 100 and p2["pending"] == 100 and p2["states"]["pending"] == 1 and p2["rows"] == 0
    assert s["planned"] == 200 and s["pending"] == 150 and s["repos_total"] == 3 and s["repos_done"] == 1


def test_in_progress_repos_list_what_is_left_in_each():
    runs = [_run(1, {"a/b": {"state": "stopped", "rows": 4, "selected": 10}, "c/d": {"state": "done", "rows": 9}})]
    assert store_report.summarise(runs)["in_progress"] == [{"repo": "a/b", "pc": "1/6", "rows": 4, "left": 6}]
