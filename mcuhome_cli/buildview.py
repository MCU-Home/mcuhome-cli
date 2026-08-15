# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The live view of a running build (cli ADR 0004, PO 2026-08-15).

A build is minutes of someone else's output, and the two questions a
person actually has — *how far along is it* and *where is this running*
— were answered by nothing while thousands of compiler lines scrolled
the terminal away. This module answers both without asking the user to
scroll:

**The step line.** Every build states its steps up front, each labeled
with where it runs (``compile (container zephyr-4.4.0-r8)``, ``sign
(local)``), and the line is repainted in place as they progress: green
done, yellow running, red failed, plain not yet.

**The window.** The build log is shown through a fixed-height frame —
the last :data:`WINDOW_LINES` lines, newest at the bottom, repainted in
place like ``docker build`` does — so the terminal shows that the build
is moving without the movement costing scrollback. The full log always
goes to a file (``build.log`` in the build directory), and the line
above the frame says so. When the build ends the frame collapses: a
finished build leaves the step line and the summary, a failed one leaves
the step line, the tail of the log and the refusal — the things worth
keeping are exactly the things that remain.

**Two implementations, one seam.** :func:`make_view` answers with the
live view only when the run is interactive (ADR 0004 §3: a TTY, no
``--no-interactive``, not a machine mode); everywhere else —  CI, pipes,
``-o json``/``json-stream`` — :class:`PlainView` keeps today's linear
behavior: log lines pass through (stdout for a human, stderr under a
machine mode) and steps print nothing of their own, because the machine
modes already carry them as ``progress`` verbs and a linear human run
sees the log itself. Both write the log file; the file is part of the
contract, not of the rendering.

