# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The output contract: three modes, seven verbs, one document.

What is tested here is the module itself rather than a command through
it — the per-command documents are each command's own test, and this is
what those documents travel in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcuhome.workbench import api

from mcuhome.cli import output as output_module
from mcuhome.cli.errors import UsageError
from mcuhome.cli.output import HUMAN, JSON, JSON_STREAM, Output

#: A hint that lays a command out under a sentence — the shape that
#: shows whether a channel re-indents what it was given.
HINT = "unset it:\n    unset MCUHOME_DOCKER"


def _stream(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line]


class TestTheModes:
    def test_human_renders_on_stdout_and_answers_no_document(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = Output(mode=HUMAN)
        output.human("a line")
        output.result({"ok": True})
        assert capsys.readouterr().out == "a line\n"

    def test_json_answers_exactly_one_document(self, capsys: pytest.CaptureFixture[str]) -> None:
        output = Output(mode=JSON)
        output.human("not printed")
        output.result({"ok": True, "name": "build.mode"})
        assert json.loads(capsys.readouterr().out) == {"ok": True, "name": "build.mode"}

    def test_json_stream_carries_the_same_document_in_its_result(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = Output(mode=JSON_STREAM)
        output.start("config print")
        output.result({"ok": True})
        messages = _stream(capsys.readouterr().out)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[-1]["document"] == {"ok": True}

    def test_a_run_answers_once(self) -> None:
        output = Output(mode=JSON)
        output.result({"ok": True})
        with pytest.raises(RuntimeError):
            output.result({"ok": True})

    def test_logs_and_warnings_go_to_stderr_in_every_mode(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for mode in output_module.MODES:
            output = Output(mode=mode)
            output.log("a log line")
            output.warn("something is off")
            captured = capsys.readouterr()
            assert captured.out == ""
            assert "a log line" in captured.err


class TestTheVerbs:
    """Seven verbs, and the key set each one carries."""

    def test_every_verb_is_one_line_with_its_keys(self, capsys: pytest.CaptureFixture[str]) -> None:
        output = Output(mode=JSON_STREAM)
        output.start("device build", steps=["context"])
        output.progress("migration_started", name="v2_secrets_layout")
        output.finding({"severity": "warning", "message": "a file is exposed", "hint": None})
        output.wait(retry_after=2.0, waited=4.0, attempt=2)
        output.stopping(seconds=None)
        output.error({"message": "no", "kind": "ConfigError"})
        output.result({"ok": False})
        messages = _stream(capsys.readouterr().out)
        assert [message["verb"] for message in messages] == [
            "start",
            "progress",
            "diagnostic",
            "wait",
            "stopping",
            "error",
            "result",
        ]
        assert messages[0] == {"verb": "start", "task": "device build", "steps": ["context"]}
        assert set(messages[3]) == {"verb", "retry_after", "waited", "attempt"}
        assert messages[4] == {"verb": "stopping", "seconds": None}

    def test_a_finding_reaches_a_person_on_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        for mode in (HUMAN, JSON):
            Output(mode=mode).finding(
                {"severity": "warning", "message": "MCUHOME_DOCKER is set.", "hint": "unset it"}
            )
            captured = capsys.readouterr()
            assert captured.out == ""
            assert "MCUHOME_DOCKER is set." in captured.err
            assert "unset it" in captured.err

    def test_a_fix_is_its_own_line_under_the_finding(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The layout a rendered refusal has, for the same reason.

        A hint joined to the message with a space is one long line whose
        second half nobody reads as an instruction.
        """
        Output(mode=HUMAN).finding(
            {
                "severity": "error",
                "message": "MCUHOME_DOCKER is set.",
                "hint": HINT,
            }
        )
        lines = capsys.readouterr().err.splitlines()
        assert lines == [
            "Error: MCUHOME_DOCKER is set.",
            "  Fix: unset it:",
            "    unset MCUHOME_DOCKER",
        ]

    def test_the_two_channels_lay_the_same_hint_out_the_same_way(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A finding and a refusal are one layout, hint included.

        The same words reach a person down two channels — as a finding
        beside a document, and as the rendering of what stopped the run
        — and a hint that laid its commands out did so on purpose. Two
        indentations for one hint is the reader wondering which of them
        means something.
        """
        Output(mode=HUMAN).finding(
            {"severity": "error", "message": "MCUHOME_DOCKER is set.", "hint": HINT}
        )
        as_finding = capsys.readouterr().err.splitlines()

        Output(mode=HUMAN).errors([UsageError("MCUHOME_DOCKER is set.", hint=HINT)])
        as_refusal = capsys.readouterr().err.splitlines()

        assert as_finding[1:] == as_refusal[1:]
        assert as_finding[0].endswith(as_refusal[0])

    def test_the_stream_never_carries_translated_vocabulary(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Verbs and keys are structure, not prose: a translation of them
        # would break every consumer, so they are plain literals.
        output = Output(mode=JSON_STREAM)
        output.start("version")
        output.result({"ok": True})
        for message in _stream(capsys.readouterr().out):
            assert message["verb"].islower()
            assert all(key.replace("_", "").isalnum() for key in message)


class TestSayingNo:
    """A refusal and a negative answer are told apart by the keys."""

    def test_a_refusal_replaces_the_document(self, capsys: pytest.CaptureFixture[str]) -> None:
        output = Output(mode=JSON)
        output.errors([UsageError("--nope is not a flag", hint="run mcuhome --help")])
        document = json.loads(capsys.readouterr().out)
        assert set(document) == {"ok", "errors"}
        assert document["ok"] is False
        assert document["errors"][0]["kind"] == "UsageError"
        assert document["errors"][0]["hint"] == "run mcuhome --help"

    def test_a_refusal_also_arrives_as_it_happens(self, capsys: pytest.CaptureFixture[str]) -> None:
        output = Output(mode=JSON_STREAM)
        output.errors([api.ConfigError("no such option")])
        messages = _stream(capsys.readouterr().out)
        assert [message["verb"] for message in messages] == ["error", "result"]
        assert messages[0]["error"]["message"] == "no such option"

    def test_a_refusal_is_rendered_on_stderr_for_a_person(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = Output(mode=HUMAN)
        output.errors([api.ConfigError("no such option", hint="try build.mode")])
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "no such option" in captured.err
        assert "try build.mode" in captured.err

    def test_an_error_document_carries_every_key_it_declares(self) -> None:
        document = UsageError("wrong").to_dict()
        assert list(document) == ["message", "file", "line", "column", "key", "hint", "kind"]

    def test_an_error_document_names_a_file_absolutely(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A program reading the document is not necessarily standing in
        # the project, so a path it can open beats one it has to join.
        file = tmp_path / "mcuhome.yaml"
        output = Output(mode=JSON)
        output.errors(
            [api.ConfigError("no such option", location=api.Location(file=file))], cwd=tmp_path
        )
        assert json.loads(capsys.readouterr().out)["errors"][0]["file"] == str(file)


class TestResolving:
    """``-o``, ``--color`` and interactivity meet in one place."""

    def test_a_machine_mode_is_never_interactive(self) -> None:
        for mode in (JSON, JSON_STREAM):
            assert output_module.resolve(mode=mode, interactive=True).interactive is False

    def test_no_color_turns_colors_off_under_auto(self) -> None:
        assert output_module.resolve(color="auto", env={"NO_COLOR": "1"}).color is False
        assert output_module.resolve(color="always", env={"NO_COLOR": "1"}).color is True
        assert output_module.resolve(color="never", env={}).color is False

    def test_styling_is_a_rendering_and_never_a_value(self) -> None:
        # Colors exist in text a human reads. A document is composed of
        # what the workbench answered, so nothing in it passes through
        # here — `mcuhome config print --color always -o json` is where
        # that is checked end to end.
        output = Output(mode=JSON, color=True)
        assert output.human("a line") is None
        assert output.style("x", "31") != "x"
