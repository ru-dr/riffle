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
# What a code may carry. Values go into .env and the screen, so nothing with a line break or control character gets in.
URL_OK = re.compile(r"^https?://[^\s\x00-\x1f\x7f]+\Z")
TOKEN_OK = re.compile(r"^[A-Za-z0-9_-]{4,200}\Z")
SPLIT_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}\Z")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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
        assert isinstance(doc, dict) and all(k in doc for k in ("u", "t", "s", "k", "n"))
        assert type(doc["k"]) is int and type(doc["n"]) is int and 1 <= doc["k"] <= doc["n"] <= 200
        assert isinstance(doc["u"], str) and URL_OK.match(doc["u"]) and isinstance(doc["t"], str) and TOKEN_OK.match(doc["t"])
        assert isinstance(doc["s"], str) and SPLIT_OK.match(doc["s"])
        argv = doc.get("a", [])
        assert isinstance(argv, list) and all(isinstance(a, str) and not CONTROL.search(a) for a in argv)
        assert doc.get("v") is None or (isinstance(doc["v"], str) and not CONTROL.search(doc["v"]))
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
        if CONTROL.search(str(val)):
            raise ValueError(f"{key} has a line break or control character in it; not written to .env")
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
           "no_api_contract", "no_scanners", "no_review_history", "pr_discovery", "split_by", "shard")


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


STATE_FILE = ".join.json"


def save_state(path: str, doc: dict) -> None:
    """Remember the join code's contents (the lead's flags, this PC's number) beside .env, so a restart of the TUI
    still shows and enforces the lead's settings. It holds the write token too, so it is private like .env."""
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(path + ".tmp", path)


def load_state(path: str) -> dict | None:
    """The remembered join, or None (missing or damaged files are ignored, never an error)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        assert all(k in doc for k in ("u", "s", "k", "n")) and isinstance(doc.get("a", []), list)
        return doc
    except (OSError, ValueError, AssertionError):
        return None


def clear_state(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


REPOS_DIR = "joined_repos"
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def apply_split(doc: dict, meta: dict | None, base: str) -> dict:
    """Use the lead's repo list: write it under <base>/joined_repos/<split>/<name> and point --repos-file at that copy, so
    this PC mines exactly the lead's list whatever is (or is not) in its own checkout. `meta` is what the store
    returns (sync.fetch_split); without it (an older store) the code's own flags stand. Returns a new doc with
    `lead`: who issued it and what it holds. Nothing from the store is trusted for a path: names are checked here too."""
    doc = dict(doc)
    lead = {"issued_by": None, "created": None, "version": doc.get("v"), "repos_name": None, "n_repos": None, "expires": None}
    if meta:
        lead.update(issued_by=meta.get("issued_by"), created=meta.get("created"), version=meta.get("version") or doc.get("v"),
                    repos_name=meta.get("repos_name"), n_repos=meta.get("n_repos"), expires=meta.get("expires"))
        name, text, split = meta.get("repos_name"), meta.get("repos_text"), str(doc.get("s", ""))
        if isinstance(text, str) and text.strip() and isinstance(name, str) and _SAFE.match(name) and _SAFE.match(split):
            folder = os.path.join(base, REPOS_DIR, split)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(path + ".tmp", path)
            rel = os.path.join(REPOS_DIR, split, name)
            flags, out, i, found = list(doc.get("a", [])), [], 0, False
            while i < len(flags):
                tok = flags[i]
                if tok == "--repos-file":
                    out += [tok, rel]
                    i += 2
                    found = True
                    continue
                if tok.startswith("--repos-file="):
                    out.append(f"--repos-file={rel}")
                    i += 1
                    found = True
                    continue
                out.append(tok)
                i += 1
            if not found:
                out += ["--repos-file", rel]
            doc["a"] = out
            lead["local_repos_file"] = rel
    doc["lead"] = lead
    return doc


def describe(doc: dict, base: str) -> dict:
    """The lead's settings in readable form for a volunteer: {settings: [(label, value)], repos: [(name, weight, pc)],
    mine: [names], lead: {...}}. Reads the local copy of the lead's repo list to say who mines what."""
    import build_dataset
    argv = list(doc.get("a", [])) + ["--shard", f"{doc['k']}/{doc['n']}"]
    s = shaping(argv)
    lead = doc.get("lead") or {}
    rows = [("Repos file", lead.get("repos_name") or s["repos_file"]),
            ("Only repos tagged", s["only_domain"] or "all"),
            ("Window", f"{s['window_from']} to {s['window_to']}" if s["window_from"] else "none (newest PRs)"),
            ("Max PRs per repo", "all" if s["full_repo"] or s["max_prs"] is None else s["max_prs"]),
            ("Skip PRs merged in last", f"{s['min_age_days']} days"),
            ("Split repos between PCs by", "PR count" if s.get("split_by") == "prs" else "commit count"),
            ("LLM flags", "on" if s["llm"] else "off"), ("SZZ label", "on" if s["szz"] else "off"),
            ("CI history", "on" if s["ci_history"] else "off"), ("Snapshots", "on" if s["snapshots"] else "off"),
            ("Scanners", "off" if s["no_scanners"] else "on"), ("Merge outcome", "on" if s["merge_outcome"] else "off")]
    repos, mine = [], []
    try:
        shares = build_dataset.plan_shares(os.path.join(base, s["repos_file"]), s["only_domain"], doc["n"], s.get("split_by") or "commits")
        for k, share in enumerate(shares, 1):
            for name, w in share:
                repos.append((name, w, k))
                if k == doc["k"]:
                    mine.append(name)
    except (OSError, ValueError):
        pass
    return {"settings": rows, "repos": repos, "mine": mine, "lead": lead}


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
    state = os.path.join(HERE, STATE_FILE)
    before = load_state(state)
    try:                                                    # anything that can fail first; the join code before the token
        doc = apply_split(doc, sync.fetch_split(doc["u"], doc["t"]), HERE)
        save_state(state, doc)
        write_env(os.path.join(HERE, ".env"), {"RIFFLE_SYNC_URL": doc["u"], "RIFFLE_SYNC_TOKEN": doc["t"]})
    except (OSError, ValueError) as e:
        save_state(state, before) if before else clear_state(state)
        print(f"Could not finish joining ({type(e).__name__}: {e}). Nothing was changed; try again.", file=sys.stderr)
        return 1
    if doc["lead"].get("local_repos_file"):
        print(f"The lead's repo list ({doc['lead'].get('repos_name')}, {doc['lead'].get('n_repos')} repos) is saved at "
              f"{doc['lead']['local_repos_file']}; the command below uses it, so you need no matching file of your own.")
    print(f"Saved to .env as {r['name']}. Your output goes to the store after each repo and when you stop.")
    print("Run this on this PC, exactly (every PC must use the same flags):")
    print("  " + command(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
