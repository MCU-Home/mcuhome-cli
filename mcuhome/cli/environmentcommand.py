# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome environment provision`` — the compiler stack, ahead of a build.

A build environment is what a build runs in: a container image, or two
packages unpacked into a per-user store. This command puts one of those
packages into this machine's store before the build that needs it —
which is what a machine that has to build offline, and a machine being
set up, needs.

The area word means that stack and nothing else: it has no connection to
the process environment or the ``MCUHOME_*`` variables.

**It cannot be stopped cleanly.** The call takes no stop predicate,
because it is bounded by the package's own size rather than by a
caller's patience, and ``Ctrl-C`` ends the process. An interrupted run
leaves nothing a build can find: the store marker is written last.
"""

from __future__ import annotations

from pathlib import Path

from mcuhome.workbench import api

from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, DIM, GREEN, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_OK

__all__ = ["environment_provision"]


def environment_provision(invocation: Invocation) -> int:
    """``mcuhome environment provision <package>``: one package in the store.

    It reports no stages — what it has to say are the log lines the
    acquisition and the unpacking report, on stderr.
    """
    output = invocation.output
    project = invocation.find_project()
    settings = invocation.settings(project=project)
    options = api.resolve_build_options(settings)
    invocation.start()
    entry = api.provision_environment(
        _package(invocation),
        options=options,
        env=invocation.env,
        # With the project, a package no operator directory offers is
        # fetched from the configured registry and checked against the
        # trust anchor the project carries.
        project=project,
        registries=settings.value("registry"),
        on_line=output.log,
    )
    _print_entry(entry, output=output)
    output.result({"ok": True, **entry.to_dict()})
    return EXIT_OK


def _package(invocation: Invocation) -> Path | str:
    """What the positional named: a package file, or a package name.

    A file is handed over as the absolute path it is, so that what is
    read does not depend on which directory the workbench happens to be
    called from — the working directory is this command line's to
    resolve. Everything else travels as the text a person wrote, which
    is what the reference grammar for a name with a constraint and a
    hash is parsed from.
    """
    text = str(invocation.flag("package"))
    path = api.expand_user_path(text, env=invocation.env)
    if not path.is_absolute():
        path = invocation.cwd / path
    return path.resolve() if path.is_file() else text


def _print_entry(entry: api.StoreEntry, *, output: Output) -> None:
    """What is in the store now, and where."""
    output.human(
        f"{output.style('✓', GREEN, BOLD)} "
        + _("{name} {version} is in the store.").format(name=entry.name, version=entry.version)
    )
    rows: list[list[str | Cell]] = [
        [Cell(_("kind"), (DIM,)), entry.kind],
        [Cell(_("sha256"), (DIM,)), entry.sha256],
        [Cell(_("path"), (DIM,)), str(entry.path)],
    ]
    output.human(format_table(rows, indent="  ", output=output))
