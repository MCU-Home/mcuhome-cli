# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The output contract (cli ADR 0004): modes, streams, colors, interactivity."""

from __future__ import annotations

import json

from mcuhome.model.errors import ConfigError

from mcuhome.cli import output as output_module
from mcuhome.cli.output import HUMAN, JSON, JSON_STREAM, Output, resolve

# --- resolve: the three knobs -----------------------------------------


def test_the_machine_modes_force_non_interactive() -> None:
    """A consumer parsing NDJSON cannot answer a question (ADR 0004 §3)."""
    for mode in (JSON, JSON_STREAM):
        resolved = resolve(mode=mode, interactive=True, env={})
        assert resolved.machine
        assert not resolved.interactive


def test_the_explicit_flags_decide_interactivity_in_human_mode() -> None:
    assert resolve(mode=HUMAN, interactive=True, env={}).interactive
    assert not resolve(mode=HUMAN, interactive=False, env={}).interactive


def test_the_interactive_default_is_tty_detection() -> None:
    """Under pytest neither stream is a TTY, so the default is off."""
    assert not resolve(mode=HUMAN, interactive=None, env={}).interactive


def test_color_always_and_never_beat_the_terminal() -> None:
    assert resolve(color="always", env={}).color
    assert not resolve(color="never", env={}).color


def test_no_color_disables_auto_detection() -> None:
    """The NO_COLOR convention: any non-empty value turns colors off."""
    assert not resolve(color="auto", env={"NO_COLOR": "1"}).color
    # And auto without a TTY (pytest) is off anyway.
    assert not resolve(color="auto", env={}).color


def test_style_is_the_identity_without_color() -> None:
    plain = Output(color=False)
    colored = Output(color=True)
    assert plain.style("text", output_module.RED) == "text"
    assert colored.style("text", output_module.RED) == "\x1b[31mtext\x1b[0m"


# --- the stream discipline --------------------------------------------


def test_human_lines_are_suppressed_in_the_machine_modes(capsys) -> None:
    Output(mode=JSON).human("for people only")
    Output(mode=JSON_STREAM).human("for people only")
    assert capsys.readouterr().out == ""
    Output(mode=HUMAN).human("for people only")
    assert capsys.readouterr().out == "for people only\n"


def test_warnings_go_to_stderr_in_every_mode(capsys) -> None:
    for mode in (HUMAN, JSON, JSON_STREAM):
        Output(mode=mode).warn("the file is group-readable")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("Warning: the file is group-readable") == 3


def test_stream_verbs_are_one_json_message_per_line(capsys) -> None:
    stream = Output(mode=JSON_STREAM)
    stream.start("build", device="node")
    stream.progress("compile", device="node")
    stream.result({"ok": True})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        {"verb": "start", "task": "build", "device": "node"},
        {"verb": "progress", "stage": "compile", "device": "node"},
        {"verb": "result", "document": {"ok": True}},
    ]


def test_start_and_progress_stay_silent_outside_the_stream_mode(capsys) -> None:
    for mode in (HUMAN, JSON):
        quiet = Output(mode=mode)
        quiet.start("build")
        quiet.progress("compile")
    assert capsys.readouterr().out == ""


def test_json_mode_emits_exactly_one_document(capsys) -> None:
    Output(mode=JSON).result({"ok": True, "device": "node"})
    assert json.loads(capsys.readouterr().out) == {"ok": True, "device": "node"}


# --- errors, in every mode --------------------------------------------


def test_errors_render_to_stderr_for_a_human(capsys, tmp_path) -> None:
    Output(mode=HUMAN).errors([ConfigError("It broke.", hint="fix it")], cwd=tmp_path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: It broke." in captured.err
    assert "Fix: fix it" in captured.err


def test_errors_are_the_failure_document_for_a_machine(capsys) -> None:
    Output(mode=JSON).errors([ConfigError("It broke.", hint="fix it")])
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["errors"][0]["message"] == "It broke."
    assert document["errors"][0]["hint"] == "fix it"
    assert document["errors"][0]["kind"] == "ConfigError"


def test_streamed_errors_come_verb_by_verb_and_then_as_the_document(capsys) -> None:
    problems = [ConfigError("first"), ConfigError("second")]
    Output(mode=JSON_STREAM).errors(problems)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["verb"] for line in lines] == ["error", "error", "result"]
    assert [line["error"]["message"] for line in lines[:2]] == ["first", "second"]
    assert lines[2]["document"]["ok"] is False
    assert len(lines[2]["document"]["errors"]) == 2


def test_the_first_error_line_is_painted_red_when_colors_are_on(capsys, tmp_path) -> None:
    Output(mode=HUMAN, color=True).errors([ConfigError("It broke.", hint="fix it")], cwd=tmp_path)
    err = capsys.readouterr().err
    assert err.startswith("\x1b[31mError: It broke.\x1b[0m")
    assert "\x1b" not in err.split("\n", 1)[1]  # the hint stays plain
