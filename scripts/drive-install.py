#!/usr/bin/env python3
"""Run a real install, in a real terminal, and assert on what a person saw.

    python3 scripts/drive-install.py --fresh     # wipe, install, check
    python3 scripts/drive-install.py             # idempotent re-run, check
    python3 scripts/drive-install.py --check transcript.log   # re-check a saved run

WHY A PTY AND NOT A PIPE. Piping answers into setup.py tests a different
program: `sys.stdin.isatty()` is false, so the realms question is never asked,
getpass warns that it cannot control echo, colour switches off, and the boot-log
follower's output interleaves differently. Every one of those is a surface a
user meets and a pipe hides. A pseudo-terminal is what makes this the same run
they get.

WHAT IT CHECKS IS WHAT WENT WRONG BEFORE. Each assertion below is a regression
somebody actually hit — a JVM's warnings arriving mid-install, a password's
length echoed back, a repo reference that only worked through GitHub's rename
redirect. They are cheap to check here and expensive to notice in the field,
which is the same argument scripts/check-copy.py makes for copy.

Exit code is 0 when every check passes, 1 otherwise, so CI can run it.
"""
import argparse
import os
import pty
import re
import select
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What to say when asked. FIRST MATCH WINS, so put the specific before the
# general — "Correct? [Y/n]" must be matched before any other [Y/n].
#
# An answer of "" means Enter, which is the point for the questions that carry a
# default: taking the default is what almost every user does, so it is what the
# scripted run should exercise.
ANSWERS = [
    (r"Type 'yes' to wipe:", "yes"),
    (r"Username:", "{username}"),
    (r"Your name:", "{display}"),
    (r"Password \(min \d+ characters\):", "{password}"),
    (r"Correct\? \[Y/n\]:", "y"),
    # The provider step when keys are already in the environment: a numbered
    # choice whose default is the first key found. Enter takes it.
    (r"Choose 1-\d+ \[", ""),
    (r"API key:", "{apikey}"),
    (r"Realm checkouts directory \[", ""),
    (r"Enable MCP access", ""),
    # Wiring somebody's global coding-agent config is not this harness's
    # business — it edits ~/.claude.json and ~/.codex/config.toml for real.
    (r"Point Claude Code at this appliance", "n"),
    (r"Point Codex at this appliance", "n"),
    (r"Send usage reports\?", "n"),
    (r"Start it now\? \[Y/n\]:", "n"),
    # Catch-all for a yes/no we did not name, answered the safe way.
    (r"\[y/N\]:", "n"),
    (r"\[Y/n\]:", "y"),
]


def drive(command: list[str], fields: dict, transcript: str, timeout: int) -> str:
    """Run [command] under a pty, answering prompts, and return what was printed.

    Reads byte by byte rather than by line: a prompt has no trailing newline —
    that is what makes it a prompt — so a line-buffered reader waits forever for
    one that never comes.
    """
    answers = [(re.compile(pattern), reply) for pattern, reply in ANSWERS]
    answered: set[int] = set()
    parent, child = pty.openpty()
    # A terminal wide enough that the appliance's own 80-column layout is not
    # re-wrapped by the pty, which would make the assertions test the wrap.
    os.environ["COLUMNS"] = "100"
    proc = subprocess.Popen(
        command, cwd=HERE, stdin=child, stdout=child, stderr=child,
        close_fds=True, env={**os.environ, "EMBABEL_NO_BROWSER": "1", "TERM": "xterm"},
    )
    os.close(child)

    seen, pending, deadline = [], "", time.time() + timeout
    while proc.poll() is None and time.time() < deadline:
        ready, _, _ = select.select([parent], [], [], 1.0)
        if not ready:
            continue
        try:
            chunk = os.read(parent, 4096).decode("utf8", "replace")
        except OSError:
            break  # the child closed the pty: it has exited
        if not chunk:
            break
        seen.append(chunk)
        pending += chunk
        for index, (pattern, reply) in enumerate(answers):
            # `answered` is per-INDEX, not global: the same question can be asked
            # twice (a rejected path is re-asked), and refusing to answer the
            # second time would hang. Only the tail is searched, so an answer is
            # not re-triggered by its own echo scrolling past.
            if pattern.search(pending[-400:]):
                os.write(parent, (reply.format(**fields) + "\n").encode())
                pending = ""
                answered.add(index)
                break
    if proc.poll() is None:
        proc.terminate()
        seen.append(f"\n[harness] TIMED OUT after {timeout}s\n")
    proc.wait(timeout=30)
    text = as_rendered("".join(seen))
    with open(transcript, "w", encoding="utf8") as f:
        f.write(text)
    return text


