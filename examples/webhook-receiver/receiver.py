#!/usr/bin/env python3
"""A reference receiver for a world's watch webhooks: every change becomes a GitHub issue.

A watch posts a diff — the rows that were ADDED, REMOVED or UPDATED in a saved view since
the last run, each with a stable key. This receiver turns that into the one thing a Slack
message is not, an owned obligation: one issue per subject, opened when the row appears,
commented when it changes, closed when it goes. The world supplies the identity (the key)
and the meaning (before, after, named transitions); this file decides what to do with it,
which is the whole point of a webhook rather than an adapter — copy it and change the
decisions.

Run it anywhere that can reach api.github.com and that the appliance can reach:

    export WORLD_WEBHOOK_SECRET=…   # the `secret` on the watch; the body is checked against it
    export GITHUB_TOKEN=…           # a fine-grained token with issues: write on one repository
    export GITHUB_REPO=owner/name
    export RECEIVER_TOKEN=…         # optional: what the watch's `headers` must carry
    python3 receiver.py             # listens on $PORT, default 8787

Then register the watch with `delivery.url` pointing here, `delivery.secret` set to the same
value, and, if you set RECEIVER_TOKEN, `delivery.headers: {"X-Receiver-Token": "…"}`.

What is deliberately NOT here: a queue, a database, retries. The world retries a delivery
that fails, and a redelivery carries the same X-World-Delivery id, so the only state this
needs is the set of diff ids it has already applied — held in memory, because a restart
that replays one diff only re-runs an idempotent mapping. GitHub calls are synchronous, so a
GitHub outage answers the world with a 500 and the world asks again.
"""
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNATURE_HEADER = "X-World-Signature"
DELIVERY_HEADER = "X-World-Delivery"
RECEIVER_TOKEN_HEADER = "X-Receiver-Token"

# A label per subject is how an issue is found again. GitHub caps a label at 50 characters
# and a key can be longer and carry characters a label cannot, so the label holds a digest of
# the key and the key itself goes in the issue body where a person can read it.
LABEL_PREFIX = "world:"


def genuine(secret: str, body: bytes, header: str | None) -> bool:
    """The signature check from the guide, verbatim: reject anything the secret did not sign."""
    if not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def subject_label(key: str) -> str:
    return LABEL_PREFIX + hashlib.sha1(key.encode()).hexdigest()[:12]


def display(change: dict) -> str:
    row = change.get("after") or change.get("before") or {}
    name = row.get("name") if isinstance(row, dict) else None
    return name if isinstance(name, str) and name.strip() else change["key"]


def field_lines(change: dict) -> list[str]:
    return [
        f"- `{f.get('path')}`: `{json.dumps(f.get('before'))}` → `{json.dumps(f.get('after'))}`"
        for f in change.get("fields", [])
    ]


def transition_labels(change: dict) -> list[str]:
    return [f"transition:{t['kind']}" for t in change.get("transitions", []) if t.get("kind")]


class GitHub:
    """The four calls this needs, over urllib so the file has no dependencies."""

    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo

    def call(self, method: str, path: str, payload: dict | None = None) -> dict | list:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"https://api.github.com/repos/{self.repo}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.load(res)

    def open_issue_for(self, label: str) -> dict | None:
        found = self.call("GET", f"/issues?state=open&labels={label}&per_page=1")
        return found[0] if found else None

    def create(self, title: str, body: str, labels: list[str]) -> dict:
        return self.call("POST", "/issues", {"title": title, "body": body, "labels": labels})

    def comment(self, number: int, body: str) -> None:
        self.call("POST", f"/issues/{number}/comments", {"body": body})

    def close(self, number: int) -> None:
        self.call("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})


def apply(diff: dict, github: GitHub) -> list[str]:
    """Map one diff onto issues and say what was done, one line per action."""
    done: list[str] = []
    watch_name = diff.get("name", diff.get("watch", "watch"))
    header = f"_{watch_name}_ · diff `{diff.get('diff')}` at {diff.get('at')}"
    for kind in ("added", "updated", "removed"):
        for change in diff.get(kind, []):
            key = change["key"]
            label = subject_label(key)
            issue = github.open_issue_for(label)
            title = f"{display(change)} — {watch_name}"
            href = (change.get("subject") or {}).get("href")
            link = f"\n\n[Open in the world]({href})" if href else ""
            if kind == "added":
                if issue:
                    github.comment(issue["number"], f"{header}\n\nAppeared again in the view.{link}")
                    done.append(f"added {key}: commented on #{issue['number']}")
                else:
                    body = f"{header}\n\nKey: `{key}`\n\n```json\n{json.dumps(change.get('after'), indent=1)}\n```{link}"
                    created = github.create(title, body, [label] + transition_labels(change))
                    done.append(f"added {key}: opened #{created['number']}")
            elif kind == "updated":
                note = f"{header}\n\n" + "\n".join(field_lines(change)) + link
                if issue:
                    github.comment(issue["number"], note)
                    done.append(f"updated {key}: commented on #{issue['number']}")
                else:
                    created = github.create(title, f"Key: `{key}`\n\n{note}", [label] + transition_labels(change))
                    done.append(f"updated {key}: opened #{created['number']}")
            else:
                if issue:
                    github.comment(issue["number"], f"{header}\n\nNo longer in the view.{link}")
                    github.close(issue["number"])
                    done.append(f"removed {key}: closed #{issue['number']}")
                else:
                    done.append(f"removed {key}: nothing open")
    return done


class Receiver(BaseHTTPRequestHandler):
    secret = ""
    receiver_token = ""
    github: GitHub | None = None
    applied: set[str] = set()

    def do_POST(self):  # noqa: N802 — the name http.server dispatches on
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not genuine(self.secret, body, self.headers.get(SIGNATURE_HEADER)):
            return self.answer(401, "signature does not match")
        if self.receiver_token and not hmac.compare_digest(
            self.headers.get(RECEIVER_TOKEN_HEADER, ""), self.receiver_token
        ):
            return self.answer(401, "receiver token does not match")
        delivery = self.headers.get(DELIVERY_HEADER, "")
        if delivery in self.applied:
            return self.answer(200, "already applied")
        try:
            done = apply(json.loads(body), self.github)
        except urllib.error.HTTPError as e:
            return self.answer(500, f"github answered {e.code}")
        self.applied.add(delivery)
        for line in done:
            print(line, flush=True)
        self.answer(200, "\n".join(done) or "nothing to do")

    def answer(self, code: int, text: str) -> None:
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quieter than the default, and never the body
        print(f"{self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    missing = [k for k in ("WORLD_WEBHOOK_SECRET", "GITHUB_TOKEN", "GITHUB_REPO") if not os.environ.get(k)]
    if missing:
        sys.exit(f"set {', '.join(missing)}")
    Receiver.secret = os.environ["WORLD_WEBHOOK_SECRET"]
    Receiver.receiver_token = os.environ.get("RECEIVER_TOKEN", "")
    Receiver.github = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
    port = int(os.environ.get("PORT", "8787"))
    print(f"receiving watch deliveries on :{port} for {os.environ['GITHUB_REPO']}", flush=True)
    ThreadingHTTPServer(("", port), Receiver).serve_forever()


if __name__ == "__main__":
    main()
