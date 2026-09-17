# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome`` — the entry point, and the order one run happens in.

The command line is a **client of the workbench**: it parses arguments,
resolves configuration, calls ``mcuhome.workbench.api`` and renders what
comes back. It holds no knowledge of its own about projects, devices,
builds or signing — where this package seems to decide something, the
decision is which function to call.

One run, in order:

1. **Help wins wherever it stands.** ``-h``/``--help`` is answered
   before the parse, because argparse would otherwise read it as a
   flag's missing value (``--board -h``) and refuse instead of helping.
   Everything after a ``--`` separator is literal and never help.
2. **The presentation is read before anything is refused** — the output
   mode, the color rule, interactivity — so that every run in a machine
   mode answers with a document, an unknown flag and a missing argument
   included.
3. **A retired spelling is refused by name**, with its successor.
4. **The parse**, whose own problems are refusals like every other one:
   exit 2, and the act never started.
5. **interact → validate → execute**, the three phases, and the exit
   code they answer.

Refusals are rendered in one place, at the bottom of this module: stderr
for a person, the refusal document (and the stream's `error` messages)
for a machine, and never an exception formatted by a command.
"""

from __future__ import annotations

import argparse
import os
import sys
from itertools import takewhile
from pathlib import Path

from mcuhome.workbench import api

from mcuhome.cli import output as output_module
from mcuhome.cli import parser as parser_module
from mcuhome.cli import phases
from mcuhome.cli.errors import UsageError, exit_code_for
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.retiredspellings import refuse_retired_spelling
from mcuhome.cli.versioncommand import version_text

__all__ = ["main"]

_HELP_FLAGS = ("-h", "--help")


def main(argv: list[str] | None = None) -> int:
    """One invocation of ``mcuhome``; the return is the exit code."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    parser = parser_module.build_parser()

    helped = _help_for(parser, tokens)
    if helped is not None:
        helped.print_help()
        return phases.EXIT_OK

    try:
        presentation = parser_module.read_presentation(tokens)
    except UsageError as refusal:
        # The mode itself was not one this command line has, so there is
        # no machine mode to answer in: the refusal is rendered for a
        # person, which is what the run has left.
        output_module.resolve().errors([refusal], cwd=Path.cwd())
        return phases.EXIT_USAGE

    output = output_module.resolve(
        mode=presentation.mode,
        color=presentation.color,
        interactive=presentation.interactive,
        env=os.environ,
    )

    try:
        if "--version" in takewhile(lambda token: token.startswith("-"), tokens):
            print(version_text())
            return phases.EXIT_OK
        refuse_retired_spelling(tokens)
        args = parser.parse_args(tokens)
        handler = getattr(args, "handler", None)
        if handler is None:
            # A noun without a verb, and `mcuhome` alone: the help of
            # what was named, and a run that did what it was asked.
            getattr(args, "show_help", parser.print_help)()
            return phases.EXIT_OK
        invocation = Invocation(
            task=args.task, args=args, output=output, env=os.environ, cwd=Path.cwd()
        )
        interact = getattr(args, "interact", None)
        validate = getattr(args, "validate", None)
        return phases.run(
            output=output,
            interact=None if interact is None else (lambda: interact(invocation)),
            validate=None if validate is None else (lambda: validate(invocation)),
            execute=lambda: int(handler(invocation)),
        )
    except api.MCUHomeError as refusal:
        # Both streams end up in the same terminal, and a command that
        # printed progress before failing must not have its refusal
        # appear above the output it refers to.
        sys.stdout.flush()
        output.errors([refusal], cwd=Path.cwd())
        return exit_code_for(refusal)
    except KeyboardInterrupt:
        # The two commands that stop cleanly do it through their stop
        # predicate; everywhere else, and after a second Ctrl-C, the run
        # ends here and what a half-written act left is what the api
        # says it leaves.
        sys.stdout.flush()
        output.log(_("Interrupted."))
        return phases.EXIT_FAILURE


def _help_for(parser: argparse.ArgumentParser, tokens: list[str]) -> argparse.ArgumentParser | None:
    """The parser whose help was asked for, or ``None`` where none was.

    The walk descends the command tree for as long as tokens name
    subcommands and ignores everything else, so ``mcuhome device new
    --board -h`` answers with the help of ``device new``.
    """
    limit = tokens.index("--") if "--" in tokens else len(tokens)
    if not any(token in _HELP_FLAGS for token in tokens[:limit]):
        return None
    current = parser
    for token in tokens[:limit]:
        if token in _HELP_FLAGS:
            continue
        sub = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sub is None:
            break
        if token in sub.choices:
            current = sub.choices[token]
    return current


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
