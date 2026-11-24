"""The hooks inside Claude Code: installed in a project's .claude/settings.json, fired by a real agent's tool calls.

Skipped unless asked for, because each case runs a headless Claude Code session (on the subscription: the
environment it is given has no API key):

  EMBABEL_HOOK_E2E=1 EMBABEL_HOOK_LIVE_URL=http://localhost:11043 EMBABEL_HOOK_LIVE_AUTH='Basic …' \\
  EMBABEL_HOOK_LIVE_WORKSPACE=~/src EMBABEL_HOOK_LIVE_PROJECT=petclinic \\
  python3 -m unittest test_embabel_hook_e2e

Each case copies the project into the workspace the appliance reads, installs the hooks there as the
project-realm skill does, runs one task, and asserts on the session's own transcript (the hooks' responses,
the tool calls) and on the working tree it left. The copy is removed afterwards. The signature case compiles the result offline (checkstyle skipped: the
demo repository's own application.properties trips its nohttp rule).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OWNER = "src/main/java/org/springframework/samples/petclinic/owner/Owner.java"
BOOKING = "src/main/java/org/springframework/samples/petclinic/owner/VisitBooking.java"
RUN = os.environ.get("EMBABEL_HOOK_E2E") and os.environ.get("EMBABEL_HOOK_LIVE_URL")


def settings(project, url, auth_file, extra_env=""):
    """The hooks as the project-realm skill installs them."""
    command = f"EMBABEL_PROJECT={project} EMBABEL_URL={url} EMBABEL_AUTH_FILE={auth_file} {extra_env} python3 {HERE}/embabel_hook.py".replace("  ", " ")
    entry = [{"type": "command", "command": command, "timeout": 60}]
    return {"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": entry}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "hooks": entry}],
    }}


@unittest.skipUnless(RUN, "set EMBABEL_HOOK_E2E=1 and the EMBABEL_HOOK_LIVE_* variables to run Claude Code against an appliance")
class HooksInClaudeCodeTest(unittest.TestCase):

    def project(self, extra_env=""):
        workspace = os.path.expanduser(os.environ["EMBABEL_HOOK_LIVE_WORKSPACE"])
        key = f"e2e-{uuid.uuid4().hex[:8]}"
        root = os.path.join(workspace, key)
        subprocess.run(["cp", "-R", os.path.join(workspace, os.environ.get("EMBABEL_HOOK_LIVE_PROJECT", "petclinic")), root], check=True)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, os.path.join(workspace, f".{key}--base"), ignore_errors=True)
        auth = tempfile.NamedTemporaryFile("w", delete=False, suffix=".auth")
        auth.write(os.environ.get("EMBABEL_HOOK_LIVE_AUTH", ""))
        auth.close()
        os.chmod(auth.name, 0o600)
        self.addCleanup(os.unlink, auth.name)
        os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
        with open(os.path.join(root, ".claude", "settings.json"), "w") as f:
            json.dump(settings(key, os.environ["EMBABEL_HOOK_LIVE_URL"], auth.name, extra_env), f, indent=1)
        subprocess.run(["git", "-C", root, "add", ".claude"], check=True)
        subprocess.run(["git", "-C", root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "install the hooks"], check=True)
        return root

    def claude(self, root, prompt):
        env = {k: os.environ[k] for k in ("HOME", "PATH", "USER", "LANG", "TERM") if k in os.environ}
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events",
             "--model", os.environ.get("EMBABEL_HOOK_E2E_MODEL", "claude-opus-5-5"), "--effort", "low", "--max-turns", "40",
             "--setting-sources", "project", "--permission-mode", "bypassPermissions", "--strict-mcp-config",
             "--disallowedTools", "WebFetch", "WebSearch", "Agent", "Task"],
            cwd=root, env=env, capture_output=True, text=True, timeout=1800)
        keep = os.environ.get("EMBABEL_HOOK_E2E_KEEP")
        if keep:
            with open(os.path.join(os.path.expanduser(keep), f"{self._testMethodName}-{uuid.uuid4().hex[:6]}.jsonl"), "w") as f:
                f.write(result.stdout)
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        init = next(e for e in events if e.get("type") == "system" and e.get("subtype") == "init")
        self.assertEqual(init.get("apiKeySource"), "none", "the session must run on the subscription")
        return events

    @staticmethod
    def hook_outputs(events):
        return [e.get("output") or "" for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"]

    @staticmethod
    def edited_files(events, root):
        files = set()
        for e in events:
            if e.get("type") == "assistant":
                for c in e["message"].get("content", []):
                    if c.get("type") == "tool_use" and c["name"] in ("Edit", "Write", "MultiEdit"):
                        files.add(os.path.relpath(c["input"].get("file_path", ""), root))
        return files

    def test_a_signature_change_is_followed_through_to_the_callers_the_hook_named(self):
        root = self.project()

        events = self.claude(root, (
            "Owner.addVisit should let a caller choose whether the visit triggers a reminder. Add a boolean "
            "parameter `notify` to Owner.addVisit (existing behaviour is notify = true) and keep the project "
            "compiling. Do not run the build or the tests; do not commit."))

        said = [o for o in self.hook_outputs(events) if "additionalContext" in o]
        # Named by file and calling method: the line moves if the agent has already edited the caller (the hook reads it live).
        self.assertTrue(any("owner/VisitBooking.java:" in o and "(in book)" in o for o in said), f"the hook named the caller: {said}")
        build = subprocess.run(["./mvnw", "-q", "-o", "-Dcheckstyle.skip", "-Dspring-javaformat.skip", "compile", "test-compile"],
                               cwd=root, capture_output=True, text=True, timeout=900)
        self.assertEqual(build.returncode, 0, f"the project, tests included, still compiles: {build.stdout[-1500:]}")
        changed = subprocess.run(["git", "-C", root, "diff", "--name-only"], capture_output=True, text=True).stdout.split()
        self.assertIn(OWNER, changed, "edited, by whatever tool the agent chose")
        self.record(f"changed={changed}")
        with open(os.path.join(root, BOOKING)) as f:
            self.assertRegex(f.read(), r"addVisit\([^)]*,[^)]*,[^)]*\)", "the caller the hook named was brought along")

    def test_a_commit_past_the_projects_cap_is_stopped_and_the_agent_keeps_within_it(self):
        root = self.project(extra_env="EMBABEL_FILE_CAP=180")

        events = self.claude(root, (
            "Add a method to Owner that returns how many of the owner's pets have at least one visit, with a "
            "short Javadoc, then commit your change with a short message."))

        denials = [o for o in self.hook_outputs(events) if '"permissionDecision": "deny"' in o or '"permissionDecision":"deny"' in o]
        log = subprocess.run(["git", "-C", root, "log", "--format=%s", "-3"], capture_output=True, text=True).stdout
        with open(os.path.join(root, OWNER)) as f:
            owner_lines = sum(1 for _ in f)
        committed = "install the hooks" not in log.splitlines()[0]
        # The agent is not told the project's cap (Owner.java is 176 lines; the method takes it past 180):
        # the hook is what tells it, at the commit, and what was committed must be within the cap.
        self.assertTrue(denials, "the over-cap commit was stopped by the hook")
        if committed:
            committed_owner = subprocess.run(["git", "-C", root, "show", f"HEAD:{OWNER}"], capture_output=True, text=True).stdout
            self.assertLessEqual(committed_owner.count("\n"), 180, "what was committed is within the cap")
        final = next((e.get("result") for e in reversed(events) if e.get("type") == "result"), "")
        self.record(f"denials={len(denials)} committed={committed} owner_lines={owner_lines}\n[e2e] agent's last word: {final[-600:]}")

    def record(self, line):
        print(f"\n[e2e] {self._testMethodName}: {line}")


if __name__ == "__main__":
    unittest.main()
