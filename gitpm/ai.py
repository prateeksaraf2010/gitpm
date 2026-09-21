"""Claude-powered project manager: tool-using agent, auto-triage, status briefs."""
import json
import os
import re
import urllib.error
import urllib.request

from .model import PRIORITIES, STATUSES, TYPES, metrics

MODEL = os.environ.get("GITPM_MODEL", "claude-sonnet-5")


class AIError(Exception):
    pass


def claude(messages, system, tools=None, max_tokens=2048):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AIError("ANTHROPIC_API_KEY is not set")
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AIError(f"Anthropic API {e.code}: {e.read().decode()[:300]}")


def _text(resp):
    return "".join(b["text"] for b in resp["content"] if b["type"] == "text").strip()


def _compact(i):
    return {k: i[k] for k in ("key", "title", "status", "priority", "type", "points", "epic",
                              "blocked", "assignees", "sprint", "age_days", "idle_days")}


SYSTEM = """You are GitPM, an AI project manager. GitHub Pull Requests are the tickets (the Jira replacement).
Statuses: backlog (draft PR labelled status:backlog), in_progress (draft PR), in_review (ready PR), done (merged), cancelled (closed).
Priorities: P0 (drop everything) .. P3 (nice to have). Points: 1,2,3,5,8,13. Types: feature, bug, chore, spike.
Use the tools to look up real data before answering; never invent tickets. Take actions the user asks for.
Never merge (status=done) or cancel a ticket unless the user explicitly asked for that ticket. Be concise; reference tickets as repo#number."""

ITEM_KEY = {"type": "string", "description": "Ticket key like owner/repo#12"}
TOOLS = [
    {"name": "list_items", "description": "List tickets, optionally filtered. Returns compact rows.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": STATUSES}, "assignee": {"type": "string"},
         "priority": {"type": "string", "enum": PRIORITIES}, "text": {"type": "string"}}}},
    {"name": "get_item", "description": "Full details of one ticket including body.",
     "input_schema": {"type": "object", "properties": {"key": ITEM_KEY}, "required": ["key"]}},
    {"name": "get_metrics", "description": "Project metrics: counts, points, cycle time, stale, blocked, workload.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "create_item", "description": "Open a new ticket (draft PR in backlog).",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "body": {"type": "string", "description": "Description + acceptance criteria"},
         "repo": {"type": "string"}, "priority": {"type": "string", "enum": PRIORITIES},
         "type": {"type": "string", "enum": TYPES}, "points": {"type": "integer"},
         "assignee": {"type": "string"}, "epic": {"type": "string"}}, "required": ["title"]}},
    {"name": "update_item", "description": "Edit priority/type/points/epic/blocked/assignees/sprint of a ticket.",
     "input_schema": {"type": "object", "properties": {
         "key": ITEM_KEY, "priority": {"type": "string", "enum": PRIORITIES}, "type": {"type": "string", "enum": TYPES},
         "points": {"type": "integer"}, "epic": {"type": "string"}, "blocked": {"type": "boolean"},
         "assignees": {"type": "array", "items": {"type": "string"}}, "sprint": {"type": "string"}},
         "required": ["key"]}},
    {"name": "transition", "description": "Move a ticket: backlog, in_progress, in_review (mark PR ready), done (MERGES the PR), cancelled (closes the PR).",
     "input_schema": {"type": "object", "properties": {"key": ITEM_KEY, "status": {"type": "string", "enum": STATUSES}},
                      "required": ["key", "status"]}},
    {"name": "comment", "description": "Post a comment on the ticket's PR.",
     "input_schema": {"type": "object", "properties": {"key": ITEM_KEY, "text": {"type": "string"}}, "required": ["key", "text"]}},
]