def as_rendered(raw: str) -> str:
    """What the SCREEN held, not what the stream carried.

    A spinner redraws itself by returning to the start of the line, so a run
    that shows one line of progress writes hundreds of frames — and a
    transcript that keeps them all is unreadable and, worse, makes a search for
    "did this appear" match something the user's terminal had already painted
    over. Keeping only the last segment of each carriage-return-separated line
    is the smallest possible terminal emulator, and enough for a wizard whose
    only cursor trick is that one.
    """
    return "\n".join(line.split("\r")[-1] for line in raw.split("\n"))


# ── the checks ──────────────────────────────────────────────────────────────
#
# Each returns a problem string, or None when it passes. Written against the
# TRANSCRIPT — what the person saw — rather than against the code, because
# every one of these bugs was invisible in the code and obvious on screen.

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def no_raw_log_lines(text: str) -> str | None:
    """A JVM's warnings are not part of a first run.

    The installer streams the app's designed operator block and any ERROR; a
    WARN is counted and summarised. Raw ` WARN ` lines on screen mean the
    follower let something through — which is how four "rename this repo"
    lines from OUR backlog once landed in a stranger's install.
    """
    leaked = [line for line in text.splitlines() if " WARN " in line]
    if leaked:
        return (f"{len(leaked)} raw WARN line(s) reached the terminal, first: "
                f"{leaked[0].strip()[:120]}")
    return None


def password_not_measured(text: str) -> str | None:
    """A password confirms as OK; its length is nobody's business.

    An API key still shows a count — a truncated paste is its characteristic
    failure — so this looks only at the password's own confirmation line.
    """
    for line in text.splitlines():
        if "Password" in line and re.search(r"\(\d+ characters\)\s*$", line.strip()):
            return f"the password's length was echoed: {line.strip()[:80]}"
    return None


def no_stale_org(text: str) -> str | None:
    """Repos that moved to embabel-worlds, named by their old org.

    They still resolve — GitHub redirects a rename — which is exactly why this
    rots silently until the day somebody creates the old name again.
    """
    stale = re.findall(r"github\.com/embabel/(default-world|realm-spec|arts-world|"
                       r"realm-esg|realm-legal|appliance|worlds-console)", text)
    if stale:
        return f"stale org reference(s) printed: embabel/{stale[0]}"
    return None


def both_doors_offered(text: str) -> str | None:
    """The closing block names both MCP servers, or the developer door is a
    secret only the release notes know about.

    Only when an install actually RAN. A re-run against a configured appliance
    ends at "already set up" and prints no surfaces block, which is correct
    behaviour and not something to fail — the idempotent path is its own rung.
    """
    clean = ANSI.sub("", text)
    if "already set up" in clean and "Your Worlds surfaces" not in clean:
        return None
    if "/mcp/dev" not in clean:
        return "the closing surfaces never mention the developer door (/mcp/dev)"
    if "/mcp" not in clean:
        return "the closing surfaces never mention the MCP endpoint"
    return None


def finished(text: str) -> str | None:
    """It actually got there. Everything above is about how the install LOOKED;
    this is the one check that it happened at all."""
    clean = ANSI.sub("", text)
    if "TIMED OUT" in clean:
        return "the run timed out"
    if not re.search(r"already set up|Sign in at|Your Worlds surfaces", clean):
        return "no completion banner — setup did not finish"
    return None


CHECKS = (no_raw_log_lines, password_not_measured, no_stale_org,
          both_doors_offered, finished)


def report(text: str) -> int:
    problems = [problem for check in CHECKS if (problem := check(text))]
    for problem in problems:
        print(f"  ✗ {problem}")
    if problems:
        print(f"\n  {len(problems)} of {len(CHECKS)} checks failed.")
        return 1
    print(f"  ✓ {len(CHECKS)} checks passed — the install reads clean.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true",
                        help="WIPE all appliance state first, then install")
    parser.add_argument("--mode", default="worlds", choices=("worlds", "me"))
    parser.add_argument("--username", default="rod")
    parser.add_argument("--display", default="Rod Johnson")
    parser.add_argument("--password", default="fresh-appliance-2026")
    parser.add_argument("--apikey", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--transcript", default="/tmp/embabel-install.log")
    parser.add_argument("--check", metavar="FILE",
                        help="skip the run; check a transcript captured earlier")
    args = parser.parse_args()

    if args.check:
        with open(args.check, encoding="utf8") as f:
            return report(f.read())

    command = ["python3", f"./{args.mode}.py"] + (["--fresh"] if args.fresh else [])
    print(f"  Driving: {' '.join(command)}   (transcript: {args.transcript})\n")
    text = drive(command, vars(args), args.transcript, args.timeout)
    print()
    return report(text)


if __name__ == "__main__":
    sys.exit(main())
