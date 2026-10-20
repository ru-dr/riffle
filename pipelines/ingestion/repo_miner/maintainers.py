"""
maintainers.py — who is a maintainer, confirmed from the repo's own history
(proposal 2.2).

author_association reflects today, and GitHub reports an org member with
private membership as CONTRIBUTOR, so company-run repos look more "outside"
than they are. The repo's timelines say more: anyone who merged or closed
someone else's PR is a maintainer from that moment on. This is the evidence:
{login: the first time they did}, built from every timeline the miner reads
(mined PRs, stale-open PRs, the lead-in), and asked point-in-time.

It catches the people who triage. An employee who never merges or closes
others' PRs still shows as an outside contributor.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from merge_labels import _event_time, _event_user, _is_bot, _login

FILE = "maintainers.json"


class Evidence:
    def __init__(self, first: dict[str, datetime] | None = None):
        self.first: dict[str, datetime] = dict(first or {})

    @classmethod
    def from_prs(cls, prs: list[dict]) -> "Evidence":
        ev = cls()
        for pr in prs:
            ev.add(pr, pr.get("_events"))
        return ev

    def add(self, pr: dict, events: list[dict] | None) -> None:
        author = _login(pr.get("user"))
        for e in events or []:
            if e.get("event") not in ("merged", "closed"):
                continue
            user = _event_user(e)
            login, when = _login(user), _event_time(e)
            if not login or not when or _is_bot(user) or login == author:
                continue
            if login not in self.first or when < self.first[login]:
                self.first[login] = when

    def confirmed(self, login: str | None, t: datetime | None) -> bool:
        """Had `login` merged or closed someone else's PR before t?"""
        when = self.first.get(str(login or "").lower())
        return bool(when and t and when < t)

    def save(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, FILE), "w", encoding="utf-8") as fh:
            json.dump({k: v.strftime("%Y-%m-%dT%H:%M:%SZ") for k, v in sorted(self.first.items())}, fh)

    @classmethod
    def load(cls, directory: str) -> "Evidence":
        path = os.path.join(directory, FILE)
        if not os.path.isfile(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls({k: datetime.fromisoformat(v.replace("Z", "+00:00")) for k, v in raw.items()})
