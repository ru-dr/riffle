"""
llm_flags.py — Source 6 semantic flags.

The LLM reads ONLY the diff (never the PR description, to avoid being misled),
returns a fixed JSON schema of enums/bools at temperature 0, and every
line-anchored claim is verified to exist in the diff before we keep it. These
are features for the model, never the verdict.

On by default (Settings.enable_llm; `--llm` is on unless turned off, and the
diff leaves the machine for the LLM backend when it is). Two backends (Settings.llm_backend):
  - "claude_cli" (default): headless `claude -p` with a JSON schema, using the
    local Claude Code login — no API key needed.
  - "api": any OpenAI-compatible endpoint, needs LLM_API_KEY.
When disabled, all flags come back None (treated as missing).

Failures: a call that gives no usable answer (no reply, or a reply with no
valid flag in it) is a "fail" fetch. The miner retries that PR in place and, if
it still fails, stops the repo (LLMUnavailable) so no row is written with
half-populated Source-6 columns. There is no separate consecutive-failure
breaker in this module: the per-PR retry in mine_repo is the breaker.
"""
from __future__ import annotations

import atexit
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import config
import gitio
import runlog


class LLMUnavailable(RuntimeError):
    """Raised by the miner when a PR's LLM answer cannot be had after its retries."""

# fixed schema — enums keep the model honest and the columns stable
FLAG_SCHEMA = {
    "llm_touches_authn_authz": "bool",
    "authz_check_change": ["none", "added", "removed", "weakened", "moved"],
    "changes_public_api_signature": "bool",
    "error_handling_change": ["none", "added", "removed", "modified"],
    "exception_swallowed": "bool",
    "db_schema_or_query_change": ["none", "schema", "query", "both"],
    "transaction_boundary_change": ["none", "added", "removed", "widened", "narrowed"],
    "input_validation_change": ["none", "added", "weakened", "modified"],
    "config_or_feature_flag_change": ["none", "config", "feature_flag", "both"],
    "feature_flag_guarding": ["n_a", "guarded", "partial", "unguarded"],
    "security_sensitive_ops": "bool",
    "resource_lifecycle_change": ["none", "acquire_no_release", "release_removed", "modified"],
    "null_handling_change": ["none", "added", "removed", "new_nullable_deref"],
    "numeric_money_precision": "bool",
    "time_handling_change": "bool",
    "retry_timeout_idempotency_change": "bool",
    "refactor_vs_behavior": ["pure_refactor", "behavior_change", "mixed"],
    "change_intent": ["feat", "fix", "refactor", "perf", "docs", "test", "build_ci", "chore"],
    "unrelated_change_share": "ordinal_0_3",
    "tests_added_for_behavior_change": ["yes", "no", "n_a"],
    "test_code_alignment": ["none", "unrelated", "partial", "aligned"],
    "satd_added": "int",
    "semantic_risk_1to5": "ordinal_1_5",
}

# llm_diff_truncated rides along with the flags: True when the size cap cut code
# out of what the model read, None when no call was made (or the answer was reused)
DIFF_TRUNCATED_COLUMN = "llm_diff_truncated"
_NULL_RESULT = {**{k: None for k in FLAG_SCHEMA}, DIFF_TRUNCATED_COLUMN: None}


def compute(repo: str, base: str, head: str, st: config.Settings) -> dict:
    """Synchronous fetch + resolve (kept for single-call use)."""
    return resolve(fetch(repo, base, head, st), st)


# Earlier LLM answers to reuse when a repo is re-mined (--remine-repo):
# {(base_sha, head_sha): {flag column: value}}. Same commits, same diff, so the
# answer still applies; reusing it saves the calls (time and plan quota).
_REUSE: dict[tuple[str, str], dict] = {}


def load_reuse(path: str, st: config.Settings | None = None) -> int:
    """Load answers saved by build_dataset --remine-repo. Returns how many.
    Every answer passes _validate. A record that names the prompt version or
    model it was made with is reused only when both match this run's (a record
    without them is an older file and is reused as before)."""
    _REUSE.clear()
    want_prompt = prompt_version(st) if st is not None else None
    try:
        with open(path, encoding="utf-8") as fh:
            for rec in json.load(fh):
                if want_prompt is not None:
                    if rec.get("prompt_version") not in (None, want_prompt):
                        continue
                    if rec.get("model") not in (None, st.llm_model):
                        continue
                flags = _validate(rec["flags"], "")
                if any(flags[k] is not None for k in FLAG_SCHEMA):
                    _REUSE[(rec["base_sha"], rec["head_sha"])] = {k: flags[k] for k in FLAG_SCHEMA}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        _REUSE.clear()
    return len(_REUSE)


