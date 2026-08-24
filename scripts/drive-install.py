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
import fcntl
import os
import pty
import re
import select
import subprocess
import sys
import termios
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

    def own_the_terminal() -> None:
        """Make the pty the child's CONTROLLING terminal, not merely its stdin.

        Handing a process a pty on fd 0 is not the same as giving it a terminal:
        `/dev/tty` resolves through the session's CONTROLLING terminal, and a
        child that only inherited the fd has none — so opening /dev/tty fails
        with ENXIO. install.sh hands over to the wizard with `< /dev/tty`
        precisely because `curl | sh` has spent stdin, so without this the one
        entry point real users take could not be driven at all.

        setsid() makes the child a session leader with no terminal; the ioctl
        then claims fd 0 — already the slave, because subprocess does its
        redirection before this runs — as the controlling one.
        """
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    # A terminal wide enough that the appliance's own 80-column layout is not
    # re-wrapped by the pty, which would make the assertions test the wrap.
    os.environ["COLUMNS"] = "100"
    proc = subprocess.Popen(
        command, cwd=HERE, stdin=child, stdout=child, stderr=child,
        close_fds=True, env={**os.environ, "EMBABEL_NO_BROWSER": "1", "TERM": "xterm"},
        preexec_fn=own_the_terminal,
    )
    os.close(child)

    # WRITTEN AS IT ARRIVES, not at the end. An install takes minutes and can
    # stall on a question this table does not know; a transcript that only
    # exists once the run finishes means the one run you most need to read —
    # the one you had to kill — leaves nothing behind. `tail -f` it.
    seen, pending, deadline = [], "", time.time() + timeout
    log = open(transcript, "w", encoding="utf8", buffering=1)
    # What the harness DID, beside what the program said. A stall is either "it
    # asked something we have no answer for" or "we answered and it hung", and
    # the transcript alone cannot tell those apart.
    actions = open(transcript + ".actions", "w", encoding="utf8", buffering=1)
    started = time.time()

    def note(message: str) -> None:
        actions.write(f"{time.time() - started:7.1f}s  {message}\n")

    note(f"running {' '.join(command)}")
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
        log.write(chunk)
        pending += chunk
        # WHAT MAKES A PROMPT A PROMPT is that nothing follows it: the program
        # wrote it and stopped, waiting. So only the text after the last newline
        # can be one — and after the last carriage return, or a spinner frame
        # counts as a question.
        #
        # This is not fussiness. Searching the whole buffer matched setup's own
        # CONFIRMATION SUMMARY ("Username: rod") as though it were the Username
        # prompt, and the harness answered it, and the answer echoed, and it
        # answered again — thousands of times in a tenth of a second, wedged
        # behind a question nobody had asked.
        prompt_line = pending.split("\n")[-1].split("\r")[-1]
        if not prompt_line.strip():
            continue
        for index, (pattern, reply) in enumerate(answers):
            # `answered` is per-INDEX, not global: the same question can be asked
            # twice (a rejected path is re-asked), and refusing to answer the
            # second time would hang.
            if pattern.search(prompt_line):
                filled = reply.format(**fields)
                os.write(parent, (filled + "\n").encode())
                note(f"answered /{pattern.pattern}/ with "
                     + ("<empty: Enter>" if filled == "" else
                        "<secret>" if "password" in pattern.pattern.lower() else repr(filled)))
                pending = ""
                answered.add(index)
                break
    # THE DEADLINE, not proc.poll(). A child that has just exited has not been
    # reaped yet, and reading its pty raises EIO the instant it goes — so the
    # loop breaks with poll() still None on a run that finished perfectly, and
    # the harness stamped TIMED OUT on a complete, correct install.
    if time.time() >= deadline:
        proc.terminate()
        tail = ANSI.sub("", as_rendered(pending)).strip().splitlines()
        note("TIMED OUT — last thing on screen: "
             + (tail[-1][:120] if tail else "(nothing)"))
        seen.append(f"\n[harness] TIMED OUT after {timeout}s\n")
    proc.wait(timeout=30)
    note(f"exit code {proc.returncode}")
    log.close()
    actions.close()
    # Rewrite once at the end as a terminal would have shown it — the live file
    # is raw so it can be followed, the saved one is readable.
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
    rendered = []
    for line in raw.split("\n"):
        # A PTY ends every line with CRLF, so the trailing \r is a LINE ENDING
        # and not a cursor move. Treating it as one meant "text\r".split("\r")[-1]
        # == "" — a transcript of nothing but blank lines, which then failed the
        # checks for reasons that had nothing to do with the product.
        line = line.rstrip("\r")
        rendered.append(line.split("\r")[-1])
    return "\n".join(rendered)


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


def transcript_is_readable(text: str) -> str | None:
    """The harness captured something a person could have read.

    First, because every other check searches this text: a renderer bug once
    reduced a whole install to blank lines, and the checks then reported the
    PRODUCT as broken. A harness that fails silently into "all clear" is worse
    than no harness, so it fails loudly into "look at me instead".
    """
    visible = [line for line in ANSI.sub("", text).splitlines() if line.strip()]
    if len(visible) < 10:
        return (f"the transcript holds only {len(visible)} visible line(s) — "
                "the HARNESS is broken, not necessarily the install")
    return None


CHECKS = (transcript_is_readable, no_raw_log_lines, password_not_measured,
          no_stale_org, both_doors_offered, finished)


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
    parser.add_argument("--installer", action="store_true",
                        help="drive install.sh — the DOWNLOAD and hand-off a real user takes, "
                             "not just the wizard. Honours EMBABEL_REF / EMBABEL_HOME")
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

    # THE REAL ENTRY POINT, when asked for it. `curl | sh` runs install.sh, which
    # downloads the checkout (from EMBABEL_REF) and hands over to the wizard with
    # `< /dev/tty` — a hand-off that had never been driven here, and which broke
    # in exactly the environment this harness creates.
    command = (["sh", "./install.sh"] if args.installer
               else ["python3", f"./{args.mode}.py"] + (["--fresh"] if args.fresh else []))
    print(f"  Driving: {' '.join(command)}   (transcript: {args.transcript})\n")
    text = drive(command, vars(args), args.transcript, args.timeout)
    print()
    return report(text)


if __name__ == "__main__":
    sys.exit(main())
