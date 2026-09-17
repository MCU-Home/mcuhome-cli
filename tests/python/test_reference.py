# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The command line is what ``docs/cli.md`` says it is.

This suite reads the reference as data (:mod:`reference`) and holds the
parser to it: every command exists with exactly the positionals and
flags the reference states, the option-flag tables are the derivation
from the workbench's option registry rather than a list somebody keeps,
every retired spelling is refused by name, and the index names the same
commands the headings do.

It also holds the boundary the whole surface task is about: no module of
``mcuhome/cli/`` imports anything of the workbench other than
``mcuhome.workbench.api``, and nothing of ``mcuhome.model`` directly —
the api re-exports what a client needs, and a client that reaches past
it is a client the surface cannot promise anything to.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pytest
import reference as ref
from mcuhome.workbench import api

from mcuhome.cli import optionflags, retiredspellings
from mcuhome.cli.main import main
from mcuhome.cli.parser import build_parser

PACKAGE = Path(__file__).resolve().parents[2] / "mcuhome" / "cli"


def _commands(parser: argparse.ArgumentParser) -> dict[tuple[str, ...], argparse.ArgumentParser]:
    """Every leaf of the parser tree, by the words that reach it."""
    found: dict[tuple[str, ...], argparse.ArgumentParser] = {}

    def walk(current: argparse.ArgumentParser, words: tuple[str, ...]) -> None:
        sub = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sub is None:
            found[words] = current
            return
        for name, child in sub.choices.items():
            walk(child, words + (name,))

    walk(parser, ())
    return found


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    return {spelling for action in parser._actions for spelling in action.option_strings}


def _positionals(parser: argparse.ArgumentParser) -> list[tuple[str, bool]]:
    return [
        (action.dest, action.nargs != "?")
        for action in parser._actions
        if not action.option_strings
    ]


def _required_flags(parser: argparse.ArgumentParser) -> set[str]:
    return {
        action.option_strings[0]
        for action in parser._actions
        if action.option_strings and action.required
    }


def _expected_flags(command: ref.DocumentedCommand) -> set[str]:
    """The flags the reference states for *command*, groups expanded."""
    flags = set(command.flags)
    if ref.BUILD_GROUP in flags:
        flags.discard(ref.BUILD_GROUP)
        flags.update(flag.spelling for flag in optionflags.build_option_flags())
    return flags | ref.global_flags()


DOCUMENTED = ref.documented_commands()
PARSED = _commands(build_parser())


class TestTheTree:
    """Every command of the reference, and nothing else."""

    def test_the_parser_has_exactly_the_documented_commands(self) -> None:
        assert set(PARSED) == set(DOCUMENTED)

    def test_the_index_names_the_same_commands_as_the_headings(self) -> None:
        assert ref.index_commands() == set(DOCUMENTED)

    @pytest.mark.parametrize("words", sorted(DOCUMENTED))
    def test_a_command_takes_the_positionals_it_documents(self, words: tuple[str, ...]) -> None:
        assert _positionals(PARSED[words]) == list(DOCUMENTED[words].positionals)

    @pytest.mark.parametrize("words", sorted(DOCUMENTED))
    def test_a_command_takes_the_flags_it_documents(self, words: tuple[str, ...]) -> None:
        assert _flags(PARSED[words]) == _expected_flags(DOCUMENTED[words])

    @pytest.mark.parametrize("words", sorted(DOCUMENTED))
    def test_a_flag_outside_brackets_is_required(self, words: tuple[str, ...]) -> None:
        assert _required_flags(PARSED[words]) == set(DOCUMENTED[words].required_flags)

    def test_every_command_answers_in_every_mode(self) -> None:
        # A client that has to know which commands it may drive is a
        # client that cannot drive the tool.
        for words, parser in PARSED.items():
            assert "-o" in _flags(parser), words


class TestOptionFlags:
    """The option-flag tables are the registry's own derivation."""

    def test_the_tables_name_every_flag_the_registry_derives(self) -> None:
        documented = {flag for flag, _option, _variable in ref.option_flag_rows()}
        derived = {flag.spelling for flag in optionflags.option_flags()}
        # `--project-dir` is a global flag and stands in that table.
        assert documented | {"--project-dir"} == derived

    @pytest.mark.parametrize("row", ref.option_flag_rows())
    def test_a_row_names_the_key_and_the_variable_it_derives_from(
        self, row: tuple[str, str, str]
    ) -> None:
        flag, key, variable = row
        declared = api.option(key)
        assert declared.flag == flag
        assert declared.env_var == variable

    def test_three_options_have_no_flag(self) -> None:
        # `build.builder` is configuration and `--builder` selects one
        # for an invocation; the two map options are written in a file.
        assert {option.name for option in api.OPTIONS if not option.flag} == {
            "build.builder",
            "builder",
            "registry",
        }

    def test_the_global_flags_are_on_every_command(self) -> None:
        for words, parser in PARSED.items():
            assert ref.global_flags() <= _flags(parser), words