def fetch(repo: str, base: str, head: str, st: config.Settings,
          skip: str | None = None) -> dict:
    """
    Thread-safe half: build the diff and call the LLM. Touches no shared state
    besides the concurrency limiter and the quota gate, so the miner can run it
    in worker threads. Returns {"status": "off"|"skipped"|"empty"|"ok"|"fail",
    "raw": dict|None}; pass the result to resolve() in PR order. `skip` is the
    reason the caller left this row out of the LLM (scope, trivial diff).
    """
    if not st.enable_llm or (st.llm_backend == "api" and not st.llm_api_key):
        return {"status": "off", "raw": None, "secs": 0.0}
    if skip:
        return {"status": "skipped", "raw": None, "secs": 0.0, "reason": skip}
    if (base, head) in _REUSE:
        kept = _validate(_REUSE[(base, head)], "")
        if any(v is not None for v in kept.values()):
            return {"status": "ok", "raw": kept, "secs": 0.0, "reused": True,
                    "model": st.llm_model, "diff_truncated": None}
    diff, truncated, has_code = llm_diff_info(repo, base, head, st)
    if not diff.strip() or not has_code:
        # nothing to judge (no diff, or only lockfiles / generated / .svg files
        # left after slimming), not a failure: the flags stay null, not neutral
        return {"status": "empty", "raw": None, "secs": 0.0}
    t0, w0 = time.monotonic(), quota_wait_s()
    _CALL.model = None
    raw = _call_with_quota(lambda: _call_claude_cli(diff, st) if st.llm_backend == "claude_cli"
                           else _call_llm(diff, st), st)
    secs = time.monotonic() - t0 - (quota_wait_s() - w0)
    runlog._acc("llm", secs)
    model = getattr(_CALL, "model", None) or st.llm_model
    flags = _validate(raw, diff) if isinstance(raw, dict) else None
    runlog.debug("llm", "call", ok=flags is not None, secs=round(secs, 2), diff_chars=len(diff),
                 backend=st.llm_backend, model=model)
    if flags is None or all(v is None for v in flags.values()):
        # no reply, or one with no valid flag in it: a failure, never an observed answer
        runlog.warn("llm", "no usable answer", secs=round(secs, 1), diff_chars=len(diff))
        return {"status": "fail", "raw": None, "secs": secs}
    return {"status": "ok", "raw": flags, "secs": secs, "model": model,
            "diff_truncated": truncated}


# ---- the diff the LLM reads ------------------------------------------------

# files whose diff says nothing the flags ask about; they are replaced by a
# one-line note so the model still knows they changed
_OMIT_EXTRA_RE = re.compile(r"\.map$|\.svg$")
_FILE_SPLIT_RE = re.compile(r"(?m)^(?=diff --git )")
_FILE_PATH_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$", re.M)


def _omit_reason(path: str) -> str | None:
    f = config.path_flags(path)
    if f.get("touches_lockfile"):
        return "lockfile"
    if f.get("is_generated") or _OMIT_EXTRA_RE.search(path):
        return "generated, vendored, minified or snapshot file"
    return None


def slim_diff(diff: str, max_chars: int) -> str:
    """Cut lockfiles and generated / vendored / minified / snapshot files down
    to one line each, then cap the size. The cap keeps whole files where it
    can and says what it dropped."""
    return slim_diff_info(diff, max_chars)[0]


def slim_diff_info(diff: str, max_chars: int) -> tuple[str, bool, bool]:
    """slim_diff plus (truncated, has_code): whether the size cap cut code the
    model would otherwise have read, and whether any file with a hunk is left
    (a diff of only omitted files or binary changes has nothing to judge)."""
    kept, notes = [], []
    for part in _FILE_SPLIT_RE.split(diff):
        if not part:
            continue
        m = _FILE_PATH_RE.match(part)
        path = m.group(2) if m else ""
        why = _omit_reason(path) if path else None
        if why:
            added = sum(1 for l in part.splitlines() if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in part.splitlines() if l.startswith("-") and not l.startswith("---"))
            notes.append(f"# {path}: {why}, +{added} -{removed} lines (diff omitted)")
        else:
            kept.append(part)
    head = ("\n".join(notes) + "\n\n") if notes else ""
    has_code = any(re.search(r"(?m)^@@ ", part) for part in kept)
    truncated = False
    out, size, dropped = [], len(head), []
    for part in kept:
        if size + len(part) <= max_chars:
            out.append(part)
            size += len(part)
        elif not out and not dropped:
            out.append(part[: max(0, max_chars - size)] + "\n# (file truncated)\n")
            size = max_chars
            truncated = True
        else:
            m = _FILE_PATH_RE.match(part)
            dropped.append(m.group(2) if m else "?")
            truncated = True
    tail = (f"\n# {len(dropped)} more changed file(s) not shown (size cap): "
            + ", ".join(dropped[:20]) + ("" if len(dropped) <= 20 else ", ...") + "\n") if dropped else ""
    return head + "".join(out) + tail, truncated, has_code


