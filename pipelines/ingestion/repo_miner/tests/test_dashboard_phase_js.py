"""The miner's big picture shows what the run is doing (RATE LIMITED, TIMELINES, MINING...), in the right colour.
The page's own renderBig() runs under Node with stub elements; layout is not tested. Skipped without Node."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

STUBS = """
const els = {};
const $ = id => els[id] || (els[id] = {id, textContent: "", style: {}, children: [],
  classList: {s: new Set(), toggle(c, on){ on === undefined ? (this.s.has(c) ? this.s.delete(c) : this.s.add(c)) : (on ? this.s.add(c) : this.s.delete(c)); }, contains(c){ return this.s.has(c); }},
  replaceChildren(){ this.children = []; }, appendChild(c){ this.children.push(c); return c; }, getBoundingClientRect(){ return {top: 0}; },
  get firstChild(){ return this.children[0] || (this._fc = this._fc || {style: {}, remove(){}, getBoundingClientRect(){ return {top: 0}; }}); }});
const el = (cls, text) => ({text, cls, getBoundingClientRect(){ return {top: 1}; }, remove(){}});
let big = {on: true}, last = {}, lastOk = Date.now();
const document = {querySelectorAll: () => []};     // the page's fitVals() finds nothing to fit
"""
RUN = """
function run(snap, ok = Date.now()) { last = snap; lastOk = ok; renderBig(); const w = $("bg_word");
  return {word: w.textContent, colour: ["run","warn","bad","done"].filter(c => w.classList.contains(c)).join(""),
          alert: $("bg_alert").textContent, label: $("bg_now_lbl").textContent}; }
const rows = [{name: "o/r", status: "warn", done: 24509, total: 89176}];
const base = {state: "running", rows, prs: "24509/89176", progress: .27, log: []};
const ph = (word, level, detail = "") => ({...base, phase: {word, level, detail}});
console.log(JSON.stringify({
  rate: run(ph("RATE LIMITED", "warn", "GitHub quota used up: resumes in 12m")),
  mining: run(ph("MINING", "run")),
  stage: run(ph("TIMELINES", "run", "o/r: timelines")),
  breaker: run({...ph("MINING", "run"), breaker: "3 failures"}),
  paused: run({...base, llm_paused: "LLM paused until 14:00"}),
  older_tui: run({...base}),
  finished: run({...base, state: "finished"}),
  stale: run(ph("MINING", "run"), Date.now() - 60000),
}));
"""


@pytest.fixture(scope="module")
def out():
    html = next(getattr(dashboard, n) for n in dir(dashboard) if isinstance(getattr(dashboard, n), str) and "renderBig" in getattr(dashboard, n))
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    fn = js[js.index("function renderBig()"): js.index('$("b_big").onclick')]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(STUBS + fn + RUN)
    try:
        r = subprocess.run(["node", fh.name], capture_output=True, text=True, timeout=30)
    finally:
        os.remove(fh.name)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_quota_wait_is_not_shown_as_mining(out):
    assert (out["rate"]["word"], out["rate"]["colour"]) == ("RATE LIMITED", "warn")
    assert "resumes in 12m" in out["rate"]["alert"] and out["rate"]["label"] == "Now stuck on"


def test_mining_and_stage_words(out):
    assert (out["mining"]["word"], out["mining"]["colour"], out["mining"]["label"]) == ("MINING", "run", "Now mining")
    assert (out["stage"]["word"], out["stage"]["colour"], out["stage"]["label"]) == ("TIMELINES", "run", "Now on")


def test_errors_outrank_the_phase_and_a_silent_page_says_so(out):
    assert (out["breaker"]["word"], out["breaker"]["colour"]) == ("STOPPED: ERROR", "bad")
    assert (out["stale"]["word"], out["stale"]["colour"]) == ("NO CONNECTION", "bad")


def test_a_tui_without_a_phase_still_works(out):
    assert out["older_tui"]["word"] == "MINING" and out["older_tui"]["label"] == "Now mining"
    assert out["paused"]["word"] == "LLM PAUSED" and out["paused"]["colour"] == "warn"
    assert (out["finished"]["word"], out["finished"]["colour"]) == ("FINISHED", "done")
