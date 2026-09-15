#!/usr/bin/env python3
"""The reference webhook receiver maps a diff onto issues the way its README says it does."""
import hashlib
import hmac
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "receiver", os.path.join(HERE, "..", "examples", "webhook-receiver", "receiver.py")
)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class FakeGitHub:
    """Issues in a list; the four calls the receiver makes, recorded."""

    def __init__(self):
        self.issues = []
        self.comments = []
        self.closed = []

    def open_issue_for(self, label):
        return next((i for i in self.issues if label in i["labels"] and i["state"] == "open"), None)

    def create(self, title, body, labels):
        issue = {"number": len(self.issues) + 1, "title": title, "body": body, "labels": labels, "state": "open"}
        self.issues.append(issue)
        return issue

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self.closed.append(number)
        self.issues[number - 1]["state"] = "closed"


# The shape a `format: json` delivery has: the same fields the adapter writes.
DIFF = {
    "watch": "w1", "name": "Supplier risk", "lens": "SupplierRisk", "diff": "d1", "at": "2026-09-15T09:00:00Z",
    "summary": "Supplier risk: 1 added, 0 removed, 1 updated",
    "added": [{"kind": "ADDED", "key": "world:9", "subject": {"href": "https://world/x/9"},
               "after": {"id": "9", "name": "Cog", "risk": "low"}, "fields": [], "transitions": []}],
    "removed": [],
    "updated": [{"kind": "UPDATED", "key": "world:4", "before": {"id": "4", "name": "Bolt", "risk": "low"},
                 "after": {"id": "4", "name": "Bolt", "risk": "high"},
                 "fields": [{"path": "risk", "before": "low", "after": "high"}],
                 "transitions": [{"kind": "HIGH_RISK", "field": "risk", "before": "low", "after": "high"}]}],
    "changeCount": 2, "addedCount": 1, "removedCount": 0, "updatedCount": 1,
}

gh = FakeGitHub()
done = receiver.apply(DIFF, gh)
assert done == ["added world:9: opened #1", "updated world:4: opened #2"], done
assert gh.issues[0]["title"] == "Cog — Supplier risk"
assert "world:9" in gh.issues[0]["body"] and "https://world/x/9" in gh.issues[0]["body"]
assert receiver.subject_label("world:9") in gh.issues[0]["labels"]
assert "transition:HIGH_RISK" in gh.issues[1]["labels"], "a named transition labels the issue"
assert "`risk`: `\"low\"` → `\"high\"`" in gh.issues[1]["body"]

# The same subject again: commented on, not duplicated; then gone: closed.
again = dict(DIFF, diff="d2", added=[], updated=[dict(DIFF["updated"][0], transitions=[])])
assert receiver.apply(again, gh) == ["updated world:4: commented on #2"]
assert len(gh.issues) == 2
gone = dict(DIFF, diff="d3", added=[], updated=[], removed=[{"kind": "REMOVED", "key": "world:4", "before": {"id": "4", "name": "Bolt"}}])
assert receiver.apply(gone, gh) == ["removed world:4: closed #2"]
assert gh.closed == [2]
assert receiver.apply(gone, gh) == ["removed world:4: nothing open"], "closing twice is a no-op"

# A row with no name is titled by its key; a label never exceeds GitHub's 50 characters.
assert receiver.display({"key": "k", "after": {"id": "1"}}) == "k"
assert len(receiver.subject_label("x" * 500)) <= 50

# The signature check is the guide's, and only the secret passes it.
body = json.dumps(DIFF).encode()
good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
assert receiver.genuine("s3cret", body, good)
assert not receiver.genuine("s3cret", body, good.replace("sha256=", "sha256=0"))
assert not receiver.genuine("s3cret", body, None)
assert not receiver.genuine("other", body, good)

print("webhook receiver: ok")