def llm_diff_info(repo: str, base: str, head: str, st: config.Settings) -> tuple[str, bool, bool]:
    """(text, truncated, has_code) of the redacted, slimmed diff for base...head."""
    diff = gitio._run(repo, "diff", "-M", f"-U{max(0, st.llm_diff_context)}",
                      f"{base}...{head}", check=False)
    if not diff.strip():
        return "", False, False
    return slim_diff_info(_redact_secrets(diff), st.llm_max_diff_chars)


def llm_diff(repo: str, base: str, head: str, st: config.Settings) -> str:
    """The redacted, slimmed diff the LLM reads for base...head."""
    return llm_diff_info(repo, base, head, st)[0]


# ---- plan quota (headless Claude Code) --------------------------------------

class QuotaHit(RuntimeError):
    """A claude -p call failed because a plan limit was reached."""

    def __init__(self, msg: str, resets_at: float | None = None):
        super().__init__(msg)
        self.resets_at = resets_at


_QUOTA_RE = re.compile(r"(usage|rate|session|weekly|credit|spend)[^\n]{0,40}limit|limit (reached|exceeded)"
                       r"|out of (credits|usage)|resets? (at|in|on)", re.I)
_QUOTA_LOCK = threading.Lock()
_QUOTA = {"checked": 0.0, "lines": None, "paused_until": 0.0}


def _plan_lines(st: config.Settings, force: bool = False):
    """Plan usage lines from claude_plan_usage(), re-read at most every
    llm_quota_poll_s seconds (it runs `claude -p /usage`, no model call)."""
    with _QUOTA_LOCK:
        now = time.time()
        if force or now - _QUOTA["checked"] >= st.llm_quota_poll_s:
            _QUOTA["lines"] = claude_plan_usage()
            _QUOTA["checked"] = now
        return _QUOTA["lines"]


def _pause_until(when: float, why: str, st: config.Settings) -> None:
    """Hold every LLM caller until `when` (all threads share one deadline).
    A reset further away than llm_quota_max_wait_h (a weekly limit) is not
    waited for: QuotaHit makes the calls fail, so the breaker stops the run."""
    if when - time.time() > st.llm_quota_max_wait_h * 3600:
        raise QuotaHit(f"{why}; resets in {(when - time.time()) / 3600:.0f} h, "
                       f"more than llm_quota_max_wait_h ({st.llm_quota_max_wait_h} h)")
    with _QUOTA_LOCK:
        if when <= _QUOTA["paused_until"]:
            return
        _QUOTA["paused_until"] = when
    runlog.info("llm", "quota pause started", why=why, until=time.strftime("%H:%M", time.localtime(when)),
                minutes=round((when - time.time()) / 60))
    at = time.strftime("%H:%M", time.localtime(when))
    import progress
    progress.emit("llm_quota", until=when, why=why)
    print(f"[llm-quota] {why}; pausing LLM calls until {at} "
          f"({(when - time.time()) / 60:.0f} min). PRs wait for their LLM answers, so "
          f"no row is written without them.", file=sys.stderr, flush=True)


_WAITED = threading.local()


def quota_wait_s() -> float:
    """Seconds this thread has spent held by a quota pause so far. Callers time
    work as elapsed minus the change in this, so a pause isn't counted as work."""
    return getattr(_WAITED, "secs", 0.0)


def _wait_paused(st: config.Settings) -> None:
    t0 = time.monotonic()
    try:
        while True:
            with _QUOTA_LOCK:
                left = _QUOTA["paused_until"] - time.time()
            if left <= 0:
                return
            time.sleep(min(left, 60))
    finally:
        waited = time.monotonic() - t0
        _WAITED.secs = quota_wait_s() + waited
        if waited >= 1:
            runlog._acc("quota_wait", waited)
            runlog.info("llm", "quota wait ended", waited_s=round(waited, 1))


def _quota_gate(st: config.Settings) -> None:
    """Before a call: if the plan is at llm_quota_pause_pct or more, wait for
    its reset. Unreadable usage (no subscription, API backend) never blocks."""
    if st.llm_backend != "claude_cli" or not st.llm_quota_wait:
        return
    for line in _plan_lines(st) or []:
        if line.get("pct") is not None and line["pct"] >= st.llm_quota_pause_pct and line.get("resets_at"):
            _pause_until(line["resets_at"] + 60, f"plan {line['label']} at {line['pct']:.0f}%", st)
    _wait_paused(st)


