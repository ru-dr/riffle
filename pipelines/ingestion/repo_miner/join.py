"""
join.py — join a split with the code the lead gave you (ADR 0015).

  python join.py riffle1.eyJ1Ijoi...      write the store URL and your token into .env, check them
  python join.py --check                 check what .env has now
  python join.py CODE --print            decode and show it, change nothing

The lead makes one code per PC with `build_dataset.py --shard-plan N --issue-tokens`. It
holds the store's address, a write token for this PC alone (it works for that split only),
this PC's number, and the exact command to run, so every machine uses the same flags. The
code is a secret: anyone who has it can upload as this PC. Do not paste it in chat; delete
the message after joining. The miner needs nothing else to send its output.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import sys

import config  # noqa: F401  (loads .env)
import sync
from version import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "riffle1."


def encode(url: str, token: str, split: str, k: int, n: int, argv: list[str]) -> str:
    doc = {"u": url, "t": token, "s": split, "k": k, "n": n, "v": __version__, "a": argv}
    return PREFIX + base64.urlsafe_b64encode(json.dumps(doc, separators=(",", ":")).encode()).decode().rstrip("=")


def decode(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(PREFIX):
        raise ValueError("that is not a join code (it starts with riffle1.)")
    body = code[len(PREFIX):]
    try:
        doc = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert all(k in doc for k in ("u", "t", "s", "k", "n")) and isinstance(doc["k"], int) and isinstance(doc["n"], int)
        assert 1 <= doc["k"] <= doc["n"] and re.match(r"^https?://", doc["u"]) and isinstance(doc.get("a", []), list)
    except (ValueError, AssertionError, KeyError):
        raise ValueError("the join code is damaged; copy it again, all of it") from None
    return doc


def write_env(path: str, values: dict[str, str]) -> None:
    """Set KEY=value lines in .env, keeping every other line, and keep the file private."""
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    for key, val in values.items():
        row = f"{key}={val}"
        hit = [i for i, line in enumerate(lines) if re.match(rf"^\s*{re.escape(key)}\s*=", line)]
        if hit:
            lines[hit[0]] = row
            for i in reversed(hit[1:]):
                del lines[i]
        else:
            lines.append(row)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


# The flags that change what is mined or which columns come out. Every PC must agree on them, or
# the merge refuses the pieces (and a split-bound token turns sync off). The rest (clone dir, workers,
# output name, resume switches, sync) are about this machine and may differ.
SHAPING = ("repos_file", "only_domain", "max_prs", "min_age_days", "window_from", "window_to", "cap_on",
           "lead_in_days", "full_repo", "llm", "llm_scope", "close_reason_llm", "llm_model", "llm_sample_pct",
           "max_unmerged", "merge_outcome", "declared_labels", "szz", "ci_history", "snapshots",
           "no_api_contract", "no_scanners", "no_review_history", "pr_discovery", "shard")


# what a join code carries: the shaping settings plus where the file goes. Machine settings (workers, clone
# dir, resume switches) and the PC number are left out; each PC picks its own.
SHARED_DESTS = {k for k in SHAPING if k != "shard"} | {"out", "out_prefix", "out_postfix"}


def shared_argv(argv: list[str]) -> list[str]:
    """The part of a command line every PC should run with: the TUI's whole command, filtered."""
    import build_dataset
    actions = build_dataset.build_parser()._option_string_actions
    out, i = [], 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        action = actions.get(name)
        takes_value = bool(action and action.nargs != 0)
        step = 1 + (1 if takes_value and "=" not in tok else 0)
        if action is not None and action.dest in SHARED_DESTS:
            out += argv[i:i + step]
        i += step
    return out


def shaping(argv: list[str]) -> dict:
    """The dataset-shaping settings a command line means, parsed the way the miner parses it."""
    import build_dataset
    try:
        ns = build_dataset.build_parser().parse_known_args(list(argv))[0]
    except SystemExit:
        raise ValueError("could not read the lead's flags") from None
    return {k: getattr(ns, k) for k in SHAPING}


def differences(lead_argv: list[str], my_argv: list[str]) -> list[tuple[str, object, object]]:
    """(setting, lead's, yours) for each shaping setting that differs. A setting the lead left at
    the miner's own default (None, e.g. --llm-model) is not compared."""
    lead, mine = shaping(lead_argv), shaping(my_argv)
    return [(k, lead[k], mine[k]) for k in SHAPING if lead[k] is not None and lead[k] != mine[k]
            or (lead[k] is None and k in ("max_prs", "window_from", "window_to", "only_domain") and mine[k] is not None)]


def command(doc: dict) -> str:
    return "python build_dataset.py " + " ".join(shlex.quote(a) for a in doc.get("a", []) + ["--shard", f"{doc['k']}/{doc['n']}"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", nargs="?")
    ap.add_argument("--check", action="store_true", help="check the URL and token in .env")
    ap.add_argument("--print", dest="show", action="store_true", help="decode the code and show it; change nothing")
    args = ap.parse_args(argv)
    if args.check or not args.code:
        r = sync.whoami()
        if r["ok"]:
            print(f"OK: signed in as {r['name']} (split {r.get('split') or 'any'}); output will go to the team store")
            return 0
        print(f"NOT READY: {r['why']}", file=sys.stderr)
        return 1 if not r.get("unset") else 2
    try:
        doc = decode(args.code)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"PC {doc['k']} of {doc['n']}, split {doc['s']}, store {doc['u']}, miner {doc.get('v', '?')}")
    if doc.get("v") and doc["v"] != __version__:
        print(f"WARNING: this code is for miner {doc['v']} and you have {__version__}. Run `git pull` first; "
              f"the merge refuses mixed versions.", file=sys.stderr)
    if args.show:
        print("command:", command(doc))
        return 0
    r = sync.whoami(doc["u"], doc["t"])
    if not r["ok"]:
        print(f"Not saved: {r['why']}. Ask the lead for a new code.", file=sys.stderr)
        return 1
    write_env(os.path.join(HERE, ".env"), {"RIFFLE_SYNC_URL": doc["u"], "RIFFLE_SYNC_TOKEN": doc["t"]})
    print(f"Saved to .env as {r['name']}. Your output goes to the store after each repo and when you stop.")
    print("Run this on this PC, exactly (every PC must use the same flags):")
    print("  " + command(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
