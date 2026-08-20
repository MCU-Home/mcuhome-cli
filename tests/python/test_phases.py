# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The interact → validate → execute contract (cli ADR 0004 §3/§4)."""

from __future__ import annotations

import json

from mcuhome.model.errors import BuildError

from mcuhome.cli import phases
from mcuhome.cli.output import JSON, Output


def test_the_exit_codes_are_exactly_three() -> None:
    """0, 1, 2 — nothing else, deliberately (ADR 0004 §4)."""
    assert (phases.EXIT_OK, phases.EXIT_FAILURE, phases.EXIT_USAGE) == (0, 1, 2)


def test_the_phases_run_in_order_and_execute_answers() -> None:
    ran: list[str] = []
    code = phases.run(
        output=Output(interactive=True),
        interact=lambda: ran.append("interact"),
        validate=lambda: ran.append("validate") or [],
        execute=lambda: ran.append("execute") or 0,
    )
    assert ran == ["interact", "validate", "execute"]
    assert code == phases.EXIT_OK


def test_interact_is_skipped_when_not_interactive() -> None:
    ran: list[str] = []
    phases.run(
        output=Output(interactive=False),
        interact=lambda: ran.append("interact"),
        execute=lambda: ran.append("execute") or 0,
    )
    assert ran == ["execute"]


def test_a_validate_problem_is_exit_2_and_execute_never_runs(capsys) -> None:
    """The boundary is sharper than "before anything is written": the
    action does not start at all (PO wording, ADR 0004 §3)."""
    ran: list[str] = []
    code = phases.run(
        output=Output(),
        validate=lambda: [BuildError("input missing", hint="pass --the-flag")],
        execute=lambda: ran.append("execute") or 0,
    )
    assert code == phases.EXIT_USAGE
    assert ran == []
    err = capsys.readouterr().err
    assert "input missing" in err
    assert "--the-flag" in err


def test_a_validate_refusal_is_still_a_document_for_a_machine(capsys) -> None:
    code = phases.run(
        output=Output(mode=JSON),
        validate=lambda: [BuildError("input missing")],
        execute=lambda: 0,
    )
    assert code == phases.EXIT_USAGE
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["errors"][0]["message"] == "input missing"


def test_execute_alone_is_a_complete_command() -> None:
    """The contract, not a class layout: empty phases are simply empty."""
    assert phases.run(output=Output(), execute=lambda: 1) == phases.EXIT_FAILURE
