# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""What the ``mcuhome`` command emits, in one place (cli ADR 0004).

Three output modes behind one ``-o/--output`` flag, and one stream
discipline for all of them:

``human`` (the default)
    stdout carries the rendering a person reads; nothing here is a
    format anyone may parse.
``json``
    stdout carries **exactly one** JSON document, emitted when the run
    is over. Failure form: ``{"ok": false, "errors": [...]}``.
``json-stream``
    NDJSON — one JSON message per line, as the run progresses. Four
    verbs for now: ``start``, ``progress``, ``error``, ``result``; the
    ``result`` message carries the same document ``-o json`` would have
    printed, under its ``document`` key. The verb vocabulary is
    append-only and consumers must ignore verbs they do not know.

Whatever the mode, stdout belongs to the document or the human
rendering and to nothing else: logs, warnings and rendered errors go to
stderr. Machine output never contains localized text in structural
positions — verbs, keys and codes are vocabulary, not prose.

Colors follow ``--color auto|always|never`` plus the ``NO_COLOR``
convention (a non-empty value disables them), and exist only in text a
human reads — a JSON document never carries an escape code.

Interactivity is resolved here too, because the machine modes force it:
``-o json``/``-o json-stream`` are non-interactive whatever a flag says
(a consumer parsing NDJSON cannot answer a question), the explicit
flags decide otherwise, and the default is TTY detection.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcuhome.model.errors import MCUHomeError

from mcuhome_cli.i18n import _

__all__ = [
    "HUMAN",
    "JSON",
    "JSON_STREAM",
    "MODES",
    "Output",
    "format_table",
    "resolve",
]

#: The values ``-o/--output`` takes, and what each means (module docstring).
HUMAN = "human"
JSON = "json"
JSON_STREAM = "json-stream"
MODES = (HUMAN, JSON, JSON_STREAM)

#: SGR codes for :meth:`Output.style`. Deliberately few: color is
#: seasoning on the uniform rendering, never information of its own — a
#: ``--color never`` run says everything the colored one does.
RED = "31"
GREEN = "32"
YELLOW = "33"
BOLD = "1"


def format_table(rows: Sequence[Sequence[str]]) -> str:
    """Aligned columns, two spaces apart — the one table style (ADR 0004 §1).

    Plain text in, plain text out: cells must not carry escape codes,
    because alignment counts characters. A caller that wants color
    styles the finished line.
    """
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    widths = [max((len(row[i]) for row in rows if i < len(row)), default=0) for i in range(columns)]
    lines = []
    for row in rows:
        cells = (text.ljust(widths[i]) for i, text in enumerate(row))
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def resolve(
    *,
    mode: str = HUMAN,
    color: str = "auto",
    interactive: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> Output:
    """The one place the three ``-o``/``--color``/interactivity knobs meet.

    *interactive* is the explicit flag pair (``--interactive`` /
    ``--no-interactive``) or ``None`` for the TTY default; the machine
    modes override it either way. *env* defaults to the process
    environment, which is the command line's to read (unlike the
    workbench's, ADR 0020) — stated here so tests can state theirs.
    """
    if env is None:
        env = os.environ
    machine = mode != HUMAN
    if color == "always":
        colored = True
    elif color == "never":
        colored = False
    else:
        colored = sys.stdout.isatty() and not env.get("NO_COLOR")
    if machine:
        is_interactive = False
    elif interactive is not None:
        is_interactive = interactive
    else:
        is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
    return Output(mode=mode, color=bool(colored), interactive=is_interactive)


@dataclass(frozen=True)
class Output:
    """One command invocation's output contract, resolved.

    Commands write through this and never test the mode themselves for
    anything the methods already decide — the discipline of the module
    docstring lives here, not in every command.
    """

    mode: str = HUMAN
    color: bool = False
    interactive: bool = False

    @property
    def machine(self) -> bool:
        """Whether stdout is a document (``json``/``json-stream``)."""
        return self.mode != HUMAN

    # -- text a human reads -------------------------------------------

    def style(self, text: str, *codes: str) -> str:
        """*text*, wrapped in SGR codes when colors are on."""
        if not self.color or not codes:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"

    def human(self, text: str = "") -> None:
        """A line of human rendering — suppressed in the machine modes."""
        if self.mode == HUMAN:
            print(text)

    def log(self, message: str) -> None:
        """A line for stderr, in every mode — logs and pre-formed warnings."""
        print(message, file=sys.stderr)

    def warn(self, problem: str) -> None:
        """A warning: *problem* is a plain sentence, the prefix is ours."""
        self.log(f"{self.style(_('Warning:'), YELLOW, BOLD)} {problem}")

    # -- the machine modes --------------------------------------------

    def start(self, task: str, **data: Any) -> None:
        """The ``start`` verb: *task* begins (``json-stream`` only)."""
        self._event({"verb": "start", "task": task, **data})

    def progress(self, stage: str, **data: Any) -> None:
        """The ``progress`` verb: an honest stage, never an invented percentage."""
        self._event({"verb": "progress", "stage": stage, **data})

    def error(self, entry: dict[str, Any]) -> None:
        """The ``error`` verb: one serialized error, as it happens."""
        self._event({"verb": "error", "error": entry})

    def result(self, document: Any) -> None:
        """The final document: the one ``-o json`` print, or the ``result`` verb."""
        if self.mode == JSON:
            print(json.dumps(document, indent=2))
        elif self.mode == JSON_STREAM:
            self._event({"verb": "result", "document": document})

    def _event(self, message: dict[str, Any]) -> None:
        # One message per line, flushed as it happens: a live consumer is
        # the point of the stream mode, and a buffered "live" stream lies.
        if self.mode == JSON_STREAM:
            print(json.dumps(message, separators=(",", ":")), flush=True)

    # -- errors, in every mode ----------------------------------------

    def errors(
        self,
        problems: Sequence[MCUHomeError],
        *,
        root: Path | None = None,
        cwd: Path | None = None,
    ) -> None:
        """Rendered refusals — stderr for a human, the failure document else.

        *root* makes file paths project-relative in the serialized form;
        *cwd* shortens them in the human one. The caller supplies the
        exit code — this only says what went wrong, in the mode's shape.
        """
        if self.mode == HUMAN:
            base = Path.cwd() if cwd is None else cwd
            for problem in problems:
                self.log(self._render(problem, base))
            return
        entries = [entry for problem in problems for entry in problem.to_dicts(root=root)]
        for entry in entries:
            self.error(entry)
        self.result({"ok": False, "errors": entries})

    def _render(self, problem: MCUHomeError, base: Path) -> str:
        text = problem.render(base)
        first, newline, rest = text.partition("\n")
        return self.style(first, RED) + newline + rest