def _call_with_quota(call, st: config.Settings):
    """Run one LLM call under the concurrency limiter, waiting out plan limits:
    a call refused for a limit is retried after the reset instead of failing.
    One call gives up (None) once it has been held by limits for more than
    llm_quota_max_wait_h in total, so repeated short resets cannot hold it forever."""
    started = time.monotonic()
    while True:
        try:
            _quota_gate(st)
            try:
                with _limiter(st):
                    return call()
            except QuotaHit as e:
                if not st.llm_quota_wait:
                    return None
                if time.monotonic() - started > st.llm_quota_max_wait_h * 3600:
                    print(f"[llm-quota] still limited after {st.llm_quota_max_wait_h} h of waiting "
                          f"({e}); giving up on this call", file=sys.stderr, flush=True)
                    return None
                resets = e.resets_at
                if resets is None:
                    for line in _plan_lines(st, force=True) or []:
                        if line.get("resets_at"):
                            resets = max(resets or 0, line["resets_at"])
                _pause_until((resets or time.time() + st.llm_quota_poll_s) + 60,
                             f"plan limit hit ({e})", st)
        except QuotaHit as e:          # the limit resets too far away to wait for
            print(f"[llm-quota] {e}", file=sys.stderr, flush=True)
            return None


def resolve(fetched: dict, st: config.Settings) -> dict:
    """
    Main-thread half: turn a fetch() result into the 23 flag columns plus
    llm_diff_truncated. A "fail" result never gets here: the miner retries it
    and stops the repo if it still fails. Anything that is not an answer
    ("off", "skipped", "empty") is all null.
    """
    if fetched.get("status") == "ok":
        out = {k: fetched["raw"].get(k) for k in FLAG_SCHEMA}
        out[DIFF_TRUNCATED_COLUMN] = fetched.get("diff_truncated")
        return out
    return dict(_NULL_RESULT)


# the model id the last call on this thread reported (set by the backends)
_CALL = threading.local()
_MODELS_SEEN: dict[str, str] = {}      # configured name -> id the backend reported
_MODELS_LOCK = threading.Lock()


def _note_model(asked: str, got: str | None) -> None:
    if got:
        _CALL.model = got
        with _MODELS_LOCK:
            _MODELS_SEEN[asked] = got


def resolved_model(st: config.Settings) -> str:
    """The model id the backend reported for st.llm_model ("claude-sonnet-..."
    for the alias "sonnet"), or the configured name when none was reported yet.
    Used for the llm_model column, so a row never claims a model that was not seen."""
    with _MODELS_LOCK:
        return _MODELS_SEEN.get(st.llm_model, st.llm_model)


_LIMITER = None
_LIMITER_LOCK = threading.Lock()

# Running LLM usage for this process (one miner = one repo), retries included.
# claude -p reports tokens and total_cost_usd per call; on a Claude Pro/Max
# login that cost is Claude Code's estimate of API pricing, not a charge.
_USAGE_LOCK = threading.Lock()
_USAGE = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0,
          "cost_usd": 0.0, "cost_known": False}


def _add_usage(inp: int = 0, out: int = 0, cache_write: int = 0, cache_read: int = 0,
               cost: float | None = None) -> None:
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        _USAGE["input"] += int(inp or 0)
        _USAGE["output"] += int(out or 0)
        _USAGE["cache_write"] += int(cache_write or 0)
        _USAGE["cache_read"] += int(cache_read or 0)
        if cost is not None:
            _USAGE["cost_usd"] += float(cost)
            _USAGE["cost_known"] = True


def usage_snapshot() -> dict:
    with _USAGE_LOCK:
        return dict(_USAGE, cost_usd=round(_USAGE["cost_usd"], 6))


_PLAN_RE = None


