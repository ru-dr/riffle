"""
scope.py — which PRs are training PRs: the rows the merge model trains on,
and the ones that get LLM calls and snapshots (--llm-scope training).

Outside contributors who are not bots (an AI agent is never a bot here), plus
a fixed sample_pct of the rest, the same PRs on every run (a hash of repo and
PR number). An unknown association (no API) counts as outside.
"""
from __future__ import annotations

import hashlib

OUTSIDE_ASSOCIATIONS = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"}


def sampled(repo_key: str, number, sample_pct: int) -> bool:
    key = f"{repo_key}#{number}".encode()
    return int(hashlib.sha1(key).hexdigest(), 16) % 100 < sample_pct


def is_training(repo_key: str, number, association: str | None, is_bot: bool,
                confirmed_maintainer: bool | None, sample_pct: int) -> bool:
    outside = ((association is None or association in OUTSIDE_ASSOCIATIONS)
               and not confirmed_maintainer)
    if not is_bot and outside:
        return True
    return sampled(repo_key, number, sample_pct)


def pr_list_is_training(pr: dict, repo_key: str, sample_pct: int) -> bool:
    """The same rule on a PR-list object, before the PR is mined (for
    --cap-on training). Maintainers confirmed from history are only known
    later, so the count it gives can come out slightly high."""
    from features import merge_signals
    user = pr.get("user") or {}
    login = user.get("login")
    agent = merge_signals.agent_name(login, (pr.get("head") or {}).get("ref"), None, pr.get("body"))
    is_bot = (user.get("type") == "Bot" or str(login or "").lower().endswith("[bot]")) and agent is None
    return is_training(repo_key, pr.get("number"), pr.get("author_association"), is_bot, None, sample_pct)
