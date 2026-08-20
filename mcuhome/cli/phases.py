# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The interact → validate → execute contract (cli ADR 0004 §3/§4).

Every command runs in three phases, in this order:

1. **interact** — only in interactive mode, and at application start:
   every question the command needs answered is asked up front, never
   scattered through the run. In non-interactive mode (a pipe, an
   explicit ``--no-interactive``, any machine output mode) the phase is
   skipped entirely.
2. **validate** — every required input present and valid, wherever it
   came from. Any gap ends the process here with exit code 2; nothing
   has run yet.
3. **execute** — the actual action. It is **never invoked** when
   validate failed: the boundary is not "before anything is written"
   but sharper — the action does not start at all.

The three phases are the *contract*, not a prescribed class layout
(the product owner's wording): a command hands :func:`run` plain
callables and keeps its own shape. The exit codes are the contract's
other half, and there are exactly three — nothing else, deliberately.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from mcuhome.model.errors import MCUHomeError

from mcuhome.cli.output import Output

__all__ = ["EXIT_FAILURE", "EXIT_OK", "EXIT_USAGE", "run"]

#: The whole exit-code vocabulary (ADR 0004 §4).
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def run(
    *,
    output: Output,
    execute: Callable[[], int],
    interact: Callable[[], None] | None = None,
    validate: Callable[[], Sequence[MCUHomeError]] | None = None,
) -> int:
    """Run one command through the three phases; the return is the exit code.

    *validate* answers with the problems it found — typed errors, so the
    refusal a user reads has the same face as every other one — and any
    problem is exit 2 with *execute* never called. A command with no
    questions and no argument-shape rules passes only *execute*; the
    contract still holds, its first two phases are simply empty.
    """
    if interact is not None and output.interactive:
        interact()
    if validate is not None:
        problems = list(validate())
        if problems:
            output.errors(problems, cwd=Path.cwd())
            return EXIT_USAGE
    return execute()