class TestRetiredSpellings:
    """Every spelling the reference retires is refused, by name."""

    def test_the_tables_hold_the_same_spellings(self) -> None:
        documented, prose_rows = ref.retired_spellings()
        # One row describes a retired *positional* in words; a mechanical
        # check cannot reach it, and a second one has to be taught here.
        assert prose_rows == 1
        implemented = {
            "mcuhome " + " ".join(entry.words) + (f" {entry.flag}" if entry.flag else "")
            for entry in retiredspellings.RETIRED_COMMANDS
        } | {entry.spelling for entry in retiredspellings.RETIRED_FLAGS}
        # The reference spells a top-level command with and without the
        # tool's name; both mean the same command.
        assert {spelling.removeprefix("mcuhome ") for spelling in documented} == {
            spelling.removeprefix("mcuhome ") for spelling in implemented
        }

    def test_a_qualified_spelling_is_retired_on_that_command_alone(self) -> None:
        documented, _prose = ref.retired_spellings()
        for entry in retiredspellings.RETIRED_FLAGS:
            _successor, qualifier = documented[entry.spelling]
            assert " ".join(entry.command) == qualifier

    @pytest.mark.parametrize(
        "entry", retiredspellings.RETIRED_COMMANDS, ids=lambda entry: " ".join(entry.words)
    )
    def test_a_retired_command_is_refused_with_its_successor(
        self, entry: retiredspellings.RetiredCommand, capsys: pytest.CaptureFixture[str]
    ) -> None:
        tokens = [*entry.words, *((entry.flag,) if entry.flag else ()), "-o", "json"]
        assert main(tokens) == 2
        document = json.loads(capsys.readouterr().out)
        assert document["errors"][0]["kind"] == "RetiredSpelling"
        assert entry.successor in document["errors"][0]["hint"]

    @pytest.mark.parametrize(
        "entry", retiredspellings.RETIRED_FLAGS, ids=lambda entry: entry.spelling
    )
    def test_a_retired_flag_is_refused_with_its_successor(
        self, entry: retiredspellings.RetiredFlag, capsys: pytest.CaptureFixture[str]
    ) -> None:
        command = entry.command or ("device", "build")
        tokens = [*command, "x", entry.spelling, "-o", "json"]
        assert main(tokens) == 2
        document = json.loads(capsys.readouterr().out)
        assert document["errors"][0]["kind"] == "RetiredSpelling"
        assert entry.successor in document["errors"][0]["hint"]

    def test_a_retired_spelling_after_the_boundary_is_a_value(self) -> None:
        # After `--` a token is a value, and a value that looks like a
        # flag is still a value.
        assert main(["config", "get", "--", "--build-dir"]) == 1


class TestTheImportBoundary:
    """The command line is a client of one module."""

    @pytest.mark.parametrize("module", sorted(PACKAGE.glob("*.py")), ids=lambda path: path.name)
    def test_a_module_imports_the_workbench_only_through_its_api(self, module: Path) -> None:
        for name in _mcuhome_imports(module):
            assert name == "mcuhome.workbench.api" or name.startswith("mcuhome.cli"), (
                f"{module.name} imports {name}"
            )

    def test_the_package_reaches_the_workbench_at_all(self) -> None:
        # The check above passes for a package that imports nothing; this
        # is what says the surface is actually the one being used.
        reached = {name for module in PACKAGE.glob("*.py") for name in _mcuhome_imports(module)}
        assert "mcuhome.workbench.api" in reached


def _mcuhome_imports(module: Path) -> list[str]:
    """Every MCUHome module *module* imports, fully qualified.

    ``from mcuhome.workbench import api`` names the module ``api``, not a
    name inside the package, and is therefore resolved to
    ``mcuhome.workbench.api`` — a check that read the statement's module
    alone would pass every internal import there is.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("mcuhome"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if not node.module.startswith("mcuhome"):
                continue
            if node.module in ("mcuhome", "mcuhome.workbench", "mcuhome.model"):
                found.extend(f"{node.module}.{alias.name}" for alias in node.names)
            else:
                found.append(node.module)
    return found
