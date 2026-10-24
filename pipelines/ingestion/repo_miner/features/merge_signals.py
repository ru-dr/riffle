"""
merge_signals.py — the AIDev feature catalog's merge-model features that the
miner's own data can supply (ADR 0009 rows, ADR 0010 point-in-time rule).

Per PR (from the PR object, its timeline and the clone at the base commit):
  has_linked_issue        "fixes #N" / "closes owner/repo#N" / an issue URL in the body
  body_headings, body_checklist_items, body_has_test_plan
                          template structure of the body
  agent_boilerplate       an agent watermark in the body ("Generated with Claude
                          Code", "Co-authored-by: Copilot", ...)
  agent_name              which coding agent opened it (claude_code, codex,
                          copilot, cursor, devin, jules, ...), from the author
                          login, head branch prefix, git identity and body
                          watermark; null for none
  head_branch_agent_prefix
                          the head branch carries an agent prefix (codex/,
                          copilot/, cursor/, ...): how the agent was detected
  author_account_age_days days from the author's account creation to open
  has_contributing, has_agents_md
                          a contributing guide (root, .github/, docs/; any case)
                          or an agent instructions file exists at the base commit

Per repo, prior-only (compute_repo: only PRs closed strictly before this one
opened, from the rows mined for the same repo):
  repo_outside_merge_rate share of outsiders' (author_association not OWNER /
                          MEMBER / COLLABORATOR) closed PRs that merged, with
                          repo_outside_closed_count beside it
  agent_recent_merge_rate share of the same agent's closed PRs that merged
                          (agent rows only), with agent_recent_closed_count
  repo_median_first_response_hours
                          median hours from open to the first human non-author
                          comment or review, over prior PRs that got one
  repo_agent_pr_load_30d  agent PRs opened in the 30 days before this one
  max_sim_open_prs        highest text similarity (title at open + body) to a
                          PR open at the moment this one opened
  max_sim_recent_merged   the same against PRs merged in the prior 90 days

null = not enough history or not observable; 0 = measured, none.
"""
from __future__ import annotations

import bisect
import re
import statistics
from datetime import datetime, timedelta, timezone

import config
import gitio

MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}

_LINKED_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:[\w.-]+/[\w.-]+)?#\d+|github\.com/[\w.-]+/[\w.-]+/issues/\d+",
    re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_CHECK_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]", re.MULTILINE)
_TEST_PLAN_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*)?\s*(?:test(?:ing)?\s*plan|"
                           r"how (?:was this|to) test(?:ed)?|testing)\b",
                           re.IGNORECASE | re.MULTILINE)

# (agent, login patterns, git-email patterns, body patterns). A login is matched whole (^...$), so a person
# named julesb or claude-docs is not an agent; the branch prefix is the agent's name plus "/" (see
# _BRANCH_PREFIX_RE), never "-", for the same reason.
_BOT = r"(?:\[bot\])?"
_AGENTS = (
    ("claude_code", (r"^claude\[bot\]$",), (r"noreply@anthropic\.com",),
     (r"generated with \[?claude code", r"co-authored-by: claude")),
    ("codex", (r"^chatgpt-codex-connector" + _BOT + r"$", r"^codex\[bot\]$"), (),
     (r"openai codex",)),
    ("copilot", (r"^copilot$", r"^copilot-swe-agent" + _BOT + r"$", r"^copilot\[bot\]$"), (),
     (r"co-authored-by: copilot",)),
    ("cursor", (r"^cursor\[bot\]$", r"^cursoragent" + _BOT + r"$"), (r"cursoragent@cursor\.com",),
     (r"cursor agent",)),
    ("devin", (r"^devin-ai-integration" + _BOT + r"$", r"^devin-ai" + _BOT + r"$"), (r"devin-ai-integration",),
     (r"devin run",)),
    ("jules", (r"^jules\[bot\]$", r"^google-labs-jules" + _BOT + r"$"), (r"jules\.google",), ()),
    ("sweep", (r"^sweep-ai" + _BOT + r"$",), (), ()),
)
_AGENT_RES = tuple((name, *(tuple(re.compile(p, re.IGNORECASE) for p in group) for group in groups))
                   for name, *groups in _AGENTS)
_BRANCH_AGENTS = {"claude": "claude_code", "codex": "codex", "copilot": "copilot", "cursor": "cursor",
                  "devin": "devin", "jules": "jules", "sweep": "sweep"}
_BRANCH_PREFIX_RE = re.compile(r"^(" + "|".join(_BRANCH_AGENTS) + r")/", re.IGNORECASE)
_BOILERPLATE_RE = re.compile(
    r"generated with \[?claude code|co-authored-by: (?:claude|copilot)|"
    r"this (?:pull request|pr) was (?:automatically )?(?:generated|created) by|"
    r"🤖 generated|devin run|openai codex|cursor agent|link to devin run",
    re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z][a-z0-9_]{2,}")
