"""The hooks against a real appliance and a real project's working tree, read live through the project workspace.

Skipped unless pointed at one:

  EMBABEL_HOOK_LIVE_URL=http://localhost:11043 \\
  EMBABEL_HOOK_LIVE_AUTH='Basic …' \\
  EMBABEL_HOOK_LIVE_WORKSPACE=~/src \\
  EMBABEL_HOOK_LIVE_PROJECT=petclinic \\
  python3 -m unittest test_embabel_hook_live

The workspace is the host folder the appliance mounts as /workspace (EMBABEL_PROJECT_WORKSPACE). Each test works
on its own copy of the project in that folder, so the appliance reads the copy's edits live, and removes the copy
when it ends. The project is embabel-worlds/petclinic-proof: Owner.addVisit is called from VisitBooking.book.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid

import embabel_hook as hook

URL = os.environ.get("EMBABEL_HOOK_LIVE_URL")
OWNER = "src/main/java/org/springframework/samples/petclinic/owner/Owner.java"
ADD_VISIT = "public void addVisit(Integer petId, Visit visit) {"


@unittest.skipUnless(URL, "set EMBABEL_HOOK_LIVE_URL (and _AUTH, _WORKSPACE, _PROJECT) to run against an appliance")
class LiveHookTest(unittest.TestCase):

    def setUp(self):
        workspace = os.path.expanduser(os.environ["EMBABEL_HOOK_LIVE_WORKSPACE"])
        source = os.path.join(workspace, os.environ.get("EMBABEL_HOOK_LIVE_PROJECT", "petclinic"))
        self.key = f"hooktest-{uuid.uuid4().hex[:8]}"
        self.root = os.path.join(workspace, self.key)
        subprocess.run(["cp", "-R", source, self.root], check=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, os.path.join(workspace, f".{self.key}--base"), ignore_errors=True)
        self.appliance = hook.Appliance(URL, self.key, os.environ.get("EMBABEL_HOOK_LIVE_AUTH"))
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)

    def after(self, tool="Edit"):
        event = {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": self.key, "cwd": self.root,
                 "tool_input": {"file_path": os.path.join(self.root, OWNER)}}
        return hook.handle(event, self.appliance, self.state.name)

    def replace(self, old, new):
        path = os.path.join(self.root, OWNER)
        with open(path) as f:
            text = f.read()
        self.assertIn(old, text)
        with open(path, "w") as f:
            f.write(text.replace(old, new))

    def test_a_signature_changed_by_a_script_names_the_caller_in_another_file_and_the_tests(self):
        self.replace(ADD_VISIT, "public void addVisit(Integer petId, Visit visit, boolean notify) {")

        out = self.after(tool="Bash")

        said = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("is now `public void addVisit(Integer petId, Visit visit, boolean notify)`", said)
        self.assertIn("owner/VisitBooking.java:39 (in book)", said)
        self.assertNotIn(f"{OWNER}:173", said, "the edited file's own use (pet.addVisit inside addVisit) is left to the agent")
        self.assertIn("Tests that exercise", said)
        self.assertIn("./mvnw", said)

    def test_an_edit_that_changes_no_signature_says_only_the_tests_once(self):
        self.replace("return this.address;", "return this.address.strip();")
        first = self.after()
        self.replace("return this.address.strip();", "return this.address.trim();")
        second = self.after()

        self.assertNotIn("changed what other files may rely on", first["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(second)

    def test_a_commit_that_takes_a_file_past_its_cap_is_stopped_with_the_reason(self):
        filler = "".join(f"\n\tpublic int filler{i}() {{\n\t\treturn {i};\n\t}}\n" for i in range(140))
        self.replace(ADD_VISIT, filler + "\n\t" + ADD_VISIT)
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": self.key, "cwd": self.root,
                 "tool_input": {"command": "git add -A && git commit -m 'grow Owner'"}}

        out = hook.handle(event, self.appliance, self.state.name)["hookSpecificOutput"]

        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn(f"{OWNER}: lines in the file", out["permissionDecisionReason"])

    def test_a_commit_within_the_rules_goes_ahead(self):
        self.replace("return this.address;", "return this.address.strip();")
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": self.key, "cwd": self.root,
                 "tool_input": {"command": "git commit -am 'strip the address'"}}

        out = hook.handle(event, self.appliance, self.state.name)

        self.assertTrue(out is None or "permissionDecision" not in out["hookSpecificOutput"], out)


if __name__ == "__main__":
    unittest.main()
