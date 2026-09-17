# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome secret`` — the values a device configuration reads.

A project keeps its secrets in ``secrets/``, one file per kind and name:
the shared ``main``, one ``device`` file per device, one ``builder`` file
per build server credential, and ``signing``, which is the reference to
the firmware key. These six commands are the only supported way to look
at them.

**No document here carries a value except one.** Listing a scope answers
the mask the workbench put there — a constant that is not a redaction of
the value, not its length, not its first character — and the one command
that answers a value is ``reveal``, which is a command of its own so
that the ask cannot be made by accident.

Every command takes the scope the same way (``--kind``, ``--name``) and
every document names it the same way: under one ``scope`` key, which is
the scope document the workbench answers, never two loose fields. The
three that write answer one shape for all of them —
``{scope, key, changed}`` — so a client renders one document instead of
three.
"""

from __future__ import annotations

from mcuhome.workbench import api

from mcuhome.cli import stdinvalue
from mcuhome.cli.errors import UsageError
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, DIM, GREEN, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_OK

__all__ = [
    "secret_delete",
    "secret_list",
    "secret_list_scopes",
    "secret_reveal",
    "secret_set",
    "secret_unset",
    "validate_set",
]


def secret_list_scopes(invocation: Invocation) -> int:
    """``mcuhome secret list-scopes``: every scope this project could have.

    ``ok`` is the verdict of the **listing**: a scope whose file is not
    there is a row with ``exists`` false, which is what a client offers
    "add a secret for this device" from.
    """
    output = invocation.output
    project = invocation.project()
    invocation.start()
    scopes = api.find_secret_scopes(project)
    _print_scopes(scopes, output=output)
    output.result({"ok": True, "scopes": [scope.to_dict() for scope in scopes]})
    return EXIT_OK


def secret_list(invocation: Invocation) -> int:
    """``mcuhome secret list``: the keys of one scope, masked."""
    output = invocation.output
    project = invocation.project()
    kind, name = _scope(invocation)
    invocation.start()
    file = api.read_secrets(project, kind=kind, name=name)
    _print_keys(file, output=output)
    output.result({"ok": True, **file.to_dict()})
    return EXIT_OK


def secret_reveal(invocation: Invocation) -> int:
    """``mcuhome secret reveal --key <key>``: the one command that answers a value.

    The scope is read before the value, because the document names the
    scope the way every other command here does — the workbench's own
    scope document — and ``reveal_secret`` answers the bare value and
    nothing around it. What the ``signing`` scope holds is a reference to
    a file, and reading it is the workbench's refusal: key material is
    neither printed nor typed in.
    """
    output = invocation.output
    project = invocation.project()
    kind, name = _scope(invocation)
    key = str(invocation.flag("key"))
    invocation.start()
    scope = api.read_secrets(project, kind=kind, name=name).scope
    value = api.reveal_secret(project, kind=kind, name=name, key=key)
    # The value alone, with nothing around it, so a person can read it
    # into a variable: `password="$(mcuhome secret reveal --key …)"`.
    output.human(value)
    output.result({"ok": True, "scope": scope.to_dict(), "key": key, "value": value})
    return EXIT_OK


def validate_set(invocation: Invocation) -> list[api.MCUHomeError]:
    """``--value -`` is settled here, like every other value a flag carries.

    The read happens in the validate phase and the answer is kept on the
    arguments, so the act starts with a value rather than with a channel
    it still has to open — and a run that would sit waiting for
    something nobody is going to type is refused as the wrong
    invocation it is.
    """
    try:
        invocation.args.value = stdinvalue.resolve_value(
            str(invocation.flag("value")), flag="--value"
        )
    except UsageError as problem:
        return [problem]
    return []


def secret_set(invocation: Invocation) -> int:
    """``mcuhome secret set --key <key> --value <value>``: write one key.

    Comments, order, blank lines and every other entry survive the edit —
    the workbench writes the file through its round-trip editor — and the
    first secret of a scope creates the file owner-only.
    """
    output = invocation.output
    project = invocation.project()
    kind, name = _scope(invocation)
    key = str(invocation.flag("key"))
    invocation.start()
    # Already read: the validate phase settles what a flag carries,
    # standard input included.
    change = api.set_secret(
        project, kind=kind, name=name, key=key, value=str(invocation.flag("value"))
    )
    _print_change(
        _("Wrote {key} to {file}.").format(key=key, file=output.path(change.scope.file)),
        _("{key} in {file} already held that value; nothing was written.").format(
            key=key, file=output.path(change.scope.file)
        ),
        change,
        output=output,
    )
    output.result({"ok": True, **change.to_dict()})
    return EXIT_OK


def secret_unset(invocation: Invocation) -> int:
    """``mcuhome secret unset --key <key>``: remove one key.

    Removing the last entry leaves an empty file: the file is the user's,
    and this command was asked to remove one secret.
    """
    output = invocation.output
    project = invocation.project()
    kind, name = _scope(invocation)
    key = str(invocation.flag("key"))
    invocation.start()
    change = api.unset_secret(project, kind=kind, name=name, key=key)
    _print_change(
        _("Removed {key} from {file}.").format(key=key, file=output.path(change.scope.file)),
        _("{file} holds no {key}; nothing was removed.").format(
            key=key, file=output.path(change.scope.file)
        ),
        change,
        output=output,
    )
    output.result({"ok": True, **change.to_dict()})
    return EXIT_OK


def secret_delete(invocation: Invocation) -> int:
    """``mcuhome secret delete --kind <kind> --name <name>``: remove a whole file.

    ``main`` and ``signing`` are the project's own and are refused by the
    workbench: they are emptied key by key rather than removed under a
    user's feet.
    """
    output = invocation.output
    project = invocation.project()
    kind, name = _scope(invocation)
    invocation.start()
    change = api.delete_secret_file(project, kind=kind, name=name)
    _print_change(
        _("Removed {file}.").format(file=output.path(change.scope.file)),
        _("There is no {file}; nothing was removed.").format(file=output.path(change.scope.file)),
        change,
        output=output,
    )
    output.result({"ok": True, **change.to_dict()})
    return EXIT_OK


# -- what these commands share ------------------------------------------


def _scope(invocation: Invocation) -> tuple[str, str]:
    """The kind and the name this invocation asked about.

    A flag that was not used is the empty name, which is what the two
    kinds that belong to the project itself take — and what the other
    two are refused over, in the workbench's words.
    """
    name = invocation.flag("name")
    return str(invocation.flag("kind")), "" if name is None else str(name)


# -- the human renderings -----------------------------------------------


def _print_scopes(scopes: tuple[api.SecretScope, ...], *, output: Output) -> None:
    """Every scope with its file, and whether that file is there."""
    if output.machine:
        return
    rows: list[list[str | Cell]] = [[_("kind"), _("name"), _("file"), _("exists")]]
    for scope in scopes:
        rows.append(
            [
                scope.kind,
                scope.name or Cell("—", (DIM,)),
                str(scope.file),
                # The path is printed whether or not the file is there:
                # a scope is a place a secret can be, and the place is
                # what somebody who wants to put one there needs.
                _("yes") if scope.exists else Cell(_("no"), (DIM,)),
            ]
        )
    output.human(format_table(rows, header=True, output=output))


def _print_keys(file: api.SecretFile, *, output: Output) -> None:
    """The keys of one scope, masked, and which devices read them."""
    if output.machine:
        return
    if not file.scope.exists:
        # What creates the file is a different act per kind: everywhere
        # else the first secret written creates it, and for the signing
        # key nothing here writes at all.
        next_act = (
            _("    mcuhome signing create-key")
            if file.scope.kind == "signing"
            else _("    mcuhome secret set --key <key> --value <value>")
        )
        output.human(
            _("There is no {file} yet.").format(file=output.path(file.scope.file))
            + "\n"
            + output.muted(next_act)
        )
        return
    if not file.keys:
        output.human(_("{file} holds no secrets.").format(file=output.path(file.scope.file)))
        return
    rows: list[list[str | Cell]] = [[_("key"), _("value"), _("read by")]]
    for entry in file.keys:
        rows.append(
            [
                entry.key,
                Cell(entry.masked, (DIM,)),
                ", ".join(entry.used_by) if entry.used_by else Cell("—", (DIM,)),
            ]
        )
    output.human(format_table(rows, header=True, output=output))


def _print_change(
    changed: str, unchanged: str, change: api.SecretChange, *, output: Output
) -> None:
    """What one write did, in the one sentence that says it.

    The value is in no sentence here, whichever way the write went: the
    one command that prints a secret is ``reveal``.
    """
    if change.changed:
        output.human(f"{output.style('✓', GREEN, BOLD)} {changed}")
        return
    output.human(unchanged)
