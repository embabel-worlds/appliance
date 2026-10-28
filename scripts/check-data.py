#!/usr/bin/env python3
"""`embabel data add` puts a file where a realm reads it, and nowhere else.

WHY THIS EXISTS. Reading your own jar, war or classes into a world used to take
`docker cp` into /data/embabel/assistant/users/<email>/<world>/data/jars — the
container's name, your account's directory and the world's, by hand. The verb is only
an improvement if what it does is what a realm then reads:

  - the world is the owner's, found without being asked for, and an ambiguous one is
    a question rather than a guess
  - a path that would walk out of data/ is refused, not normalised
  - a folder arrives under the name asked for, without .git or node_modules, owned by
    root like the rest of /data
  - something already there by that name is REPLACED — a merged target/classes keeps
    the class files of deleted classes, and rows for code that no longer exists
  - the key printed is the path under data/, which is what a producer reads
  - a relative path is resolved where it was typed, not in the appliance directory

Docker is stood in for: nothing here needs a running appliance.

    python3 scripts/check-data.py
"""
import contextlib
import io
import os
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from embabel_setup import clidata, worlddata  # noqa: E402
from embabel_setup.cliparser import build_parser  # noqa: E402
from embabel_setup.core import SetupError  # noqa: E402

failures = []
OWNER = "ada@example.com"
HOME = f"{worlddata.USERS_ROOT}/{OWNER}"


def check(ok, message):
    if not ok:
        failures.append(message)


class FakeDocker:
    """`docker exec` against a pretend container: the worlds it has and the paths that exist."""

    def __init__(self, worlds=("default",), existing=()):
        self.worlds = list(worlds)
        self.existing = set(existing)
        self.calls = []

    def __call__(self, *argv, timeout=30):
        self.calls.append(argv)
        command = argv[2:]
        if command[:2] == ("ls", "-1"):
            return SimpleNamespace(returncode=0, stdout="\n".join(self.worlds) + "\n")
        if command[0] == "test":
            return SimpleNamespace(returncode=0 if command[2] in self.existing else 1, stdout="")
        if command[:2] == ("ls", "-1Ap"):
            return SimpleNamespace(returncode=0, stdout="shop-1.0.war\nshop-classes/\n")
        return SimpleNamespace(returncode=0, stdout="")


class FakeCopy:
    """`docker cp - container:dir`: keeps the tar it is sent, and where it was sent."""
    sent = []

    def __init__(self, argv, **_):
        self.argv = argv
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: FakeCopy.sent.append((self.argv[-1], self.stdin.getvalue()))
        self.stderr = io.BytesIO()
        self.returncode = 0

    def wait(self):
        return self.returncode


def members(tar_bytes):
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        return {m.name: m for m in tar.getmembers()}


def run(docker, fn, *a, **kw):
    with patch.object(worlddata, "_docker", docker), \
         patch.object(worlddata, "admin_username", lambda container: OWNER), \
         patch.object(worlddata.subprocess, "Popen", FakeCopy):
        return fn(*a, **kw)


# Paths that could leave data/ are refused; the rest are kept as a realm writes them.
for bad in ("../admin", "/etc", "jars/../../x"):
    try:
        worlddata.clean_relative(bad, "--to")
        failures.append(f"'{bad}' was accepted as a folder under data/")
    except SetupError:
        pass
check(worlddata.clean_relative("jars/", "--to") == "jars", "a trailing slash is not part of the folder")
check(worlddata.clean_relative("./src/shop", "--to") == "src/shop", "./ is the folder itself")

# The world: default when there is one, the only one otherwise, a question when it is not clear.
check(run(FakeDocker(["default", "demo"]), worlddata.world_root, "c")[1] == "default", "default is the default world")
check(run(FakeDocker(["demo"]), worlddata.world_root, "c")[1] == "demo", "an owner's only world is the one meant")
for worlds, asked in ((["demo", "work"], None), (["default"], "nope")):
    try:
        run(FakeDocker(worlds), worlddata.world_root, "c", asked)
        failures.append(f"worlds {worlds}, asked {asked}: a world was guessed")
    except SetupError:
        pass
with patch.dict(os.environ, {"EMBABEL_USER": "bob"}):
    docker = FakeDocker()
    root, _ = run(docker, worlddata.world_root, "c")
    check(root == f"{worlddata.USERS_ROOT}/bob/default", "EMBABEL_USER names whose world it is")

