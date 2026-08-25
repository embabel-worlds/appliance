"""The Embabel palette, and deciding whether a terminal may have it.

Depends on nothing in this package: it is the leaf every other module paints
with, and keeping it that way is what stops a colour question becoming a reason
to import the backup logic.
"""

from __future__ import annotations
import os
import shutil
import sys

from .core import APPLIANCE_DIR
from .words import COPY_DIR

# ── colour ──────────────────────────────────────────────────────────────────
#
# RESTRAINT IS THE POINT. This runs in terminals people screen-share, pipe into
# files, and read over ssh on a bad connection. So: the SIXTEEN basic colours,
# never 256 or truecolour; one accent, not a palette; and nothing carrying
# meaning that the words do not already carry — a red line says "problem" twice
# as fast, but a line that is ONLY red says nothing at all to the quarter of
# readers who cannot see it, or to the log file it lands in tomorrow.
#
# OFF IS THE SAFE DEFAULT and it is checked in this order:
#
#   NO_COLOR         set to anything  -> off. The convention (no-color.org).
#   FORCE_COLOR /    set              -> on, tty or not. For CI logs that render
#   CLICOLOR_FORCE                       ANSI, and for testing this file.
#   not a tty                        -> off. `embabel status > file` must be
#                                       readable, and `| grep` must still match.
#   TERM=dumb, TERM unset            -> off.
#   Windows                          -> on ONLY if the console accepts VT, which
#                                       is asked of the OS rather than assumed.
#
# Windows deserves the paragraph. Its console ignored ANSI for thirty years and
# printed the escapes as literal garbage; Windows 10 added opt-in VT processing,
# and Terminal enables it by default. ENABLE_VIRTUAL_TERMINAL_PROCESSING is the
# flag, set through kernel32 — and if that call fails for any reason at all, the
# answer is no colour rather than a screenful of `←[0m`.

# THE EMBABEL PALETTE, from appliance-kit/css/palette.css — the same --sb-*
# tokens the Worlds console, the Me app and every bundled theme read. The CLI
# joining that contract rather than inventing a second one is the whole point:
# "the schematic — light lines on black, indigo as the one signal".
BRAND = {
    "accent": "#625fff",    # --sb-accent, the one signal
    "link": "#c7d2ff",      # --sb-link
    "success": "#3ecf8e",   # --sb-success
    "error": "#f87171",     # --sb-error
    "warning": "#dcaa37",   # --sb-warning
    "muted": "#6a7282",     # --sb-text-muted
}

# THREE TIERS, because a terminal that cannot render #625fff must not be handed
# it. Truecolour gets the brand exactly; 256 gets the nearest cube entry,
# computed rather than guessed; and everything else gets the basic sixteen.
#
# The basic tier is mapped BY MEANING, not by distance, and that is deliberate:
# nearest-RGB puts --sb-error (#f87171, a soft red) on grey and --sb-success
# (#3ecf8e) on cyan, because those are the closest of sixteen bad options. A
# cross that is grey and a tick that is cyan are worse than no colour at all.
BASIC_16 = {"accent": 94, "link": 36, "success": 92, "error": 91,
            "warning": 33, "muted": 90}

_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


