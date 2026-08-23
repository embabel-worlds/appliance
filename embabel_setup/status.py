"""One self-updating line, and the three lamps on it.

ONE OBJECT OWNS THE BOTTOM LINE. The boot log prints from its own thread at the
same time, and two writers sharing a line shred each other — so every concurrent
log line goes through StatusLine.log, which erases, prints above, and redraws.
That coordination is why this is a class and not a print().
"""
import re
import shutil
import sys
import threading
import time

from .colour import _ANSI, _UNICODE_OK, BULLET, COLOUR, MIDDOT, accent, dim, good
from .dockerlib import _answers, _docker, find_graph_container, image_progress, mode_of

# ── the status line ─────────────────────────────────────────────────────────
#
# First boot takes a minute or two, and for most of it the terminal said one
# sentence and then nothing. Silence during a long wait is indistinguishable
# from a hang, and the honest fix is not a spinner — it is telling the truth
# about what is happening, which the host can actually observe.
#
# WHAT IT SHOWS IS REAL, never a fake percentage. Three lamps, each read from
# docker or from the API on every tick:
#
#   graph    the neo4j container's own healthcheck
#   server   the mode container's healthcheck
#   API      whether the door answers HTTP yet
#
# A boot that stalls therefore shows WHICH of the three it stalled on, which is
# the difference between "it is slow" and a support conversation.
#
# ONE WRITER FOR THE BOTTOM LINE. The boot log prints from its own thread at the
# same time, and two writers sharing a line produce shredded output — so every
# log line goes through [log], which erases the status, prints, and redraws it.
# That coordination is the whole reason this is a class and not a print().
#
# It disables itself whenever colour does: piped output must stay clean, and a
# progress animation in a CI log is thousands of lines of carriage returns.

_ESCAPES = re.compile(r"\x1b\[[0-9;]*m")


def _width() -> int:
    """Usable columns. One short of the terminal so a full-width line cannot
    wrap on the terminals that scroll at the last column rather than the first
    character past it."""
    return max(40, shutil.get_terminal_size((80, 24)).columns - 1)


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_ASCII = "|/-\\"
class StatusLine:
    """A single self-updating line, safe to share with a thread that prints."""

    def __init__(self) -> None:
        self.enabled = COLOUR and _UNICODE_OK is not None
        self.text = ""
        self.started = 0.0
        self.frame = 0
        self.live = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _frames(self) -> str:
        return SPINNER if _UNICODE_OK else SPINNER_ASCII

    def _render(self) -> str:
        elapsed = int(time.monotonic() - self.started)
        mark = accent(self._frames()[self.frame % len(self._frames())])
        clock = dim(f"{elapsed // 60}:{elapsed % 60:02d}")
        line = f"  {mark} {self.text}  {clock}"
        # Truncated on the VISIBLE length, which is not len(): the escape codes
        # in a coloured line are bytes the terminal never draws, so measuring the
        # raw string cuts a legible line short and lets a long one wrap anyway.
        visible = len(_ESCAPES.sub("", line))
        room = _width()
        if visible <= room:
            return line
        # Cut the plain text and repaint, rather than slicing mid-escape.
        return _ESCAPES.sub("", line)[:room - 1] + dim("…")

    def _erase(self) -> None:
        # Overwrite with spaces rather than an ANSI erase: \r plus blanks works
        # on every terminal this runs on, including the ones that ignore CSI K.
        # The TERMINAL'S width, not a guess. This was a fixed 78, and the moment
        # the line grew past it — which naming an image pull does — the tail of
        # the previous line survived every redraw as visible wreckage.
        sys.stdout.write("\r" + " " * _width() + "\r")

    def _animate(self) -> None:
        while not self._stop.wait(0.12):
            with self._lock:
                if self.live:
                    self.frame += 1
                    sys.stdout.write("\r" + self._render())
                    sys.stdout.flush()

    def start(self, text: str) -> None:
        if not self.enabled:
            print(f"  {text}")
            return
        with self._lock:
            self.text, self.started, self.live = text, time.monotonic(), True
        if self._thread is None:
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()

    def set(self, text: str) -> None:
        """Change what the line says without restarting the clock — the elapsed
        time is of the WAIT, not of the phase, because that is the number
        somebody is deciding whether to worry about."""
        if not self.enabled:
            return
        with self._lock:
            self.text = text

    def log(self, line: str) -> None:
        """Print above the status line. Every concurrent writer uses this."""
        with self._lock:
            if self.live and self.enabled:
                self._erase()
            print(line)
            if self.live and self.enabled:
                sys.stdout.write("\r" + self._render())
                sys.stdout.flush()

    def stop(self, final: str | None = None) -> None:
        with self._lock:
            if self.live and self.enabled:
                self._erase()
            self.live = False
            if final:
                print(final)
STATUS = StatusLine()
def boot_phase(container: str | None, base: str) -> str:
    """The three lamps, read fresh. Cheap enough for a 3-second poll: two docker
    inspects against the local daemon and nothing over the network."""
    def health(name: str) -> str:
        run = _docker("inspect", "-f",
                      "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                      name, timeout=10)
        return run.stdout.strip() if run and run.returncode == 0 else "?"

    def lamp(ok: bool, label: str) -> str:
        return f"{good(BULLET) if ok else MIDDOT} {label if ok else dim(label)}"

    graph = find_graph_container()
    parts = [lamp(graph is not None and health(graph) == "healthy", "graph")]
    if container:
        parts.append(lamp(health(container) == "healthy", "server"))
    parts.append(lamp(_answers(base), "API"))
    line = "Starting the appliance   " + dim(" · ").join(parts)
    pulling = image_progress(mode_of(container))
    return line + ("   " + pulling if pulling else "")