def run_tool(store, name, a):
    if name == "list_items":
        rows = store.list_items()
        for f in ("status", "priority"):
            if a.get(f):
                rows = [r for r in rows if r[f] == a[f]]
        if a.get("assignee"):
            rows = [r for r in rows if a["assignee"] in r["assignees"]]
        if a.get("text"):
            rows = [r for r in rows if a["text"].lower() in r["title"].lower()]
        return [_compact(r) for r in rows][:80]
    if name == "get_item":
        i = store.get_item(a["key"])
        return {**_compact(i), "body": i["body"][:2000], "url": i["url"], "reviewers": i["reviewers"]}
    if name == "get_metrics":
        return metrics(store.list_items())
    if name == "create_item":
        i = store.create_item(a.get("repo"), a["title"], a.get("body", ""), a.get("priority"), a.get("type"),
                              a.get("points"), a.get("assignee"), a.get("epic"))
        return {"created": i["key"], "url": i["url"]}
    if name == "update_item":
        f = {k: a[k] for k in ("priority", "type", "points", "epic", "blocked", "assignees") if k in a}
        if "points" in f:
            f["points"] = str(f["points"])
        if f:
            store.set_fields(a["key"], **f)
        if a.get("sprint"):
            store.set_sprint(a["key"], a["sprint"])
        return _compact(store.get_item(a["key"]))
    if name == "transition":
        return _compact(store.transition(a["key"], a["status"]))
    if name == "comment":
        store.comment(a["key"], a["text"] + "\n\n<sub>— GitPM</sub>")
        return {"ok": True}
    raise ValueError(f"unknown tool {name}")


def agent(store, message, history=None, max_steps=10):
    """Run the tool loop. Returns (reply_text, actions_taken, updated_history)."""
    messages = list(history or []) + [{"role": "user", "content": message}]
    actions = []
    for _ in range(max_steps):
        resp = claude(messages, SYSTEM, TOOLS, 2048)
        messages.append({"role": "assistant", "content": resp["content"]})
        if resp["stop_reason"] != "tool_use":
            return _text(resp), actions, messages
        results = []
        for b in resp["content"]:
            if b["type"] != "tool_use":
                continue
            try:
                out, err = run_tool(store, b["name"], b["input"]), False
            except Exception as e:  # surface tool errors to the model so it can recover
                out, err = str(e), True
            if b["name"] in ("create_item", "update_item", "transition", "comment"):
                actions.append({"tool": b["name"], "input": b["input"], "error": err})
            results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": err,
                            "content": json.dumps(out)[:12000]})
        messages.append({"role": "user", "content": results})
    return "Stopped after too many steps.", actions, messages


def _json_from(text):
    m = re.search(r"\[.*\]|\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def triage(store, keys=None, apply=True):
    """Fill in missing priority/type/points for untriaged open tickets."""
    items = [i for i in store.list_items() if i["status"] in ("backlog", "in_progress", "in_review")
             and not (i["priority"] and i["type"] and i["points"])]
    if keys:
        items = [i for i in items if i["key"] in keys]
    items = items[:25]
    if not items:
        return []
    payload = [{"key": i["key"], "title": i["title"], "body": i["body"][:600], "priority": i["priority"],
                "type": i["type"], "points": i["points"]} for i in items]
    resp = claude([{"role": "user", "content":
                    "Triage these tickets. For each, fill ONLY missing fields. priority in P0-P3, type in "
                    "feature/bug/chore/spike, points in 1,2,3,5,8,13 (Fibonacci effort), plus a one-line reason. "
                    "Reply with ONLY a JSON array of {key, priority, type, points, reason}.\n" + json.dumps(payload)}],
                  SYSTEM, max_tokens=3000)
    by_key = {i["key"]: i for i in items}
    out = []
    for r in _json_from(_text(resp)) or []:
        it = by_key.get(r.get("key"))
        if not it:
            continue
        f = {}
        if not it["priority"] and r.get("priority") in PRIORITIES:
            f["priority"] = r["priority"]
        if not it["type"] and r.get("type") in TYPES:
            f["type"] = r["type"]
        if not it["points"] and r.get("points"):
            f["points"] = str(r["points"])
        if f and apply:
            store.set_fields(it["key"], **f)
        out.append({"key": it["key"], "set": f, "reason": r.get("reason", "")})
    return out


def brief(store, kind="standup"):
    items = store.list_items()
    live = [_compact(i) for i in items if i["status"] in ("backlog", "in_progress", "in_review")]
    recent = [_compact(i) for i in items if i["status"] == "done"][:15]
    prompt = {"standup": "Write a crisp daily standup: what shipped, what's in flight, what's at risk, and 3 concrete recommended actions.",
              "sprint": "Write a sprint health report: progress vs scope by points, throughput trend, risks, and what to cut or re-plan.",
              "risks": "List the top project risks (blocked, stale, overloaded people, P0/P1 not moving) with a specific fix each."}[kind]
    data = json.dumps({"metrics": metrics(items), "open": live, "recently_done": recent})
    resp = claude([{"role": "user", "content": f"{prompt}\nUse markdown, be concise, cite ticket keys.\n\nDATA:\n{data}"}], SYSTEM, max_tokens=1500)
    return _text(resp)