Repaints happen under a lock and are throttled: the log lines arrive
from whatever thread drives the build (the container reader, the west
tee, the session client's event loop), and a terminal repainted for
every line of a 1200-target build would burn more time drawing than
compiling.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from shutil import get_terminal_size
from typing import IO, TextIO
from unicodedata import east_asian_width

from mcuhome_cli.output import GREEN, RED, YELLOW, Output

__all__ = [
    "PENDING",
    "RUNNING",
    "DONE",
    "FAILED",
    "LOG_FILE",
    "WINDOW_LINES",
    "BuildStep",
    "LiveView",
    "PlainView",
    "ViewStream",
    "log_tail",
    "make_view",
]

#: The four states a step can be in — also the order they may move in.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

#: How many log lines the live frame shows at once (PO 2026-08-15).
WINDOW_LINES = 15

#: The full build log, next to the artifacts in the build directory.
LOG_FILE = "build.log"

#: Seconds between repaints. A repaint also always happens on a step
#: change and on close, so the throttle only thins the log stream.
_REPAINT_EVERY = 0.05

_GLYPHS = {PENDING: "·", RUNNING: "▶", DONE: "✓", FAILED: "✗"}
_COLORS = {RUNNING: (YELLOW,), DONE: (GREEN,), FAILED: (RED,)}


@dataclass
class BuildStep:
    """One step of the step line: a key to address it, a label to show.

    The label carries the *where* (``compile (container r8)``) because
    making the execution place visible is half of why this view exists.
    """

    key: str
    label: str
    state: str = PENDING


def make_view(
    steps: list[BuildStep],
    *,
    output: Output,
    log_path: Path,
    stream: TextIO | None = None,
) -> LiveView | PlainView:
    """The right view for this run: live on an interactive human run.

    *log_path* is where the full log goes in either case; parents are
    created, an existing file is truncated — the log belongs to this
    build the way the artifacts do. *stream* is the test seam for the
    live view's terminal.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if output.mode == "human" and output.interactive:
        return LiveView(steps, output=output, log_path=log_path, stream=stream)
    return PlainView(steps, output=output, log_path=log_path)


def log_tail(path: Path, lines: int = 25) -> str:
    """The last *lines* lines of *path*, for the failure rendering."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


class ViewStream:
    """A ``TextIO``-shaped adapter feeding a view line by line.

    ``local-dev``'s compiler tees its subprocess output into a stream
    object in-process (``workspace.run_build``), so anything with
    ``write``/``flush`` can stand in for the terminal — this hands the
    lines to the view instead, buffering partial writes until their
    newline arrives.
    """

    def __init__(self, view: LiveView | PlainView) -> None:
        self._view = view
        self._pending = ""
        # The buffer is read-modify-write state and the writers may sit
        # on different threads (a tee thread writing, the driver
        # flushing at the end) — same reason the view itself locks.
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        lines: list[str] = []
        with self._lock:
            self._pending += text
            while "\n" in self._pending:
                line, _, self._pending = self._pending.partition("\n")
                lines.append(line)
        for line in lines:
            self._view.line(line)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            rest, self._pending = self._pending, ""
        if rest:
            self._view.line(rest)


@dataclass
class _ViewBase:
    steps: list[BuildStep]
    output: Output
    log_path: Path

    def __post_init__(self) -> None:
        self._log: IO[str] | None = self.log_path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    # -- the seam the build drives ------------------------------------

    def step(self, key: str) -> None:
        """*key* starts running; every step before it is done.

        One verb on purpose: the driver only ever says "this is where I
        am now", and the view derives the rest — a protocol a build
        method can emit without owning a state machine.
        """
        with self._lock:
            for entry in self.steps:
                if entry.key == key:
                    entry.state = RUNNING
                    break
                if entry.state in (PENDING, RUNNING):
                    entry.state = DONE
        self._on_change()

    def line(self, text: str) -> None:
        """One log line — into the file always, into the rendering as fits."""
        text = text.rstrip("\n")
        with self._lock:
            if self._log is not None:
                self._log.write(text + "\n")
        self._on_line(text)

    def close(self, *, success: bool) -> None:
        """The run is over; settle the states and stop painting."""
        with self._lock:
            if self._log is not None:
                self._log.close()
                self._log = None
            if success:
                # A run that ended well went through everything it
                # stated; a stated step it skipped would be a driver bug
                # this line has no way to see.
                for entry in self.steps:
                    if entry.state in (PENDING, RUNNING):
                        entry.state = DONE
            else:
                failed = False
                for entry in self.steps:
                    if entry.state == RUNNING:
                        entry.state = FAILED
                        failed = True
                if not failed:
                    # Nothing was mid-flight (the failure came between
                    # steps): mark the first open step failed so the line
                    # does not claim an untroubled run.
                    for entry in self.steps:
                        if entry.state == PENDING:
                            entry.state = FAILED
                            break
        self._on_close(success)

    # -- what the two implementations fill in --------------------------

    def _on_change(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_line(self, text: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_close(self, success: bool) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- shared rendering ----------------------------------------------

    def step_line(self) -> str:
        parts = []
        for entry in self.steps:
            glyph = _GLYPHS[entry.state]
            text = f"{glyph} {entry.label}"
            codes = _COLORS.get(entry.state)
            parts.append(self.output.style(text, *codes) if codes else text)
        return "  ".join(parts)


@dataclass
class PlainView(_ViewBase):
    """Today's linear behavior, plus the log file.

    Log lines pass through — stdout for a human, stderr under a machine
    mode, where stdout belongs to the document — and the steps print
    nothing: a linear human run watches the log itself, and the machine
    modes carry the steps as ``progress`` verbs already.
    """

    def _on_change(self) -> None:
        pass

    def _on_line(self, text: str) -> None:
        if self.output.machine:
            self.output.log(text)
        else:
            print(text)

    def _on_close(self, success: bool) -> None:
        pass


@dataclass
class LiveView(_ViewBase):
    """The repainted block: step line, log pointer, framed log window."""

    stream: TextIO | None = None
    _window: list[str] = field(default_factory=list)
    _painted_lines: int = 0
    _painted_at: float = 0.0
    _closed: bool = False

    def _terminal(self) -> TextIO:
        return self.stream if self.stream is not None else sys.stdout

    # -- seam reactions -------------------------------------------------

    def _on_change(self) -> None:
        self._paint(force=True)

    def _on_line(self, text: str) -> None:
        with self._lock:
            self._window.append(text)
            del self._window[:-WINDOW_LINES]
        self._paint()

    def _on_close(self, success: bool) -> None:
        # Collapse: repaint one last time without the frame, leaving the
        # settled step line and the log pointer as the run's residue.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            terminal = self._terminal()
            self._rewind(terminal)
            terminal.write(self.step_line() + "\n")
            terminal.write(self._pointer() + "\n")
            terminal.flush()
            self._painted_lines = 2

    # -- painting -------------------------------------------------------

    def _pointer(self) -> str:
        return f"  log: {self.log_path}"

    def _rewind(self, terminal: TextIO) -> None:
        """Back to the top of the painted block, erasing it downward.

        Nothing painted, nothing erased: the first paint must not clear
        terminal content below the cursor that was never this view's.
        """
        if self._painted_lines:
            terminal.write(f"\x1b[{self._painted_lines}F")
            terminal.write("\x1b[0J")

    def _paint(self, *, force: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            now = time.monotonic()
            if not force and now - self._painted_at < _REPAINT_EVERY:
                return
            self._painted_at = now
            size = get_terminal_size()
            width = max(size.columns, 20)
            inner = width - 4
            # A block taller than the terminal cannot be rewound — the
            # cursor stops at the top row and every repaint would stack
            # a copy. Clamp the window to what fits; the rewind uses the
            # height actually painted, so the block may change size.
            visible = max(min(WINDOW_LINES, size.lines - 5), 3)
            terminal = self._terminal()
            self._rewind(terminal)
            lines = [self.step_line(), self._pointer()]
            lines.append("┌" + "─" * (width - 2) + "┐")
            window = self._window[-visible:]
            window = [""] * (visible - len(window)) + window
            for text in window:
                lines.append("│ " + _clip(text, inner) + " │")
            lines.append("└" + "─" * (width - 2) + "┘")
            terminal.write("\n".join(lines) + "\n")
            terminal.flush()
            self._painted_lines = len(lines)


#: Whole ANSI control sequences — CSI (colors, cursor movement), OSC
#: (titles), and the two-byte escapes. Removing only the escape byte
#: would leave ``[31m`` behind as literal text in the frame.
_ANSI = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)?|[@-Z\\-_])")


def _cells(char: str) -> int:
    """Terminal cells *char* occupies — CJK and friends take two."""
    return 2 if east_asian_width(char) in ("W", "F") else 1


def _clip(text: str, width: int) -> str:
    """*text* cut and padded to exactly *width* terminal cells.

    Tabs would move the cursor past the frame; escape sequences would
    style or steer it; a wide character counts what it occupies, not
    one. All of it is foreign to a frame this module draws, so it is
    measured and flattened rather than trusted.
    """
    text = _ANSI.sub("", text.replace("\t", "    ")).replace("\x1b", "")
    total = sum(_cells(char) for char in text)
    if total <= width:
        return text + " " * (width - total)
    used = 0
    kept: list[str] = []
    for char in text:
        cost = _cells(char)
        if used + cost > width - 1:
            break
        kept.append(char)
        used += cost
    return "".join(kept) + "…" + " " * (width - used - 1)
