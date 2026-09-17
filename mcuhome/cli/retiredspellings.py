# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Spellings this command line used to have, and what they are now.

One thing has one spelling. A command or a flag that was renamed is
**refused by name**, with its successor in the message and exit code 2 —
never accepted quietly as an alias, because two spellings for one thing
is what makes a surface unlearnable, and a refusal that names the
successor teaches it once.

The check runs before the parse, so a retired spelling is answered with
the name it has today rather than with "unrecognized arguments". Only
what stands before a ``--`` separator is examined: after it, a token is
a value and a value that looks like a flag is still a value.

The tables are the ones ``docs/cli.md`` lists under *Retired
spellings*, and the list is shortened when it stops helping anybody.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mcuhome.cli.errors import RetiredSpelling
from mcuhome.cli.i18n import _

__all__ = [
    "RETIRED_COMMANDS",
    "RETIRED_FLAGS",
    "RetiredCommand",
    "RetiredFlag",
    "refuse_retired_spelling",
]


@dataclass(frozen=True)
class RetiredCommand:
    """A command spelling that was renamed."""

    #: The words as they were typed, without ``mcuhome``.
    words: tuple[str, ...]
    #: The whole command it is today.
    successor: str
    #: A flag that was part of the old spelling and picks this successor
    #: over the entry without it (``device matter-pairing --new``).
    flag: str = ""


@dataclass(frozen=True)
class RetiredFlag:
    """A flag spelling that was renamed."""

    spelling: str
    #: What states the same thing today — a flag, or a command where the
    #: act became one of its own.
    successor: str
    #: The command the flag belonged to; empty means every command.
    command: tuple[str, ...] = ()


#: Every retired command spelling. The two-word entries are matched
#: first, so ``device boards`` is answered as itself rather than as
#: ``device``.
RETIRED_COMMANDS: tuple[RetiredCommand, ...] = (
    RetiredCommand(("clean",), "mcuhome device clean"),
    RetiredCommand(("doctor",), "mcuhome host check"),
    RetiredCommand(("schema", "config"), "mcuhome device print-schema"),
    RetiredCommand(("schema", "registry"), "mcuhome device list-supported"),
    RetiredCommand(("public-key",), "mcuhome signing print-public-key"),
    RetiredCommand(("device", "boards"), "mcuhome device list-boards"),
    RetiredCommand(("device", "first-time-setup"), "mcuhome device install-bootloader"),
    RetiredCommand(("device", "matter-pairing"), "mcuhome device print-matter-pairing"),
    RetiredCommand(
        ("device", "matter-pairing"), "mcuhome device create-matter-pairing", flag="--new"
    ),
    RetiredCommand(("validate",), "mcuhome device validate"),
    RetiredCommand(("build",), "mcuhome device build"),
    RetiredCommand(("sign",), "mcuhome device sign-firmware"),
    RetiredCommand(("new",), "mcuhome device new"),
    RetiredCommand(("init",), "mcuhome project init"),
    RetiredCommand(("init-pairing",), "mcuhome device create-matter-pairing"),
    RetiredCommand(("device", "init-pairing"), "mcuhome device create-matter-pairing"),
)

#: Every retired flag spelling. A flag with a command is retired there
#: and nowhere else — ``--name`` is the device's own name on
#: ``device new`` and a scope's name everywhere else.
RETIRED_FLAGS: tuple[RetiredFlag, ...] = (
    RetiredFlag("--build-dir", "--out-dir"),
    RetiredFlag(
        "--sdk-sources",
        "--build-sdk-sources, and the two kinds beside it: "
        "--build-workspace-sources and --build-tools-sources",
    ),
    RetiredFlag("--build-token", "--build-server-token"),
    RetiredFlag("--no-wait", "--no-wait-for-turn"),
    RetiredFlag("--max-wait", "--max-wait-seconds"),
    RetiredFlag("--name", "--friendly-name", command=("device", "new")),
    RetiredFlag("--generate-only", "mcuhome device generate-application"),
    RetiredFlag("--project", "--scope project"),
    RetiredFlag("--user", "--scope user"),
    RetiredFlag("--system", "--scope system"),
    RetiredFlag("--confirm-upgrade", "--confirm"),
    RetiredFlag("--json", "-o json"),
    RetiredFlag("--server", "--build-server"),
    RetiredFlag("--token", "--build-server-token"),
    RetiredFlag("--method", "--build-target and --build-mode"),
)


def refuse_retired_spelling(tokens: Sequence[str]) -> None:
    """Refuse *tokens* where they carry a spelling that was retired.

    Raises :class:`~mcuhome.cli.errors.RetiredSpelling`; answers nothing
    when every spelling is a current one.
    """
    examined = list(tokens[: tokens.index("--")]) if "--" in tokens else list(tokens)
    words = tuple(_leading_words(examined))
    flags = {token.partition("=")[0] for token in examined if token.startswith("--")}

    command = _retired_command(words, flags)
    if command is not None:
        raise RetiredSpelling(
            _("{spelling} is not a command any more.").format(
                spelling="mcuhome "
                + " ".join(command.words + ((command.flag,) if command.flag else ()))
            ),
            hint=_("it is {successor} now.").format(successor=command.successor),
        )
    for retired in RETIRED_FLAGS:
        if retired.spelling not in flags:
            continue
        if retired.command and tuple(words[: len(retired.command)]) != retired.command:
            continue
        raise RetiredSpelling(
            _("{spelling} is not a flag any more.").format(spelling=retired.spelling),
            hint=_("it is {successor} now.").format(successor=retired.successor),
        )


def _leading_words(tokens: Sequence[str]) -> list[str]:
    """The words before the first flag — what names the command."""
    words: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            break
        words.append(token)
    return words


def _retired_command(words: Sequence[str], flags: set[str]) -> RetiredCommand | None:
    """The entry *words* name, the longest and most specific one first."""
    for length in (2, 1):
        candidate = tuple(words[:length])
        if len(candidate) < length:
            continue
        matches = [entry for entry in RETIRED_COMMANDS if entry.words == candidate]
        with_flag = [entry for entry in matches if entry.flag and entry.flag in flags]
        if with_flag:
            return with_flag[0]
        plain = [entry for entry in matches if not entry.flag]
        if plain:
            return plain[0]
    return None
