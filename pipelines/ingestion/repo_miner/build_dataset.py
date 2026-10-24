#!/usr/bin/env python3
"""
build_dataset.py — clone a list of GitHub repos and mine them all into ONE
growing dataset file.

Reads GitHub URLs (one per line) from a repos file, clones each into the clone
directory (removing any existing copy first — a fresh FULL clone, since the
extractor needs complete history; do not use --depth), then runs mine_repo.py on
each with --append so every repo's rows accumulate in a single output file,
de-duplicated by (repo, pr_number).

Usage:
    python build_dataset.py
        # reads repos.txt -> writes dataset.parquet, cloning into cloned_repos\\

    python build_dataset.py --repos-file repos.txt --out dataset.parquet ^
        --clone-dir cloned_repos --max-prs 200 [--ci-history] [--llm] [--skip-existing]

Flags:
    --repos-file   file with one GitHub URL per line (# comments allowed)
    --out          single dataset file rows are appended to (.parquet/.csv/.jsonl)
    --clone-dir    where repos are cloned (created if missing)
    --max-prs N    cap PRs per repo (passed through to the miner)
    --ci-history   build the historical CI-fail-rate features (slow; passed through)
    --llm          enable the LLM semantic flags (headless Claude Code by default; passed through)
    --skip-existing  reuse an already-cloned repo instead of re-cloning (faster re-runs)
    --keep-clones  do NOT delete each clone after mining it (default: delete, so
                   only one repo's clone is on disk at a time)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time

import naming
import progress
import runlog
import sync
from version import __version__


TEST_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_output", "test_dataset.jsonl")


_WEIGHT_RE = re.compile(r"\[~?([\d,]+) commits\]")
_PRS_RE = re.compile(r"\[~?([\d,]+) PRs\]")
_DOMAIN_RE = re.compile(r"\[\s*domain\s*:([^\]]*)\]", re.IGNORECASE)
_DOMAIN_OK = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_WARNED: set[str] = set()                                   # one warning per distinct problem, however often a line is parsed


def _warn_once(msg: str) -> None:
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"[repos] WARNING: {msg}", flush=True)


def _parse_domain(line: str) -> str | None:
    """Dataset domain from a repos.txt line's "[domain: web-ts]" tag (ADR 0011), lower-cased
    ("[Domain: Web-TS]" is the same tag). A tag that is not a valid name is warned about and
    read as no tag, so a typo does not drop a repo from --only-domain unnoticed."""
    m = _DOMAIN_RE.search(line)
    if not m:
        return None
    val = m.group(1).strip().lower()
    if not _DOMAIN_OK.match(val):
        _warn_once(f"malformed domain tag {m.group(0)!r} in {line.split('#', 1)[0].strip() or line.strip()!r}; "
                   f"read as untagged (a name is letters, digits and hyphens)")
        return None
    return val


def _parse_weight(line: str) -> int | None:
    """Approx. commit count from a repos.txt line's "[~N commits]" tag."""
    m = _WEIGHT_RE.search(line)
    return int(m.group(1).replace(",", "")) if m else None


def _parse_prs(line: str) -> int | None:
    """Total PR count from a repos.txt line's "[N PRs]" tag. Shown in the shard
    plan only; the split itself balances on commits."""
    m = _PRS_RE.search(line)
    return int(m.group(1).replace(",", "")) if m else None


