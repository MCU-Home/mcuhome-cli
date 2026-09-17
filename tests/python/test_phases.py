# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""interact → validate → execute, and the three exit codes.

The boundary the contract draws is sharper than "before anything is
written": a problem found in validate means the act does not start at
all.
"""

from __future__ import annotations

import json

import pytest
from mcuhome.workbench import api

from mcuhome.cli import phases
from mcuhome.cli.output import JSON, Output


class TestThePhases:
    def test_the_phases_run_in_order(self) -> None:
        happened: list[str] = []
        phases.run(
            output=Output(interactive=True),
            interact=lambda: happened.append("interact"),
            validate=lambda: (happened.append("validate"), [])[1],
            execute=lambda: (happened.append("execute"), phases.EXIT_OK)[1],
        )
        assert happened == ["interact", "validate", "execute"]

    def test_a_non_interactive_run_asks_nothing(self) -> None:
        happened: list[str] = []
        phases.run(
            output=Output(interactive=False),
            interact=lambda: happened.append("interact"),
            execute=lambda: phases.EXIT_OK,
        )
        assert happened == []

    def test_a_problem_in_validate_stops_before_the_act(self) -> None:
        happened: list[str] = []
        code = phases.run(
            output=Output(),
            validate=lambda: [api.ConfigError("the invocation was wrong")],
            execute=lambda: (happened.append("execute"), phases.EXIT_OK)[1],
        )
        assert code == phases.EXIT_USAGE
        assert happened == []

    def test_a_problem_in_validate_is_the_refusal_document(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        phases.run(
            output=Output(mode=JSON),
            validate=lambda: [api.ConfigError("the invocation was wrong")],
            execute=lambda: phases.EXIT_OK,
        )
        document = json.loads(capsys.readouterr().out)
        assert document["ok"] is False
        assert document["errors"][0]["message"] == "the invocation was wrong"

    def test_a_command_with_no_questions_and_no_rules_still_runs(self) -> None:
        assert phases.run(output=Output(), execute=lambda: phases.EXIT_FAILURE) == 1

    def test_the_whole_exit_vocabulary_is_three_values(self) -> None:
        assert (phases.EXIT_OK, phases.EXIT_FAILURE, phases.EXIT_USAGE) == (0, 1, 2)
