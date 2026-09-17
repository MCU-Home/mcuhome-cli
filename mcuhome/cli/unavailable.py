# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Commands that exist and cannot be performed.

An act MCUHome cannot do yet still has its command: the answer to "how
do I flash this" is the command itself saying what is not there, not a
missing name a person has to guess at. Such a command refuses with
``CapabilityUnavailable`` and exit 1, so a client greys a button with
the reason instead of telling somebody that something failed.

The refusal names the act and what it waits on, in the tool's own words.
"""

from __future__ import annotations

from collections.abc import Callable

from mcuhome.cli.errors import CapabilityUnavailable
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation

__all__ = ["refuses", "unavailable"]


def unavailable(task: str, *, waits_on: str) -> CapabilityUnavailable:
    """The refusal of *task*, naming what it waits on."""
    return CapabilityUnavailable(
        _("mcuhome {task} cannot run in this version of MCUHome.").format(task=task),
        hint=waits_on,
    )


def refuses(task: str, *, waits_on: str) -> Callable[[Invocation], int]:
    """A handler that refuses *task* and calls nothing."""

    def handler(invocation: Invocation) -> int:
        del invocation  # nothing is resolved: the act does not happen
        raise unavailable(task, waits_on=waits_on)

    return handler
