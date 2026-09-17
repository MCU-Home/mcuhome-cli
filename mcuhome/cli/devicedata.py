# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome device print-schema``, ``list-boards`` and ``list-supported``.

Two of these are **data commands**: the device-file JSON Schema and the
whole hardware and Matter table are documents MCUHome *publishes* for a
client to read, not the result of an act. Neither carries ``ok`` — a
schema with an ``ok`` key is not a schema — so the exit code is the
whole verdict a machine mode gets, and ``human`` prints the document
itself rather than a rendering of it: a schema has no human rendering,
and the catalog is summarised in counts because the part of it a person
actually reads is the board table ``device list-boards`` prints.

``device list-boards`` is the third command and is not one of the two:
it answers **part of** the hardware and Matter table, a projection that
takes the two board lists whole and under the names that table gives
them. A projection is the command's own document, so it carries ``ok``
like every other one — the api call it wraps just happens to be the same
one ``list-supported`` prints entirely.
"""

from __future__ import annotations

from typing import Any

from mcuhome.workbench import api

from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import Cell, Output, format_table
from mcuhome.cli.phases import EXIT_OK

__all__ = ["device_list_boards", "device_list_supported", "device_print_schema"]

#: The top-level lists ``device list-supported`` summarises in counts, in
#: the order the document itself declares them.
_COUNTED = (
    "boards",
    "planned_boards",
    "drivers",
    "planned_drivers",
    "clusters",
    "planned_clusters",
    "device_types",
    "planned_device_types",
    "attribute_sizes",
)


def device_print_schema(invocation: Invocation) -> int:
    """``mcuhome device print-schema``: the JSON Schema of a device file.

    A data command: the machine document is the schema itself, alone,
    with no ``ok``, and ``human`` prints the same text — a schema has no
    human rendering of its own.
    """
    invocation.start()
    schema = api.device_schema()
    # The text and nothing around it, so `> device.schema.json` is a
    # schema file: the rendering already ends in one newline.
    invocation.output.human(api.to_json(schema).rstrip("\n"))
    invocation.output.result(schema)
    return EXIT_OK


def device_list_boards(invocation: Invocation) -> int:
    """``mcuhome device list-boards``: what MCUHome can build for.

    A projection of the hardware and Matter table: the two board lists,
    taken whole and under the names that table gives them, never
    rebuilt, renamed or filtered.
    """
    output = invocation.output
    invocation.start()
    registry = api.device_registry()
    boards = registry["boards"]
    planned_boards = registry["planned_boards"]
    _print_boards(boards, planned_boards, output=output)
    output.result({"ok": True, "boards": boards, "planned_boards": planned_boards})
    return EXIT_OK


def device_list_supported(invocation: Invocation) -> int:
    """``mcuhome device list-supported``: everything MCUHome knows about hardware and Matter.

    A data command, like ``print-schema``: the machine document is the
    whole hardware and Matter table, alone, with no ``ok``; ``human``
    renders the counts, because the part of this table a person actually
    reads is the board table ``device list-boards`` prints.
    """
    output = invocation.output
    invocation.start()
    registry = api.device_registry()
    _print_supported(registry, output=output)
    output.result(registry)
    return EXIT_OK


# -- the human renderings -------------------------------------------------


def _print_boards(
    boards: list[dict[str, Any]], planned_boards: list[dict[str, Any]], *, output: Output
) -> None:
    """The supported boards with their transports, and the planned ones with their reason."""
    if output.machine:
        return
    if not boards:
        output.human(_("No boards yet."))
    else:
        rows: list[list[str | Cell]] = [[_("board"), _("transports")]]
        for board in boards:
            rows.append([board["name"], ", ".join(board["transports"])])
        output.human(format_table(rows, header=True, output=output))
    if planned_boards:
        output.human()
        output.human(output.heading(_("Planned")))
        planned_rows: list[list[str | Cell]] = [[_("board"), _("reason")]]
        for planned in planned_boards:
            planned_rows.append([planned["name"], planned["reason"]])
        output.human(format_table(planned_rows, header=True, output=output))


def _print_supported(document: dict[str, Any], *, output: Output) -> None:
    """The counts per top-level list, and where the whole document is."""
    if output.machine:
        return
    rows: list[list[str | Cell]] = [[_("what"), _("count")]]
    for key in _COUNTED:
        rows.append([key, str(len(document[key]))])
    output.human(format_table(rows, header=True, align="lr", output=output))
    output.human()
    output.human(
        output.muted(_("mcuhome device list-supported -o json prints the whole document."))
    )
