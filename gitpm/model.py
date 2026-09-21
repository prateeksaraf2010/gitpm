"""Ticket model: a Pull Request *is* the work item.

Status is derived from PR state + labels (no separate database):
  draft + label status:backlog -> backlog
  draft                        -> in_progress
  open (ready for review)      -> in_review
  merged                       -> done
  closed, not merged           -> cancelled
Other fields are labels: priority:P0..P3, type:*, pts:N, epic:*, status:blocked.
"""
from datetime import datetime, timezone

STATUSES = ["backlog", "in_progress", "in_review", "done", "cancelled"]
PRIORITIES = ["P0", "P1", "P2", "P3"]
TYPES = ["feature", "bug", "chore", "spike"]
BACKLOG_LABEL = "status:backlog"
BLOCKED_LABEL = "status:blocked"


def _now():
    return datetime.now(timezone.utc)


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def label_value(labels, prefix):
    for l in labels:
        if l.startswith(prefix + ":"):
            return l.split(":", 1)[1]
    return None


def derive_status(pr):
    if pr.get("merged_at") or pr.get("merged"):
        return "done"
    if pr["state"] == "closed":
        return "cancelled"
    if pr.get("draft"):
        return "backlog" if BACKLOG_LABEL in pr["labels"] else "in_progress"
    return "in_review"


def to_item(pr, repo):
    """Normalize a (GitHub-shaped) PR dict into a ticket."""
    labels = pr["labels"]
    pts = label_value(labels, "pts")
    created, updated = parse_ts(pr["created_at"]), parse_ts(pr["updated_at"])
    closed = parse_ts(pr.get("merged_at") or pr.get("closed_at"))
    status = derive_status(pr)
    end = closed or _now()
    return {
        "key": f"{repo}#{pr['number']}",
        "repo": repo,
        "number": pr["number"],
        "title": pr["title"],
        "body": pr.get("body") or "",
        "url": pr["html_url"],
        "status": status,
        "priority": label_value(labels, "priority"),
        "type": label_value(labels, "type"),
        "points": int(pts) if pts and pts.isdigit() else None,
        "epic": label_value(labels, "epic"),
        "blocked": BLOCKED_LABEL in labels,
        "labels": labels,
        "assignees": pr.get("assignees", []),
        "author": pr.get("author"),
        "sprint": pr.get("milestone"),
        "created_at": pr["created_at"],
        "updated_at": pr["updated_at"],
        "closed_at": pr.get("merged_at") or pr.get("closed_at"),
        "age_days": round((end - created).total_seconds() / 86400, 1),
        "idle_days": round((_now() - updated).total_seconds() / 86400, 1),
        "reviewers": pr.get("reviewers", []),
        "comments": pr.get("comments", 0),
    }


def label_diff(item, status=None, priority=None, type=None, points=None, epic=None, blocked=None):
    """Turn field edits into (labels_to_add, labels_to_remove)."""
    add, remove = [], []

    def setp(prefix, value):
        if value is None:
            return
        for l in item["labels"]:
            if l.startswith(prefix + ":"):
                remove.append(l)
        if value != "":
            add.append(f"{prefix}:{value}")

    setp("priority", priority)
    setp("type", type)
    setp("pts", points)
    setp("epic", epic)
    if blocked is True:
        add.append(BLOCKED_LABEL)
    elif blocked is False and BLOCKED_LABEL in item["labels"]:
        remove.append(BLOCKED_LABEL)
    add = [a for a in add if a not in item["labels"]]
    return add, remove


def metrics(items):
    """Deterministic project metrics (no AI needed)."""
    by = {s: [i for i in items if i["status"] == s] for s in STATUSES}
    open_items = [i for i in items if i["status"] in ("backlog", "in_progress", "in_review")]
    done = by["done"]
    cycle = sorted(i["age_days"] for i in done)
    load = {}
    for i in open_items:
        for a in i["assignees"] or ["unassigned"]:
            d = load.setdefault(a, {"items": 0, "points": 0})
            d["items"] += 1
            d["points"] += i["points"] or 0
    now = _now()
    week_ago = 7 * 86400
    throughput = [0] * 4  # merged per week, last 4 weeks (oldest first)
    for i in done:
        age = (now - parse_ts(i["closed_at"])).total_seconds()
        w = int(age // week_ago)
        if w < 4:
            throughput[3 - w] += 1
    return {
        "counts": {s: len(v) for s, v in by.items()},
        "points": {s: sum(i["points"] or 0 for i in v) for s, v in by.items()},
        "median_cycle_days": cycle[len(cycle) // 2] if cycle else None,
        "throughput_4w": throughput,
        "stale": [i["key"] for i in open_items if i["idle_days"] >= 7],
        "blocked": [i["key"] for i in open_items if i["blocked"]],
        "unassigned": [i["key"] for i in open_items if not i["assignees"]],
        "untriaged": [i["key"] for i in open_items if not (i["priority"] and i["type"] and i["points"])],
        "p0_open": [i["key"] for i in open_items if i["priority"] == "P0"],
        "workload": load,
    }