def _nearest_256(hex_colour: str) -> int:
    """The closest xterm-256 index: the 6×6×6 colour cube, plus the 24 greys."""
    want = tuple(int(hex_colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    best, best_distance = 16, None
    for index in range(216):
        r, g, b = index // 36, (index // 6) % 6, index % 6
        candidate = (_CUBE_LEVELS[r], _CUBE_LEVELS[g], _CUBE_LEVELS[b])
        distance = sum((a - b) ** 2 for a, b in zip(candidate, want))
        if best_distance is None or distance < best_distance:
            best, best_distance = 16 + index, distance
    for index in range(24):
        value = 8 + 10 * index
        distance = sum((a - value) ** 2 for a in want)
        if distance < best_distance:
            best, best_distance = 232 + index, distance
    return best


def _depth() -> str:
    """How much colour this terminal can actually take."""
    if os.environ.get("COLORTERM", "") in ("truecolor", "24bit"):
        return "true"
    term = os.environ.get("TERM", "")
    if "256color" in term or os.environ.get("COLORTERM"):
        return "256"
    return "basic"


def _brand_codes(depth: str) -> dict:
    if depth == "true":
        return {name: "38;2;{};{};{}".format(
            *(int(hexv.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)))
            for name, hexv in BRAND.items()}
    if depth == "256":
        return {name: f"38;5;{_nearest_256(hexv)}" for name, hexv in BRAND.items()}
    return {name: str(code) for name, code in BASIC_16.items()}


_ANSI = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "underline": "\033[4m"}


def _windows_vt() -> bool:
    """Ask the console to interpret ANSI, and report whether it agreed."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # -11 is STD_OUTPUT_HANDLE; 0x0004 is ENABLE_VIRTUAL_TERMINAL_PROCESSING.
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _colour_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    if sys.platform == "win32":
        return bool(os.environ.get("WT_SESSION")) or _windows_vt()
    return True


COLOUR = _colour_enabled()
DEPTH = _depth()
for _name, _code in _brand_codes(DEPTH).items():
    _ANSI[_name] = f"\033[{_code}m"


def paint(text: str, *styles: str) -> str:
    """Wrap text in styles, or return it untouched. Every colour in this file
    goes through here, so turning colour off is one decision and not forty."""
    if not COLOUR or not styles:
        return text
    return "".join(_ANSI[s] for s in styles if s in _ANSI) + text + _ANSI["reset"]


# The vocabulary. Call sites say what a thing IS, never which colour it gets —
# so the palette can change in one place, and a reader of the code can see the
# intent rather than decode it.
def bold(text: str) -> str: return paint(text, "bold")
def dim(text: str) -> str: return paint(text, "muted")
def accent(text: str) -> str: return paint(text, "accent")
def good(text: str) -> str: return paint(text, "success")
def warn(text: str) -> str: return paint(text, "warning")
def bad(text: str) -> str: return paint(text, "error")
def url(text: str) -> str: return paint(text, "link", "underline")


# Marks, paired with their colour so a tick is never green in one place and
# plain in another. ASCII fallbacks because a Windows console on an old code
# page renders ✓ as a question mark, and a checklist of question marks is worse
# than a checklist of plus signs.
_UNICODE_OK = sys.stdout.encoding is None or "utf" in (sys.stdout.encoding or "").lower()
TICK = good("✓" if _UNICODE_OK else "+")
CROSS = bad("✗" if _UNICODE_OK else "x")
BULLET = "•" if _UNICODE_OK else "*"
MIDDOT = dim("·" if _UNICODE_OK else "-")
ARROW = "←" if _UNICODE_OK else "<-"
RULE_CHAR = "─" if _UNICODE_OK else "-"


# The wordmark, for a terminal too narrow for the art — which is most of them,
# since the art is 100 columns and a terminal opens at 80.
WORDMARK = "<<  E M B A B E L  >>"


def banner_art() -> str:
    """The Embabel banner, in the brand's own indigo, when it fits.

    The art is the SERVER'S — embabel-agent's banner.txt, the same one the JVM
    prints on boot — so the terminal and the server show one mark rather than
    two interpretations of one. It lives in copy/ like every other thing this
    program says, and install.sh carries its own copy because it runs before
    there is a checkout to read; scripts/check-copy.py fails if the two drift.

    Width decides. Wrapped ASCII art is not a logo, it is a mess, and a first
    impression that arrives broken is worse than one that arrives small.
    """
    try:
        with open(os.path.join(APPLIANCE_DIR, COPY_DIR, "banner.txt"), encoding="utf-8") as f:
            art = f.read().rstrip("\n")
    except OSError:
        return "  " + paint(WORDMARK, "bold", "accent")
    widest = max((len(line) for line in art.splitlines()), default=0)
    if not _UNICODE_OK or shutil.get_terminal_size((80, 24)).columns < widest + 2:
        return "  " + paint(WORDMARK, "bold", "accent")
    return "\n".join(paint(line, "accent") for line in art.splitlines())


def banner(subtitle: str) -> str:
    """The first two lines setup prints. A function so the preview script and the
    program cannot drift — the whole reason the colour looked absent once already
    was two copies of the same line, one of them stale."""
    return ("\n" + banner_art() + "\n\n  " + paint("Embabel appliance", "bold", "accent")
            + dim(" — ") + subtitle + "\n  " + rule())


def rule(width: int = 60) -> str:
    return dim(RULE_CHAR * width)


def heading(text: str, width: int = 60) -> str:
    """A section title with a rule running to the margin — the shape the setup
    wizard already used, now with the title carrying the emphasis instead of the
    line. Trimmed to width so a long title never wraps the rule onto its own row."""
    label = f"{RULE_CHAR}{RULE_CHAR} {text} "
    tail = RULE_CHAR * max(0, width - len(label))
    return dim(f"{RULE_CHAR}{RULE_CHAR} ") + paint(text, "bold", "accent") + " " + dim(tail)