_STOP = frozenset("the and for this that with from are was were has have not but you "
                  "your can will should would into when then than also just its "
                  "pull request add adds added fix fixes fixed update updates".split())

_CONTRIB_RE = re.compile(r"^(?:\.github/|docs/)?contributing(?:\.(?:md|rst|txt|adoc))?$", re.IGNORECASE)
_AGENTS_RE = re.compile(r"^(?:agents\.md|claude\.md|\.cursorrules|\.cursor|"
                        r"\.github/copilot-instructions\.md)$", re.IGNORECASE)


def _ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- per PR
def body_features(body: str | None) -> dict:
    body = body or ""
    return {
        "has_linked_issue": bool(_LINKED_RE.search(body)),
        "body_headings": len(_HEADING_RE.findall(body)),
        "body_checklist_items": len(_CHECK_RE.findall(body)),
        "body_has_test_plan": bool(_TEST_PLAN_RE.search(body)),
        "agent_boilerplate": bool(_BOILERPLATE_RE.search(body)),
    }


def agent_name(login: str | None, head_ref: str | None, git_email: str | None,
               body: str | None) -> str | None:
    """The coding agent that opened a PR, from its author login, head branch prefix, git identity or body
    watermark; None for none. Checked in the agents' order, the first agent with any hit wins."""
    login, head_ref, git_email, body = login or "", head_ref or "", git_email or "", body or ""
    prefix = _BRANCH_PREFIX_RE.match(head_ref)
    branch = _BRANCH_AGENTS[prefix.group(1).lower()] if prefix else None
    for name, logins, emails, bodies in _AGENT_RES:
        if (any(rx.search(login) for rx in logins) or name == branch
                or any(rx.search(git_email) for rx in emails) or any(rx.search(body) for rx in bodies)):
            return name
    return None


def repo_files_at(repo: str, base: str) -> dict:
    """has_contributing / has_agents_md at the base commit: a contributing
    guide at the root, in .github/ or docs/ (any case and extension), and an
    agent instructions file (AGENTS.md, CLAUDE.md, .cursorrules, .cursor/,
    .github/copilot-instructions.md). Two cheap ls-tree calls, no recursion."""
    root = gitio._run(repo, "ls-tree", "--name-only", base, check=False).split("\n")
    sub = gitio._run(repo, "ls-tree", "--name-only", base, "--", ".github/", "docs/",
                     check=False).split("\n")
    names = [n for n in root + sub if n]
    return {"has_contributing": any(_CONTRIB_RE.match(n) for n in names),
            "has_agents_md": any(_AGENTS_RE.match(n) for n in names)}


def first_response_at(pr: dict, events: list[dict] | None) -> str | None:
    """When a human other than the author first commented or reviewed: an
    outcome, used only as prior-PR history for repo_median_first_response."""
    if events is None:
        return None
    author = str((pr.get("user") or {}).get("login", "")).lower()
    for ev in events:
        if ev.get("event") not in ("commented", "reviewed"):
            continue
        user = ev.get("user") or ev.get("actor") or {}
        login = str(user.get("login", "")).lower()
        if user.get("type") == "Bot" or login.endswith("[bot]") or login == author:
            continue
        when = ev.get("submitted_at") or ev.get("created_at")
        if when:
            return when
    return None


def tokens(title: str | None, body: str | None) -> list[str]:
    words = _WORD_RE.findall(f"{title or ''} {body or ''}".lower())
    return sorted({w for w in words if w not in _STOP})


def sidecar(pr: dict, row: dict, events: list[dict] | None, title_open: str | None) -> dict:
    """What compute_repo needs about this PR (kept in the checkpoint)."""
    return {
        "number": pr.get("number"),
        "opened": pr.get("created_at"),
        "closed": pr.get("merged_at") or pr.get("closed_at"),
        "merged": bool(pr.get("merged_at")),
        "outsider": (None if not pr.get("author_association")
                     else pr["author_association"] not in MAINTAINERS
                     and not pr.get("_author_maintainer")),
        "agent": row.get("agent_name"),
        "first_response": first_response_at(pr, events),
        "tokens": tokens(title_open if title_open is not None else pr.get("title"),
                         pr.get("body")),
    }


# ---------------------------------------------------------------- per repo
COLUMNS = ("repo_outside_merge_rate", "repo_outside_closed_count",
           "agent_recent_merge_rate", "agent_recent_closed_count",
           "repo_median_first_response_hours", "repo_agent_pr_load_30d",
           "max_sim_open_prs", "max_sim_recent_merged")


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


SIDECAR = "__merge_signals__"