def claude_plan_usage(timeout: float = 45) -> list[dict] | None:
    """Plan limits from Claude Code's own /usage, run headless (`claude -p
    /usage`). It is a local command: no model call, no tokens, no quota.
    Returns [{"label": "Session", "pct": 32, "resets": "Oct 6, 4pm (...)"}, ...]
    (session, and week lines when the plan reports them), or None when claude
    isn't installed, isn't on a subscription, or the output can't be read."""
    import re
    import shutil
    global _PLAN_RE
    if shutil.which("claude") is None:
        return None
    if _PLAN_RE is None:
        _PLAN_RE = re.compile(r"^Current ([^:]+):\s*(\d+(?:\.\d+)?)%\s*used(?:\s*·\s*resets\s*(.+))?$")
    try:
        p = subprocess.run(["claude", "-p", "/usage", "--output-format", "json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout,
                           cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
        text = json.loads(p.stdout).get("result") or ""
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        return None
    out = []
    for line in text.splitlines():
        m = _PLAN_RE.match(line.strip())
        if m:
            resets = (m.group(3) or "").strip() or None
            out.append({"label": m.group(1).strip().capitalize(), "pct": float(m.group(2)),
                        "resets": resets, "resets_at": _parse_reset(resets)})
    return out or None


def _parse_reset(text: str | None) -> float | None:
    """'Oct 6, 3:59pm (America/New_York)' -> Unix time, so the UI can show a
    live countdown. The year isn't printed: take this year, or next year if
    that would be in the past (a reset is always ahead)."""
    import re
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not text:
        return None
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{1,2}),?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:\(([^)]+)\))?",
                 text.strip(), re.I)
    if not m:
        return None
    mon, day, hh, mm, ap, tz = m.groups()
    try:
        zone = ZoneInfo(tz) if tz else None
        now = datetime.now(zone)
        h = int(hh) % 12 + (12 if ap.lower() == "pm" else 0)
        month = datetime.strptime(mon.title(), "%b").month
        when = now.replace(month=month, day=int(day), hour=h, minute=int(mm or 0), second=0, microsecond=0)
        if when.timestamp() < now.timestamp() - 3600:
            when = when.replace(year=when.year + 1)
        return when.timestamp()
    except (ValueError, KeyError, OSError):
        return None


def usage_summary(u: dict | None = None) -> str:
    u = u or usage_snapshot()
    if not u["calls"]:
        return "no LLM calls"
    total_in = u["input"] + u["cache_write"] + u["cache_read"]
    cost = f" · ${u['cost_usd']:.2f} at API prices" if u["cost_known"] else ""
    return (f"{u['calls']} calls · {total_in:,} tokens in ({u['cache_read']:,} cache read, "
            f"{u['cache_write']:,} cache write) · {u['output']:,} out{cost}")


def _limiter(st: config.Settings) -> threading.Semaphore:
    """Caps concurrent LLM calls (Settings.llm_concurrency) across workers."""
    global _LIMITER
    with _LIMITER_LOCK:
        if _LIMITER is None:
            _LIMITER = threading.BoundedSemaphore(max(1, st.llm_concurrency))
    return _LIMITER


