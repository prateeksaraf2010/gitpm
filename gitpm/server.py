"""HTTP server: serves the dashboard and a small JSON API. Run: python -m gitpm"""
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ai
from .demo import DemoStore
from .github import GitHubError, GitHubStore
from .model import metrics

PUBLIC = Path(__file__).resolve().parent.parent / "public"


def make_store():
    token, repos = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITPM_REPOS", "")
    repos = [r.strip() for r in repos.split(",") if r.strip()]
    if token and repos:
        return GitHubStore(token, repos), False
    print("! GITHUB_TOKEN / GITPM_REPOS not set -> running in DEMO mode with sample data", file=sys.stderr)
    return DemoStore(), True


STORE, DEMO = make_store()
_cache = {"t": 0, "items": None}


def items(fresh=False):
    if fresh or not _cache["items"] or time.time() - _cache["t"] > 20:
        _cache.update(t=time.time(), items=STORE.list_items())
    return _cache["items"]


def invalidate():
    _cache["items"] = None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _guard(self, fn):
        try:
            self._send(200, fn())
        except (GitHubError, ai.AIError) as e:
            self._send(502, {"error": str(e)})
        except (KeyError, StopIteration, ValueError) as e:
            self._send(400, {"error": f"bad request: {e!r}"})
        except Exception as e:
            self._send(500, {"error": repr(e)})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/config":
            return self._send(200, {"demo": DEMO, "repos": STORE.repos, "ai": bool(os.environ.get("ANTHROPIC_API_KEY"))})
        if u.path == "/api/items":
            return self._guard(lambda: {"items": items("fresh" in parse_qs(u.query)), "metrics": metrics(items())})
        m = re.fullmatch(r"/api/brief/(standup|sprint|risks)", u.path)
        if m:
            return self._guard(lambda: {"markdown": ai.brief(STORE, m.group(1))})
        f = PUBLIC / ("index.html" if u.path == "/" else u.path.lstrip("/"))
        if f.is_file() and PUBLIC in f.resolve().parents:
            ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css"}.get(f.suffix[1:], "application/octet-stream")
            return self._send(200, f.read_bytes(), ctype + "; charset=utf-8")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path
        b = self._body()

        def run(fn):
            def w():
                r = fn()
                invalidate()
                return r
            self._guard(w)

        if p == "/api/items":
            return run(lambda: STORE.create_item(b.get("repo"), b["title"], b.get("body", ""), b.get("priority"),
                                                 b.get("type"), b.get("points"), b.get("assignee"), b.get("epic")))
        if p == "/api/transition":
            return run(lambda: STORE.transition(b["key"], b["status"]))
        if p == "/api/fields":
            key = b.pop("key")
            return run(lambda: STORE.set_fields(key, **b))
        if p == "/api/triage":
            return run(lambda: {"triaged": ai.triage(STORE, b.get("keys"))})
        if p == "/api/agent":
            def go():
                reply, actions, hist = ai.agent(STORE, b["message"], b.get("history"))
                return {"reply": reply, "actions": actions, "history": hist}
            return run(go)
        self._send(404, {"error": "not found"})


def main():
    port = int(os.environ.get("PORT", "8787"))
    print(f"GitPM dashboard -> http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
