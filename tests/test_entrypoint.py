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
    "init",
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
    "init-pairing",
    "list",
]
CONFIG_COMMANDS = ["print", "get", "set", "unset"]


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


@pytest.mark.parametrize("command", TOP_LEVEL)
def test_every_top_level_command_has_help(capsys, command: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([command, "--help"])
    assert caught.value.code == 0
    assert f"usage: mcuhome {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", DEVICE_COMMANDS)
def test_every_device_command_has_help(capsys, command: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["device", command, "--help"])
    assert caught.value.code == 0
    assert f"usage: mcuhome device {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", CONFIG_COMMANDS)
def test_every_config_command_has_help(capsys, command: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["config", command, "--help"])
    assert caught.value.code == 0
    assert f"usage: mcuhome config {command}" in capsys.readouterr().out


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


def test_a_bare_noun_prints_its_help_and_succeeds(capsys) -> None:
    """`mcuhome device` / `mcuhome config` are nouns, not actions."""
    assert main(["device"]) == 0
    assert "usage: mcuhome device" in capsys.readouterr().out
    assert main(["config"]) == 0
    assert "usage: mcuhome config" in capsys.readouterr().out


def test_build_help_advertises_the_probed_flags(capsys) -> None:
    """The flags a machine driving the command feature-probes for."""
    with pytest.raises(SystemExit):
        main(["device", "build", "--help"])
    out = capsys.readouterr().out
    for flag in (
        "--model",
        "--no-sign",
        "--public-key",
        "-o",
        "--project-dir",
        "--builder",
        "--build-mode",
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
