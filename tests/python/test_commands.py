# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``version`` and ``config``, in all three modes.

The commands run against a real project and the real workbench: a mock
of a call that is this cheap would be a test of the mock. What the suite
closes off is a child process and a socket (``conftest``), and no
command here needs either.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
from mcuhome.workbench import api

from mcuhome.cli import __version__ as command_line_version
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.main import main
from mcuhome.cli.output import Output
from mcuhome.cli.parser import build_parser

#: What this version of the command line cannot do. Both wait on
#: platform work rather than on a command being written: flashing a
#: built image, and the one-time board preparation.
REFUSING = {
    "device flash",
    "device install-bootloader",
}


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


class TestVersion:
    def test_it_answers_the_command_line_and_the_stack(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["version", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "command_line", "stack"]
        assert document["command_line"] == command_line_version
        assert document["stack"] == api.stack_versions()

    def test_the_stream_starts_and_ends(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["version", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0] == {"verb": "start", "task": "version"}

    def test_a_person_reads_one_line_per_package(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["version"]) == 0
        printed = capsys.readouterr().out.splitlines()
        assert printed[0] == f"mcuhome-cli {command_line_version}"
        assert len(printed) == 1 + len(api.stack_versions())


class TestConfigPrint:
    def test_it_answers_every_declared_option(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "print", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "config"]
        # One entry per declared option except the bootstrap one.
        assert set(document["config"]) == {
            option.name for option in api.OPTIONS if not option.bootstrap
        }
        assert document["config"]["build.mode"] == {
            "value": "container",
            "origin": "default",
            "source": None,
        }

    def test_it_works_outside_a_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["config", "print", "-o", "json"]) == 0
        assert _document(capsys)["ok"] is True

    def test_a_document_carries_no_escape_code(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "print", "--color", "always", "-o", "json"]) == 0
        printed = capsys.readouterr().out
        assert "\x1b" not in printed and "\\u001b" not in printed

    def test_a_person_reads_a_table(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "print"]) == 0
        printed = capsys.readouterr().out
        assert "build.mode" in printed
        assert "container" in printed


class TestConfigGet:
    def test_it_answers_one_value_with_its_layer(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "get", "build.target", "-o", "json"]) == 0
        assert _document(capsys) == {
            "ok": True,
            "name": "build.target",
            "value": "local",
            "origin": "default",
            "source": None,
        }

    def test_it_answers_a_map_entry_key(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "set", "registry.packages.example.org.untrusted", "true"]) == 0
        capsys.readouterr()
        assert main(["config", "get", "registry.packages.example.org.untrusted", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["value"] is True
        assert document["origin"] == "project"
        assert document["source"] == str(in_project / "mcuhome.yaml")

    def test_a_key_nobody_declared_is_refused_before_any_layer(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "get", "build.nope", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "ConfigError"

    def test_a_person_reads_the_value_and_where_it_came_from(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "get", "build.target"]) == 0
        printed = capsys.readouterr().out.splitlines()
        assert printed[0] == "local"
        assert "default" in printed[1]


class TestConfigSetAndUnset:
    def test_set_writes_the_parsed_value_into_the_project_file(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "set", "build.mode", "subprocess", "-o", "json"]) == 0
        assert _document(capsys) == {
            "ok": True,
            "name": "build.mode",
            "value": "subprocess",
            "scope": "project",
            "file": str(in_project / "mcuhome.yaml"),
        }
        assert "subprocess" in (in_project / "mcuhome.yaml").read_text(encoding="utf-8")

    def test_set_answers_a_path_as_the_document_spells_one(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "set", "build.cache_root", "~/caches", "-o", "json"]) == 0
        assert isinstance(_document(capsys)["value"], str)

    def test_set_refuses_a_value_the_declaration_does_not_take(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The value is a positional rather than a flag's, so it is the
        # command running and saying no: exit 1.
        assert main(["config", "set", "build.mode", "sideways", "-o", "json"]) == 1
        assert _document(capsys)["errors"][0]["kind"] == "ConfigError"

    def test_set_refuses_outside_a_project_for_the_default_scope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["config", "set", "build.mode", "subprocess", "-o", "json"]) == 1
        hint = _document(capsys)["errors"][0]["hint"]
        assert "mcuhome project init" in hint

    def test_the_user_scope_needs_no_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code = main(["config", "set", "build.mode", "subprocess", "--scope", "user", "-o", "json"])
        assert code == 0
        document = _document(capsys)
        assert document["scope"] == "user"
        assert Path(document["file"]).is_file()

    def test_unset_says_whether_anything_was_there(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "unset", "build.mode", "-o", "json"]) == 0
        assert _document(capsys)["removed"] is False
        assert main(["config", "set", "build.mode", "subprocess"]) == 0
        capsys.readouterr()
        assert main(["config", "unset", "build.mode", "-o", "json"]) == 0
        assert _document(capsys)["removed"] is True

    def test_a_scope_nobody_offers_is_a_wrong_invocation(self) -> None:
        assert main(["config", "set", "build.mode", "subprocess", "--scope", "global"]) == 2


