"""
settings_schema.py: the lead's shared settings as data, for the store's admin form.

The admin page edits a split's settings with toggles, selects and fields instead of typed flags. What it shows
comes from this file's output (`riffle-data/server/static/settings-schema.json`), built from the miner's own
argument parser and `join.SHAPING`, so the form cannot offer a flag the miner does not have or miss one it
shares. A test keeps the committed JSON equal to what this prints.

    python settings_schema.py            # print it
    python settings_schema.py --write    # refresh the committed copy
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "infra", "docker", "riffle-data", "server",
                                            "static", "settings-schema.json"))

GROUPS = [
    {"id": "mining", "title": "What is mined", "note": "Which PRs, and which parts of each. Changing these changes the rows, so repos mined before the change differ from the rest."},
    {"id": "llm", "title": "LLM flags", "note": "The optional explainer-side labels. A different model, scope or limit changes llm_* columns."},
    {"id": "features", "title": "Feature groups", "note": "Switch whole groups of columns off. Off means the columns are null, not zero."},
    {"id": "remine", "title": "Re-mining", "note": "Off by default. A re-mine drops a repo's finished rows before it mines them again."},
    {"id": "locked", "title": "Split and files", "note": "Fixed for this split: changing them moves repos between PCs or renames files, and PCs could overlap or lose rows. Plan a new split from the miner's TUI instead."},
]

# dest -> (group, label, how to show it). kind: bool (on/off/default), flag (on/off, a bare switch), choice, int, text, date.
# invert: the flag turns the thing OFF (--no-scanners), so the form shows "on" when it is absent.
META = {
    "min_age_days": ("mining", "Skip PRs merged in the last N days", "int", {}),
    "max_prs": ("mining", "Cap on PRs per repo", "int", {"empty": "no cap"}),
    "full_repo": ("mining", "Mine each repo's whole history", "flag", {}),
    "window_from": ("mining", "Window start", "date", {}),
    "window_to": ("mining", "Window end", "date", {}),
    "lead_in_days": ("mining", "History lead-in (days)", "int", {}),
    "cap_on": ("mining", "What the PR cap counts", "choice", {}),
    "merge_outcome": ("mining", "Closed-unmerged PRs and merge-outcome labels", "bool", {}),
    "max_unmerged": ("mining", "Cap on unmerged rows per repo", "int", {"empty": "no cap"}),
    "declared_labels": ("mining", "Declared labels", "bool", {}),
    "snapshots": ("mining", "Capture PR snapshots", "bool", {}),
    "ci_history": ("mining", "Historical CI-fail rate (slow, uses the GitHub quota)", "bool", {}),
    "szz": ("mining", "SZZ bug tracing", "bool", {}),
    "pr_discovery": ("mining", "How PRs are found", "choice", {}),
    "max_files_deep": ("mining", "Skip deep per-file features above N files", "int", {}),
    "llm": ("llm", "LLM flags on", "bool", {}),
    "llm_model": ("llm", "Model", "text", {"example": "haiku"}),
    "llm_backend": ("llm", "Backend", "choice", {}),
    "llm_scope": ("llm", "Which rows get LLM calls", "choice", {}),
    "llm_sample_pct": ("llm", "Share of other rows still sent (%)", "int", {}),
    "close_reason_llm": ("llm", "When the LLM reads a close reason", "choice", {}),
    "llm_max_diff_chars": ("llm", "Diff size sent (characters)", "int", {}),
    "llm_diff_context": ("llm", "Context lines around each change", "int", {}),
    "no_scanners": ("features", "Code scanners (semgrep)", "flag", {"invert": True}),
    "no_api_contract": ("features", "API contract diff", "flag", {"invert": True}),
    "no_review_history": ("features", "Review history", "flag", {"invert": True}),
    "auto_remine": ("remine", "Re-mine stale repos at start", "bool", {}),
    "repos_file": ("locked", "Repos file", "text", {"locked": True}),
    "only_domain": ("locked", "Only this domain", "text", {"locked": True}),
    "split_by": ("locked", "Split balances by", "choice", {"locked": True}),
    "out": ("locked", "Output file", "text", {"locked": True}),
    "out_prefix": ("locked", "Output name prefix", "text", {"locked": True}),
    "out_postfix": ("locked", "Output name suffix", "text", {"locked": True}),
}


# plain-language help where the parser's text is aimed at someone typing flags
HELP = {
    "auto_remine": "At start, drop and mine again any repo this PC finished with an older miner or with blame failing. Leave it off to keep finished rows as they are.",
    "ci_history": "Reads every workflow run back to 2019 for the CI-fail rate. Hours on a big repo, and it uses the GitHub quota.",
    "szz": "Finds the commits that introduced bugs later fixed. Slow; cached after the first run.",
    "full_repo": "Every closed PR of each repo, not a date window or a cap. Cannot be combined with a window or a cap.",
    "cap_on": "What the PR cap counts: every PR, or only PRs from outside contributors. Only matters with a window and a cap.",
    "merge_outcome": "Also mine closed-unmerged PRs and add the merge-outcome labels. Off gives breakage rows only.",
    "llm_sample_pct": "With the scope set to training, the share of other rows still sent to the LLM (default 5).",
    "split_by": "What the split balances between PCs: repos' commit counts or PR counts. Every PC must use the same value.",
    "max_prs": "Keep only the newest N PRs of each repo. Empty means no cap.",
}


def _plain(text: str) -> str:
    """The parser's help without its flag-typing asides."""
    import re
    text = " ".join((text or "").replace("%%", "%").split())
    text = re.sub(r"\s*\(passed through\)", "", text)
    text = re.sub(r"\(passed through; ([^)]*)\)", r"(\1)", text)
    text = re.sub(r"[;,]?\s*reaches the miner as \w+", "", text)
    return re.sub(r"\s*\(passed through\)", "", text).strip()


def build() -> dict:
    sys.path.insert(0, HERE)
    import build_dataset
    import join
    seen, settings = set(), []
    actions = {a.dest: a for a in build_dataset.build_parser()._actions if a.dest in join.SHARED_DESTS}
    missing = sorted(join.SHARED_DESTS - set(META))
    if missing:
        raise SystemExit(f"settings_schema.py: no entry in META for {', '.join(missing)}")
    for gi, g in enumerate(GROUPS):
        for dest, (group, label, kind, extra) in META.items():
            if group != g["id"] or dest in seen:
                continue
            seen.add(dest)
            a = actions[dest]
            flag = next(o for o in a.option_strings if not o.startswith("--no-")) if kind == "bool" else a.option_strings[0]
            item = {"dest": dest, "flag": flag, "kind": kind, "label": label, "group": group,
                    "help": HELP.get(dest) or _plain(a.help)}
            if kind == "bool":
                item["default"] = bool(a.default)
                item["off_flag"] = next(o for o in a.option_strings if o.startswith("--no-"))
            elif kind == "flag":
                item["invert"] = bool(extra.get("invert"))
            elif kind == "choice":
                item["choices"] = list(a.choices)
                item["default"] = a.default
            else:
                item["default"] = a.default
            for k in ("empty", "example", "locked"):
                if k in extra:
                    item[k] = extra[k]
            settings.append(item)
    return {"version": 1, "groups": GROUPS, "settings": settings}


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="refresh the committed copy the admin page serves")
    args = ap.parse_args()
    text = render()
    if args.write:
        with open(SCHEMA_PATH, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {SCHEMA_PATH}")
    else:
        sys.stdout.write(text)
