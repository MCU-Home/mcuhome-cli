# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Every configuration option's flag, derived rather than written down.

A flag that carries an option is ``--`` plus the option's key with every
``.`` and ``_`` as ``-`` — ``build.sdk_sources`` becomes
``--build-sdk-sources`` — and the workbench derives that spelling itself
(``Option.flag``). This module is what turns the registry into argparse
arguments and back into :class:`~mcuhome.workbench.api.Argument` values,
so the command line has no list of its own to keep in step: an option
the workbench declares tomorrow has its flag here the same day, and one
it retires loses it.

Three things follow from the derivation and are implemented here:

* A **list**-valued option is repeatable and each use appends, because
  the flag *is* the key and the repetition already says "another one".
* A **boolean** derives both ``--x`` and ``--no-x``, and both always
  exist: "not used" and "turned off" are different statements.
* An option whose argument channel is closed has **no flag at all**
  (``build.builder``, and the two map options, which are written in a
  file or not at all).

**What is parsed where.** argparse keeps the text; the value is parsed
in the validate phase (:func:`arguments`) through the option's own
declaration, so a value of the wrong shape is a wrong invocation — exit
2, with the act never started — while the same value out of a file or
the environment is refused by the workbench while the command runs. An
unused flag is **absent** from the answer rather than ``None``: "the
flag was not used" and "the flag was used to clear the value" are
different statements, and only the command line can tell them apart.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mcuhome.workbench import api

from mcuhome.cli.errors import UsageError
from mcuhome.cli.i18n import _

__all__ = [
    "OptionFlag",
    "add_option_flags",
    "arguments",
    "build_option_flags",
    "option_flags",
    "problems",
    "project_option_flags",
    "signing_option_flags",
]

#: What a flag's value is called in the help, by the option's kind. The
#: two list kinds carry the singular: one use of the flag is one entry.
_METAVARS = {
    "string": "VALUE",
    "path": "PATH",
    "paths": "PATH",
    "strings": "NAME",
    "integer": "N",
    "number": "N",
}


@dataclass(frozen=True)
class OptionFlag:
    """One declared option as a command-line flag."""

    option: api.Option
    #: Where argparse keeps the text — the key with dots and underscores
    #: as underscores, which is reversible like the flag itself.
    dest: str

    @property
    def spelling(self) -> str:
        """``--build-sdk-sources`` — the workbench's own derivation."""
        return self.option.flag

    @property
    def negative(self) -> str:
        """``--no-x`` for a boolean, empty for every other kind."""
        return f"--no-{self.spelling[2:]}" if self.option.kind == "boolean" else ""

    @property
    def repeatable(self) -> bool:
        """Whether each use of the flag appends one entry."""
        return self.option.kind in ("paths", "strings")

    @property
    def help(self) -> str:
        """What the flag does, and which key and variable it is."""
        spellings = self.option.name
        if self.option.env_var:
            spellings += f", {self.option.env_var}"
        return f"{self.option.help} [{spellings}]"


def option_flags(options: Sequence[api.Option] = api.OPTIONS) -> tuple[OptionFlag, ...]:
    """Every option of *options* whose argument channel is open, as flags."""
    return tuple(
        OptionFlag(option=option, dest=option.name.replace(".", "_"))
        for option in options
        if option.arguments
    )


def _area(area: str) -> tuple[OptionFlag, ...]:
    return tuple(flag for flag in option_flags() if flag.option.area == area)


def build_option_flags() -> tuple[OptionFlag, ...]:
    """The flags of the ``build`` area — this machine's build section.

    Offered by the four commands that resolve it: ``device build``,
    ``context create``, ``environment provision`` and ``host check``.
    """
    return _area("build")


def signing_option_flags() -> tuple[OptionFlag, ...]:
    """``--signing-key`` and ``--signing-imgtool``."""
    return _area("signing")


def project_option_flags() -> tuple[OptionFlag, ...]:
    """``--project-dir`` — the bootstrap option every command offers."""
    return _area("project")


def add_option_flags(
    parser: argparse.ArgumentParser,
    flags: Sequence[OptionFlag],
    *,
    group: str | None = None,
) -> None:
    """Add *flags* to *parser*, each in the one spelling it derives.

    *group* names an argument group the flags are listed under in the
    help, so a command's own flags stay on top and the option flags read
    as the block they are.
    """
    target: argparse._ActionsContainer = (
        parser if group is None else parser.add_argument_group(group)
    )
    for flag in flags:
        if flag.option.kind == "boolean":
            target.add_argument(
                flag.spelling, dest=flag.dest, action="store_true", default=None, help=flag.help
            )
            target.add_argument(
                flag.negative,
                dest=flag.dest,
                action="store_false",
                help=_("{option} turned off").format(option=flag.option.name),
            )
            continue
        target.add_argument(
            flag.spelling,
            dest=flag.dest,
            action="append" if flag.repeatable else "store",
            default=None,
            metavar=_METAVARS[flag.option.kind],
            choices=list(flag.option.choices) or None,
            help=flag.help,
        )