class TestTheArgumentsChannel:
    """What a flag carries reaches the configuration ladder as an argument.

    ``config get`` has no option flags of its own — the reference gives
    them to the four commands that resolve a build — so the channel is
    driven here through the parser and the invocation, which is what
    every one of those commands does with it.
    """

    def _invocation(self, tokens: list[str], *, cwd: Path) -> Invocation:
        args = build_parser().parse_args(tokens)
        return Invocation(
            task="host check", args=args, output=Output(), env=dict(os.environ), cwd=cwd
        )

    def test_a_flag_wins_over_every_file_and_names_its_spelling(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "set", "build.mode", "container"]) == 0
        capsys.readouterr()
        invocation = self._invocation(
            ["host", "check", "--build-mode", "subprocess"], cwd=in_project
        )
        settings = invocation.settings(project=invocation.find_project())
        assert settings.setting("build.mode").to_dict() == {
            "value": "subprocess",
            "origin": "arguments",
            # The spelling the person used, not a key they never wrote.
            "source": "--build-mode",
        }

    def test_a_flag_that_was_not_used_is_absent(self, in_project: Path) -> None:
        invocation = self._invocation(["host", "check"], cwd=in_project)
        assert invocation.option_arguments() == ()

    def test_a_list_flag_appends_once_per_use(self, in_project: Path) -> None:
        invocation = self._invocation(
            [
                "host",
                "check",
                "--build-sdk-sources",
                "/one",
                "--build-sdk-sources",
                "/two",
            ],
            cwd=in_project,
        )
        settings = invocation.settings(project=invocation.find_project())
        assert settings.setting("build.sdk_sources").to_dict()["value"] == ["/one", "/two"]

    def test_the_bootstrap_option_is_not_an_argument(self, project: Path, tmp_path: Path) -> None:
        # `--project-dir` decides where the project layer *is*; handing
        # it to the layer that reads it is what that layer refuses.
        invocation = self._invocation(
            ["host", "check", "--project-dir", str(project)], cwd=tmp_path
        )
        assert invocation.option_arguments() == ()
        assert invocation.find_project() is not None
        assert invocation.project().root == project


class TestWhatIsNotImplementedYet:
    """Every command of the reference answers; some of them say no.

    The list is taken from the parser rather than written down, so a
    command that grows a real handler leaves it by itself.
    """

    @staticmethod
    def _tree() -> dict[tuple[str, ...], argparse.ArgumentParser]:
        found: dict[tuple[str, ...], argparse.ArgumentParser] = {}

        def walk(parser: argparse.ArgumentParser, words: tuple[str, ...]) -> None:
            sub = next(
                (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
            )
            if sub is None:
                found[words] = parser
                return
            for name, child in sub.choices.items():
                walk(child, (*words, name))

        walk(build_parser(), ())
        return found

    @staticmethod
    def _invocation(words: tuple[str, ...], parser: argparse.ArgumentParser) -> list[str]:
        """A well-formed invocation: the positionals and the required flags."""
        tokens = list(words)
        for action in parser._actions:
            if not action.option_strings and action.nargs != "?":
                tokens.append("x")
            elif action.required and action.option_strings:
                value = action.choices[0] if action.choices else "x"
                tokens.extend([action.option_strings[0], value])
        return tokens

    def test_it_refuses_with_the_condition_and_exits_one(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        refusing = {
            words: parser
            for words, parser in self._tree().items()
            if parser.get_default("handler").__module__ == "mcuhome.cli.unavailable"
        }
        # The implemented commands of this version are the ones missing.
        assert {" ".join(words) for words in refusing} == REFUSING
        for words, parser in refusing.items():
            tokens = [*self._invocation(words, parser), "-o", "json"]
            assert main(tokens) == 1, tokens
            document = _document(capsys)
            assert set(document) == {"ok", "errors"}, tokens
            assert document["errors"][0]["kind"] == "CapabilityUnavailable", tokens
            assert " ".join(words) in document["errors"][0]["message"]
