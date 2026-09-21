"""In-memory store with the same interface as GitHubStore, seeded with sample data.
Used when GITHUB_TOKEN is not set so the dashboard/agent can be tried instantly."""
from datetime import datetime, timedelta, timezone

from .model import BACKLOG_LABEL, label_diff, to_item

REPO = "acme/webapp"


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class DemoStore:
    repos = [REPO]

    def __init__(self):
        S = [  # title, state, draft, merged, labels, assignees, created_days_ago, idle_days, milestone
            ("Add SSO login with Okta", "open", False, False, ["priority:P0", "type:feature", "pts:8", "epic:auth"], ["maya"], 9, 1, "Sprint 12"),
            ("Fix N+1 query on /projects endpoint", "open", True, False, ["priority:P1", "type:bug", "pts:3"], ["dev"], 5, 0.5, "Sprint 12"),
            ("Migrate CI to GitHub Actions", "open", False, False, ["priority:P2", "type:chore", "pts:5", "status:blocked"], ["sam"], 21, 12, "Sprint 12"),
            ("Dark mode for settings page", "open", True, False, ["status:backlog", "type:feature"], [], 3, 3, None),
            ("Investigate websocket disconnects", "open", True, False, ["status:backlog", "priority:P1"], [], 2, 2, None),
            ("Upgrade to Postgres 16", "closed", False, True, ["priority:P2", "type:chore", "pts:5"], ["sam"], 14, 3, "Sprint 11"),
            ("Rate-limit public API", "closed", False, True, ["priority:P1", "type:feature", "pts:5", "epic:platform"], ["maya"], 10, 5, "Sprint 11"),
            ("Fix CSV export encoding", "closed", False, True, ["priority:P3", "type:bug", "pts:2"], ["dev"], 8, 9, "Sprint 11"),
            ("Drop legacy v1 endpoints", "closed", False, False, ["priority:P3", "type:chore"], [], 30, 20, None),
            ("Onboarding checklist redesign", "open", False, False, ["priority:P2", "type:feature", "pts:5"], ["ana"], 6, 4, "Sprint 12"),
        ]
        self.prs = []
        for n, (t, st, dr, mg, lb, asg, c, idle, ms) in enumerate(S, start=101):
            self.prs.append({
                "number": n, "title": t, "body": f"Ticket: {t}", "html_url": f"https://github.com/{REPO}/pull/{n}",
                "state": st, "draft": dr, "merged_at": _ago(idle) if mg else None,
                "closed_at": _ago(idle) if st == "closed" else None, "created_at": _ago(c),
                "updated_at": _ago(idle), "labels": lb, "assignees": asg, "author": "maya",
                "milestone": ms, "reviewers": [], "comments": 0})

    def _find(self, key):
        return next(p for p in self.prs if p["number"] == int(key.split("#")[1]))

    def list_items(self):
        return [to_item(p, REPO) for p in self.prs]

    def get_item(self, key):
        return to_item(self._find(key), REPO)

    def create_item(self, repo=None, title="", body="", priority=None, type=None, points=None,
                    assignee=None, epic=None):
        n = max(p["number"] for p in self.prs) + 1
        labels = [BACKLOG_LABEL] + [f"{k}:{v}" for k, v in
                                    (("priority", priority), ("type", type), ("pts", points), ("epic", epic)) if v]
        self.prs.append({"number": n, "title": title, "body": body, "html_url": f"https://github.com/{REPO}/pull/{n}",
                         "state": "open", "draft": True, "merged_at": None, "closed_at": None,
                         "created_at": _ago(0), "updated_at": _ago(0), "labels": labels,
                         "assignees": [assignee] if assignee else [], "author": "you",
                         "milestone": None, "reviewers": [], "comments": 0})
        return self.get_item(f"{REPO}#{n}")

    def update_labels(self, key, add, remove):
        p = self._find(key)
        p["labels"] = [l for l in p["labels"] if l not in remove] + [a for a in add if a not in p["labels"]]
        p["updated_at"] = _ago(0)

    def assign(self, key, logins):
        p = self._find(key)
        p["assignees"] = sorted(set(p["assignees"]) | set(logins))
        p["updated_at"] = _ago(0)

    def comment(self, key, text):
        p = self._find(key)
        p["comments"] += 1
        p["updated_at"] = _ago(0)

    def set_sprint(self, key, title):
        self._find(key)["milestone"] = title

    def transition(self, key, to):
        p = self._find(key)
        lb = [l for l in p["labels"] if l != BACKLOG_LABEL]
        if to == "backlog":
            p.update(state="open", draft=True, merged_at=None, closed_at=None, labels=lb + [BACKLOG_LABEL])
        elif to == "in_progress":
            p.update(state="open", draft=True, merged_at=None, closed_at=None, labels=lb)
        elif to == "in_review":
            p.update(state="open", draft=False, merged_at=None, closed_at=None, labels=lb)
        elif to == "done":
            p.update(state="closed", draft=False, merged_at=_ago(0), closed_at=_ago(0), labels=lb)
        elif to == "cancelled":
            p.update(state="closed", closed_at=_ago(0), labels=lb)
        p["updated_at"] = _ago(0)
        return self.get_item(key)

    def set_fields(self, key, **fields):
        item = self.get_item(key)
        add, remove = label_diff(item, **{k: v for k, v in fields.items() if k != "assignees"})
        self.update_labels(key, add, remove)
        if fields.get("assignees"):
            self.assign(key, fields["assignees"])
        return self.get_item(key)