def balanced_shards(weights: list[int | None], n: int) -> list[list[int]]:
    """
    Split item indices into n shards with EQUAL repo counts (differing by at
    most 1) and balanced total weight (approx. commits: drives clone, history
    walk and SZZ cost; with a fixed --max-prs the API/LLM cost per repo is the
    same). Greedy: heaviest repo first, each to the lightest shard that still
    has room. Deterministic, so every machine computes the same split.
    Unknown weights count as the median.
    """
    known = sorted(w for w in weights if w)
    median = known[len(known) // 2] if known else 1
    w = [x or median for x in weights]
    base, extra = divmod(len(w), n)
    caps = [base + (1 if k < extra else 0) for k in range(n)]
    shards: list[list[int]] = [[] for _ in range(n)]
    totals = [0] * n
    for i in sorted(range(len(w)), key=lambda i: (-w[i], i)):
        k = min((k for k in range(n) if len(shards[k]) < caps[k]),
                key=lambda k: (totals[k], k))
        shards[k].append(i)
        totals[k] += w[i]
    return [sorted(sh) for sh in shards]           # keep file order within a shard


def split_id(entries, weights, n: int, by: str = "commits") -> str:
    """Short fingerprint of everything the split depends on (ordered repo list,
    the sizes it balances, PC count and what it balances by). PCs that print the same ID got disjoint shares
    of the same list; a different ID means a different repos.txt, PC count or --split-by."""
    import hashlib
    key = "\n".join(f"{o}/{r}={w}" for (o, r, _), w in zip(entries, weights)) + f"\n{n}" + ("" if by == "commits" else f"\nby={by}")
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def _warn_untagged(entries, weights, by: str = "commits") -> None:
    """Repos without the tag the split balances on ([~N commits] or [N PRs]) count as the median size, so
    the split can drift; say so instead of splitting quietly wrong."""
    missing = [f"{o}/{r}" for (o, r, _), w in zip(entries, weights) if not w]
    if missing:
        tag = "[~N commits]" if by == "commits" else "[N PRs]"
        print(f"[shard] WARNING: {len(missing)} repo(s) have no {tag} tag and "
              f"count as median size, so the split may be unbalanced: "
              f"{', '.join(missing)}. Add a tag" + (", e.g. from `git rev-list --count HEAD`." if by == "commits" else "."),
              flush=True)


def _print_shard_plan(entries, weights, n: int, prs=None, by: str = "commits") -> str:
    prs = prs or [None] * len(entries)
    basis = prs if by == "prs" else weights                 # what the split balances
    _warn_untagged(entries, basis, by)
    shards = balanced_shards(basis, n)
    totals = [sum(weights[i] or 0 for i in sh) for sh in shards]
    sid = split_id(entries, basis, n, by)
    bt = [sum(basis[i] or 0 for i in sh) for sh in shards]
    print(f"[shard-plan] split ID {sid}: {len(entries)} repos over {n} PCs, balanced by {by} "
          f"(heaviest/lightest PC: {max(bt) / max(1, min(bt)):.2f}x)")
    for k, sh in enumerate(shards, 1):
        pr_total = sum(prs[i] or 0 for i in sh)
        pr_note = f", {pr_total:,} PRs" if any(prs[i] for i in sh) else ""
        print(f"  PC {k}/{n}: {len(sh)} repos, ~{totals[k - 1]:,} commits{pr_note}")
        for i in sh:                                  # one repo per line
            w = f"~{weights[i]:,} commits" if weights[i] else "untagged"
            if prs[i]:
                w += f", {prs[i]:,} PRs"
            print(f"    - {entries[i][0]}/{entries[i][1]}  ({w})")
    return sid


PLAN_ONLY_FLAGS = {"--shard-plan": 1, "--issue-tokens": 0, "--token-days": 1, "--tokens-out": 1}


def _run_flags(argv: list[str]) -> list[str]:
    """This command line without the flags that only make the plan: what every PC should run
    (join.py adds --shard K/N)."""
    out, i = [], 0
    while i < len(argv):
        name = argv[i].split("=", 1)[0]
        if name in PLAN_ONLY_FLAGS:
            i += 1 + (PLAN_ONLY_FLAGS[name] if "=" not in argv[i] else 0)
            continue
        out.append(argv[i])
        i += 1
    return out


def _issue_tokens(args, sid: str, n: int) -> None:
    """Ask the team store for one write token per PC, bound to this split, and print each
    PC's join code. Needs RIFFLE_SYNC_URL and RIFFLE_ADMIN_TOKEN (the lead's admin token)."""
    import urllib.error
    import urllib.request
    import join
    url, admin = (os.environ.get("RIFFLE_SYNC_URL") or "").rstrip("/"), os.environ.get("RIFFLE_ADMIN_TOKEN") or ""
    if not url or not admin:
        sys.exit("[tokens] set RIFFLE_SYNC_URL and RIFFLE_ADMIN_TOKEN (an admin token from the store: "
                 "`manage.py add-admin NAME`) in .env to issue tokens")
    flags = _run_flags(sys.argv[1:])
    repos_file = getattr(args, "repos_file", None)
    repos_text = None
    if repos_file:
        try:                                 # the repo list itself travels, so another PC does not need the same file
            with open(repos_file, encoding="utf-8") as fh:
                repos_text = fh.read()
        except OSError:
            pass
    payload = {"split_id": sid, "n": n, "days": args.token_days, "flags": flags, "miner_version": __version__}
    if repos_text is not None:
        payload.update(repos_name=os.path.basename(repos_file), repos_text=repos_text)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{url}/v1/admin/splits", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {admin}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            pcs = json.loads(r.read())["pcs"]
    except urllib.error.HTTPError as e:
        sys.exit(f"[tokens] the store refused: HTTP {e.code} {e.read()[:200].decode('utf-8', 'replace')}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"[tokens] cannot reach the store: {e}")
    print(f"\n[tokens] one write token per PC, valid for split {sid} only"
          + (f", for {args.token_days:g} days" if args.token_days else "") + ". Send each code ONLY to that PC's owner:")
    lines = []
    for p in pcs:
        code = join.encode(url, p["token"], sid, p["k"], p["n"], flags)
        lines.append(f"PC {p['k']}/{p['n']}\t{code}")
        print(f"\n  PC {p['k']} of {p['n']}  ({p['pc']})\n  python join.py {code}")
    if args.tokens_out:
        fd = os.open(args.tokens_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"\n[tokens] also saved to {args.tokens_out} (private to you). Delete it when the codes are sent.")
    print("\n[tokens] running this again for the same split issues new tokens and revokes the old ones.")


def plan_shares(repos_file: str, only_domain: str | None, n: int, by: str = "commits") -> list[list[tuple[str, int | None]]]:
    """Who mines what: the repos file split over n PCs the way a run splits it, as [(owner/name, commits tag)] per PC
    (index 0 is PC 1). Used to show a volunteer which repos are theirs."""
    with open(repos_file, encoding="utf-8") as fh:
        lines = _dedupe_lines([line for line in fh if _parse_url(line)])
    if only_domain:
        lines = [ln for ln in lines if _parse_domain(ln) == only_domain.strip().lower()]
    entries = [_parse_url(ln) for ln in lines]
    weights = [_parse_weight(ln) for ln in lines]
    basis = [_parse_prs(ln) for ln in lines] if by == "prs" else weights
    return [[(f"{entries[i][0]}/{entries[i][1]}", weights[i]) for i in sh] for sh in balanced_shards(basis, n)]


def _mined_repos(out: str) -> dict[str, int]:
    """Lower-cased "owner/name" of every repo already in the dataset, so a
    rerun after a stop skips finished repos. A repo only lands in --out after
    a clean, complete mine (a stopped one stays in its .partial.jsonl
    checkpoint), so presence here means done. Checks the .jsonl fallback too.
    Returns {repo: rows already mined}, so the TUI can count them as done."""
    found: dict[str, int] = {}
    cands = [out]
    if out.endswith(".parquet"):
        cands.append(out.rsplit(".", 1)[0] + ".jsonl")
    for path in cands:
        if not os.path.isfile(path):
            continue
        try:
            if path.endswith(".parquet"):
                import pyarrow.parquet as pq
                for r in pq.read_table(path, columns=["repo"]).column("repo").to_pylist():
                    if r:
                        found[str(r).lower()] = found.get(str(r).lower(), 0) + 1
            else:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            r = json.loads(line).get("repo")
                            if r:
                                found[str(r).lower()] = found.get(str(r).lower(), 0) + 1
        except Exception as e:                              # noqa: BLE001
            print(f"[resume] couldn't read {path} ({e}); not skipping any repo", flush=True)
            return {}
    return found


def _write_test_parquet(jsonl_path: str) -> None:
    """Test runs also save the same rows as parquet (what a real run writes),
    so the parquet path can be checked before mining the full corpus."""
    out = jsonl_path.rsplit(".", 1)[0] + ".parquet"
    try:
        import pandas as pd
        df = pd.read_json(jsonl_path, lines=True, dtype=False)
        df.to_parquet(out, index=False)
        back = pd.read_parquet(out)
        print(f"[test] parquet: {out} ({back.shape[0]} rows x {back.shape[1]} cols)", flush=True)
    except ImportError:
        print("[test] parquet skipped: pip install pandas pyarrow", flush=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"[test] parquet FAILED: {type(e).__name__}: {e}", flush=True)


_NAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def _valid_name(part: str) -> bool:
    """An owner or repo name that is safe as a path component and in a clone URL: GitHub's own
    alphabet, and never "." or ".." (they would walk out of the clone directory) or a leading "-" (an option to git)."""
    return bool(_NAME_OK.match(part)) and part not in (".", "..") and not part.startswith("-")


def _parse_url(line: str):
    """(owner, name, url) from a GitHub URL line, or None for blanks/comments/bad. Only the first two
    path segments count, so a pasted browser URL (.../tree/main, ?tab=readme) names its repo; owner and
    name must be GitHub-legal (_valid_name), anything else is not a repo line."""
    url = line.split("#", 1)[0].strip()       # drop inline "# Train" style comments
    if not url:
        return None
    m = re.search(r"github\.com[:/]+([^/\s?#]+)/([^/\s?#]+)(?:[/?#]\S*)?$", url)
    if not m:
        return None
    owner, name = m.group(1), m.group(2)
    if name.endswith(".git"):
        name = name[:-4]
    if not (_valid_name(owner) and _valid_name(name)):
        return None
    return owner, name, url


def _canonical_url(owner: str, name: str) -> str:
    """What git clones from: the anchored https URL of the validated owner/name, not the line as written
    (which may carry a scheme or helper git would honour, or trailing text)."""
    return f"https://github.com/{owner}/{name}"


def _safe_path(base: str, *parts: str) -> str | None:
    """base/parts joined, or None unless its real path stays inside base (a name such as ".." or a
    symlink planted in the directory must not make a clone, rmtree or cache write land elsewhere)."""
    root = os.path.realpath(base)
    path = os.path.realpath(os.path.join(root, *parts))
    return path if path != root and path.startswith(root + os.sep) else None


def _dedupe_lines(lines: list[str]) -> list[str]:
    """Drop a repo listed twice (owner/name compared case-insensitively, the way GitHub names them),
    keeping the first and saying so: a duplicate would be cloned into the same directory twice."""
    seen: dict[str, str] = {}
    out = []
    for ln in lines:
        o, n, _ = _parse_url(ln)
        key = f"{o}/{n}".lower()
        if key in seen:
            _warn_once(f"{o}/{n} is listed twice (first as {seen[key]}); keeping the first")
            continue
        seen[key] = f"{o}/{n}"
        out.append(ln)
    return out


def _rmtree(path: str) -> None:
    """Remove a tree, handling Windows' read-only .git files."""
    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    shutil.rmtree(path, onerror=onerror)


# Blame-derived columns. A repo mined while blame was failing (before v1.6.0,
# for repos that ship .git-blame-ignore-revs) has these all empty or zero.
_BLAME_COLS = ("max_file_top_author_share", "max_file_minor_contributor_count",
               "min_mod_line_age_days_min", "max_mod_lines_distinct_authors")


def _read_out(out: str):
    """The dataset as a pandas DataFrame (parquet, or the .jsonl fallback)."""
    import pandas as pd
    for path in ([out] + ([out.rsplit(".", 1)[0] + ".jsonl"] if out.endswith(".parquet") else [])):
        if os.path.isfile(path):
            return path, (pd.read_parquet(path) if path.endswith(".parquet")
                          else pd.read_json(path, lines=True))
    return None, None


def _audit(out: str) -> list[str]:
    """Blame coverage per repo, measured only on PRs that touch files that
    already existed (AGE is set): a PR that only adds new files has no history
    to blame, so it can't count against the repo. Among the rest coverage
    should be ~100%. Returns the repos to re-mine (coverage under 50%)."""
    path, df = _read_out(out)
    if df is None or df.empty:
        print(f"[audit] nothing in {out}")
        return []
    cols = [c for c in _BLAME_COLS if c in df.columns]
    bad, check = [], []
    print(f"[audit] {path}: {len(df)} rows, {df['repo'].nunique()} repos")
    print(f"  {'repo':40s} {'rows':>5s}  {'new-only':>8s}  {'blame':>6s}  {'szz+':>4s}")
    for repo, g in df.groupby("repo"):
        has = (g[cols].fillna(0) != 0).any(axis=1) if cols else pd_false(g)
        existing = g["AGE"].notna() if "AGE" in g else has | True
        n_new = int((~existing).sum())
        cov = float(has[existing].mean()) if existing.any() else float("nan")
        szz = int(g["label_szz_bug"].fillna(False).astype(bool).sum()) if "label_szz_bug" in g else 0
        note = ""
        if existing.sum() >= 10 and cov < 0.5:
            bad.append(repo); note = "   <- re-mine"
        elif existing.sum() >= 10 and cov < 0.95:
            check.append(repo); note = "   <- check"
        cov_s = "   n/a" if cov != cov else f"{cov:6.0%}"
        print(f"  {repo:40s} {len(g):5d}  {n_new:8d}  {cov_s}  {szz:4d}{note}")
    print("  (blame = share of PRs touching existing files that got blame data; "
          "new-only = PRs that only add files, which have no history to blame)")
    if bad:
        print("[audit] re-mine: blame was failing for these. Fix with:\n  python build_dataset.py "
              + " ".join(f"--remine-repo {r}" for r in bad) + " <your usual flags>")
    if check:
        print("[audit] check: some blames failed for " + ", ".join(check) + ". Look for "
              "'[blame] warning' in logs/run-*.log; one-off failures (a corrupt object, "
              "an LFS file) are harmless, a pattern means re-mine after fixing the cause")
    if not bad and not check:
        print("[audit] every repo has blame data where there was history to blame")
    return bad


def pd_false(g):
    import pandas as pd
    return pd.Series(False, index=g.index)


def _save_llm_reuse(rows, path: str, repo: str) -> None:
    """Keep the LLM answers of rows about to be dropped, keyed by their commits,
    so the re-mine reuses them instead of calling the LLM again. Their blame
    features were wrong; their LLM answers weren't."""
    from features import llm_flags
    cols = [c for c in llm_flags.FLAG_SCHEMA if c in rows.columns]
    if not cols or rows.empty:
        return
    recs = []
    for r in rows.to_dict("records"):
        flags = {c: (None if r.get(c) is None or r.get(c) != r.get(c) else r.get(c)) for c in cols}
        if r.get("base_sha") and r.get("head_sha") and any(v is not None for v in flags.values()):
            rec = {"base_sha": r["base_sha"], "head_sha": r["head_sha"]}
            for key, col in (("prompt_version", "llm_prompt_version"), ("model", "llm_model")):
                v = r.get(col)                              # what made the answer: load_reuse reuses it only for the same prompt and model
                if isinstance(v, str) and v:
                    rec[key] = v
            recs.append({**rec,
                         "flags": {k: (v.item() if hasattr(v, "item") else v) for k, v in flags.items()}})
    if recs:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(recs, fh)
        print(f"[remine] {repo}: kept {len(recs)} LLM answers to reuse ({path})", flush=True)


def _drop_repo(out: str, repo: str, cache_dir: str = "ci_cache") -> None:
    """Remove one repo's rows from the dataset (atomic rewrite), plus its SZZ
    cache and checkpoint lines, so the next run mines it from scratch."""
    key = repo.lower()
    owner, _, name = repo.partition("/")
    if not (_valid_name(owner) and _valid_name(name)):
        raise ValueError(f"not a repo name: {repo!r}")
    path, df = _read_out(out)
    if df is not None and "repo" in df.columns:
        mine = df[df["repo"].str.lower() == key]
        _save_llm_reuse(mine, os.path.join(cache_dir, f"{owner}_{name}.llm_reuse.json"), repo)
        keep = df[df["repo"].str.lower() != key]
        n = len(df) - len(keep)
        if n:
            tmp = path + ".tmp"
            if path.endswith(".parquet"):
                keep.to_parquet(tmp, index=False)
            else:
                keep.to_json(tmp, orient="records", lines=True)
            os.replace(tmp, path)
        print(f"[remine] {repo}: removed {n} rows from {path}", flush=True)
    for f in (os.path.join(cache_dir, f"{owner}_{name}.szz.json"),
              os.path.join(cache_dir, f"{owner}_{name}.szz.json.partial.jsonl"),
              os.path.join(cache_dir, f"{owner}_{name}.szz.json.blames.jsonl"),
              os.path.join(cache_dir, f"{owner}_{name}.selection.json")):
        if os.path.isfile(f):
            os.remove(f)
            print(f"[remine] {repo}: removed {f}", flush=True)
    ckpt = out + ".partial.jsonl"
    if os.path.isfile(ckpt):
        with open(ckpt, "rb") as fh:
            lines = fh.readlines()
        # a line of another repo, and a damaged one that names no repo, stay: only this repo's rows go
        kept = [l for l in lines if _line_repo(l) != key]
        if len(kept) != len(lines):
            tmp = ckpt + ".tmp"
            with open(tmp, "wb") as fh:
                fh.writelines(kept)
            os.replace(tmp, ckpt)
            print(f"[remine] {repo}: removed {len(lines) - len(kept)} checkpointed rows", flush=True)


# --- skipping -----------------------------------------------------------------
# A repo can be skipped up front (--skip-big N: more than N commits by its
# repos.txt tag) or on request while the batch runs: the TUI writes repo names
# to the file named by RIFFLE_CONTROL, one per line. A skip never writes rows to
# --out, so a later run without it mines the repo as normal (a repo stopped
# mid-mine keeps its checkpoint and resumes).
_CONTROL = os.getenv("RIFFLE_CONTROL")


def _skip_requested(repo: str) -> bool:
    """Is `repo` named in the skip-control file? The TUI rewrites that file (truncate, then write), so a
    read can land on an empty file between the two: an empty read is tried once more before it counts."""
    if not _CONTROL:
        return False
    for attempt in (0, 1):
        try:
            with open(_CONTROL, encoding="utf-8") as fh:
                names = {l.strip().lower() for l in fh if l.strip()}
        except OSError:
            return False
        if names or attempt:
            return repo.lower() in names
        time.sleep(0.05)
    return False


def _own_group(proc) -> bool:
    """True when `proc` leads a process group of its own (started with start_new_session), so a signal to the
    group reaches its git and semgrep children too and never us."""
    try:
        return os.name != "nt" and os.getpgid(proc.pid) == proc.pid != os.getpgrp()
    except OSError:
        return False


def _signal_child(proc, sig: int) -> None:
    try:
        if _own_group(proc):
            os.killpg(proc.pid, sig)
        else:
            proc.send_signal(sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _stop_child(proc, signal) -> None:
    """SIGINT the miner (its whole process group, so git and semgrep grandchildren go too) so it keeps its
    checkpoint; kill the group after 30 s. A second Stop while waiting kills it at once and is passed on:
    the batch must stop, not carry on to the next repo."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            _signal_child(proc, signal.SIGINT)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            _signal_child(proc, signal.SIGKILL)
        proc.kill()
        proc.wait()
    except KeyboardInterrupt:          # a second Stop: don't wait, and don't swallow it
        if os.name != "nt":
            _signal_child(proc, signal.SIGKILL)
        proc.kill()
        proc.wait()
        raise


def _clone_stall_s() -> float | None:
    """Seconds of silence after which a clone is killed (--clone-stall-s / RIFFLE_CLONE_STALL_S, default 600; 0 = never)."""
    import gitio
    return gitio._env_seconds("RIFFLE_CLONE_STALL_S", gitio.CLONE_STALL_S)


def _run_watched(cmd: list[str], repo: str, stall_s: float | None = None) -> tuple[int, bool]:
    """Run cmd, stopping it if a skip is requested for repo meanwhile.
    Returns (exit code, skipped). The miner gets SIGINT first so it stops
    cleanly with its checkpoint, then is killed if it doesn't exit. With `stall_s` (a clone, which
    prints progress on stderr) the process is also killed after that long without any output; a
    stalled one returns exit code -9. The miner runs in its own process group, so a Stop reaches its children."""
    import signal
    import threading
    kw: dict = {"creationflags": 0x00000200} if os.name == "nt" else {"start_new_session": True}   # Windows: its own group, so CTRL_BREAK reaches it alone
    last = [time.monotonic()]
    if stall_s:
        kw.update(stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    proc = subprocess.Popen(cmd, **kw)
    if stall_s:
        def _pump():
            out = getattr(sys.stderr, "buffer", None)
            while True:
                chunk = proc.stderr.read1(4096) if hasattr(proc.stderr, "read1") else proc.stderr.read(4096)
                if not chunk:
                    return
                last[0] = time.monotonic()
                try:
                    if out is not None:
                        out.write(chunk)
                        out.flush()
                except (OSError, ValueError):
                    pass
        threading.Thread(target=_pump, daemon=True).start()
    while True:
        try:
            return proc.wait(timeout=1), False
        except subprocess.TimeoutExpired:
            pass
        except KeyboardInterrupt:
            # Stop: make sure the miner stops too (it may not share our
            # process group), then let main() end the batch quietly
            _stop_child(proc, signal)
            raise
        try:
            _sync_beat()
        except KeyboardInterrupt:                           # a Stop during the heartbeat stops the miner too
            _stop_child(proc, signal)
            raise
        if stall_s and time.monotonic() - last[0] > stall_s:
            print(f"[clone] no output for {stall_s:.0f}s; killing the stalled clone", flush=True)
            _signal_child(proc, signal.SIGKILL if os.name != "nt" else signal.SIGTERM)
            proc.kill()
            proc.wait()
            return -9, False
        if _skip_requested(repo):
            print(f"[skip] {repo}: skip requested; stopping it", flush=True)
            _stop_child(proc, signal)
            return proc.returncode, True


# --- central sync (ADR 0015) ---------------------------------------------------
# Set once the run's output name is final; run() reads it on a Stop. "repos" is
# each repo's state for the store's progress page: pending, mining, done, failed
# or skipped, with its finished rows and, once selected, how many PRs it has.
_SYNC: dict | None = None
LOG_BEAT_S = 900           # everything that changed (log, trace, checkpoint, finished tables, health) goes to the store this often
BEAT_SYNC_BUDGET_S = 120   # ...and a heartbeat sync never holds the batch loop longer than this
BEAT_MAX_FILE_BYTES = 48 * 1024 ** 2   # a bigger file is left for the repo's end or Stop (about 0.5 MB/s fits 120 s)
HEARTBEAT_S = 180          # status-only update (progress, current repo) while a long repo mines


def _sync_begin(entries: list, done_repos: dict) -> None:
    global _SYNC
    if _SYNC is None:
        return
    try:
        me = sync.whoami()
    except Exception as e:                                  # noqa: BLE001  (sync must never stop a run)
        me = {"ok": False, "why": f"{type(e).__name__}: {e}"}
    sid = getattr(_SYNC["args"], "split_id", None)
    if not me["ok"]:
        print(f"[sync] OFF for this run: {me['why']}. Fix it with `python join.py --check`; the run goes on "
              f"and writes everything locally.", flush=True)
        progress.emit("sync", off=me["why"])
        _SYNC = None
        return
    if me.get("split") and me["split"] != sid:
        print(f"[sync] OFF for this run: your token is for split {me['split']}, but these flags make split "
              f"{sid or 'none'}. The repos file, domain, shard count or sizes differ from the lead's plan; "
              f"use the command join.py printed.", flush=True)
        progress.emit("sync", off=f"token is for split {me['split']}, these flags make {sid or 'none'}")
        _SYNC = None
        return
    print(f"[sync] on as {me['name']}: output goes to the team store after each repo and on a Stop", flush=True)
    progress.emit("sync", on=me["name"])
    _SYNC["repos"] = {f"{o}/{n}": ({"state": "done", "rows": done_repos[f"{o}/{n}".lower()]}
                                   if f"{o}/{n}".lower() in done_repos else {"state": "pending"})
                      for o, n, _ in entries}
    _SYNC["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _SYNC["beat"] = time.monotonic()
    _sync_beat(True, "started")


def _sync_mark(repo: str, state: str, args=None) -> None:
    """Record a repo's state; for a finished one also its row count, read from
    the dataset (the repo column only)."""
    if _SYNC is None or repo not in _SYNC.get("repos", {}):
        return
    rec = {"state": state}
    if state == "done" and args is not None:
        rec["rows"] = _mined_repos(args.out).get(repo.lower(), 0)
    _SYNC["repos"][repo] = rec
    if state == "mining":
        _sync_beat(True, "repo started")


def _ci_cache_dir(args=None) -> str:
    """Where the miner keeps its per-repo caches: the run's --ci-cache-dir if it has one, else the
    miner's own default (Settings.ci_cache_dir), the same directory the child writes to."""
    import config
    return getattr(args, "ci_cache_dir", None) or config.DEFAULT.ci_cache_dir


def _selected_count(repo: str) -> int | None:
    """PRs a started repo selected, from its pinned selection (None: not yet)."""
    owner, _, name = repo.partition("/")
    try:
        with open(os.path.join(_ci_cache_dir(), f"{owner}_{name}.selection.json"), encoding="utf-8") as fh:
            sel = json.load(fh)
        return sum(len(sel.get(k) or []) for k in ("merged", "unmerged", "stale"))
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return None


def _run_settings(args) -> dict:
    """What this PC's run is set to, for the admin's per-run view. Names and switches only: no keys, no paths."""
    import config
    g = lambda n: getattr(args, n, None)                    # noqa: E731
    llm = bool(g("llm"))
    try:
        cfg = config.Settings()
        model, backend = (g("llm_model") or cfg.llm_model), cfg.llm_backend
    except Exception:                                       # noqa: BLE001  (a status must never fail the run)
        model, backend = g("llm_model"), None
    return {"llm": llm, "llm_model": model if llm else None, "llm_backend": backend if llm else None,
            "llm_scope": g("llm_scope") if llm else None, "llm_concurrency": g("llm_concurrency") if llm else None,
            "szz": bool(g("szz")), "ci_history": bool(g("ci_history")), "scanners": not g("no_scanners"),
            "workers": g("workers"), "max_prs": g("max_prs"), "window_from": g("window_from"), "window_to": g("window_to"),
            "full_repo": bool(g("full_repo")), "cap_on": g("cap_on"), "only_domain": g("only_domain"),
            "repos_file": os.path.basename(g("repos_file") or "") or None, "shard": g("shard")}


_REPO_FIELD = re.compile(rb'\{\s*"repo"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _line_repo(line: bytes) -> str | None:
    """Lower-cased repo of one checkpoint line, or None for a blank or damaged line. Rows are wide (a
    hundred columns, some with long text), so the leading "repo" field is read with a regex and the
    whole line is parsed as JSON only when it is not there (another field first)."""
    line = line.strip()
    if not line:
        return None
    m = _REPO_FIELD.match(line)
    try:
        if m:
            raw = m.group(1)
            val = json.loads(b'"' + raw + b'"') if b"\\" in raw else raw.decode("utf-8")
        else:
            val = json.loads(line).get("repo")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return None                                         # a line cut by a crash
    return str(val).lower() if val else None


# per checkpoint path: what has been counted, up to which byte, and a fingerprint of the file's start and of
# the bytes just before that point, so an append is read from where it left off and a rewrite is read afresh
_CKPT_CACHE: dict[str, dict] = {}
_FP = 256


def _fingerprint(fh, pos: int) -> bytes:
    fh.seek(0)
    head = fh.read(_FP)
    fh.seek(max(0, pos - _FP))
    return head + b"|" + fh.read(min(_FP, pos))


def _checkpointed(path: str) -> dict[str, int]:
    """Rows per lower-cased repo in the shared checkpoint. One file holds every unfinished repo's rows
    (a failed or stopped repo keeps its rows there for the rerun), so a line counts only for its own repo.
    The status heartbeat calls this every few minutes on a file that can reach gigabytes, so it keeps what it
    counted per (size, mtime) and, when the file only grew, reads just the new lines."""
    try:
        st = os.stat(path)
    except OSError:
        _CKPT_CACHE.pop(path, None)
        return {}
    c = _CKPT_CACHE.get(path)
    if c and c["size"] == st.st_size and c["mtime"] == st.st_mtime_ns:
        # (size, mtime) can survive a rewrite (a coarse clock, a restored mtime): the
        # fingerprint of the first and last bytes read so far tells, for 512 bytes of reading
        try:
            with open(path, "rb") as fh:
                same = c["pos"] <= st.st_size and _fingerprint(fh, c["pos"]) == c["fp"]
        except OSError:
            same = True
        if same:
            return dict(c["counts"])
        _CKPT_CACHE.pop(path, None)
        c = None
    try:
        with open(path, "rb") as fh:
            if c and c["pos"] <= st.st_size and c["pos"] > 0 and _fingerprint(fh, c["pos"]) == c["fp"]:
                counts, pos = dict(c["counts"]), c["pos"]    # only appended to since the last look
            else:
                counts, pos = {}, 0
            fh.seek(pos)
            tail = b""
            for line in fh:
                if not line.endswith(b"\n"):
                    tail = line                              # not finished (or a crash cut it): looked at, not remembered
                    break
                repo = _line_repo(line)
                if repo:
                    counts[repo] = counts.get(repo, 0) + 1
                pos += len(line)
            fp = _fingerprint(fh, pos)
    except OSError:
        return dict(c["counts"]) if c else {}
    _CKPT_CACHE[path] = {"size": st.st_size, "mtime": st.st_mtime_ns, "pos": pos, "fp": fp, "counts": counts}
    out = dict(counts)
    repo = _line_repo(tail) if tail else None
    if repo:
        out[repo] = out.get(repo, 0) + 1
    return out


def _sync_status(why: str) -> dict:
    """The run.json body: counts per repo and totals. PRs of repos not yet
    started are unknown (their selection does not exist yet), so `prs_remaining`
    covers the started repos only and `repos_pending` says how many are left.
    `prs_total` is the split plan's PR share for this PC (an estimate; never below the
    PRs already counted), or None when there is no plan."""
    args = _SYNC["args"]
    repos = _SYNC.get("repos", {})
    ckpt = _checkpointed(args.out + ".partial.jsonl")
    for repo, rec in repos.items():
        if rec["state"] == "mining":
            rec["selected"] = _selected_count(repo)
        if rec["state"] != "done":
            n = ckpt.get(repo.lower(), 0)
            if n or rec["state"] == "mining":
                rec["rows"] = n
            else:
                rec.pop("rows", None)
    if why == "stopped":                                    # a Stop leaves nothing mining: the store must not say so
        repos = {k: (dict(r, state="stopped") if r["state"] == "mining" else r) for k, r in repos.items()}
    states = [r["state"] for r in repos.values()]
    mining = next(((k, r) for k, r in repos.items() if r["state"] in ("mining", "stopped")), None)
    prs_done = sum(r.get("rows", 0) for r in repos.values())
    remaining = (max(0, (mining[1].get("selected") or 0) - mining[1].get("rows", 0)) if mining else 0)
    wait_until = None                                       # the child's GitHub quota wait, if it is in one
    try:
        with open(os.path.join(_ci_cache_dir(args), ".rate_wait"), encoding="utf-8") as fh:
            until = int(fh.read().strip())
        if until > time.time():
            wait_until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until))
    except (OSError, ValueError):
        pass
    return dict(_SYNC["counts"], reason=why, rate_wait_until=wait_until, pc=args.shard, split_id=getattr(args, "split_id", None),
                miner_version=__version__, out=os.path.basename(args.out), argv=sys.argv[1:], settings=_run_settings(args),
                started_at=_SYNC.get("started_at"), repos=repos, current=mining[0] if mining and mining[1]["state"] == "mining" else None,
                repos_total=len(repos), repos_done=states.count("done"), repos_failed=states.count("failed"),
                repos_skipped=states.count("skipped"), repos_pending=states.count("pending"),
                prs_done=prs_done, prs_remaining=remaining,
                prs_total=(max(getattr(args, "plan_prs", None) or 0, prs_done + remaining) or None))


def _sync_prefix(status: dict) -> str:
    return f"{__version__}/{status['split_id'] or 'nosplit'}"


def _sync_now(why: str, **counts) -> None:
    """Send this PC's output to the central store, if one is configured. Never
    fails or blocks the run (sync.py): an unreachable store is a warning."""
    if _SYNC is None:
        return
    _SYNC["counts"].update(counts)
    try:
        status = _sync_status(why)
        args = _SYNC["args"]
        sync.report(sync.sync(args.out, _sync_prefix(status), status,
                              health_dir=getattr(args, "health_dir", "health"),
                              repos=set(_SYNC.get("repos", {}))), why)
        _SYNC["beat"] = time.monotonic()
    except Exception as e:                                  # noqa: BLE001
        print(f"[sync] failed ({type(e).__name__}: {e}); the run is unaffected", file=sys.stderr, flush=True)


def _sync_beat(force: bool = False, why: str = "heartbeat") -> None:
    """A status-only update (no files): every HEARTBEAT_S while a child runs, and at once when `force` (the run
    started, a repo started), so the store shows a PC the moment it begins instead of after its first repo."""
    if _SYNC is None or not _SYNC.get("repos") or (not force and time.monotonic() - _SYNC.get("beat", 0) < HEARTBEAT_S):
        return
    _SYNC["beat"] = time.monotonic()
    try:
        status = _sync_status(why)
        sync.put_status(_sync_prefix(status), status)
        if time.monotonic() - _SYNC.get("log_beat", -LOG_BEAT_S) >= LOG_BEAT_S:
            _SYNC["log_beat"] = time.monotonic()
            args = _SYNC["args"]                            # the full sync: only what changed goes, so a failed send is retried
            res = sync.sync(args.out, _sync_prefix(status), status,
                            health_dir=getattr(args, "health_dir", "health"),
                            repos=set(_SYNC.get("repos", {})), budget_s=BEAT_SYNC_BUDGET_S,
                            max_bytes=BEAT_MAX_FILE_BYTES)
            if res["sent"] or res["failed"]:
                sync.report(res, "heartbeat")
    except Exception:                                       # noqa: BLE001
        pass


def _write_shard_manifest(args, entries: list, ok: int, failed: list) -> None:
    """Beside a sharded dataset, record which split, PC and miner version made it
    (merge_shards.py checks these before joining the PCs' files)."""
    kn = naming.parse_shard(args.shard)
    if args.test or not kn:
        return
    snap_out, commits_out = naming.snapshot_outs(args.out)
    manifest = {"split_id": getattr(args, "split_id", None), "pc": kn[0], "of": kn[1],
                "miner_version": __version__, "host": socket.gethostname(),
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "repos": sorted(f"{o}/{n}" for o, n, _ in entries), "mined_ok": ok, "failed": failed,
                "files": {"dataset": os.path.basename(args.out),
                          "snapshots": os.path.basename(snap_out) if os.path.isfile(snap_out) else None,
                          "commit_features": os.path.basename(commits_out) if os.path.isfile(commits_out) else None}}
    try:
        with open(naming.manifest_path(args.out), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
    except OSError as e:
        print(f"[warn] could not write the shard manifest: {e}", flush=True)


def _assemble_snapshots(args) -> None:
    """Build the snapshot tables once every repo is mined (ADR 0010; snapshots.py).
    They need the finished at-open rows of the whole dataset, so this runs at the
    end, not per PR. Never fails the batch: the raw captures stay, and
    `python snapshots.py` rebuilds the tables by hand."""
    if args.test or not args.snapshots or not args.assemble_snapshots:
        return
    dataset = args.out if os.path.isfile(args.out) else os.path.splitext(args.out)[0] + ".jsonl"
    out, commits_out = naming.snapshot_outs(dataset)
    if not os.path.isfile(dataset) or not os.path.isdir(args.snapshot_root):
        print(f"[snapshots] nothing to assemble (dataset {'found' if os.path.isfile(dataset) else 'missing'}, "
              f"captures {'found' if os.path.isdir(args.snapshot_root) else 'missing'})", flush=True)
        return
    import snapshots
    print("[snapshots] assembling the snapshot tables from the captures...", flush=True)
    progress.emit("assemble", state="running", out=out)
    runlog.stage("assemble snapshots")
    try:
        r = snapshots.assemble_files(dataset, args.snapshot_root, out, commits_out)
    except Exception as e:                                  # noqa: BLE001
        runlog.stage(None)
        runlog.error("snapshots", "assembly failed", exc=type(e).__name__, err=str(e)[:300])
        print(f"[snapshots] assembly FAILED ({type(e).__name__}: {e}); the captures are kept, "
              f"rebuild with: python snapshots.py --dataset {dataset} --raw-root {args.snapshot_root}",
              flush=True)
        progress.emit("assemble", state="failed", err=f"{type(e).__name__}: {e}")
        return
    runlog.stage(None)
    runlog.info("snapshots", "assembled", **r)
    print(f"[snapshots] {r['snapshots']} snapshots from {r['prs']} PRs -> {out}; "
          f"{r['commits']} head commits -> {commits_out}  ({r['secs']}s)", flush=True)
    progress.emit("assemble", state="done", **r)


def validate_options(args) -> list[str]:
    """Every problem with this combination of options, found before anything is cloned or called, so a bad
    flag costs a second and not a night. `--max-prs 0` means no cap (what the miner does with it): it is
    normalised to None here so both sides read it the same way."""
    import datetime as _dt
    bad: list[str] = []
    g = lambda n: getattr(args, n, None)                    # noqa: E731
    if g("max_prs") is not None:
        if args.max_prs < 0:
            bad.append("--max-prs can't be negative (0 or leaving it out means no cap)")
        elif args.max_prs == 0:
            args.max_prs = None
    if g("shard_plan") is not None and args.shard_plan < 1:
        bad.append(f"--shard-plan wants at least 1 PC, got {args.shard_plan}")
    full = g("full_repo")
    if full:
        clash = [f for f, v in (("--window-from", g("window_from")), ("--window-to", g("window_to")),
                                ("--max-prs", g("max_prs")), ("--cap-on", g("cap_on")),
                                ("--lead-in-days", g("lead_in_days")), ("--test", g("test"))) if v not in (None, False, "")]
        if clash:
            bad.append("--full-repo mines everything; it can't be combined with " + ", ".join(clash))
    wf, wt = g("window_from"), g("window_to")
    if bool(wf) != bool(wt):
        bad.append("--window-from and --window-to go together (give both)")
    dates = {}
    for flag, val in (("--window-from", wf), ("--window-to", wt)):
        if val:
            try:
                dates[flag] = _dt.datetime.strptime(val, "%Y-%m-%d").date()
            except ValueError:
                bad.append(f"{flag} wants a real date as YYYY-MM-DD, got {val!r}")
    if len(dates) == 2 and dates["--window-from"] > dates["--window-to"]:
        bad.append(f"--window-from {wf} is after --window-to {wt}")
    for flag, name, low in (("--min-age-days", "min_age_days", 0), ("--lead-in-days", "lead_in_days", 0),
                            ("--max-unmerged", "max_unmerged", 0), ("--skip-big", "skip_big", 0),
                            ("--repo-retries", "repo_retries", 0), ("--max-files-deep", "max_files_deep", 0),
                            ("--workers", "workers", 1), ("--llm-concurrency", "llm_concurrency", 1),
                            ("--test-prs", "test_prs", 1), ("--llm-max-diff-chars", "llm_max_diff_chars", 1),
                            ("--llm-diff-context", "llm_diff_context", 0)):
        v = g(name)
        if v is not None and v < low:
            bad.append(f"{flag} must be {low} or more, got {v}")
    pct = g("llm_sample_pct")
    if pct is not None and not 0 <= pct <= 100:
        bad.append(f"--llm-sample-pct is a percentage from 0 to 100, got {pct}")
    stall = g("clone_stall_s")
    if stall is not None and stall < 0:
        bad.append(f"--clone-stall-s can't be negative, got {stall:g}")
    if g("shard"):
        try:
            k, n = (int(x) for x in args.shard.split("/"))
            assert 1 <= k <= n
        except (ValueError, AssertionError):
            bad.append(f"--shard must look like K/N with 1 <= K <= N, got {args.shard!r}")
    for repo in g("remine_repo") or []:
        o, _, n = repo.partition("/")
        if repo.count("/") != 1 or not (_valid_name(o) and _valid_name(n)):
            bad.append(f"--remine-repo wants OWNER/NAME, got {repo!r}")
    return bad


def _export_child_settings(args) -> None:
    """Flags that shape what the miner computes but that it reads from its environment (Settings defaults)
    go into ours, which every miner and clone process inherits."""
    for val, var in ((args.llm_backend, "LLM_BACKEND"), (args.llm_diff_context, "LLM_DIFF_CONTEXT"),
                     (args.max_files_deep, "RIFFLE_MAX_FILES_DEEP"), (args.clone_stall_s, "RIFFLE_CLONE_STALL_S")):
        if val is not None:
            os.environ[var] = f"{val:g}" if isinstance(val, float) else str(val)


def build_parser() -> argparse.ArgumentParser:
    """The command line, on its own so join.py and the TUI can parse a command the way the miner will."""
    ap = argparse.ArgumentParser(
        description="Clone a list of GitHub repos and mine them into one dataset.",
        allow_abbrev=False)                                 # _run_flags and join.py match flags by their full names
    ap.add_argument("--repos-file", default="repos.txt",
                    help="file with one GitHub URL per line (# comments allowed)")
    ap.add_argument("--out", default="dataset.parquet",
                    help="single output dataset (rows appended, deduped)")
    ap.add_argument("--out-prefix", default="", metavar="TEXT",
                    help="text put before the output file's name; placeholders {pc} {n} {host} {date}")
    ap.add_argument("--out-postfix", default="", metavar="TEXT",
                    help="text put after the output file's name (before the extension), "
                         "replacing the automatic pc tag; same placeholders")
    ap.add_argument("--assemble-snapshots", action=argparse.BooleanOptionalAction, default=True,
                    help="when the batch ends, build the snapshot tables from what --snapshots "
                         "captured (<out>_snapshots, <out>_commit_features); on by default")
    ap.add_argument("--no-auto-name", action="store_true",
                    help="don't add the automatic pc1of3 tag to the output name of a --shard run")
    ap.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARN", "ERROR"],
                    help="detail of the structured run trace (logs/run-*.trace.jsonl): DEBUG "
                         "adds every git/API/LLM call, INFO (default) one line per PR and stage")
    ap.add_argument("--clone-dir", default="cloned_repos",
                    help="directory to clone repos into")
    ap.add_argument("--max-prs", type=int, default=None)
    ap.add_argument("--min-age-days", type=int, default=90,
                    help="skip PRs merged in the last N days (passed through; default 90, 0 = off)")
    ap.add_argument("--ci-history", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--szz", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--declared-labels", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--merge-outcome", action=argparse.BooleanOptionalAction, default=True,
                    help="also mine closed-unmerged PRs and add the merge-outcome "
                         "labels (ADR 0009, default on); --no-merge-outcome for "
                         "breakage rows only")
    ap.add_argument("--max-unmerged", type=int, default=None,
                    help="cap unmerged rows per repo (default: the window's real mix)")
    ap.add_argument("--pr-discovery", choices=["api", "git"], default="api")
    ap.add_argument("--llm", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--no-scanners", action="store_true")
    ap.add_argument("--no-api-contract", action="store_true")
    ap.add_argument("--no-review-history", action="store_true")
    ap.add_argument("--llm-model", default=None, help="e.g. sonnet, opus, haiku (passed through)")
    ap.add_argument("--workers", type=int, default=None,
                    help="PRs mined in parallel per repo (passed through)")
    ap.add_argument("--llm-concurrency", type=int, default=None,
                    help="max concurrent LLM calls (passed through)")
    ap.add_argument("--llm-scope", choices=["training", "all"], default=None,
                    help="rows that get LLM calls: training = outside contributors (not bots) "
                         "plus a 5%% sample of the rest (default); all = every row (passed through)")
    ap.add_argument("--llm-sample-pct", type=int, default=None,
                    help="with --llm-scope training, %% of other rows still sent (default 5) (passed through)")
    ap.add_argument("--close-reason-llm", choices=["other_only", "always"], default=None,
                    help="other_only: ask the LLM only when the rules find no close reason "
                         "(default); always: every unmerged PR with human activity (passed through)")
    ap.add_argument("--llm-max-diff-chars", type=int, default=None,
                    help="cap on the diff sent to the LLM after lockfiles and generated "
                         "files are cut (default 25000) (passed through)")
    ap.add_argument("--llm-quota-wait", action=argparse.BooleanOptionalAction, default=None,
                    help="wait for the Claude plan to reset instead of failing calls (default on) (passed through)")
    ap.add_argument("--llm-diff-context", type=int, default=None,
                    help="context lines around each change in the diff sent to the LLM (default 1); "
                         "reaches the miner as LLM_DIFF_CONTEXT")
    ap.add_argument("--llm-backend", choices=["claude_cli", "api"], default=None,
                    help="LLM backend for the flags (default: LLM_BACKEND, else claude_cli); reaches the "
                         "miner as LLM_BACKEND")
    ap.add_argument("--max-files-deep", type=int, default=None, metavar="N",
                    help="a PR touching more than N files skips the per-file deep features (blame, line "
                         "history, scanners) instead of stalling the repo (default 300; 0 = never skip); "
                         "reaches the miner as RIFFLE_MAX_FILES_DEEP")
    ap.add_argument("--clone-stall-s", type=float, default=None, metavar="S",
                    help="kill a git clone that prints nothing for S seconds and retry it (default 600, "
                         "or RIFFLE_CLONE_STALL_S; 0 = never)")
    ap.add_argument("--window-from", default=None, help="date window start YYYY-MM-DD (passed through)")
    ap.add_argument("--window-to", default=None, help="date window end YYYY-MM-DD (passed through)")
    ap.add_argument("--lead-in-days", type=int, default=None, help="history lead-in days (passed through)")
    ap.add_argument("--cap-on", choices=["all", "training"], default=None,
                    help="what --max-prs counts in window mode (passed through)")
    ap.add_argument("--full-repo", action="store_true",
                    help="mine each repo's whole history (every closed PR), not a date window or "
                         "a --max-prs cap (passed through)")
    ap.add_argument("--split-by", choices=["commits", "prs"], default="commits",
                    help="what --shard balances between PCs: the repos' commit counts (the [~N commits] tags; clone, history and "
                         "SZZ cost) or their PR counts (the [N PRs] tags; the per-PR cost that dominates a --full-repo run). "
                         "Every PC must use the same value")
    ap.add_argument("--only-domain", default=None,
                    help="mine only repos tagged [domain: NAME] in the repos file")
    ap.add_argument("--snapshots", action=argparse.BooleanOptionalAction, default=True,
                    help="capture what PR snapshots need (passed through; default on)")
    ap.add_argument("--sync", action=argparse.BooleanOptionalAction, default=True,
                    help="send logs, health, datasets and the shard manifest to the team store after each "
                         "repo, on a Stop and at the end (default on, but only when RIFFLE_SYNC_URL and "
                         "RIFFLE_SYNC_TOKEN are set; ci_cache/ stays on this PC)")
    ap.add_argument("--snapshot-root", default="snapshots_raw",
                    help="capture files go to <root>/<owner>_<name>/ (default snapshots_raw)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="reuse an existing clone instead of re-cloning")
    ap.add_argument("--keep-clones", action="store_true",
                    help="keep each clone after mining (default: delete it before "
                         "cloning the next, so only one clone is on disk at a time)")
    ap.add_argument("--repo-retries", type=int, default=2,
                    help="extra attempts per repo if clone or mine fails (default 2, "
                         "so 3 total). The miner checkpoints, so a retried mine "
                         "resumes rather than redoing finished PRs.")
    ap.add_argument("--shard", default=None, metavar="K/N",
                    help="this machine is PC K of N: mine only its share of the repos "
                         "file. Shares have equal repo counts and balanced size (by the "
                         "[~N commits] tags), and every PC computes the same split")
    ap.add_argument("--shard-plan", type=int, default=None, metavar="N",
                    help="print how the repos split over N PCs, then exit")
    ap.add_argument("--issue-tokens", action="store_true",
                    help="with --shard-plan: also ask the team store for one write token per PC, bound to "
                         "this split, and print each PC's join code (needs RIFFLE_SYNC_URL and "
                         "RIFFLE_ADMIN_TOKEN in .env). Give the plan the flags every PC should run with")
    ap.add_argument("--token-days", type=float, default=None, metavar="D",
                    help="with --issue-tokens: the tokens stop working after D days")
    ap.add_argument("--tokens-out", default=None, metavar="FILE",
                    help="with --issue-tokens: also write the join codes to FILE (kept private to you)")
    ap.add_argument("--remine", action="store_true",
                    help="mine every repo even if it's already in --out (default: "
                         "skip finished repos, so a rerun after a stop resumes)")
    ap.add_argument("--remine-repo", action="append", default=[], metavar="OWNER/NAME",
                    help="drop this repo's rows, SZZ cache and checkpoint from --out, then "
                         "mine it again (repeatable). Use after --audit flags a repo")
    ap.add_argument("--skip-big", type=int, default=0, metavar="N",
                    help="skip repos with more than N commits (by their repos.txt "
                         "[~N commits] tag) for a quick run; 0 = off. Skipped repos aren't "
                         "written, so a later run without it mines them")
    ap.add_argument("--auto-remine", action="store_true",
                    help="before mining, audit --out and re-mine every repo in this run's "
                         "share whose blame data shows it was mined while blame was failing")
    ap.add_argument("--audit", action="store_true",
                    help="report each repo's share of rows with blame data and flag repos "
                         "mined while blame was failing, then exit")
    ap.add_argument("--test", action="store_true",
                    help="test run: first repo only, --test-prs PRs, fresh output in "
                         "test_output/test_dataset.jsonl (readable JSON lines); clone kept")
    ap.add_argument("--test-prs", type=int, default=10)
    ap.add_argument("--test-repo", default="https://github.com/pallets/flask",
                    help="repo used by --test instead of the repos file (default: "
                         "pallets/flask, the smallest repo in the corpus)")
    return ap


def main():
    import config
    config.stop_on_break()
    ap = build_parser()
    args = ap.parse_args()
    problems = validate_options(args)
    if problems:
        sys.exit("[options] " + "\n[options] ".join(problems) + "\nNothing was cloned or changed.")
    _export_child_settings(args)
    if sys.stdin.isatty() and not (args.test or args.shard_plan or args.audit):
        import doctor                                       # a run started from a terminal starts on the newest main (not under the TUI,
        doctor.auto_update()                                # which already checked, and not in automation: no TTY)
    if args.llm and not (args.shard_plan or args.audit):     # plan and audit need no LLM
        import config
        from features import llm_flags
        why = llm_flags.unavailable_reason(config.Settings())
        if why:
            sys.exit(f"[llm] unavailable: {why}. The LLM flags are part of the default dataset; "
                     f"fix the backend (python doctor.py), or pass --no-llm to mine without them "
                     f"on purpose.")

    # a sharded run tags its dataset (dataset_pc1of3.parquet) so PCs' files can't be mixed up
    plain_out = args.out
    args.out = naming.tagged_out(args.out, args.shard, args.out_prefix, args.out_postfix,
                                 auto=not args.no_auto_name)
    if args.out != plain_out and not args.shard_plan:
        note = (f"; {plain_out} exists but is not used, so a resume starts from {args.out}"
                if os.path.exists(plain_out) and not os.path.exists(args.out) else "")
        print(f"[out] {plain_out} -> {args.out}{note}", flush=True)
    stem = naming.run_stem(args.shard)
    if not (args.audit or args.shard_plan):
        runlog.configure(path=os.environ.get("RIFFLE_TRACE")
                         or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs",
                                         stem + ".trace.jsonl"),
                         level=args.log_level)
    if runlog.enabled("INFO"):
        os.makedirs(os.path.dirname(os.environ["RIFFLE_TRACE"]), exist_ok=True)
        runlog.info("batch", "start", argv=sys.argv[1:], out=args.out, version=__version__,
                    host=socket.gethostname(), level=args.log_level or "INFO")

    global _SYNC
    if args.sync and sync.settings() and not (args.test or args.audit or args.shard_plan):
        _SYNC = {"args": args, "counts": {}}
    if args.audit:
        _audit(args.out)
        return
    size_of: dict = {}
    domain_of: dict = {}
    if args.test:
        entries = [p for p in [_parse_url(args.test_repo)] if p]
    else:
        if not os.path.isfile(args.repos_file):
            sys.exit(f"repos file not found: {args.repos_file}")
        with open(args.repos_file, encoding="utf-8") as fh:
            lines = _dedupe_lines([line for line in fh if _parse_url(line)])
        entries = [_parse_url(line) for line in lines]
        domains = [_parse_domain(line) for line in lines]
        n_listed = len(entries)
        if args.only_domain:
            want = args.only_domain.strip().lower()
            keep = [i for i, d in enumerate(domains) if d == want]
            lines = [lines[i] for i in keep]
            entries, domains = [entries[i] for i in keep], [domains[i] for i in keep]
            print(f"[domain] {len(entries)} repos tagged [domain: {want}]", flush=True)
            if not entries and n_listed:
                print(f"[domain] WARNING: 0 repos tagged {args.only_domain!r} in {args.repos_file} "
                      f"(tags are case-insensitive, e.g. [domain: web-ts])", flush=True)
        weights = [_parse_weight(line) for line in lines]
        pr_counts = [_parse_prs(line) for line in lines]
        size_of = {f"{o}/{n}".lower(): w for (o, n, _), w in zip(entries, weights)}
        domain_of.update({f"{o}/{n}".lower(): d for (o, n, _), d in zip(entries, domains)})
        if args.shard_plan:
            sid = _print_shard_plan(entries, weights, args.shard_plan, pr_counts, args.split_by)
            if args.issue_tokens:
                _issue_tokens(args, sid, args.shard_plan)
            return
        if args.shard:
            k, n = (int(x) for x in args.shard.split("/"))        # validated by validate_options
            basis = pr_counts if args.split_by == "prs" else weights      # what the split balances
            _warn_untagged(entries, basis, args.split_by)
            sid = split_id(entries, basis, n, args.split_by)
            args.split_id = sid
            mine = balanced_shards(basis, n)[k - 1]
            total = sum(weights[i] or 0 for i in mine)
            prs_total = sum(pr_counts[i] or 0 for i in mine)
            args.plan_prs = prs_total or None             # this PC's PR share, for the status's total
            entries = [entries[i] for i in mine]
            print(f"[shard] split ID {sid} · PC {k}/{n}: {len(entries)} repos, ~{total:,} "
                  f"commits" + (f", {prs_total:,} PRs" if prs_total else "") + f" (balanced by {args.split_by}). Every PC must show the same split ID "
                  f"(same repos.txt, PC count and --split-by) or shares may overlap.", flush=True)
    if not entries:
        if args.only_domain and not args.test and n_listed:
            sys.exit(f"0 repos tagged {args.only_domain!r} in {args.repos_file}; nothing to mine "
                     f"({n_listed} repos are listed, none with that tag)")
        sys.exit("no valid GitHub URLs found in the repos file")
    if args.test:
        import config
        args.max_prs = args.test_prs
        # fixed window and no age cutoff, so the PR set doesn't depend on the day or the form
        args.window_from, args.window_to = config.TEST_WINDOW_FROM, config.TEST_WINDOW_TO
        args.min_age_days, args.cap_on, args.max_unmerged = 0, None, None
        args.skip_existing = True            # reuse the test clone between runs
        args.out = TEST_OUT
        args.keep_clones = True
        os.makedirs(os.path.dirname(TEST_OUT), exist_ok=True)
        for f in (TEST_OUT, TEST_OUT + ".partial.jsonl", TEST_OUT.rsplit(".", 1)[0] + ".parquet"):
            if os.path.isfile(f):
                os.remove(f)                 # fresh file every test run
        print(f"[test] {entries[0][0]}/{entries[0][1]}, {args.max_prs} PRs -> {TEST_OUT}",
              flush=True)

    # Re-mine only repos this run will actually mine (this PC's share), and
    # drop their old rows only then; dropping a repo another PC mines would
    # lose its rows here without replacing them.
    mine_here = {f"{o}/{n}".lower() for o, n, _ in entries}
    if args.auto_remine and not args.test:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            flagged = _audit(args.out)
        auto = [r for r in flagged if r.lower() in mine_here
                and r.lower() not in {x.lower() for x in args.remine_repo}]
        others = [r for r in flagged if r.lower() not in mine_here]
        if auto:
            print(f"[auto-remine] blame was failing for {', '.join(auto)}; re-mining them", flush=True)
        else:
            print("[auto-remine] audit found nothing to re-mine in this run's repos", flush=True)
        if others:
            print(f"[auto-remine] also flagged, but mined by another PC: {', '.join(others)}", flush=True)
        args.remine_repo = list(args.remine_repo) + auto
    for repo in args.remine_repo:
        if repo.lower() not in mine_here:
            sys.exit(f"--remine-repo {repo}: not in this run's repos"
                     + (f" (this is PC {args.shard}; it's in another PC's share)" if args.shard else
                        f" (not in {args.repos_file})") + ". Nothing was changed.")
    for repo in args.remine_repo:
        _drop_repo(args.out, repo)

    os.makedirs(args.clone_dir, exist_ok=True)
    done_repos = {} if (args.remine or args.test) else _mined_repos(args.out)
    if done_repos:
        print(f"[resume] {len(done_repos)} repos already in {args.out}; skipping them "
              f"(--remine to redo)", flush=True)
    here = os.path.dirname(os.path.abspath(__file__))
    miner = os.path.join(here, "mine_repo.py")

    print(f"[batch] {len(entries)} repos -> {args.out}\n", flush=True)
    progress.emit("batch_start", repos=[f"{o}/{n}" for o, n, _ in entries], out=args.out)
    _sync_begin(entries, done_repos)
    attempts = max(1, args.repo_retries + 1)
    ok, failed, skipped = 0, [], []
    if args.skip_big and not args.test:
        big = [f"{o}/{n}" for o, n, _ in entries if (size_of.get(f"{o}/{n}".lower()) or 0) > args.skip_big
               and f"{o}/{n}".lower() not in done_repos]
        if big:
            print(f"[skip] {len(big)} repos over {args.skip_big:,} commits will be skipped this run: "
                  + ", ".join(big), flush=True)
    aborted = False
    for i, (owner, name, url) in enumerate(entries, 1):
        print(f"=== [{i}/{len(entries)}] {owner}/{name} ===", flush=True)
        target = _safe_path(args.clone_dir, f"{owner}_{name}")
        if target is None:
            print(f"[clone] REFUSED: {owner}/{name} would be cloned outside {args.clone_dir} "
                  f"(a link in the clone directory, or a bad name); skipping this repo\n", flush=True)
            failed.append(f"{owner}/{name} (unsafe path)")
            progress.emit("repo_failed", repo=f"{owner}/{name}", stage="clone", rc=None)
            _sync_mark(f"{owner}/{name}", "failed")
            continue
        url = _canonical_url(owner, name)
        if f"{owner}/{name}".lower() in done_repos:
            print("[resume] already mined; skipping\n", flush=True)
            ok += 1
            n_done = done_repos[f"{owner}/{name}".lower()]
            progress.emit("repo_done", repo=f"{owner}/{name}", rows=n_done, total=n_done,
                          status="ok", already=True)
            continue
        commits = size_of.get(f"{owner}/{name}".lower()) or 0
        why = ("big" if args.skip_big and not args.test and commits > args.skip_big
               else "requested" if _skip_requested(f"{owner}/{name}") else None)
        if why:
            note = f"~{commits:,} commits > {args.skip_big:,}" if why == "big" else "skipped from the TUI"
            print(f"[skip] {owner}/{name}: {note}; not mined this run\n", flush=True)
            skipped.append(f"{owner}/{name} ({why})")
            progress.emit("repo_done", repo=f"{owner}/{name}", rows=0, status="skipped",
                          skip=why, commits=commits)
            _sync_mark(f"{owner}/{name}", "skipped")
            continue
        _sync_mark(f"{owner}/{name}", "mining")
        progress.emit("repo_queue", repo=f"{owner}/{name}", i=i, n=len(entries), stage="clone")

        # --- clone (retried) — one clone kept across mine retries -------------
        if os.path.isdir(target):
            if args.skip_existing:
                print(f"[clone] reusing existing {target}", flush=True)
            else:
                print(f"[clone] removing existing {target}", flush=True)
                _rmtree(target)
        cloned = os.path.isdir(target)
        for c_attempt in range(1, attempts + 1):
            if cloned:
                break
            print(f"[clone] git clone {url} (attempt {c_attempt}/{attempts})", flush=True)
            rc, was_skipped = _run_watched(["git", "clone", "--progress", "--", url, target], f"{owner}/{name}",
                                           stall_s=_clone_stall_s())
            if was_skipped:
                break
            if rc == 0:
                cloned = True
                break
            print(f"[clone] FAILED (exit {rc})", flush=True)
            if os.path.isdir(target):
                _rmtree(target)                      # clear a partial clone
            if c_attempt < attempts:
                time.sleep(min(120, 15 * c_attempt))
        if not cloned and _skip_requested(f"{owner}/{name}"):
            if os.path.isdir(target):
                _rmtree(target)                      # a half-made clone is useless
            print(f"[skip] {owner}/{name}: skipped while cloning\n", flush=True)
            skipped.append(f"{owner}/{name} (requested)")
            progress.emit("repo_done", repo=f"{owner}/{name}", rows=0, status="skipped", skip="requested")
            _sync_mark(f"{owner}/{name}", "skipped")
            continue
        if not cloned:
            print("[clone] giving up after retries; skipping repo\n", flush=True)
            failed.append(f"{owner}/{name} (clone)")
            progress.emit("repo_failed", repo=f"{owner}/{name}", stage="clone", rc=rc)
            _sync_mark(f"{owner}/{name}", "failed")
            continue

        # --- mine (retried; checkpoint makes a retry resume, not redo) --------
        cmd = [sys.executable, miner, "--repo", target,
               "--out", args.out, "--append"]
        if args.max_prs is not None:
            cmd += ["--max-prs", str(args.max_prs)]
        cmd += ["--min-age-days", str(args.min_age_days)]
        for on, flag in ((args.ci_history, "ci-history"), (args.szz, "szz"),
                         (args.declared_labels, "declared-labels"), (args.llm, "llm")):
            cmd.append(f"--{flag}" if on else f"--no-{flag}")
        if not args.merge_outcome:
            cmd.append("--no-merge-outcome")
        if args.max_unmerged is not None:
            cmd += ["--max-unmerged", str(args.max_unmerged)]
        if args.pr_discovery != "api":
            cmd += ["--pr-discovery", args.pr_discovery]
        if args.no_scanners:
            cmd.append("--no-scanners")
        if args.no_api_contract:
            cmd.append("--no-api-contract")
        if args.no_review_history:
            cmd.append("--no-review-history")
        if args.llm_model:
            cmd += ["--llm-model", args.llm_model]
        if args.workers is not None:
            cmd += ["--workers", str(args.workers)]
        if args.llm_concurrency is not None:
            cmd += ["--llm-concurrency", str(args.llm_concurrency)]
        for opt in ("llm_scope", "llm_sample_pct", "close_reason_llm", "llm_max_diff_chars"):
            if getattr(args, opt) is not None:
                cmd += ["--" + opt.replace("_", "-"), str(getattr(args, opt))]
        if args.full_repo:
            cmd.append("--full-repo")
        if args.window_from:
            cmd += ["--window-from", args.window_from, "--window-to", args.window_to or ""]
        if args.lead_in_days is not None:
            cmd += ["--lead-in-days", str(args.lead_in_days)]
        if args.cap_on:
            cmd += ["--cap-on", args.cap_on]
        if domain_of.get(f"{owner}/{name}".lower()):
            cmd += ["--domain", domain_of[f"{owner}/{name}".lower()]]
        cmd.append("--snapshots" if args.snapshots else "--no-snapshots")
        if args.snapshots:
            cmd += ["--snapshot-dir", os.path.join(args.snapshot_root, f"{owner}_{name}")]
        if args.llm_quota_wait is not None:
            cmd.append("--llm-quota-wait" if args.llm_quota_wait else "--no-llm-quota-wait")

        mined = False
        progress.emit("repo_queue", repo=f"{owner}/{name}", i=i, n=len(entries), stage="mine")
        was_skipped = False
        for m_attempt in range(1, attempts + 1):
            rc, was_skipped = _run_watched(cmd, f"{owner}/{name}")
            if was_skipped:
                break
            if rc == 3:
                break                       # LLM circuit breaker: don't retry
            if rc == 0:
                ok += 1
                mined = True
                break
            print(f"[mine] FAILED (exit {rc}) attempt {m_attempt}/{attempts}",
                  flush=True)
            if m_attempt < attempts:
                time.sleep(min(120, 15 * m_attempt))
        if rc == 3:
            print("[mine] LLM circuit breaker tripped; aborting the batch so the "
                  "dataset isn't left with half-populated LLM columns. Fix the LLM, "
                  "then rerun with --skip-existing to resume.", flush=True)
            failed.append(f"{owner}/{name} (llm breaker)")
            aborted = True
            _sync_mark(f"{owner}/{name}", "failed")
            break
        if was_skipped:
            print(f"[skip] {owner}/{name}: stopped mid-mine; finished PRs stay in the "
                  f"checkpoint, so a later run resumes it", flush=True)
            skipped.append(f"{owner}/{name} (requested)")
            progress.emit("repo_done", repo=f"{owner}/{name}", rows=0, status="skipped", skip="requested")
            _sync_mark(f"{owner}/{name}", "skipped")
            if not args.keep_clones and os.path.isdir(target):
                print(f"[clone] removing {target} (skipped)", flush=True)
                _rmtree(target)
            print(flush=True)
            continue
        if not mined:
            print("[mine] giving up after retries; moving on", flush=True)
            failed.append(f"{owner}/{name} (mine)")
            progress.emit("repo_failed", repo=f"{owner}/{name}", stage="mine", rc=rc)

        # delete the clone once its repo is fully mined, so disk never piles up
        # (one clone on disk at a time). Its rows are in --out and a rerun skips
        # finished repos, so the clone isn't needed again. This holds even with
        # --skip-existing: that flag only reuses a clone left by a stopped or
        # failed repo, which is kept so its resume doesn't re-clone. ci_cache/
        # and checkpoints are kept. --keep-clones opts out.
        if mined and not args.keep_clones and os.path.isdir(target):
            print(f"[clone] removing {target} (mining done)", flush=True)
            _rmtree(target)
        elif not mined and os.path.isdir(target):
            print(f"[clone] keeping {target} so a rerun resumes without re-cloning",
                  flush=True)
        print(flush=True)
        _sync_mark(f"{owner}/{name}", "done" if mined else "failed", args)
        _sync_now("repo done", repos_total=len(entries), repos_ok=ok, failed=list(failed))

    if args.test and os.path.isfile(TEST_OUT):
        _write_test_parquet(TEST_OUT)
    print(f"[batch] done: {ok}/{len(entries)} repos mined -> {args.out}"
          + (f" ({len(skipped)} skipped)" if skipped else ""))
    _assemble_snapshots(args)
    _write_shard_manifest(args, entries, ok, failed)
    # an aborted batch (the LLM breaker) is not "done": the store treats only "batch done" as a finished PC
    _sync_now("batch aborted" if aborted else "batch done", repos_total=len(entries), repos_ok=ok, failed=list(failed))
    progress.emit("batch_done", ok=ok, n=len(entries), failed=failed, skipped=skipped)
    runlog.info("batch", "done", ok=ok, n=len(entries), failed=failed, skipped=skipped, out=args.out)
    if runlog.enabled("INFO"):
        print(f"[trace] {os.environ['RIFFLE_TRACE']}  (summary: python trace_report.py <file>)", flush=True)
    if skipped:
        print("[batch] skipped (mine them later by running again without the skip):")
        for s_ in skipped:
            print("  -", s_)
    if failed:
        print("[batch] failures:")
        for f in failed:
            print("  -", f)


def run():
    """Entry point: main(), with a Stop ending the batch quietly."""
    try:
        main()
    except KeyboardInterrupt:
        # Stop from the TUI or ctrl+c: finished repos are in --out and finished
        # PRs in each repo's checkpoint, so this is not an error
        print("[stop] batch interrupted; finished repos and PRs are kept, so a rerun "
              "resumes", file=sys.stderr, flush=True)
        try:
            _sync_now("stopped")
        except KeyboardInterrupt:           # a second Stop skips the sync
            print("[sync] skipped; the next run syncs", file=sys.stderr, flush=True)
        sys.exit(130)


if __name__ == "__main__":
    run()
