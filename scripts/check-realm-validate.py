#!/usr/bin/env python3
"""`embabel realm validate` runs the server image's validator, and nothing else.

What has to stay true for it to be a check a realm repository can rely on:

- The command is reachable as `realm validate` and `realms validate`, with every flag.
- The image is THIS appliance's: EMBABEL_VERSION from the environment, then .env, then
  the default the compose file names — the same order compose itself applies.
- The checkout and every --with directory are mounted READ-ONLY, each under its own
  name, and the validator's flags pass through untouched.
- The validator's status is the command's status (0 passes, 1 fails), and docker's own
  failures — no such command in an old image, an image it cannot run — come back as 2
  with a sentence, never as a pass or as the realm's fault.
- A directory that does not exist is refused before docker is ever called.

Docker is never called: subprocess.run is replaced.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from embabel_setup import cli, realmvalidate  # noqa: E402
from embabel_setup.cliparser import build_parser  # noqa: E402

failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


# ── the command surface ──────────────────────────────────────────────────────
for verb in ("realm", "realms"):
    args = build_parser().parse_args([verb, "validate", "./r", "--with", "a", "--with", "b",
                                      "--json", "--strict", "--image", "x:1"])
    check(args.realms_command == "validate", f"`{verb} validate` does not reach validate")
    check(args.with_dirs == ["a", "b"], f"`{verb} validate` --with gave {args.with_dirs}")
    check(args.json and args.strict and args.image == "x:1", f"`{verb} validate` dropped a flag")
plain = build_parser().parse_args(["realm", "validate", "."])
check(plain.with_dirs == [] and not plain.json and not plain.strict and plain.image is None,
      "`realm validate .` has defaults it should not")

# With several appliances, a run that names its image needs none of them; one that does not
# is about an appliance's EMBABEL_VERSION, and must say which.
for argv, expected in ((["realm", "validate", ".", "--image", "x:1"], 0), (["realm", "validate", "."], 2)):
    args = build_parser().parse_args(argv)
    with patch.object(cli.s, "installed_instances", return_value=["one", "two"]), \
            patch.dict(os.environ, {"EMBABEL_INSTANCE": ""}), contextlib.redirect_stderr(io.StringIO()):
        got = cli.resolve_instance(args)
    check(got == expected, f"`embabel {' '.join(argv)}` with two appliances resolved as {got}, not {expected}")

# ── which image ──────────────────────────────────────────────────────────────
with patch.dict(os.environ, {"EMBABEL_VERSION": "1.2.3"}), \
        patch.object(realmvalidate, "env_file_value", return_value="9.9.9"):
    image = realmvalidate.appliance_image("worlds")
    check(image == "ghcr.io/embabel/assistant:1.2.3", f"the environment's EMBABEL_VERSION lost: {image}")
with patch.dict(os.environ, {"EMBABEL_VERSION": ""}), \
        patch.object(realmvalidate, "env_file_value", return_value="9.9.9"):
    image = realmvalidate.appliance_image("worlds")
    check(image == "ghcr.io/embabel/assistant:9.9.9", f".env's EMBABEL_VERSION lost: {image}")
for mode, compose in (("worlds", "docker-compose-worlds.yml"), ("me", "docker-compose-me.yml")):
    with open(os.path.join(ROOT, compose)) as f:
        found = realmvalidate.IMAGE_LINE.search(f.read())
    check(found is not None, f"{compose}: no server image line with an EMBABEL_VERSION default")
    with patch.dict(os.environ, {"EMBABEL_VERSION": ""}), \
            patch.object(realmvalidate, "env_file_value", return_value=None):
        image = realmvalidate.appliance_image(mode)
        expected = f"ghcr.io/embabel/assistant:{found.group(2) if found else '?'}"
        check(image == expected, f"{mode}: with no pin the image is {image}, the compose default is {expected}")

# ── the docker run ───────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    realm = os.path.join(tmp, "realm-thing")
    deps = [os.path.join(tmp, "a", "realm-code-model"), os.path.join(tmp, "b", "realm-code-model")]
    for d in [realm, *deps]:
        os.makedirs(d)

    argv = realmvalidate.docker_argv(realm, deps, "img:7", as_json=True, strict=True)
    check(argv[:5] == ["docker", "run", "--rm", "--entrypoint", "realm-validate"],
          f"not a throwaway run of the validator: {argv[:5]}")
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    check(mounts == [f"{realm}:/realms/realm-thing:ro",
                     f"{deps[0]}:/with/0/realm-code-model:ro",
                     f"{deps[1]}:/with/1/realm-code-model:ro"],
          f"mounts are not read-only, named and distinct: {mounts}")
    after = argv[argv.index("img:7") + 1:]
    check(after == ["/realms/realm-thing", "--with", "/with/0/realm-code-model",
                    "--with", "/with/1/realm-code-model", "--json", "--strict"],
          f"the validator is not handed what was asked: {after}")
    bare = realmvalidate.docker_argv(realm + os.sep, [], "img:7")
    check(bare[-1] == "/realms/realm-thing", f"a trailing separator lost the realm's name: {bare[-1]}")

    def status_of(returncode):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode)

        out = io.StringIO()
        with patch.object(realmvalidate.subprocess, "run", fake_run), contextlib.redirect_stdout(out):
            status = realmvalidate.run_validation(realm, deps, "img:7")
        return status, out.getvalue(), calls

    for code in (0, 1, 2):
        status, said, calls = status_of(code)
        check(status == code, f"the validator exited {code} and the command said {status}")
        check(said == "", f"the validator's own exit {code} got a docker sentence: {said!r}")
        check(len(calls) == 1, "docker was not called exactly once")
    status, said, _ = status_of(realmvalidate.NO_SUCH_COMMAND)
    check(status == 2 and "predates" in said and "img:7" in said,
          f"an image without the validator read as {status}: {said!r}")
    status, said, _ = status_of(realmvalidate.DOCKER_COULD_NOT_RUN)
    check(status == 2 and "Docker could not run img:7" in said,
          f"docker failing to start read as {status}: {said!r}")

    calls = []
    out = io.StringIO()
    with patch.object(realmvalidate.subprocess, "run", lambda *a, **k: calls.append(a)), \
            contextlib.redirect_stdout(out):
        status = realmvalidate.run_validation(os.path.join(tmp, "nope"), [], "img:7")
    check(status == 2 and "Not a directory" in out.getvalue() and not calls,
          "a missing directory reached docker, or was not refused")

if failures:
    print("✗ realm validate:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("✓ realm validate runs this appliance's image, read-only, and reports the validator's status")
