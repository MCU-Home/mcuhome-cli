# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The order one run happens in: help, the mode, refusals, the parse.

These are the rules that hold whatever command was typed — the ones a
person meets before any act starts.
"""

from __future__ import annotations

import json
from importlib.metadata import entry_points

import pytest

from mcuhome.cli import phases
from mcuhome.cli.main import main
from mcuhome.cli.parser import build_parser, read_presentation


class TestTheEntryPoint:
    def test_the_console_script_is_this_function(self) -> None:
        scripts = entry_points(group="console_scripts")
        assert scripts["mcuhome"].value == "mcuhome.cli.main:main"

    def test_no_command_prints_help_and_succeeds(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == phases.EXIT_OK
        assert "usage: mcuhome" in capsys.readouterr().out

    def test_an_area_without_an_act_prints_that_area(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device"]) == phases.EXIT_OK
        printed = capsys.readouterr().out
        assert "usage: mcuhome device" in printed
        assert "list-boards" in printed

    def test_the_version_flag_prints_what_the_command_answers(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--version"]) == phases.EXIT_OK
        text = capsys.readouterr().out
        assert main(["version"]) == phases.EXIT_OK
        assert capsys.readouterr().out == text


class TestHelp:
    """Help wins wherever it stands."""

    @pytest.mark.parametrize(
        "tokens",
        [
            ["device", "new", "--help"],
            ["device", "new", "-h"],
            ["-h", "device", "new"],
            ["device", "new", "--board", "-h"],
            ["device", "-h", "new"],
        ],
    )
    def test_help_anywhere_answers_the_command_it_names(
        self, tokens: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(tokens) == phases.EXIT_OK
        assert "usage: mcuhome device new" in capsys.readouterr().out

    def test_after_the_boundary_help_is_a_value(self) -> None:
        # `mcuhome config get -- --help` asks about an option named
        # `--help`, which is refused as an option nobody declared.
        assert main(["config", "get", "--", "--help"]) == phases.EXIT_FAILURE

    def test_every_command_has_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        def walk(parser: argparse.ArgumentParser, words: list[str]) -> None:
            sub = next(
                (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
            )
            if sub is None:
                assert main([*words, "--help"]) == phases.EXIT_OK
                assert capsys.readouterr().out.startswith("usage: mcuhome")
                return
            for name, child in sub.choices.items():
                walk(child, [*words, name])

        walk(build_parser(), [])


class TestTheModeIsReadFirst:
    """``-o`` is read before any other argument is refused."""

    @pytest.mark.parametrize(
        "tokens",
        [
            ["device", "build", "--nope", "-o", "json"],
            ["-o", "json", "device", "build", "--nope"],
            ["device", "build", "--nope", "--output=json"],
            ["device", "build", "--nope", "-ojson"],
            ["config", "set", "-o", "json"],
        ],
    )
    def test_a_wrong_invocation_still_answers_a_document(
        self, tokens: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(tokens) == phases.EXIT_USAGE
        document = json.loads(capsys.readouterr().out)
        assert document["ok"] is False
        assert document["errors"][0]["kind"] == "UsageError"

    def test_a_wrong_invocation_ends_the_stream_with_one_result(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "build", "--nope", "-o", "json-stream"]) == phases.EXIT_USAGE
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert [message["verb"] for message in messages] == ["error", "result"]

    def test_a_mode_nobody_offers_is_refused_for_a_person(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["version", "-o", "yaml"]) == phases.EXIT_USAGE
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "-o takes one of" in captured.err

    def test_the_presentation_is_read_off_the_tokens(self) -> None:
        assert read_presentation(["-o", "json"]).mode == "json"
        assert read_presentation(["--output=json-stream"]).mode == "json-stream"
        assert read_presentation(["--color", "never"]).color == "never"
        assert read_presentation(["--no-interactive"]).interactive is False
        assert read_presentation(["--interactive"]).interactive is True
        # After the boundary nothing is a flag any more.
        assert read_presentation(["--", "-o", "json"]).mode == "human"


class TestExitCodes:
    """Three, and deliberately no more."""

    def test_a_command_that_did_what_it_was_asked_is_zero(self) -> None:
        assert main(["version"]) == 0

    def test_a_refusal_that_ran_is_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["config", "get", "nope.key", "-o", "json"]) == 1
        assert json.loads(capsys.readouterr().out)["errors"][0]["kind"] == "ConfigError"

    def test_a_wrong_invocation_is_two(self) -> None:
        assert main(["config", "get"]) == 2

    def test_a_value_a_flag_carries_is_parsed_before_the_act(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The same value out of a file or the environment is refused
        # while the command runs and is 1; from a flag it is 2.
        assert main(["host", "check", "--build-memory", "3x", "-o", "json"]) == 2
        document = json.loads(capsys.readouterr().out)
        assert document["errors"][0]["kind"] == "UsageError"
        assert "--build-memory" in document["errors"][0]["hint"]

    def test_a_capability_that_is_not_there_yet_is_one(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "flash", "kitchen", "-o", "json"]) == 1
        document = json.loads(capsys.readouterr().out)
        assert document["errors"][0]["kind"] == "CapabilityUnavailable"
