# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome config`` — reading and writing the option registry.

``print`` and ``get`` answer the effective value with the layer it came
from; ``set`` and ``unset`` edit exactly one file through the
workbench's round-trip editor, so comments, order and ``!file``
references survive.

The name these commands take is an option key — ``build.mode``,
``signing.key`` — or one entry of a map option
(``builder.<name>.target``, ``registry.<base-domain>.untrusted``,
``registry.<base-domain>.mirrors.<source>``, …). Map entries are how
builders and package registries are configured from the command line;
there is no ``builder`` and no ``registry`` command group, because a
builder is configuration and a command group would be a second place to
state it.

A key nobody declared is refused by the workbench with the words a
configuration file would be refused with, and a value the declaration
does not take is refused where it is written rather than at the next
build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcuhome.workbench import api

from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import DIM, Cell, format_table
from mcuhome.cli.phases import EXIT_OK

__all__ = ["config_get", "config_print", "config_set", "config_unset"]


def config_print(invocation: Invocation) -> int:
    """``mcuhome config print``: every option, its value, and where it came from."""
    invocation.start()
    project = invocation.find_project()
    settings = invocation.settings(project=project)
    config = settings.to_dict()
    _print_settings(invocation, config)
    invocation.output.result({"ok": True, "config": config})
    return EXIT_OK


def config_get(invocation: Invocation) -> int:
    """``mcuhome config get <name>``: one option's effective value."""
    name = str(invocation.args.name)
    invocation.start()
    # The declaration first: a key nobody declared is refused with the
    # same sentence a configuration file writing it would be refused
    # with, before any layer is read.
    api.option(name)
    project = invocation.find_project()
    entry = invocation.settings(project=project).setting(name).to_dict()
    output = invocation.output
    output.human(_text(entry["value"]))
    output.human(
        output.muted(
            _("{origin} ({source})").format(origin=entry["origin"], source=entry["source"])
            if entry["source"]
            else _("{origin}").format(origin=entry["origin"])
        )
    )
    output.result({"ok": True, "name": name, **entry})
    return EXIT_OK


def config_set(invocation: Invocation) -> int:
    """``mcuhome config set <name> <value>``: write one option into one file."""
    name = str(invocation.args.name)
    scope = str(invocation.args.scope)
    invocation.start()
    file = _config_file(invocation, scope)
    written = api.set_config_value(file, name, str(invocation.args.value), env=invocation.env)
    invocation.output.human(
        _("{name} = {value} in {file}").format(
            name=name, value=_text(written), file=invocation.output.path(file)
        )
    )
    invocation.output.result(
        {
            "ok": True,
            "name": name,
            "value": _document_value(written),
            "scope": scope,
            "file": str(file),
        }
    )
    return EXIT_OK


def config_unset(invocation: Invocation) -> int:
    """``mcuhome config unset <name>``: remove one option from one file."""
    name = str(invocation.args.name)
    scope = str(invocation.args.scope)
    invocation.start()
    file = _config_file(invocation, scope)
    removed = api.unset_config_value(file, name)
    invocation.output.human(
        _("{name} removed from {file}").format(name=name, file=invocation.output.path(file))
        if removed
        else _("{name} was not set in {file}").format(name=name, file=invocation.output.path(file))
    )
    invocation.output.result(
        {"ok": True, "name": name, "removed": removed, "scope": scope, "file": str(file)}
    )
    return EXIT_OK


def _config_file(invocation: Invocation, scope: str) -> Path:
    """The file *scope* is edited in, refused where that scope has none."""
    return api.resolve_config_file(scope, project=invocation.find_project(), env=invocation.env)


def _print_settings(invocation: Invocation, config: dict[str, Any]) -> None:
    """The whole registry as a table: what, to what, from where."""
    output = invocation.output
    rows: list[list[str | Cell]] = [[_("option"), _("value"), _("origin"), _("source")]]
    for name, entry in config.items():
        origin = str(entry["origin"])
        rows.append(
            [
                name,
                _text(entry["value"]),
                # A value nobody set is what the registry declares, and
                # the rendering says so quietly rather than loudly.
                Cell(origin, (DIM,)) if origin == "default" else Cell(origin),
                str(entry["source"] or ""),
            ]
        )
    output.human(format_table(rows, header=True, output=output))


def _text(value: Any) -> str:
    """One value as a person reads it — a list on one line, empty as a dash."""
    if value is None or value == () or value == [] or value == {}:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        # A structured entry is named by what it is keyed on — the
        # builder's name, the registry's base domain — and the whole
        # document of it is what the machine modes carry.
        named = value.get("name") or value.get("base_domain")
        return str(named) if named else ", ".join(str(key) for key in value)
    if isinstance(value, list | tuple):
        return ", ".join(_text(item) for item in value)
    return str(value)


def _document_value(value: Any) -> Any:
    """One parsed value, JSON-ready.

    ``set_config_value`` answers what the declaration parsed — a path is
    a ``Path``, a list a tuple — and a document carries the same value in
    the shape JSON has for it. Nothing is renamed and nothing is added:
    the command names what it was asked about under one key.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list | tuple):
        return [_document_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _document_value(item) for key, item in value.items()}
    return value
