#!/usr/bin/env python3
"""Claude Code hooks that put the appliance's answers in front of a coding agent at the moment they matter.

An agent that can grep finds code well, and rarely asks a tool for anything it thinks grep can answer. What
it does not ask, and gets wrong, is what its edit did to the rest of the project. So these hooks do not offer a search. They answer, unasked:

  after a change      which signatures the working tree now has that HEAD did not, and where else each one
                      is used; and, the first time a file changes, the tests that cover it and the command
                      that runs them. Whatever made the change: an Edit, a Write, or a script run in Bash;
  before commit/push  what the project's rules will refuse in the files being committed, read from the
                      working tree as it is now. A definite refusal blocks the command, with the reason;
                      anything short of that is said and the command goes ahead.

Every answer comes from a saved view (realm-polyglot-code: FileDeclarations, WhoCalls, TestsFor, PushChecks,
RulesHeadroom) over the project's working tree, which the appliance reads live through its project workspace
(EMBABEL_PROJECT_WORKSPACE). A hook that cannot reach the appliance, or gets anything unexpected, says nothing
and lets the agent carry on: it must never be the reason a tool call fails.

Configured by environment, set in the project's .claude/settings.json hook commands:

  EMBABEL_PROJECT      the project key the appliance reads the working tree by (a workspace folder name)
  EMBABEL_URL          the appliance (default http://localhost:11043)
  EMBABEL_AUTH_FILE    a file holding the Authorization header value (Bearer <key>); never the key itself
  EMBABEL_FILE_CAP, EMBABEL_METHOD_CAP, EMBABEL_TYPE_CAP   the project's own size rules (default 600, 60, 50)
  EMBABEL_HOOK_STATE   where a session's state is kept: what it has seen and said (default ~/.cache/embabel-agent-hook)

The project's working tree is expected directly in the workspace folder, as `<workspace>/<project>`; the hook
keeps each changed file's HEAD copy beside it, in `<workspace>/.<project>--base`, for the appliance to compare.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

CALLABLE = {"function", "method", "constructor"}
TYPES = {"class", "interface", "struct", "enum", "record", "trait", "protocol", "object", "type"}
# Tools after which the working tree may have changed: the edit tools, and Bash, which agents edit with too.
WATCHED_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}
# `git commit`, `git push`, and either with git's own options first (`git -C dir commit`, `git -c k=v push`).
COMMIT_OR_PUSH = re.compile(r"\bgit(?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?))*\s+(commit|push)\b")
MAX_NAMES = 5
MAX_SITES = 8
MAX_FILES = 20
TIMEOUT_SECONDS = 15


class Appliance:
    """The saved views this hook reads, over one project. Any failure is None: the caller says nothing."""

    def __init__(self, url, project, authorization):
        self.url, self.project, self.authorization = url.rstrip("/"), project, authorization

    def for_project(self, project):
        """The same appliance, read as another project (the HEAD copies beside the working tree)."""
        return Appliance(self.url, project, self.authorization)

    def view(self, view, /, **args):
        """Run [view] with these arguments. Positional-only: views take a `name` argument of their own (WhoCalls)."""
        body = json.dumps({"args": {"project": self.project, **args}}).encode()
        headers = {"Content-Type": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        request = urllib.request.Request(f"{self.url}/api/v1/views/{view}/invoke", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                envelope = json.load(response)
        except (OSError, ValueError):
            return None
        if envelope.get("outcome") not in ("OK", "EMPTY", "PARTIAL"):
            return None
        return envelope.get("data") or []


def git(root, *args):
    result = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    return result.stdout if result.returncode == 0 else ""


def project_root(cwd):
    return git(cwd, "rev-parse", "--show-toplevel").strip() or cwd


def relative(path, root):
    if not path:
        return None
    absolute = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    root = os.path.realpath(root)
    return os.path.relpath(absolute, root) if absolute.startswith(root + os.sep) else None


# ── after an edit ───────────────────────────────────────────────────────────────────────────────────────

def signatures(rows):
    """Each callable or type, by (kind, owner, name), with the set of signatures it is written with (overloads)."""
    found = {}
    for row in rows:
        kind = row.get("kind")
        if kind in CALLABLE or kind in TYPES:
            found.setdefault((kind, row.get("owner") or "", row.get("name")), set()).add(row.get("signature") or "")
    return found


def changed_declarations(before, after):
    """What an edit did to the file's declarations: [(name, kind, before-signatures, after-signatures)].

    A callable whose signatures differ, or that is gone; a type that is gone. A type's own signature line
    changing (an added interface) is not reported: its uses still compile against it.
    """
    was, now = signatures(before), signatures(after)
    changes = []
    for key in sorted(was, key=lambda k: (k[2] or "", k[1], k[0])):
        kind, _, name = key
        if key not in now:
            changes.append((name, kind, sorted(was[key]), []))
        elif kind in CALLABLE and was[key] != now[key]:
            changes.append((name, kind, sorted(was[key] - now[key]), sorted(now[key] - was[key])))
    return changes[:MAX_NAMES]


def uses_elsewhere(appliance, name, path):
    rows = appliance.view("WhoCalls", name=name)
    if rows is None:
        return None
    return [r for r in rows if r.get("file") != path]


def describe_change(name, kind, was, now, sites):
    shown = ", ".join(f"{s.get('file')}:{s.get('line')}" + (f" (in {s.get('caller')})" if s.get("caller") else "") for s in sites[:MAX_SITES])
    more = f", and {len(sites) - MAX_SITES} more" if len(sites) > MAX_SITES else ""
    what = f"`{was[0]}` is gone" if not now else f"`{was[0] if was else name}` is now `{now[0]}`"
    return f"- {kind} `{name}`: {what}. {len(sites)} other use(s) by that name: {shown}{more}."


def tests_for(appliance, after_rows, path):
    types = [r.get("name") for r in after_rows if r.get("kind") in TYPES and not r.get("owner")]
    stem = os.path.splitext(os.path.basename(path))[0]
    commands, tests = [], []
    for name in (types or [stem])[:3]:
        for row in appliance.view("TestsFor", type=name) or []:
            if row.get("path") == path:
                continue
            tests.append(row.get("path") or row.get("test"))
            if row.get("command") and row["command"] not in commands:
                commands.append(row["command"])
    return sorted(set(t for t in tests if t)), commands[:3]


def base_project(root, project):
    """Where the hook keeps each changed file as it is at HEAD, for the appliance to read as a project of its own.

    A sibling of the working tree in the same workspace, hidden (`.shop--base` beside `shop`), so the appliance
    reads it with the same mount and nothing appears in the project. Only files the session changed are copied.
    """
    key = f".{project}--base"
    return key, os.path.join(os.path.dirname(root), key)


def base_declarations(appliance, state, root, project, path):
    """The declarations of [path] as committed at HEAD; [] for a file HEAD does not have; None when unknown."""
    head = git(root, "rev-parse", "HEAD").strip()
    text = git(root, "show", f"HEAD:{path}")
    if not head or not text:
        return []
    key, folder = base_project(root, project)
    copied = state.setdefault("__base__", {})
    target = os.path.join(folder, path)
    if copied.get(path) != head or not os.path.exists(target):
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as f:
                f.write(text)
        except OSError:
            return None
        copied[path] = head
    return appliance.for_project(key).view("FileDeclarations", path=path)


def changed_since_seen(root, state):
    """Files that differ from HEAD and have changed since this session last looked at them."""
    seen = state.setdefault("__seen__", {})
    changed = []
    for line in git(root, "status", "--porcelain", "--untracked-files=no").splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            continue
        stamp = f"{os.path.getmtime(full)}:{os.path.getsize(full)}"
        if seen.get(path) != stamp:
            seen[path] = stamp
            changed.append(path)
    return changed[:MAX_FILES]


def after_change(appliance, state, root):
    """What the files changed since the last look did to the rest of the project, said once per finding.

    Read from the working tree, whatever changed it: agents edit with Edit and Write, and as often with a script
    run through Bash, which a hook on the edit tools alone never sees. Compared against HEAD, because what the rest
    of the project compiles against is what is committed, not what the file was a minute ago.
    """
    lines, said = [], set(state.setdefault("__said__", []))
    tested = state.setdefault("__tests_said__", [])
    for path in changed_since_seen(root, state):
        now = appliance.view("FileDeclarations", path=path)
        if not now:
            continue
        base = base_declarations(appliance, state, root, appliance.project, path)
        findings = []
        if base:
            for name, kind, was, became in changed_declarations(base, now):
                finding = json.dumps([path, name, became])
                if finding in said:
                    continue
                sites = uses_elsewhere(appliance, name, path)
                said.add(finding)
                if sites:
                    findings.append(describe_change(name, kind, was, became, sites))
        if findings:
            lines.append(f"Your change to {path} affects what other files rely on (matched by name, so check the receiver):")
            lines += findings
        if path not in tested:
            tested.append(path)
            tests, commands = tests_for(appliance, now, path)
            if tests:
                lines.append(f"Tests that exercise {path}: {', '.join(tests[:6])}." + (f" Run: {'  |  '.join(commands)}" if commands else ""))
    state["__said__"] = sorted(said)
    return "\n".join(lines) or None


# ── before commit or push ───────────────────────────────────────────────────────────────────────────────

def changed_files(root, push):
    files = set()
    for line in git(root, "status", "--porcelain").splitlines():
        entry = line[3:].split(" -> ")[-1].strip().strip('"')
        if entry:
            files.add(entry)
    if push:
        upstream = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").strip() or "origin/HEAD"
        files.update(git(root, "diff", "--name-only", f"{upstream}...HEAD").split())
    return sorted(f for f in files if os.path.isfile(os.path.join(root, f)))[:MAX_FILES]


def rule_findings(appliance, path):
    caps = {"fileCap": int(os.environ.get("EMBABEL_FILE_CAP", 600)), "methodCap": int(os.environ.get("EMBABEL_METHOD_CAP", 60)),
            "typeCap": int(os.environ.get("EMBABEL_TYPE_CAP", 50))}
    blocking, notes = [], []
    for row in appliance.view("RulesHeadroom", path=path, **caps) or []:
        text = f"{path}: {row.get('rule')} — {row.get('subject')}: {row.get('value')} of {row.get('cap')}"
        if row.get("verdict") == "over":
            blocking.append(f"{text}. {row.get('advice', '')}".strip())
        elif row.get("verdict") == "near":
            notes.append(f"{text} (near the cap).")
    for row in appliance.view("PushChecks", path=path) or []:
        text = f"{path}:{row.get('line')}: {row.get('rule')} — {row.get('subject')}. {row.get('advice', '')}".strip()
        (blocking if row.get("verdict") == "refused" else notes).append(text)
    return blocking, notes


def before_commit_or_push(appliance, event, root):
    command = event.get("tool_input", {}).get("command", "")
    match = COMMIT_OR_PUSH.search(command)
    if not match:
        return None
    blocking, notes = [], []
    for path in changed_files(root, push=match.group(1) == "push"):
        b, n = rule_findings(appliance, path)
        blocking += b
        notes += n
    if blocking:
        return {"permissionDecision": "deny", "permissionDecisionReason":
                "This would be refused by the project's rules, read from your working tree now:\n- " + "\n- ".join(blocking)
                + ("\nAlso worth checking:\n- " + "\n- ".join(notes) if notes else "")}
    if notes:
        return {"additionalContext": "Before this goes in, the project's rules note:\n- " + "\n- ".join(notes)}
    return None


# ── plumbing ────────────────────────────────────────────────────────────────────────────────────────────

def load_state(directory, session):
    path = os.path.join(directory, re.sub(r"[^A-Za-z0-9_.-]", "_", session or "none") + ".json")
    try:
        with open(path) as f:
            return path, json.load(f)
    except (OSError, ValueError):
        return path, {}


def save_state(path, state):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def authorization():
    if os.environ.get("EMBABEL_AUTHORIZATION"):
        return os.environ["EMBABEL_AUTHORIZATION"]
    path = os.environ.get("EMBABEL_AUTH_FILE")
    if path:
        try:
            with open(os.path.expanduser(path)) as f:
                return f.read().strip()
        except OSError:
            return None
    return None


def handle(event, appliance, state_dir):
    """The hook output for one event, as a dict for stdout, or None for nothing to say."""
    root = project_root(event.get("cwd") or os.getcwd())
    hook, tool = event.get("hook_event_name"), event.get("tool_name")
    if hook == "PostToolUse" and tool in WATCHED_TOOLS:
        state_path, state = load_state(state_dir, event.get("session_id"))
        said = after_change(appliance, state, root)
        save_state(state_path, state)
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": said}} if said else None
    if tool == "Bash" and hook == "PreToolUse":
        decision = before_commit_or_push(appliance, event, root)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}} if decision else None
    return None


def main():
    try:
        event = json.load(sys.stdin)
        project = os.environ.get("EMBABEL_PROJECT")
        if not project:
            return
        appliance = Appliance(os.environ.get("EMBABEL_URL", "http://localhost:11043"), project, authorization())
        state_dir = os.path.expanduser(os.environ.get("EMBABEL_HOOK_STATE", "~/.cache/embabel-agent-hook"))
        output = handle(event, appliance, state_dir)
        if output:
            print(json.dumps(output))
    except Exception as e:  # noqa: BLE001 — a hook must never be why a tool call fails
        print(f"embabel hook: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
