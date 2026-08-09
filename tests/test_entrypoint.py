# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests: the console entry point loads and every command has help.

The build server feature-probes ``mcuhome build --help`` (see
mcuhome_buildserver/builder.py in the dashboard repository), so the help
text of the main commands is machine-facing surface, not just courtesy.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from mcuhome_cli.cli import build_parser, main

COMMANDS = [
    "new",
    "validate",
    "build",
    "sign",
    "public-key",
    "schema",
    "init-pairing",
    "clean",
]


def test_the_console_script_points_at_this_package() -> None:
    """`pip install mcuhome` is what puts `mcuhome` on PATH (ADR 0020 §2)."""
    found = [ep for ep in entry_points(group="console_scripts") if ep.name == "mcuhome"]
    assert found, "no console script called `mcuhome` is installed"
    entry = found[0]
    assert entry.value == "mcuhome_cli.cli:main"
    assert entry.load() is main


def test_top_level_help_works(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    assert "usage: mcuhome" in capsys.readouterr().out


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help(capsys, command: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([command, "--help"])
    assert caught.value.code == 0
    assert f"usage: mcuhome {command}" in capsys.readouterr().out


def test_the_parser_knows_exactly_the_documented_commands() -> None:
    """The surface of builder-pipeline.md §8, no silent additions."""
    parser = build_parser()
    # argparse has no public accessor for its subparsers action.
    subparsers = next(action for action in parser._actions if action.dest == "command")
    assert sorted(subparsers.choices) == sorted(COMMANDS)


def test_build_help_advertises_the_probed_flags(capsys) -> None:
    """The flags the build server's feature probe looks for."""
    with pytest.raises(SystemExit):
        main(["build", "--help"])
    out = capsys.readouterr().out
    for flag in ("--model", "--no-sign", "--public-key", "--json"):
        assert flag in out