with tempfile.TemporaryDirectory() as made:
    tmp = os.path.realpath(made)  # macOS's /var is a link to /private/var, and getcwd() says the latter
    # A file: sent into data/jars under its own name, and the key is the path under data/.
    war = os.path.join(tmp, "shop-1.0.war")
    with open(war, "wb") as f:
        f.write(b"PK")
    with open(os.path.join(tmp, "shop-1.0-sources.jar"), "wb") as f:
        f.write(b"PK")
    FakeCopy.sent.clear()
    docker = FakeDocker()
    added = run(docker, worlddata.add_data, "c", war, "jars")
    check(added.key == "jars/shop-1.0.war" and not added.replaced and not added.is_dir, f"a war's key: {added}")
    check(("exec", "c", "mkdir", "-p", f"{HOME}/default/data/jars") in docker.calls, "data/jars is made first")
    check(FakeCopy.sent[0][0] == f"c:{HOME}/default/data/jars", f"sent to {FakeCopy.sent[0][0]}")
    check(list(members(FakeCopy.sent[0][1])) == ["shop-1.0.war"], "a file arrives under its own name")
    check(worlddata.sources_beside(war) == os.path.join(tmp, "shop-1.0-sources.jar"), "a war's sources jar beside it is noticed")
    check(worlddata.sources_beside(os.path.join(tmp, "shop-1.0-sources.jar")) is None, "a sources jar has no sources jar")

    # A folder: renamed, without tool folders, root-owned, and replacing what was there.
    classes = os.path.join(tmp, "app", "target", "classes")
    os.makedirs(os.path.join(classes, "com", "acme"))
    os.makedirs(os.path.join(classes, ".git"))
    os.makedirs(os.path.join(classes, "node_modules", "x"))
    for name in ("com/acme/Cache.class", ".git/HEAD", "node_modules/x/a.js"):
        with open(os.path.join(classes, name), "wb") as f:
            f.write(b"\xca\xfe")
    FakeCopy.sent.clear()
    target = f"{HOME}/default/data/jars/shop-classes"
    docker = FakeDocker(existing={target})
    added = run(docker, worlddata.add_data, "c", classes, "jars", "shop-classes")
    check(added.key == "jars/shop-classes" and added.replaced and added.is_dir, f"a folder's key: {added}")
    check(("exec", "c", "rm", "-rf", target) in docker.calls, "the folder of that name is replaced, not merged")
    sent = members(FakeCopy.sent[0][1])
    check("shop-classes/com/acme/Cache.class" in sent, f"the classes arrive under the name asked for: {sorted(sent)}")
    check(not any(".git" in n or "node_modules" in n for n in sent), f"tool folders stay behind: {sorted(sent)}")
    check(all(m.uid == 0 and m.uname == "root" for m in sent.values()), "everything arrives root-owned")

    for bad_name in ("a/b", ".."):
        try:
            run(FakeDocker(), worlddata.add_data, "c", classes, "jars", bad_name)
            failures.append(f"--as '{bad_name}' was accepted")
        except SetupError:
            pass

    # The verb: a relative path is the one typed, and what is printed is the key.
    here = os.getcwd()
    os.chdir(tmp)
    try:
        args = build_parser().parse_args(["data", "add", "shop-1.0.war", "--to", "jars"])
    finally:
        os.chdir(here)
    check(args.sources == [war], f"a relative path resolves where it was typed: {args.sources}")
    out = io.StringIO()
    with patch.object(clidata, "current_mode", lambda: "worlds"), \
         patch.object(clidata.s, "find_mode_container", lambda mode: "c"), \
         contextlib.redirect_stdout(out):
        code = run(FakeDocker(), clidata.cmd_data, args)
    printed = out.getvalue()
    check(code == 0, "data add succeeds")
    check("jars/shop-1.0.war" in printed, f"the key is printed: {printed}")
    check("data add " + os.path.join(tmp, "shop-1.0-sources.jar") in printed, f"the sources jar is offered: {printed}")

    out = io.StringIO()
    with patch.object(clidata, "current_mode", lambda: "worlds"), \
         patch.object(clidata.s, "find_mode_container", lambda mode: "c"), \
         contextlib.redirect_stdout(out):
        code = run(FakeDocker(existing={f"{HOME}/default/data/jars"}), clidata.cmd_data,
                   build_parser().parse_args(["data", "ls", "jars"]))
    check(code == 0 and "shop-classes/" in out.getvalue(), f"data ls lists the folder: {out.getvalue()}")

if failures:
    print("check-data: FAILED")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("check-data: ok")
