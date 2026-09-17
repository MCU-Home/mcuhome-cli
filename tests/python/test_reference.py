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
import inspect
import json
import re
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

#: The exported names that are functions — what "calls" can mean. A
#: class the reference names in passing is a type, not a call.
API_FUNCTIONS = frozenset(
    name
    for name in api.__all__
    if callable(getattr(api, name)) and not inspect.isclass(getattr(api, name))
)


def _api_functions(tokens: list[str]) -> set[str]:
    """The api functions among *tokens*, calls written with parentheses included."""
    return {
        name
        for name in (re.sub(r"\(.*\)?$", "", token) for token in tokens)
        if name in API_FUNCTIONS
    }


def _retired_key(entry: object) -> str:
    """The spelling the reference's *Retired* column writes for *entry*."""
    words = getattr(entry, "words", None)
    if words is None:
        return entry.spelling  # type: ignore[attr-defined]
    flag = getattr(entry, "flag", "")
    return "mcuhome " + " ".join([*words, *([flag] if flag else [])])


def _retired_id(entry: object) -> str:
    return _retired_key(entry)


def _documented_row(entry: object) -> tuple[str, str]:
    """The reference's row for *entry*: its successors and its qualifier.

    The reference writes a top-level command with the tool's name and
    without it — ``mcuhome validate`` beside ``build`` — and both mean
    the same command, so both spellings are looked for.
    """
    documented, _prose = ref.retired_spellings()
    key = _retired_key(entry)
    if key in documented:
        return documented[key]
    return documented[key.removeprefix("mcuhome ")]


class TestTheTree:
    """Every command of the reference, and nothing else."""

    def test_the_parser_has_exactly_the_documented_commands(self) -> None:
        assert set(PARSED) == set(DOCUMENTED)

    def test_the_index_names_the_same_commands_as_the_headings(self) -> None:
        assert ref.index_commands() == set(DOCUMENTED)

    @pytest.mark.parametrize("words", sorted(DOCUMENTED))
    def test_the_index_row_names_every_call_the_section_does(self, words: tuple[str, ...]) -> None:
        """The two places a command's api calls are written agree.

        A command's section names the calls in prose and the index names
        them in one cell; the cell is what a reader scans, so a call the
        prose introduced and the cell never learned is a row that lies.
        Only exported **functions** count — a class in the prose is a
        type a field carries, not a call — and a name the prose
        attributes to another call belongs in the api reference rather
        than here, which is why none of them is written this way.
        """
        assert _api_functions(ref.prose_calls()[words]) <= _api_functions(ref.index_calls()[words])

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

    def test_two_variables_stand_outside_the_two_tables(self) -> None:
        # The section says which two and why; a third one appearing here
        # without the sentence being rewritten is what this catches.
        listed = {variable for _flag, _option, variable in ref.option_flag_rows()}
        declared = {option.env_var for option in api.OPTIONS if option.env_var}
        assert declared - listed == {"MCUHOME_BUILD_BUILDER", "MCUHOME_PROJECT_DIR"}

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

    @pytest.mark.parametrize(
        "entry",
        [*retiredspellings.RETIRED_COMMANDS, *retiredspellings.RETIRED_FLAGS],
        ids=_retired_id,
    )
    def test_the_successor_is_the_one_the_reference_names(self, entry: object) -> None:
        cell, _qualifier = _documented_row(entry)
        named = ref.quoted(cell)
        successor = entry.successor  # type: ignore[attr-defined]
        if named:
            # A row that names its successor in words alone ("the same
            # act under its area") has nothing to compare against; every
            # other one has to begin with what the reference spells.
            assert any(successor.startswith(spelling) for spelling in named), (
                f"{successor!r} is not one of {named}"
            )

    @pytest.mark.parametrize(
        "entry",
        [*retiredspellings.RETIRED_COMMANDS, *retiredspellings.RETIRED_FLAGS],
        ids=_retired_id,
    )
    def test_a_successor_names_something_the_tree_has(self, entry: object) -> None:
        # A successor nobody can type teaches nothing.
        successor = entry.successor  # type: ignore[attr-defined]
        for words in re.findall(r"mcuhome ([a-z][a-z-]*(?: [a-z][a-z-]*)?)", successor):
            assert tuple(words.split()) in PARSED, words
        flags = {spelling for parser in PARSED.values() for spelling in _flags(parser)}
        for flag in re.findall(r"(?<![\w-])(--?[a-z][a-z-]*)", successor):
            assert flag in flags, flag

    def test_a_qualified_spelling_is_retired_on_that_command_alone(self) -> None:
        for entry in retiredspellings.RETIRED_FLAGS:
            _successor, qualifier = _documented_row(entry)
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
