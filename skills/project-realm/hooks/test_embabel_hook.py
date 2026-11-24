"""Tests for embabel_hook: what each hook says, when it says nothing, and that it never fails a tool call.

Run: python3 -m unittest discover -s skills/project-realm/hooks
The appliance is a fake answering from canned rows; the commit path runs against real temporary git repositories.
"""
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import embabel_hook as hook

PATH = "src/main/java/shop/Till.java"


def decl(kind, name, signature, owner="Till", line=10):
    return {"kind": kind, "owner": owner, "name": name, "signature": signature, "fromLine": line, "toLine": line + 3}


class FakeAppliance:
    """Answers each view from `answers[name]`, a list or a function of the args; records every call.

    [base] answers for the HEAD copies the hook keeps beside the working tree (`.<project>--base`).
    """

    def __init__(self, project="shop", base=None, **answers):
        self.project, self.base, self.answers, self.calls = project, base, answers, []

    def for_project(self, project):
        assert project == f".{self.project}--base", project
        return self.base or FakeAppliance(project)

    def view(self, view, /, **args):
        self.calls.append((view, args))
        answer = self.answers.get(view, [])
        return answer(**args) if callable(answer) else answer


class Repo:
    """A real git repository, with one committed file: in [folder], or a temporary one removed when [test] ends."""

    def __init__(self, test, folder=None):
        if folder is None:
            self.dir = tempfile.TemporaryDirectory()
            test.addCleanup(self.dir.cleanup)
            folder = self.dir.name
        os.makedirs(folder, exist_ok=True)
        self.root = os.path.realpath(folder)
        self.run("init", "-q")
        self.run("config", "user.email", "t@example.com")
        self.run("config", "user.name", "t")
        self.write(PATH, "class Till {}\n")
        self.run("add", "-A")
        self.run("commit", "-q", "-m", "first")

    def run(self, *args):
        subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True)

    def write(self, path, text):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)


class SignatureDiffTest(unittest.TestCase):

    def test_a_changed_parameter_list_is_reported_with_both_signatures(self):
        before = [decl("method", "total", "public int total(List<Item> items)")]
        after = [decl("method", "total", "public int total(List<Item> items, Currency currency)")]

        self.assertEqual(hook.changed_declarations(before, after),
                         [("total", "method", ["public int total(List<Item> items)"], ["public int total(List<Item> items, Currency currency)"])])

    def test_a_removed_method_and_a_removed_type_are_reported_as_gone(self):
        before = [decl("class", "Till", "class Till", owner=""), decl("method", "refund", "void refund()")]

        changes = hook.changed_declarations(before, [])

        self.assertEqual({(name, kind, tuple(now)) for name, kind, _, now in changes}, {("Till", "class", ()), ("refund", "method", ())})

    def test_an_edit_inside_a_body_a_field_value_or_a_type_header_is_not_a_signature_change(self):
        before = [decl("method", "total", "int total()"), decl("field", "RATE", "static final int RATE = 1"),
                  decl("class", "Till", "class Till", owner="")]
        after = [decl("method", "total", "int total()", line=12), decl("field", "RATE", "static final int RATE = 2"),
                 decl("class", "Till", "class Till implements Auditable", owner="")]

        self.assertEqual(hook.changed_declarations(before, after), [])

    def test_overloads_are_compared_as_a_set_and_only_the_changed_one_is_named(self):
        before = [decl("method", "add", "void add(Item i)"), decl("method", "add", "void add(Item i, int n)")]
        after = [decl("method", "add", "void add(Item i)"), decl("method", "add", "void add(Item i, long n)")]

        self.assertEqual(hook.changed_declarations(before, after), [("add", "method", ["void add(Item i, int n)"], ["void add(Item i, long n)"])])

    def test_at_most_five_names_are_reported_so_a_rewrite_is_not_a_wall_of_text(self):
        before = [decl("method", f"m{i}", f"void m{i}()") for i in range(9)]

        self.assertEqual(len(hook.changed_declarations(before, [])), hook.MAX_NAMES)


