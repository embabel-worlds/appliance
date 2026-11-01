"""What the appliance says, loaded from copy/ rather than embedded in code.

The words are prose, with different editors and a different review cadence from
the logic, so they live in files and this module is only the loader.
"""

from __future__ import annotations
import os
import re
import textwrap

from .core import APPLIANCE_DIR, SetupError
from .launcher import launcher

# ── copy ────────────────────────────────────────────────────────────────────
#
# THE WORDS LIVE IN copy/, NOT IN THE CODE. Everything a person reads while
# setting the appliance up is prose, and prose has different editors, different
# reviewers and a different review cadence from the logic around it. Buried in
# print() calls it could only be changed by someone willing to touch setup.py,
# hand-wrapped to the right column, and diffed against a wall of quoting — so it
# drifted from worlds.embabel.com, which is the one place it must agree with.
#
# THE FILES HOLD WORDS ONLY. No colour, no indentation, no line breaks that
# matter: write paragraphs, separate them with a blank line, and let [say] wrap
# and indent them at render time. An editor who wants to change a sentence
# should not have to think about the eightieth column, and a sentence that grows
# by two words should not reflow four lines in the diff.
#
# Values are interpolated by name — {path}, {port} — so a copy file can name a
# thing the program computes without knowing how.

COPY_DIR = "copy"
# The column prose wraps at. 76 plus a two-space indent keeps the whole thing
# inside 80, which is still what a terminal opens at.
COPY_WIDTH = 76


def copy_text(name: str, **fields) -> str:
    """One copy file, interpolated, wrapped and indented ready to print.

    A missing file raises rather than printing nothing: copy ships with the
    code, so its absence is a packaging bug, and silence is the one failure mode
    nobody notices until a user reports an empty screen.
    """
    path = os.path.join(APPLIANCE_DIR, COPY_DIR, f"{name}.txt")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise SetupError(f"Missing copy file {COPY_DIR}/{name}.txt — {e}")
    raw = name_the_instance(raw)
    return wrap_copy(raw.format(**fields) if fields else raw)


# A command in copy is written as `embabel <verb>`, which acts on the default
# instance. On any other instance the reader has to type --instance as well, so
# it goes in here rather than in every file that names a command.
#
# AND THE LAUNCHER ITSELF, for the same reason. When `embabel` on PATH is some
# other program, a copy file that says `embabel up` is telling the reader to run
# that program; the full path to this appliance's launcher goes in instead. See
# launcher.py for how that is decided.
COMMAND = re.compile(r"(?<![\w/.$-])embabel (?=[a-z-])")


def name_the_instance(text: str) -> str:
    """[text] with every `embabel` command made to run THIS appliance: `--instance
    <name>` unless it is the default one, and the full launcher path unless the
    bare name on PATH is ours."""
    from .settings import DEFAULT_INSTANCE, instance
    command = launcher(APPLIANCE_DIR)
    if instance() != DEFAULT_INSTANCE:
        command += f" --instance {instance()}"
    if command == "embabel":
        return text
    # A function, not a template: a Windows path is full of backslashes, which a
    # replacement string would read as escapes.
    return COMMAND.sub(lambda _: command + " ", text)


# AN ASIDE, MARKED AS ONE. `((like this))` renders dim.
#
# Copy files carry no escape codes — that is what keeps them editable by
# somebody who never opens Python — but the emphasis a sentence needs is a
# property OF the sentence, and the writer is the one who knows which half is
# secondary. So the file marks the aside and the renderer decides what dim
# means, which is the same division as the wrapping: intent here, mechanics
# there.
#
# Double parens because a single one appears in ordinary prose, and because an
# unrendered marker still reads as an aside — if this feature were removed
# tomorrow the words would degrade to "(text)" rather than to nonsense.
#
# DOTALL, so a marker split across a wrap still renders: the dim spans the
# newline, which costs nothing, where a line-at-a-time substitution would leave
# the markers visible exactly when a paragraph grew.
ASIDE = re.compile(r"\(\((.+?)\)\)", re.DOTALL)


def wrap_copy(raw: str, indent: str = "  ", width: int = COPY_WIDTH) -> str:
    """Paragraphs wrapped and indented; blank lines kept; a line that begins with
    whitespace in the FILE is left exactly as written.

    That last rule is the escape hatch, and it is what makes a copy file able to
    hold a command someone will retype:

        embabel realms link ~/src

    Wrapping that would break it, so anything already indented is passed
    through — the same convention as markdown, which is what these files look
    like anyway.
    """
    out = []
    for block in raw.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        if block.startswith((" ", "\t")):
            # dedent, not strip: the block's own INTERNAL alignment is the point
            # of writing it verbatim. Stripping every line flattened the JSON
            # shape below into a list of unaligned fragments.
            body = textwrap.dedent(block).rstrip()
            out.append("\n".join((indent + line) if line.strip() else ""
                                  for line in body.splitlines()))
        else:
            flat = " ".join(line.strip() for line in block.splitlines())
            out.append(textwrap.fill(flat, width=width,
                                     initial_indent=indent, subsequent_indent=indent))
    # Imported HERE, not at module scope: colour reads COPY_DIR from this module,
    # so importing it back at the top is a cycle — and the failure is an
    # ImportError on a partially initialised module, which reads like a broken
    # install rather than like two files pointing at each other.
    from .colour import dim
    return ASIDE.sub(lambda match: dim(match.group(1)), "\n\n".join(out))


def say(name: str, **fields) -> None:
    """Print a copy block. The one call site style for prose, so a block can
    never be half-wrapped or half-indented by whoever added it last."""
    print(copy_text(name, **fields))
