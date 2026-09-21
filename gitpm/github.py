"""GitHub-backed store. Every ticket is a PR; creating a ticket opens a draft PR
carrying a small .gitpm/items/<slug>.md file so the PR has a real diff."""
import base64
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .model import BACKLOG_LABEL, label_diff, to_item

API = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHubStore:
    def __init__(self, token, repos):
        self.token, self.repos = token, repos

    # ---- transport
    def _req(self, method, path, body=None, graphql=False):
        url = f"{API}/graphql" if graphql else (path if path.startswith("http") else API + path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gitpm",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}, r.headers
        except urllib.error.HTTPError as e:
            raise GitHubError(f"{method} {path}: {e.code} {e.read().decode()[:300]}")

    def _gql(self, query, **variables):
        out, _ = self._req("POST", None, {"query": query, "variables": variables}, graphql=True)
        if out.get("errors"):
            raise GitHubError(str(out["errors"])[:300])
        return out["data"]

    def _paginate(self, path):
        out = []
        while path:
            page, headers = self._req("GET", path)
            out += page
            m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            path = m.group(1) if m else None
        return out

    # ---- reads
    def _repo_items(self, repo):
        prs = self._paginate(f"/repos/{repo}/pulls?state=all&per_page=100&sort=updated&direction=desc")
        return [to_item(self._norm(p), repo) for p in prs[:300]]

    @staticmethod
    def _norm(p):
        return {
            "number": p["number"], "title": p["title"], "body": p.get("body"),
            "html_url": p["html_url"], "state": p["state"], "draft": p.get("draft", False),
            "merged_at": p.get("merged_at"), "closed_at": p.get("closed_at"),
            "created_at": p["created_at"], "updated_at": p["updated_at"],
            "labels": [l["name"] for l in p["labels"]],
            "assignees": [a["login"] for a in p["assignees"]],
            "reviewers": [r["login"] for r in p.get("requested_reviewers", [])],
            "author": p["user"]["login"],
            "milestone": (p.get("milestone") or {}).get("title"),
            "node_id": p["node_id"],
        }

    def list_items(self):
        with ThreadPoolExecutor(max_workers=4) as ex:
            return [i for chunk in ex.map(self._repo_items, self.repos) for i in chunk]

    def get_item(self, key):
        repo, num = key.split("#")
        p, _ = self._req("GET", f"/repos/{repo}/pulls/{num}")
        return to_item(self._norm(p), repo)

    # ---- writes
    def create_item(self, repo, title, body="", priority=None, type=None, points=None,
                    assignee=None, epic=None):
        repo = repo or self.repos[0]
        info, _ = self._req("GET", f"/repos/{repo}")
        base = info["default_branch"]
        sha = self._req("GET", f"/repos/{repo}/git/ref/heads/{base}")[0]["object"]["sha"]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "item"
        branch = f"pm/{slug}-{sha[:5]}"
        self._req("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        md = f"# {title}\n\n{body}\n"
        self._req("PUT", f"/repos/{repo}/contents/.gitpm/items/{slug}.md", {
            "message": f"pm: open ticket '{title}'", "branch": branch,
            "content": base64.b64encode(md.encode()).decode()})
        pr, _ = self._req("POST", f"/repos/{repo}/pulls", {
            "title": title, "head": branch, "base": base, "draft": True,
            "body": body or "_Created by GitPM._"})
        labels = [BACKLOG_LABEL]
        for prefix, v in (("priority", priority), ("type", type), ("pts", points), ("epic", epic)):
            if v:
                labels.append(f"{prefix}:{v}")
        self._req("POST", f"/repos/{repo}/issues/{pr['number']}/labels", {"labels": labels})
        if assignee:
            self._req("POST", f"/repos/{repo}/issues/{pr['number']}/assignees", {"assignees": [assignee]})
        return self.get_item(f"{repo}#{pr['number']}")

    def update_labels(self, key, add, remove):
        repo, num = key.split("#")
        for l in remove:
            try:
                self._req("DELETE", f"/repos/{repo}/issues/{num}/labels/{urllib.request.quote(l, safe='')}")
            except GitHubError:
                pass
        if add:
            self._ensure_labels(repo, add)
            self._req("POST", f"/repos/{repo}/issues/{num}/labels", {"labels": add})

    def _ensure_labels(self, repo, names):
        for n in names:
            try:
                self._req("POST", f"/repos/{repo}/labels", {"name": n, "color": "6e7781"})
            except GitHubError:
                pass  # already exists

    def assign(self, key, logins):
        repo, num = key.split("#")
        self._req("POST", f"/repos/{repo}/issues/{num}/assignees", {"assignees": logins})

    def comment(self, key, text):
        repo, num = key.split("#")
        self._req("POST", f"/repos/{repo}/issues/{num}/comments", {"body": text})

    def set_sprint(self, key, title):
        repo, num = key.split("#")
        ms = self._paginate(f"/repos/{repo}/milestones?state=all&per_page=100")
        m = next((m for m in ms if m["title"] == title), None)
        if not m:
            m, _ = self._req("POST", f"/repos/{repo}/milestones", {"title": title})
        self._req("PATCH", f"/repos/{repo}/issues/{num}", {"milestone": m["number"]})

    def transition(self, key, to):
        repo, num = key.split("#")
        item = self.get_item(key)
        cur = item["status"]
        if cur == to:
            return item
        pr, _ = self._req("GET", f"/repos/{repo}/pulls/{num}")
        node = pr["node_id"]
        if to == "backlog":
            if not pr["draft"]:
                self._gql("mutation($id:ID!){convertPullRequestToDraft(input:{pullRequestId:$id}){clientMutationId}}", id=node)
            self.update_labels(key, [BACKLOG_LABEL], [])
        elif to == "in_progress":
            if cur == "cancelled":
                self._req("PATCH", f"/repos/{repo}/pulls/{num}", {"state": "open"})
            if not pr["draft"] and cur == "in_review":
                self._gql("mutation($id:ID!){convertPullRequestToDraft(input:{pullRequestId:$id}){clientMutationId}}", id=node)
            self.update_labels(key, [], [BACKLOG_LABEL])
        elif to == "in_review":
            if cur == "cancelled":
                self._req("PATCH", f"/repos/{repo}/pulls/{num}", {"state": "open"})
            self.update_labels(key, [], [BACKLOG_LABEL])
            if pr["draft"] or cur == "cancelled":
                self._gql("mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){clientMutationId}}", id=node)
        elif to == "done":
            if pr["draft"]:
                self._gql("mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){clientMutationId}}", id=node)
            self._req("PUT", f"/repos/{repo}/pulls/{num}/merge", {"merge_method": "squash"})
        elif to == "cancelled":
            self._req("PATCH", f"/repos/{repo}/pulls/{num}", {"state": "closed"})
        else:
            raise GitHubError(f"unknown status {to}")
        return self.get_item(key)

    def set_fields(self, key, **fields):
        item = self.get_item(key)
        add, remove = label_diff(item, **{k: v for k, v in fields.items() if k != "assignees"})
        if add or remove:
            self.update_labels(key, add, remove)
        if fields.get("assignees"):
            self.assign(key, fields["assignees"])
        return self.get_item(key)
