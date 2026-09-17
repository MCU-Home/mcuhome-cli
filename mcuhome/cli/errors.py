# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The conditions this command line refuses on its own behalf.

Almost every refusal a person reads here comes from the workbench, where
the condition has a class name and that name is the ``kind`` of the
error document. Three conditions have no call to raise them, because the
call never happens:

``UsageError``
    the invocation was wrong — an unknown command or flag, a missing
    argument, flags that contradict each other, a value of the wrong
    shape.
``RetiredSpelling``
    a command or a flag this command line used to have. The message
    names the successor; it is never accepted as an alias, because two
    spellings for one thing is what makes a surface unlearnable.
``CapabilityUnavailable``
    the act exists as a command and MCUHome cannot perform it yet. A
    client greys a button with the reason instead of telling a person
    that something failed.

The set is closed and append-only: a ``kind`` that is not one of these
three is a workbench exception. All three carry the one error document —
``message``, ``file``, ``line``, ``column``, ``key``, ``hint``,
``kind`` — with no file to point at, so that a consumer reads one shape
whoever refused.

The exit code follows the condition and not the command
(:func:`exit_code_for`): an invocation that was wrong is 2 and nothing
ran, everything else is 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcuhome.workbench.api import MCUHomeError

from mcuhome.cli.phases import EXIT_FAILURE, EXIT_USAGE

__all__ = [
    "CapabilityUnavailable",
    "CommandLineRefusal",
    "RetiredSpelling",
    "UsageError",
    "exit_code_for",
]


class CommandLineRefusal(MCUHomeError):
    """A refusal this command line states itself.

    Never raised directly — the three conditions below are the whole
    vocabulary, and each of them is listed in ``docs/cli.md``.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self, base: Path | None = None) -> str:
        """The refusal as a person reads it on stderr."""
        del base  # these three conditions stand in no file
        lines = [f"Error: {self.message}"]
        if self.hint:
            lines.append(f"  Fix: {self.hint}")
        return "\n".join(lines)

    def to_dict(self, *, root: Path | None = None) -> dict[str, Any]:
        """The one error document, with `kind` naming the condition."""
        del root  # no file, so nothing to make relative
        return {
            "message": self.message,
            "file": None,
            "line": None,
            "column": None,
            "key": None,
            "hint": self.hint,
            "kind": type(self).__name__,
        }

    def __str__(self) -> str:
        return self.render()


class UsageError(CommandLineRefusal):
    """The invocation was wrong, and nothing ran."""


class RetiredSpelling(CommandLineRefusal):
    """A spelling this command line used to have; the message names its successor."""


class CapabilityUnavailable(CommandLineRefusal):
    """The act has a command and MCUHome cannot perform it yet."""


def exit_code_for(error: MCUHomeError) -> int:
    """The exit code a refusal answers with.

    A wrong invocation is 2 because the act never started; every other
    refusal ran far enough to say no and is 1. A value a flag carries is
    parsed before the act starts and is therefore 2, while the same value
    out of a file or the environment is refused while the command runs
    and is 1 — which is the workbench's refusal and lands here as one.
    """
    return EXIT_USAGE if isinstance(error, UsageError | RetiredSpelling) else EXIT_FAILURE