class History:
    """The repo's PRs as merge-signal inputs (one sidecar each), indexed so the
    COLUMNS can be computed as of any moment: at a PR's open for the mined
    rows, or at a snapshot time (snapshots.py). Strictly prior-only: a PR
    counts once it closed before the moment, and a first response once it
    happened before it."""

    def __init__(self, sidecars: list[dict]):
        far = datetime.max.replace(tzinfo=timezone.utc)
        self.far = far
        self.prs = []
        for sc in sidecars:
            if not sc or not _ts(sc.get("opened")):
                continue
            self.prs.append({
                "sc": sc, "number": sc.get("number"), "opened": _ts(sc["opened"]),
                "closed": _ts(sc.get("closed")) or far, "merged": sc.get("merged"),
                "outsider": sc.get("outsider"), "agent": sc.get("agent"),
                "first_hours": ((_ts(sc["first_response"]) - _ts(sc["opened"]))
                                .total_seconds() / 3600 if sc.get("first_response") else None),
                "first_at": _ts(sc.get("first_response")),
                "tokens": frozenset(sc.get("tokens") or ())})
        self.by_number: dict = {}
        for q in self.prs:
            self.by_number.setdefault(q["number"], q)
        self.by_close = sorted((p for p in self.prs if p["closed"] < far), key=lambda p: p["closed"])
        self.close_times = [p["closed"] for p in self.by_close]
        self.by_open = sorted(self.prs, key=lambda p: p["opened"])
        self.open_times = [p["opened"] for p in self.by_open]
        # open_now needs "opened < t and closed > t". Never-closed PRs are scanned as they are;
        # a closed one can only be open at t if it opened within the longest closed span before it
        self.unclosed = [p for p in self.by_open if p["closed"] >= far]
        self.max_span = max((p["closed"] - p["opened"] for p in self.by_close), default=timedelta(0))

    def at(self, p: dict, t: datetime, st: config.Settings, with_unmerged: bool) -> dict:
        """COLUMNS for PR p (an entry of self.prs) as of moment t."""
        out = {c: None for c in COLUMNS}
        lo = bisect.bisect_left(self.close_times, t - timedelta(days=st.merge_signal_lookback_days))
        hi = bisect.bisect_left(self.close_times, t)           # closed strictly before t
        # a PR is never its own history, and neither is a second sidecar of the same PR number
        # (a resumed run can write one twice)
        same = lambda q: q is p or (p["number"] is not None and q["number"] == p["number"])  # noqa: E731
        prior = [q for q in self.by_close[lo:hi] if not same(q)]
        outs = [q for q in prior if q["outsider"]]
        out["repo_outside_closed_count"] = len(outs)
        if with_unmerged and len(outs) >= st.merge_signal_min_prs:
            out["repo_outside_merge_rate"] = round(sum(q["merged"] for q in outs) / len(outs), 4)
        if p["agent"]:
            mine = [q for q in prior if q["agent"] == p["agent"]]
            out["agent_recent_closed_count"] = len(mine)
            if with_unmerged and len(mine) >= st.merge_signal_min_prs:
                out["agent_recent_merge_rate"] = round(sum(q["merged"] for q in mine) / len(mine), 4)
        resp = [q["first_hours"] for q in prior
                if q["first_hours"] is not None and q["first_at"] < t]
        if len(resp) >= st.merge_signal_min_prs:
            out["repo_median_first_response_hours"] = round(statistics.median(resp), 2)
        a = bisect.bisect_left(self.open_times, t - timedelta(days=30))
        b = bisect.bisect_left(self.open_times, t)
        out["repo_agent_pr_load_30d"] = sum(1 for q in self.by_open[a:b] if q["agent"] and not same(q))
        # open at t: opened before t and not yet closed
        w = bisect.bisect_left(self.open_times, t - self.max_span)
        open_now = [q for q in self.by_open[w:b] if q["closed"] > t and not same(q)]
        open_now += [q for q in self.unclosed if q["opened"] < t - self.max_span and not same(q)]
        out["max_sim_open_prs"] = round(max((_jaccard(p["tokens"], q["tokens"])
                                             for q in open_now), default=0.0), 4)
        m = bisect.bisect_left(self.close_times, t - timedelta(days=90))
        recent = [q for q in self.by_close[m:hi] if q["merged"] and not same(q)]
        out["max_sim_recent_merged"] = (round(max(_jaccard(p["tokens"], q["tokens"])
                                                  for q in recent), 4) if recent else None)
        return out


def compute_repo(rows: list[dict], st: config.Settings, with_unmerged: bool,
                 lead_in: list[dict] = ()) -> None:
    """Fill COLUMNS on every row that carries a sidecar, from the other rows
    and the lead-in sidecars (PRs before the mined block, metadata only),
    strictly prior to each row's open. Merge rates need the unmerged rows: when
    merge-outcome mining is off every mined PR merged, so they stay null."""
    carriers = [r for r in rows if r.get(SIDECAR)]
    hist = History([r[SIDECAR] for r in carriers] + list(lead_in))
    by_sc = {id(p["sc"]): p for p in hist.prs}
    for r in carriers:
        p = by_sc.get(id(r[SIDECAR]))
        if p is not None:
            r.update(hist.at(p, p["opened"], st, with_unmerged))
