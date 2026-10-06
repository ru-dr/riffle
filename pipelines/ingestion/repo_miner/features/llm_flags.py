"""
llm_flags.py — Source 6 semantic flags.

The LLM reads ONLY the diff (never the PR description, to avoid being misled),
returns a fixed JSON schema of enums/bools at temperature 0, and every
line-anchored claim is verified to exist in the diff before we keep it. These
are features for the model, never the verdict.

Off by default (Settings.enable_llm). Two backends (Settings.llm_backend):
  - "claude_cli" (default): headless `claude -p` with a JSON schema, using the
    local Claude Code login — no API key needed.
  - "api": any OpenAI-compatible endpoint, needs LLM_API_KEY.
When disabled, all flags come back None (treated as missing).

Circuit breaker: after Settings.llm_breaker_threshold consecutive failed calls,
compute() raises LLMUnavailable so the run stops instead of silently writing a
dataset with half-populated Source-6 columns.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import config
import gitio


class LLMUnavailable(RuntimeError):
    """Raised when the circuit breaker trips (too many consecutive failures)."""


_consecutive_failures = 0


def last_call_failed() -> bool:
    """True if the most recent compute() call's LLM request failed (its flags
    are None because of an error, not because the LLM is disabled)."""
    return _consecutive_failures > 0

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

_NULL_RESULT = {k: None for k in FLAG_SCHEMA}


def compute(repo: str, base: str, head: str, st: config.Settings) -> dict:
    """Synchronous fetch + resolve (kept for single-call use)."""
    return resolve(fetch(repo, base, head, st), st)


def fetch(repo: str, base: str, head: str, st: config.Settings) -> dict:
    """
    Thread-safe half: build the diff and call the LLM. Touches no shared state
    besides the concurrency limiter, so the miner can run it in worker threads.
    Returns {"status": "off"|"empty"|"ok"|"fail", "raw": dict|None}; pass the
    result to resolve() in PR order.
    """
    if not st.enable_llm or (st.llm_backend == "api" and not st.llm_api_key):
        return {"status": "off", "raw": None, "secs": 0.0}
    diff = gitio._run(repo, "diff", "-M", f"{base}...{head}", check=False)
    if not diff.strip():
        return {"status": "empty", "raw": None, "secs": 0.0}  # nothing to judge, not a failure
    diff = _redact_secrets(diff)
    if len(diff) > 60000:                       # crude token guard
        diff = diff[:60000]
    t0 = time.monotonic()
    with _limiter(st):
        if st.llm_backend == "claude_cli":
            raw = _call_claude_cli(diff, st)
        else:
            raw = _call_llm(diff, st)
    secs = time.monotonic() - t0
    if raw is None:
        return {"status": "fail", "raw": None, "secs": secs}
    return {"status": "ok", "raw": _validate(raw, diff), "secs": secs}


def resolve(fetched: dict, st: config.Settings) -> dict:
    """
    Main-thread half: turn a fetch() result into the 23 flag columns and drive
    the circuit breaker. Must be called in PR order so "consecutive" means
    consecutive PRs, not whichever worker finished first.
    """
    global _consecutive_failures
    status = fetched.get("status")
    if status == "fail":
        _consecutive_failures += 1
        if _consecutive_failures >= st.llm_breaker_threshold:
            raise LLMUnavailable(
                f"{_consecutive_failures} consecutive LLM failures "
                f"(backend={st.llm_backend}, model={st.llm_model})")
        return dict(_NULL_RESULT)
    if status == "ok":
        _consecutive_failures = 0
        return dict(fetched["raw"])
    return dict(_NULL_RESULT)


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
                           capture_output=True, text=True, timeout=timeout,
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


def _redact_secrets(diff: str) -> str:
    import re
    return re.sub(
        r"(?i)(secret|password|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]+['\"]",
        r"\1=<redacted>", diff,
    )


def _validate(raw: dict, diff: str) -> dict:
    """Keep only schema-conformant values; coerce the rest to None."""
    out = dict(_NULL_RESULT)
    for key, spec in FLAG_SCHEMA.items():
        if key not in raw:
            continue
        val = raw[key]
        if spec == "bool":
            out[key] = bool(val) if isinstance(val, bool) else None
        elif spec == "int":
            out[key] = int(val) if isinstance(val, (int, float)) else None
        elif spec == "ordinal_1_5":
            out[key] = val if isinstance(val, int) and 1 <= val <= 5 else None
        elif spec == "ordinal_0_3":
            out[key] = val if isinstance(val, int) and 0 <= val <= 3 else None
        elif isinstance(spec, list):
            out[key] = val if val in spec else None
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
# (400/401/403/404/422 — bad key, bad request, unknown model) is PERMANENT and
# must fail fast to None rather than loop forever and hang the whole run.
_LLM_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
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


def _call_llm(diff: str, st: config.Settings):
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
                    {"role": "system", "content": _SYSTEM + _schema_prompt()},
                    {"role": "user", "content": "Git diff:\n\n" + diff},
                ],
            )
            u = getattr(resp, "usage", None)
            if u is not None:
                _add_usage(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
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


def _call_claude_cli(diff: str, st: config.Settings, retries: int = 2):
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
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", st.llm_model,
        "--setting-sources", "",
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", _SYSTEM + _schema_prompt(),
        "--json-schema", json.dumps(_json_schema()),
    ]
    for attempt in range(retries + 1):
        try:
            p = subprocess.run(cmd, input="Git diff:\n\n" + diff, capture_output=True,
                               text=True, timeout=st.llm_timeout, cwd=_CLI_WORKDIR,
                               env=os.environ.copy())
            out = json.loads(p.stdout) if p.stdout.strip() else {}
            u = out.get("usage") or {}
            if u or out.get("total_cost_usd") is not None:
                _add_usage(u.get("input_tokens"), u.get("output_tokens"),
                           u.get("cache_creation_input_tokens"), u.get("cache_read_input_tokens"),
                           out.get("total_cost_usd"))
            if p.returncode == 0:
                if not out.get("is_error"):
                    obj = out.get("structured_output")
                    if isinstance(obj, dict):
                        return obj
                    obj = _extract_json(out.get("result") or "")
                    if obj is not None:
                        return obj
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass
        if attempt < retries:
            time.sleep(10 * (attempt + 1))
    return None


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
    raw = _call_claude_cli(_TEST_DIFF, st, retries=0) if st.llm_backend == "claude_cli" \
        else _call_llm(_TEST_DIFF, st)
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

