# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome version`` — which MCUHome packages are installed here.

The first thing to state in a bug report, which is why ``--version``
exists as a flag beside the command: the flag prints the same answer as
text.

The workbench answers the packages it knows of; this package's own
version is the one fact it cannot know — it cannot know what embeds it —
and is therefore the one value this command line states beside the
document rather than inside it.
"""

from __future__ import annotations

from mcuhome.workbench import api

from mcuhome.cli import __version__ as command_line_version
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.phases import EXIT_OK

__all__ = ["version", "version_text"]


def version(invocation: Invocation) -> int:
    """``mcuhome version``: the command line, the workbench, the stack."""
    invocation.start()
    stack = api.stack_versions()
    for line in _lines(stack):
        invocation.output.human(line)
    invocation.output.result({"ok": True, "command_line": command_line_version, "stack": stack})
    return EXIT_OK


def version_text() -> str:
    """What ``mcuhome --version`` prints: the same answer, as text."""
    return "\n".join(_lines(api.stack_versions()))


def _lines(stack: dict[str, str]) -> list[str]:
    """One line per package, the command line's own first.

    A package that is not installed answers the empty string, and the
    line says that rather than showing a blank where a version belongs.
    """
    lines = [f"mcuhome-cli {command_line_version}"]
    lines.extend(f"{name} {installed or 'not installed'}" for name, installed in stack.items())
    return lines
