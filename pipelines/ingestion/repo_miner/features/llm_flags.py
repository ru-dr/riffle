"""
llm_flags.py — Source 6 semantic flags.

The LLM reads ONLY the diff (never the PR description, to avoid being misled),
returns a fixed JSON schema of enums/bools at temperature 0, and every
line-anchored claim is verified to exist in the diff before we keep it. These
are features for the model, never the verdict.

Off by default (Settings.enable_llm). Provider-agnostic: implement _call_llm
for your provider. When disabled, all flags come back None (treated as missing).
"""
from __future__ import annotations

import json
import sys
import time

import config
import gitio

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
    if not st.enable_llm or not st.llm_api_key:
        return dict(_NULL_RESULT)
    diff = gitio._run(repo, "diff", "-M", f"{base}...{head}", check=False)
    diff = _redact_secrets(diff)
    if len(diff) > 60000:                       # crude token guard
        diff = diff[:60000]
    raw = _call_llm(diff, st)
    if raw is None:
        return dict(_NULL_RESULT)
    return _validate(raw, diff)


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
