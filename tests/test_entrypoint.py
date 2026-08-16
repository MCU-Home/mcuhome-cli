# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests: the console entry point loads and every command has help.

A machine driving this surface feature-probes ``mcuhome device build
--help`` (cli ADR 0002), so the help text of the main commands is
machine-facing surface, not just courtesy.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from mcuhome_cli.cli import build_parser, main

#: The decided vocabulary (cli ADR 0003 §2): project- and
#: environment-scoped commands top-level, device-scoped ones under the
#: ``device`` noun, configuration verbs under ``config``.
TOP_LEVEL = [
    "project",
    "config",
    "device",
    "schema",
    "public-key",
    "doctor",
    "clean",
]
DEVICE_COMMANDS = [
    "new",
    "validate",
    "build",
    "sign-firmware",
    "flash",
    "first-time-setup",
    "matter-pairing",
    "list",
    "boards",
]
CONFIG_COMMANDS = ["print", "get", "set", "unset"]
PROJECT_COMMANDS = ["init", "info", "upgrade"]


def test_the_console_script_points_at_this_package() -> None:
    """`pip install mcuhome` is what puts `mcuhome` on PATH (ADR 0020 §2)."""
    found = [ep for ep in entry_points(group="console_scripts") if ep.name == "mcuhome"]
    assert found, "no console script called `mcuhome` is installed"
    entry = found[0]
    assert entry.value == "mcuhome_cli.cli:main"
    assert entry.load() is main


def test_top_level_help_works(capsys) -> None:
    # Help is the pre-parse scan's answer (PO 2026-08-15): a plain
    # return, not argparse's SystemExit.
    assert main(["--help"]) == 0
    assert "usage: mcuhome" in capsys.readouterr().out


@pytest.mark.parametrize("command", TOP_LEVEL)
def test_every_top_level_command_has_help(capsys, command: str) -> None:
    assert main([command, "--help"]) == 0
    assert f"usage: mcuhome {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", DEVICE_COMMANDS)
def test_every_device_command_has_help(capsys, command: str) -> None:
    assert main(["device", command, "--help"]) == 0
    assert f"usage: mcuhome device {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", CONFIG_COMMANDS)
def test_every_config_command_has_help(capsys, command: str) -> None:
    assert main(["config", command, "--help"]) == 0
    assert f"usage: mcuhome config {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", PROJECT_COMMANDS)
def test_every_project_command_has_help(capsys, command: str) -> None:
    assert main(["project", command, "--help"]) == 0
    assert f"usage: mcuhome project {command}" in capsys.readouterr().out


def _choices(parser, dest: str):
    # argparse has no public accessor for its subparsers action.
    return next(action for action in parser._actions if action.dest == dest).choices


def test_the_parser_knows_exactly_the_documented_commands() -> None:
    """The decided vocabulary, no silent additions (cli ADR 0003 §2)."""
    parser = build_parser()
    top = _choices(parser, "command")
    assert sorted(top) == sorted(TOP_LEVEL)
    assert sorted(_choices(top["device"], "device_command")) == sorted(DEVICE_COMMANDS)
    assert sorted(_choices(top["config"], "config_command")) == sorted(CONFIG_COMMANDS)
    assert sorted(_choices(top["project"], "project_command")) == sorted(PROJECT_COMMANDS)


def test_a_bare_noun_prints_its_help_and_succeeds(capsys) -> None:
    """`mcuhome device` / `mcuhome config` are nouns, not actions."""
    assert main(["device"]) == 0
    assert "usage: mcuhome device" in capsys.readouterr().out
    assert main(["config"]) == 0
    assert "usage: mcuhome config" in capsys.readouterr().out
    assert main(["project"]) == 0
    assert "usage: mcuhome project" in capsys.readouterr().out


def test_build_help_advertises_the_probed_flags(capsys) -> None:
    """The flags a machine driving the command feature-probes for."""
    assert main(["device", "build", "--help"]) == 0
    out = capsys.readouterr().out
    for flag in (
        "--model",
        "--no-sign",
        "--public-key",
        "-o",
        "--project-dir",
        "--builder",
        "--build-mode",
        "--container-image",
    ):
        assert flag in out
    assert "--json" not in out
    assert "--config-root" not in out
    assert "--method" not in out.replace("--build-mode", "")


def test_the_retired_spellings_are_gone(capsys) -> None:
    """ADR 0023 §5 / cli ADR 0003 §4: no aliases, argparse exit 2."""
    for argv in (
        ["validate", "x"],
        ["build", "x"],
        ["sign", "x"],
        ["new", "x"],
        ["init-pairing", "x"],
        ["device", "init-pairing", "x"],
        # `mcuhome init` moved into the project noun, without an alias.
        ["init"],
    ):
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 2
        capsys.readouterr()
    for argv in (
        ["device", "build", "x", "--method", "local"],
        ["device", "build", "x", "--server", "u"],
        ["device", "build", "x", "--token", "t"],
    ):
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 2
        capsys.readouterr()


# --------------------------------------------------------------------------
# The help contract (PO 2026-08-15)
# --------------------------------------------------------------------------


def test_help_wins_wherever_it_stands(capsys) -> None:
    """`--board -h` must help, not complain about --board's missing value."""
    assert main(["device", "new", "--board", "-h"]) == 0
    assert "usage: mcuhome device new" in capsys.readouterr().out


def test_help_is_literal_after_the_separator(capsys) -> None:
    """`--` ends the help scan: what follows is data, and parsing decides."""
    with pytest.raises(SystemExit) as caught:
        main(["device", "new", "--", "-h"])
    assert caught.value.code == 2


def test_usage_lines_identify_rather_than_enumerate(capsys) -> None:
    """The usage line: positionals + required flags + [options], no more."""
    assert main(["device", "new", "--help"]) == 0
    usage = capsys.readouterr().out.splitlines()[0]
    assert "--board TARGET" in usage
    assert usage.endswith("[options]")
    assert "--color" not in usage
    assert "--name" not in usage


def test_a_usage_error_points_at_help(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["device", "new"])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "Run mcuhome device new --help for the full option list." in err


def test_command_options_come_before_the_general_ones(capsys) -> None:
    """The command's own options first, the shared ones as their own group."""
    assert main(["device", "new", "--help"]) == 0
    out = capsys.readouterr().out
    assert 0 < out.index("--board") < out.index("general options:") < out.index("--color")
