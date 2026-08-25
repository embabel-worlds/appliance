"""One folder to attach to an issue, with the secrets left out.

This appliance holds somebody's email, contacts and documents, so a diagnostic
bundle is an exfiltration shape if it is careless. Two rules, enforced here
rather than left to a warning: .env VALUES are never copied, and logs are
filtered to warnings and errors because an INFO line can carry a document title.
"""

from __future__ import annotations
import os
import re
import shutil
import json
import time

from .colour import MIDDOT, TICK, dim
from .core import APPLIANCE_DIR
from .dockerlib import _docker, appliance_containers, stray_sandbox_containers
from .settings import env_file, env_path, instance
from .backup import backup_timestamp
from .versions import appliance_versions

# ── bug report ──────────────────────────────────────────────────────────────
#
# One folder somebody can attach to an issue, instead of six rounds of "and can
# you also send…". The contents are chosen by what has actually been asked for
# in those rounds: which images, which commit, what docker says, what the
# service said before it stopped saying anything.
#
# WHAT IT MUST NOT CONTAIN. This appliance holds somebody's email, contacts and
# documents, so a diagnostic bundle is a data-exfiltration shape if it is
# careless. Two rules, both enforced here rather than left to a warning:
#
#   - .env values are NEVER copied. The KEYS are, because "is OPENAI_API_KEY
#     set" is a real diagnostic question and "what is it" never is.
#   - Logs are filtered to WARN, ERROR and stack traces by DEFAULT. An INFO
#     line in this server can carry a document title, a contact's name, or the
#     text of a query somebody typed. The full log is available behind a flag
#     that says what it is, so including it is a decision somebody made.
#
# The bundle is left as a FOLDER and a zip beside it, so it can be read before
# it is sent. A bundle you cannot inspect is one people send blind or not at all.

BUGREPORT_LOG_LINES = 2000
# Lines worth keeping from a JVM log without keeping the JVM log. Anchored to
# the level field Spring writes, plus the shapes a stack trace takes.
LOG_INTERESTING = re.compile(
    r"\b(WARN|ERROR|FATAL|SEVERE)\b|^\s+at\s+[\w$.]+\(|^(Caused by|Suppressed):|Exception|Error:")
def _redacted_env() -> str:
    """Which settings exist and whether they have a value — never the value.

    A key with an empty value and a key that is absent are DIFFERENT bugs, and
    the whole point of this file is telling them apart, so both are reported.
    """
    path = env_path()
    if not os.path.exists(path):
        return f"# no {env_file()} — this appliance has not been set up here\n"
    lines = [f"# {env_file()}, VALUES REMOVED. Key, then whether it holds anything.\n"]
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            lines.append(f"{key.strip()} = {f'set ({len(value)} chars)' if value else 'EMPTY'}\n")
    if len(lines) == 1:
        lines.append("# (the file exists but holds no settings)\n")
    return "".join(lines)
def _container_log(name: str, everything: bool) -> str:
    run = _docker("logs", "--tail", str(BUGREPORT_LOG_LINES), name, timeout=60)
    if not run:
        return "(could not read this container's log)\n"
    text = (run.stdout or "") + (run.stderr or "")
    if everything:
        return text
    kept = [line for line in text.splitlines() if LOG_INTERESTING.search(line)]
    header = (f"# FILTERED to warnings, errors and stack traces — {len(kept)} of "
              f"{len(text.splitlines())} lines from the last {BUGREPORT_LOG_LINES}.\n"
              f"# An INFO line here can carry a document title or somebody's name, so the\n"
              f"# full log is only included with `embabel bugreport --all-logs`.\n\n")
    return header + "\n".join(kept) + "\n"
def bug_report(dest_dir: str, extra: dict, everything: bool = False) -> str:
    """Collect a diagnostic bundle into a new timestamped folder, and zip it.

    `extra` is text the CALLER already has — the CLI's own doctor and status
    output. Re-deriving those here would be a second implementation of both,
    free to disagree with what the operator was just shown on screen.
    """
    dest = os.path.join(os.path.abspath(os.path.expanduser(dest_dir)),
                        f"embabel-bugreport-{instance()}-{backup_timestamp()}")
    os.makedirs(dest, exist_ok=True)

    def write(name: str, text: str) -> None:
        with open(os.path.join(dest, name), "w") as f:
            f.write(text)

    for name, text in extra.items():
        write(name, text)

    write("versions.json", json.dumps(appliance_versions(), indent=2) + "\n")
    write("env-keys.txt", _redacted_env())

    containers = appliance_containers()
    write("containers.txt", "".join(
        f"{c['name']:<38} {c['state']:<10} {c['status']:<28} {c['image']}\n" for c in containers)
        or "(no containers belonging to this appliance)\n")

    write("sandboxes.txt", "".join(f"{n}\n" for n in stray_sandbox_containers(mine_only=False))
          or "(none)\n")

    for section, argv in (("docker-info.txt", ("info",)),
                          ("docker-disk.txt", ("system", "df", "-v")),
                          ("docker-model.txt", ("model", "status"))):
        run = _docker(*argv, timeout=60)
        write(section, (run.stdout + run.stderr) if run else "(command failed)\n")

    os.makedirs(os.path.join(dest, "logs"), exist_ok=True)
    for container in containers:
        write(os.path.join("logs", f"{container['name']}.log"),
              _container_log(container["name"], everything))

    write("README.txt",
          "Embabel appliance bug report — " + time.strftime("%c") + "\n\n"
          "Attach the .zip beside this folder to your issue. Read it first if you\n"
          "like — that is why it is left unpacked.\n\n"
          "WHAT IS NOT HERE: no .env VALUES (env-keys.txt lists the keys and whether\n"
          "each holds anything, never what), no documents, no graph contents.\n\n"
          + ("LOGS ARE COMPLETE in this bundle — it was taken with --all-logs. An INFO\n"
             "line can carry a document title, a contact's name, or a query somebody\n"
             "typed. Read logs/ before sending this to anyone.\n"
             if everything else
             "LOGS ARE FILTERED to warnings, errors and stack traces. If a maintainer\n"
             "needs more, `embabel bugreport --all-logs` includes everything — read it\n"
             "before sending, because INFO lines can carry personal data.\n"))

    archive = shutil.make_archive(dest, "zip", root_dir=dest)
    return archive
