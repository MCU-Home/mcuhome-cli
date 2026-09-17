# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""One run of the command line, with everything a command is given.

A command gets the parsed arguments, the output contract, the process
environment and the working directory — and takes nothing out of the
process itself. The workbench never reads either of the last two
(``resolve_settings``, ``resolve_project`` and everything below them are
handed what this module holds), and a command line is the one program
whose environment *is* the user's answer, so reading it happens here and
nowhere deeper.

The two bootstrap answers every command needs are here as well, because
they are the same two steps everywhere: which project this invocation is
about (:meth:`Invocation.project` for a command that needs one,
:meth:`Invocation.find_project` for one that works outside a project),
and what the configuration ladder resolved to
(:meth:`Invocation.settings`), with the findings the resolution reports
travelling to the output contract as they happen.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from mcuhome.workbench import api

from mcuhome.cli import optionflags
from mcuhome.cli.output import Output

__all__ = ["Invocation"]


@dataclass(frozen=True)
class Invocation:
    """What one command was asked, and what it may answer through."""

    #: The command as typed without its flags — ``"config print"``,
    #: ``"version"`` — which is what the stream's `start` message names.
    task: str
    args: argparse.Namespace
    output: Output
    env: Mapping[str, str]
    cwd: Path

    def start(self, **data: object) -> None:
        """The `start` message of this command, with its own facts."""
        self.output.start(self.task, **data)

    def flag(self, name: str, default: object = None) -> object:
        """One of the command's own flags, or *default* where it has none."""
        return getattr(self.args, name, default)

    # -- the bootstrap ------------------------------------------------

    @property
    def project_dir(self) -> str | None:
        """What ``--project-dir`` carried, unparsed — the ladder expands it."""
        stated = getattr(self.args, "project_dir", None)
        return None if stated is None else str(stated)

    def project(self, *, require_version: bool = True) -> api.Project:
        """The project this invocation is about; refuses where there is none."""
        return api.resolve_project(
            self.project_dir, env=self.env, cwd=self.cwd, require_version=require_version
        )

    def find_project(self, *, require_version: bool = True) -> api.Project | None:
        """The project this invocation is about, or ``None`` outside one.

        The same ladder as :meth:`project` — a stated directory that is
        not a project is still a refusal, because a person who named one
        meant it — but an upward search that finds nothing answers
        ``None``: the configuration commands work outside a project, and
        so does the one that creates it.
        """
        if self.project_dir is not None:
            return self.project(require_version=require_version)
        found = api.find_project_root(self.cwd)
        if found is None:
            return None
        return api.read_project(found, require_version=require_version)

    # -- the configuration ladder -------------------------------------

    def option_arguments(
        self, flags: Sequence[optionflags.OptionFlag] | None = None
    ) -> tuple[api.Argument, ...]:
        """The option values this invocation carried, parsed and spelled."""
        return optionflags.arguments(self.args, env=self.env, flags=flags)

    def settings(
        self,
        *,
        project: api.Project | None,
        flags: Sequence[optionflags.OptionFlag] | None = None,
    ) -> api.Settings:
        """The resolved configuration, its findings reported as they happen.

        The command line states no defaults of its own: it passes no
        program layer, so no value it resolves ever carries that origin —
        every value it hands on is one a person wrote.
        """
        return api.resolve_settings(
            project=project,
            env=self.env,
            args=self.option_arguments(flags),
            on_warning=lambda finding: self.output.finding(finding.to_dict()),
        )