def arguments(
    namespace: argparse.Namespace,
    *,
    env: Mapping[str, str],
    flags: Sequence[OptionFlag] | None = None,
) -> tuple[api.Argument, ...]:
    """The option values this invocation actually carried, parsed.

    Each answer names the spelling it arrived in, so a refusal from the
    configuration layer quotes the flag a person typed rather than a key
    they never wrote. Raises :class:`~mcuhome.cli.errors.UsageError` for
    a value the declaration does not take.
    """
    answered: list[api.Argument] = []
    for flag in option_flags() if flags is None else flags:
        raw = getattr(namespace, flag.dest, None)
        if raw is None or flag.option.bootstrap:
            # The bootstrap option decides where the project layer is and
            # is consumed before the merge runs; handing it to the
            # configuration layer is what that layer refuses.
            continue
        answered.append(
            api.Argument(
                name=flag.option.name,
                value=_parse(flag, raw, env=env),
                flag=flag.negative if raw is False and flag.negative else flag.spelling,
            )
        )
    return tuple(answered)


def problems(
    namespace: argparse.Namespace,
    *,
    env: Mapping[str, str],
    flags: Sequence[OptionFlag] | None = None,
) -> list[UsageError]:
    """Every option value this invocation carried that does not parse.

    The validate phase of every command: a value a **flag** carries is
    checked before the act starts, whether or not the command would ever
    have read it, so ``--build-memory 3x`` is exit 2 wherever it is
    written. All of them are answered at once, because a person who
    fixes three things in one pass is happier than one who runs the
    command three times.
    """
    found: list[UsageError] = []
    for flag in option_flags() if flags is None else flags:
        raw = getattr(namespace, flag.dest, None)
        if raw is None:
            continue
        try:
            _parse(flag, raw, env=env)
        except UsageError as problem:
            found.append(problem)
    return found


def _parse(flag: OptionFlag, raw: object, *, env: Mapping[str, str]) -> object:
    """*raw* in the option's own type, or a refusal naming the flag."""
    option = flag.option
    if option.kind == "boolean":
        return bool(raw)
    if option.kind == "paths":
        return tuple(api.expand_user_path(item, env=env) for item in _items(raw))
    if option.kind == "strings":
        return tuple(str(item) for item in _items(raw))
    text = str(raw)
    if option.kind == "path":
        return api.expand_user_path(text, env=env)
    if option.kind == "integer":
        return _whole_number(flag, text)
    if option.kind == "number":
        return _fraction(flag, text)
    if option.name == "build.memory":
        # The one string option whose shape the workbench can check
        # before the run: a memory limit that was misread would either
        # strangle every build or bound nothing at all. Its own parser
        # says so, in its own words, and the flag makes it exit 2.
        try:
            api.parse_memory(text, key=flag.spelling)
        except api.ConfigError as refusal:
            raise UsageError(refusal.message, hint=refusal.hint) from None
    return text


def _items(raw: object) -> list[str]:
    return [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]


def _whole_number(flag: OptionFlag, text: str) -> int:
    try:
        number = int(text)
    except ValueError:
        raise UsageError(
            _("{flag} takes a whole number, not {value!r}.").format(flag=flag.spelling, value=text),
            hint=flag.option.help or None,
        ) from None
    minimum = flag.option.minimum
    if minimum is not None and number < minimum:
        raise UsageError(
            _("{flag} takes at least {minimum}, not {value}.").format(
                flag=flag.spelling, minimum=minimum, value=number
            ),
            hint=flag.option.help or None,
        )
    return number


def _fraction(flag: OptionFlag, text: str) -> float:
    try:
        number = float(text)
    except ValueError:
        raise UsageError(
            _("{flag} takes a number, not {value!r}.").format(flag=flag.spelling, value=text),
            hint=flag.option.help or None,
        ) from None
    minimum = flag.option.minimum
    if minimum is not None and number <= minimum:
        raise UsageError(
            _("{flag} takes a number greater than {minimum}, not {value:g}.").format(
                flag=flag.spelling, minimum=minimum, value=number
            ),
            hint=flag.option.help or None,
        )
    return number