_REDACT_PATTERNS = (
    (r"(?i)\b(authorization\s*[:=]\s*\"?(?:bearer|basic|token)\s+)[^\s\"']+", r"\1<redacted>"),
    (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", "bearer <redacted>"),
    # key/value assignments: quoted (incl. JSON "key": "value") or bare values
    (r"""(?i)(\b[\w.-]*(?:secret|passw(?:or)?d|token|api[_-]?key|auth|credential|private[_-]?key)[\w.-]*["']?\s*[:=]\s*)"""
     r"""(?:"[^"\n]*"|'[^'\n]*'|[^\s,;'"]{6,})""", r"\1<redacted>"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", "<redacted private key>"),
    (r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
     r"|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})", "<redacted>"),
    (r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+:)[^\s/@]+@", r"\1<redacted>@"),
)
_REDACT_RES = tuple((re.compile(p, re.S if "PRIVATE" in p else 0), r) for p, r in _REDACT_PATTERNS)
_DIFF_PREFIX = "Git diff:\n\n"


def _redact_secrets(diff: str) -> str:
    for rx, repl in _REDACT_RES:
        diff = rx.sub(repl, diff)
    return diff


_PROMPT_VERSIONS: dict[tuple, str] = {}


def prompt_parts(st: config.Settings | None = None) -> dict:
    """Everything besides the prompt text that shapes what the LLM reads: the
    slimming rules, redaction, size caps, diff prefix, transcript format and
    backend. Folded into prompt_version(); also the list of Settings the
    caller should treat as shaping (llm_max_diff_chars, llm_diff_context,
    llm_backend)."""
    import merge_labels
    st = st or config.Settings()
    return {
        "omit_extra": _OMIT_EXTRA_RE.pattern,
        "lockfile_rules": config.PATH_RULES["touches_lockfile"],
        "generated_rules": config.GENERATED_PATTERNS,
        "redaction": [p for p, _ in _REDACT_PATTERNS],
        "diff_prefix": _DIFF_PREFIX,
        "max_diff_chars": st.llm_max_diff_chars,
        "diff_context": st.llm_diff_context,
        "backend": st.llm_backend,
        "transcript": merge_labels.TRANSCRIPT_FORMAT,
    }


def prompt_version(st: config.Settings | None = None) -> str:
    """Short hash of everything the LLM is asked and shown (the flags' and the
    close classifier's prompts and schemas, plus prompt_parts), written on
    every row as llm_prompt_version so rows from different prompts can be told
    apart. Pass the run's Settings: the caps and backend are part of it."""
    st = st or config.Settings()
    key = (st.llm_max_diff_chars, st.llm_diff_context, st.llm_backend)
    if key not in _PROMPT_VERSIONS:
        import hashlib
        import merge_labels
        text = "\n".join([_SYSTEM, _schema_prompt(), json.dumps(_json_schema(), sort_keys=True),
                           merge_labels._CLASSIFIER_SYSTEM,
                           json.dumps(merge_labels._CLASSIFIER_SCHEMA, sort_keys=True),
                           json.dumps(prompt_parts(st), sort_keys=True)])
        _PROMPT_VERSIONS[key] = hashlib.sha1(text.encode()).hexdigest()[:12]
    return _PROMPT_VERSIONS[key]


def _as_int(val) -> int | None:
    """An integer from a JSON number: not a bool, not NaN/inf, 3.0 counts as 3."""
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and math.isfinite(val) and val.is_integer():
        return int(val)
    return None


def _validate(raw: dict, diff: str) -> dict:
    """Keep only schema-conformant values; coerce the rest to None."""
    out = {k: None for k in FLAG_SCHEMA}
    for key, spec in FLAG_SCHEMA.items():
        if key not in raw:
            continue
        val = raw[key]
        if spec == "bool":
            out[key] = val if isinstance(val, bool) else None
        elif spec == "int":
            n = _as_int(val)
            out[key] = n if n is not None and n >= 0 else None
        elif spec == "ordinal_1_5":
            n = _as_int(val)
            out[key] = n if n is not None and 1 <= n <= 5 else None
        elif spec == "ordinal_0_3":
            n = _as_int(val)
            out[key] = n if n is not None and 0 <= n <= 3 else None
        elif isinstance(spec, list):
            out[key] = val if isinstance(val, str) and val in spec else None
    return out


_CLIENT_CACHE: dict = {}


def _get_client(st: config.Settings):
    """Lazily build (and cache) an OpenAI SDK client pointed at the configured
    endpoint. Returns None if the openai package isn't installed."""
    try:
        from openai import OpenAI
    except Exception:
        return None
    key = (st.llm_base_url, st.llm_api_key)
    if key not in _CLIENT_CACHE:
        _CLIENT_CACHE[key] = OpenAI(api_key=st.llm_api_key, base_url=st.llm_base_url)
    return _CLIENT_CACHE[key]


def _schema_prompt() -> str:
    """Render FLAG_SCHEMA as an allowed-values spec so the model returns the
    exact keys/enums we expect (kept in sync with FLAG_SCHEMA automatically)."""
    lines = []
    for key, spec in FLAG_SCHEMA.items():
        if spec == "bool":
            allowed = "true or false"
        elif spec == "int":
            allowed = "a non-negative integer (a count)"
        elif spec == "ordinal_1_5":
            allowed = "an integer from 1 to 5"
        elif spec == "ordinal_0_3":
            allowed = "an integer from 0 to 3"
        elif isinstance(spec, list):
            allowed = "one of " + json.dumps(spec)
        else:
            allowed = "a value"
        lines.append(f'  "{key}": {allowed}')
    return "{\n" + ",\n".join(lines) + "\n}"


_SYSTEM = (
    "You are a precise code-review risk analyzer. You are given ONLY a git diff "
    "(no PR title or description). Judge strictly from what the diff shows; never "
    "speculate about code you cannot see, and never invent line references. "
    "Return a SINGLE JSON object with EXACTLY the keys below and no others. Output "
    "only the JSON object: no markdown, no code fences, no commentary. When a flag "
    'does not clearly apply, use the most neutral value ("none"/"n_a", false, or '
    "the lowest number). Keys and their allowed values:\n"
)


# transient HTTP statuses / error kinds worth retrying; everything else
# (400/401/403/404/422 - bad key, bad request, unknown model) is PERMANENT and
# must fail fast to None rather than loop forever and hang the whole run.
_LLM_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
_LLM_MAX_ATTEMPTS = 6


def _err_status(e) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(e, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _err_retry_after(e) -> int | None:
    resp = getattr(e, "response", None)
    hdrs = getattr(resp, "headers", None)
    if hdrs:
        ra = hdrs.get("retry-after") or hdrs.get("Retry-After")
        if ra and str(ra).isdigit():
            return int(ra)
    return None


def _call_llm(diff: str, st: config.Settings, system: str | None = None,
              prefix: str = _DIFF_PREFIX):
    """
    Call the configured chat model (default: Claude via Anthropic's
    OpenAI-compatible endpoint) to extract the Source-6 semantic flags from the
    diff. Returns a raw dict (sanitized later by _validate) or None.

    Retries transient failures (429 rate limit, 5xx, timeouts, connection
    drops) with backoff + Retry-After so a provider hiccup doesn't silently drop
    the feature; fails FAST on permanent errors (bad key / request / unknown
    model) so a misconfiguration can't hang the run. Giving up is logged, never
    silent. Swap models/endpoint via LLM_MODEL / LLM_BASE_URL. No temperature is
    sent — newer Claude models reject it, and the task is deterministic enough by
    construction (schema-constrained, evidence-only prompt).
    """
    client = _get_client(st)
    if client is None:
        return None
    last = None
    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            resp = client.chat.completions.create(
                model=st.llm_model,
                messages=[
                    {"role": "system", "content": system or (_SYSTEM + _schema_prompt())},
                    {"role": "user", "content": prefix + diff},
                ],
            )
            u = getattr(resp, "usage", None)
            if u is not None:
                _add_usage(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
            _note_model(st.llm_model, getattr(resp, "model", None))
            text = (resp.choices[0].message.content or "") if resp.choices else ""
            return _extract_json(text)
        except Exception as e:                           # noqa: BLE001
            last = e
            status = _err_status(e)
            name = type(e).__name__.lower()
            transient = (status in _LLM_RETRY_STATUS
                         or any(k in name for k in ("timeout", "connection", "ratelimit")))
            if not transient:
                # permanent (bad key / request / unknown model) -> don't hammer it
                print(f"[llm] permanent error ({status or type(e).__name__}); "
                      "leaving flags missing for this PR", file=sys.stderr)
                return None
            if attempt == _LLM_MAX_ATTEMPTS - 1:
                break
            wait = _err_retry_after(e) or min(60, 2 ** attempt)
            time.sleep(wait)
    print(f"[llm] transient failures persisted after {_LLM_MAX_ATTEMPTS} attempts "
          f"({type(last).__name__}); flags for this PR left missing", file=sys.stderr)
    return None


def _json_schema() -> dict:
    """FLAG_SCHEMA as a JSON Schema, so `claude -p --json-schema` enforces the
    exact keys/enums (still re-checked by _validate)."""
    props = {}
    for key, spec in FLAG_SCHEMA.items():
        if spec == "bool":
            props[key] = {"type": "boolean"}
        elif spec == "int":
            props[key] = {"type": "integer", "minimum": 0}
        elif spec == "ordinal_1_5":
            props[key] = {"type": "integer", "minimum": 1, "maximum": 5}
        elif spec == "ordinal_0_3":
            props[key] = {"type": "integer", "minimum": 0, "maximum": 3}
        elif isinstance(spec, list):
            props[key] = {"enum": spec}
    return {"type": "object", "properties": props,
            "required": list(FLAG_SCHEMA), "additionalProperties": False}


_CLI_WORKDIR: str | None = None


def _call_claude_cli(diff: str, st: config.Settings, retries: int = 2,
                     system: str | None = None, schema: dict | None = None,
                     prefix: str = _DIFF_PREFIX):
    """
    Run headless Claude Code (`claude -p`) on the diff with a JSON schema.
    Uses the local Claude Code login (no API key). Runs from an empty temp dir
    with no tools, MCP, settings, or slash commands, so no CLAUDE.md, hooks, or
    plugins leak into the judgement. Retries with backoff (rate limits), then
    returns None on failure.
    """
    global _CLI_WORKDIR
    with _LIMITER_LOCK:
        if _CLI_WORKDIR is None:
            _CLI_WORKDIR = tempfile.mkdtemp(prefix="riffle-llm-")
            atexit.register(shutil.rmtree, _CLI_WORKDIR, True)
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", st.llm_model,
        "--setting-sources", "",
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", system or (_SYSTEM + _schema_prompt()),
        "--json-schema", json.dumps(schema or _json_schema()),
    ]
    for attempt in range(retries + 1):
        try:
            p = subprocess.run(cmd, input=prefix + diff, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=st.llm_timeout, cwd=_CLI_WORKDIR,
                               env=os.environ.copy())
            try:
                out = json.loads(p.stdout) if p.stdout.strip() else {}
                if not isinstance(out, dict):
                    out = {}
                unparsed = ""
            except json.JSONDecodeError:
                out, unparsed = {}, p.stdout[:2000]      # plain-text output can carry the limit message too
            u = out.get("usage") or {}
            if u or out.get("total_cost_usd") is not None:
                _add_usage(u.get("input_tokens"), u.get("output_tokens"),
                           u.get("cache_creation_input_tokens"), u.get("cache_read_input_tokens"),
                           out.get("total_cost_usd"))
            used = [m for m in (out.get("modelUsage") or {}) if isinstance(m, str)]
            if len(used) == 1:
                _note_model(st.llm_model, used[0])
            if p.returncode != 0 or out.get("is_error") or unparsed:
                said = f"{out.get('result') or ''} {unparsed} {p.stderr or ''}"
                if _QUOTA_RE.search(said):
                    m = re.search(r"resets?\s+(?:at\s+|on\s+)?([^\n.]+)", said, re.I)
                    raise QuotaHit(" ".join(said.split())[:160], _parse_reset(m.group(1)) if m else None)
            if p.returncode == 0:
                if not out.get("is_error"):
                    obj = out.get("structured_output")
                    if isinstance(obj, dict):
                        return obj
                    obj = _extract_json(out.get("result") or "")
                    if obj is not None:
                        return obj
        except (subprocess.TimeoutExpired, OSError) as e:
            runlog.warn("llm", "claude call failed", exc=type(e).__name__, attempt=attempt + 1,
                        of=retries + 1, will_retry=attempt < retries)
        if attempt < retries:
            time.sleep(st.llm_call_retry_s * (attempt + 1))
    return None


def unavailable_reason(st: config.Settings) -> str | None:
    """Why the configured LLM backend can't run here, or None if it can."""
    if st.llm_backend == "claude_cli":
        return None if shutil.which("claude") else "`claude` (Claude Code) not on PATH"
    if not st.llm_api_key:
        return "LLM_BACKEND=api but LLM_API_KEY is not set"
    if _get_client(st) is None:
        return "the openai package is not installed"
    return None


def ask_json(system: str, schema: dict, text: str, st: config.Settings) -> dict | None:
    """One schema-constrained call on the configured backend, for callers
    other than the diff flags (the close-reason classifier). Shares the
    concurrency limiter and usage counters. None when the LLM is off or fails."""
    if not st.enable_llm or (st.llm_backend == "api" and not st.llm_api_key):
        return None
    if st.llm_backend == "claude_cli":
        return _call_with_quota(
            lambda: _call_claude_cli(text, st, system=system, schema=schema, prefix=""), st)
    return _call_with_quota(
        lambda: _call_llm(text, st, system=system + "\nSchema: " + json.dumps(schema), prefix=""), st)


def _extract_json(text: str):
    """Pull a JSON object out of the model's reply, tolerating stray prose or
    ```json fences. Returns a dict or None."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b == -1 or b < a:
        return None
    try:
        obj = json.loads(text[a:b + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


_TEST_DIFF = """diff --git a/app/auth.py b/app/auth.py
--- a/app/auth.py
+++ b/app/auth.py
@@ -10,7 +10,6 @@ def delete_user(request, user_id):
-    if not request.user.is_admin:
-        raise PermissionDenied()
     try:
         User.objects.get(id=user_id).delete()
-    except User.DoesNotExist:
-        raise NotFound()
+    except Exception:
+        pass
"""


def self_test(st: config.Settings) -> tuple[bool, str]:
    """One LLM call on a known-risky sample diff (auth check removed, exception
    swallowed). Returns (ok, message) — checks login, model, and headless mode."""
    t0 = time.monotonic()
    try:
        raw = _call_claude_cli(_TEST_DIFF, st, retries=0) if st.llm_backend == "claude_cli" \
            else _call_llm(_TEST_DIFF, st)
    except QuotaHit as e:
        return False, f"FAIL: plan limit reached ({e})"
    secs = time.monotonic() - t0
    if raw is None:
        return False, (f"FAIL: no valid response from backend={st.llm_backend} "
                       f"model={st.llm_model} after {secs:.1f}s "
                       "(check `claude` login / model name / LLM_API_KEY)")
    flags = _validate(raw, _TEST_DIFF)
    filled = sum(v is not None for v in flags.values())
    sane = flags.get("authz_check_change") in ("removed", "weakened") and \
        flags.get("exception_swallowed") is True
    return filled == len(FLAG_SCHEMA), (
        f"{'OK' if filled == len(FLAG_SCHEMA) else 'PARTIAL'}: backend={st.llm_backend} "
        f"model={st.llm_model} {secs:.1f}s, {filled}/{len(FLAG_SCHEMA)} flags, "
        f"authz_check_change={flags.get('authz_check_change')}, "
        f"exception_swallowed={flags.get('exception_swallowed')}, "
        f"semantic_risk={flags.get('semantic_risk_1to5')}"
        + ("" if sane else "  (warning: model missed the planted risks)"))