class AfterChangeTest(unittest.TestCase):
    """What the hook says after a tool call changed the working tree, against what HEAD declares."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.repo = Repo(self, os.path.join(self.workspace.name, "shop"))
        self.state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.state_dir.cleanup)
        self.head = [decl("class", "Till", "class Till", owner=""), decl("method", "total", "int total(List<Item> items)")]
        self.now = [decl("class", "Till", "class Till", owner=""), decl("method", "total", "int total(List<Item> items, Currency c)")]
        self.uses = [{"file": PATH, "line": 30, "caller": "self"},
                     {"file": "src/main/java/shop/Checkout.java", "line": 41, "caller": "pay"},
                     {"file": "src/main/java/shop/Report.java", "line": 7, "caller": "daily"}]

    def after(self, tool="Bash", session="s1", appliance=None):
        event = {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": session, "cwd": self.repo.root,
                 "tool_input": {"command": "python3 edit.py"}}
        return hook.handle(event, appliance, self.state_dir.name)

    def appliance(self, now=None, uses=None, tests=None):
        return FakeAppliance(base=FakeAppliance(".shop--base", FileDeclarations=self.head), FileDeclarations=now or self.now,
                             WhoCalls=self.uses if uses is None else uses, TestsFor=tests or [])

    def test_a_signature_changed_by_a_script_in_bash_names_every_other_use_and_the_tests(self):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 0; } }\n")
        appliance = self.appliance(tests=[{"path": "src/test/java/shop/TillTest.java", "command": "./mvnw -q test -Dtest=TillTest"}])

        said = self.after(appliance=appliance)["hookSpecificOutput"]["additionalContext"]

        self.assertIn("int total(List<Item> items)` is now `int total(List<Item> items, Currency c)`", said)
        self.assertIn("src/main/java/shop/Checkout.java:41 (in pay)", said)
        self.assertIn("src/main/java/shop/Report.java:7 (in daily)", said)
        self.assertNotIn(f"{PATH}:30", said, "uses inside the changed file are the agent's own edit")
        self.assertIn("2 other use(s)", said)
        self.assertIn("./mvnw -q test -Dtest=TillTest", said)
        self.assertEqual([c for c in appliance.calls if c[0] == "WhoCalls"], [("WhoCalls", {"name": "total"})])

    def test_the_head_copy_is_kept_beside_the_working_tree_for_the_appliance_to_read(self):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 0; } }\n")

        self.after(appliance=self.appliance())

        with open(os.path.join(self.workspace.name, ".shop--base", PATH)) as f:
            self.assertEqual(f.read(), "class Till {}\n")

    def test_each_finding_and_each_files_tests_are_said_once_a_session(self):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 0; } }\n")
        appliance = self.appliance(tests=[{"path": "src/test/java/shop/TillTest.java", "command": "run"}])
        self.assertIsNotNone(self.after(appliance=appliance))

        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 1; } }\n")

        self.assertIsNone(self.after(appliance=appliance), "the same signature again, and the tests already named")
        self.assertIsNotNone(self.after(appliance=appliance, session="s2") or self.touch_and_ask("s2", appliance),
                             "another session has not been told")

    def touch_and_ask(self, session, appliance):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 2; } }\n")
        return self.after(appliance=appliance, session=session)

    def test_a_file_unchanged_since_the_last_look_is_not_asked_about_again(self):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 0; } }\n")
        appliance = self.appliance()
        self.after(appliance=appliance)
        appliance.calls.clear()

        self.assertIsNone(self.after(tool="Bash", appliance=appliance))
        self.assertEqual(appliance.calls, [], "nothing changed, so nothing was asked")

    def test_a_clean_working_tree_asks_nothing(self):
        appliance = self.appliance()

        self.assertIsNone(self.after(appliance=appliance))
        self.assertEqual(appliance.calls, [])

    def test_a_new_file_has_no_signatures_to_have_changed_and_names_its_tests(self):
        self.repo.write("src/main/java/shop/Refund.java", "class Refund {}\n")
        self.repo.run("add", "src/main/java/shop/Refund.java")
        appliance = self.appliance(now=[decl("class", "Refund", "class Refund", owner="")],
                                   tests=[{"path": "src/test/java/shop/RefundTest.java", "command": "run"}])

        said = self.after(appliance=appliance)["hookSpecificOutput"]["additionalContext"]

        self.assertNotIn("affects what other files rely on", said)
        self.assertIn("RefundTest", said)
        self.assertNotIn("WhoCalls", [c[0] for c in appliance.calls])

    def test_a_signature_change_nothing_else_uses_says_nothing_about_it(self):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 0; } }\n")

        self.assertIsNone(self.after(appliance=self.appliance(uses=[{"file": PATH, "line": 30}])))

    def test_an_appliance_that_does_not_answer_means_nothing_is_said(self):
        self.repo.write(PATH, "class Till { int total(List<Item> items, Currency c) { return 0; } }\n")
        appliance = FakeAppliance(FileDeclarations=None)

        self.assertIsNone(self.after(appliance=appliance))


class BeforeCommitTest(unittest.TestCase):

    def setUp(self):
        self.repo = Repo(self)

    def bash(self, command):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s", "cwd": self.repo.root,
                "tool_input": {"command": command}}

    def test_a_file_over_its_cap_blocks_the_commit_and_says_why(self):
        self.repo.write(PATH, "class Till { /* grown */ }\n")
        appliance = FakeAppliance(
            RulesHeadroom=[{"rule": "lines in the file", "subject": PATH, "value": 616, "cap": 600, "verdict": "over", "advice": "Split it."}],
            PushChecks=[])

        out = hook.handle(self.bash("git commit -am 'add filler'"), appliance, tempfile.gettempdir())["hookSpecificOutput"]

        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("lines in the file", out["permissionDecisionReason"])
        self.assertIn("616 of 600", out["permissionDecisionReason"])
        self.assertEqual({c[1]["path"] for c in appliance.calls}, {PATH})
        self.assertEqual([c[1] for c in appliance.calls if c[0] == "RulesHeadroom"],
                         [{"path": PATH, "fileCap": 600, "methodCap": 60, "typeCap": 50}])

    def test_the_projects_own_caps_are_the_ones_asked_about(self):
        self.repo.write(PATH, "class Till { }\n")
        appliance = FakeAppliance(RulesHeadroom=[], PushChecks=[])
        with mock.patch.dict(os.environ, {"EMBABEL_FILE_CAP": "400"}):
            hook.handle(self.bash("git commit -m x"), appliance, tempfile.gettempdir())

        self.assertEqual(appliance.calls[0][1]["fileCap"], 400)

    def test_a_check_short_of_a_refusal_is_said_and_the_commit_goes_ahead(self):
        self.repo.write(PATH, "class Till { }\n")
        appliance = FakeAppliance(RulesHeadroom=[{"rule": "lines in the file", "subject": PATH, "value": 590, "cap": 600, "verdict": "near"}],
                                  PushChecks=[{"rule": "bean or listener", "line": 3, "subject": "Till", "verdict": "check", "advice": "Update the inventory."}])

        out = hook.handle(self.bash("git commit -m x"), appliance, tempfile.gettempdir())["hookSpecificOutput"]

        self.assertNotIn("permissionDecision", out)
        self.assertIn("near the cap", out["additionalContext"])
        self.assertIn("Update the inventory.", out["additionalContext"])

    def test_a_refused_push_check_blocks(self):
        self.repo.write(PATH, "class Till { }\n")
        appliance = FakeAppliance(RulesHeadroom=[], PushChecks=[{"rule": "string literals joined with +", "line": 9, "subject": "msg", "verdict": "refused", "advice": "Use a multiline string."}])

        out = hook.handle(self.bash("git commit -m x"), appliance, tempfile.gettempdir())["hookSpecificOutput"]

        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn(f"{PATH}:9", out["permissionDecisionReason"])

    def test_a_clean_working_tree_asks_nothing(self):
        appliance = FakeAppliance()

        self.assertIsNone(hook.handle(self.bash("git commit -m x"), appliance, tempfile.gettempdir()))
        self.assertEqual(appliance.calls, [])

    def test_only_commit_and_push_are_judged(self):
        self.repo.write(PATH, "class Till { }\n")
        appliance = FakeAppliance(RulesHeadroom=[{"rule": "r", "subject": "s", "value": 9, "cap": 1, "verdict": "over"}])
        for command in ("git status", "git log --oneline", "grep -r commit .", "echo git commit", "./mvnw test"):
            out = hook.handle(self.bash(command), appliance, tempfile.gettempdir())
            if command == "echo git commit":
                continue  # a commit named inside another command is still judged: better a false stop than a missed one
            self.assertIsNone(out, command)
        for command in ("git commit -m x", "git -C . commit -m x", "git add -A && git commit -m x", "git push origin HEAD"):
            self.assertIsNotNone(hook.handle(self.bash(command), appliance, tempfile.gettempdir()), command)

    def test_a_push_also_judges_the_commits_not_yet_upstream(self):
        bare = tempfile.TemporaryDirectory()
        self.addCleanup(bare.cleanup)
        subprocess.run(["git", "init", "-q", "--bare", bare.name], check=True)
        self.repo.run("remote", "add", "origin", bare.name)
        self.repo.run("push", "-q", "-u", "origin", "HEAD")
        self.repo.write("src/main/java/shop/Checkout.java", "class Checkout {}\n")
        self.repo.run("add", "-A")
        self.repo.run("commit", "-q", "-m", "second")
        appliance = FakeAppliance(RulesHeadroom=[], PushChecks=[])

        hook.handle(self.bash("git push"), appliance, tempfile.gettempdir())

        self.assertIn("src/main/java/shop/Checkout.java", {c[1]["path"] for c in appliance.calls})


class NeverFailsTest(unittest.TestCase):
    """Whatever goes wrong, the hook prints nothing a tool call could fail on, and exits normally."""

    def run_main(self, stdin, env):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
            hook.main()
        return out.getvalue()

    def test_malformed_input_says_nothing(self):
        self.assertEqual(self.run_main("not json", {"EMBABEL_PROJECT": "p"}), "")

    def test_no_project_configured_says_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.run_main(json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit"}), {}), "")

    def test_an_unreachable_appliance_says_nothing(self):
        repo = Repo(self)
        event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "session_id": "x", "cwd": repo.root,
                 "tool_input": {"file_path": os.path.join(repo.root, PATH)}}
        self.assertEqual(self.run_main(json.dumps(event), {"EMBABEL_PROJECT": "p", "EMBABEL_URL": "http://127.0.0.1:9"}), "")


class ApplianceClientTest(unittest.TestCase):
    """The view call itself: the envelope, the header, and the outcomes that count as no answer."""

    def serve(self, status, body):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"], seen["auth"] = self.path, self.headers.get("Authorization")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}", seen

    def test_the_view_is_invoked_with_the_project_and_the_header(self):
        url, seen = self.serve(200, {"outcome": "OK", "data": [{"name": "total"}]})

        rows = hook.Appliance(url, "shop", "Bearer k").view("WhoCalls", name="total")

        self.assertEqual(rows, [{"name": "total"}])
        self.assertEqual(seen["path"], "/api/v1/views/WhoCalls/invoke")
        self.assertEqual(seen["body"], {"args": {"project": "shop", "name": "total"}})
        self.assertEqual(seen["auth"], "Bearer k")

    def test_an_answer_that_is_not_complete_or_honestly_short_is_no_answer(self):
        for outcome in ("FAILED", "SOURCE_UNAVAILABLE", "TOO_BROAD"):
            url, _ = self.serve(200, {"outcome": outcome, "data": []})
            self.assertIsNone(hook.Appliance(url, "shop", None).view("WhoCalls", name="x"), outcome)

    def test_an_http_error_is_no_answer(self):
        url, _ = self.serve(401, {"error": "no"})
        self.assertIsNone(hook.Appliance(url, "shop", None).view("WhoCalls", name="x"))


if __name__ == "__main__":
    unittest.main()
