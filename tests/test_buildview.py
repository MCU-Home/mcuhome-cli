# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The live build view: the step line, the frame, the log file.

The frame is ANSI painting, so these tests read what the view wrote to
its stream — the escape codes are the behavior, not an implementation
detail: a block that stops rewinding scrolls the terminal, and a frame
that grows eats it.
"""

from __future__ import annotations

import io
import os

import pytest

from mcuhome_cli import buildview
from mcuhome_cli.buildview import (
    DONE,
    FAILED,
    BuildStep,
    LiveView,
    PlainView,
    ViewStream,
    log_tail,
    make_view,
)
from mcuhome_cli.output import Output

INTERACTIVE = Output(mode="human", color=False, interactive=True)
PLAIN = Output(mode="human", color=False, interactive=False)
MACHINE = Output(mode="json-stream", color=False, interactive=False)


def _steps() -> list[BuildStep]:
    return [
        BuildStep("validate", "validate", state=DONE),
        BuildStep("compile", "compile (container r8)"),
        BuildStep("sign", "sign (local)"),
    ]


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(buildview, "_REPAINT_EVERY", 0.0)
    monkeypatch.setattr(buildview, "get_terminal_size", lambda: os.terminal_size((80, 24)))


def _live(tmp_path) -> tuple[LiveView, io.StringIO]:
    stream = io.StringIO()
    view = LiveView(_steps(), output=INTERACTIVE, log_path=tmp_path / "build.log", stream=stream)
    return view, stream


# --------------------------------------------------------------------------
# Which view a run gets
# --------------------------------------------------------------------------


def test_an_interactive_human_run_gets_the_live_view(tmp_path) -> None:
    view = make_view(_steps(), output=INTERACTIVE, log_path=tmp_path / "build.log")
    assert isinstance(view, LiveView)
    view.close(success=True)


@pytest.mark.parametrize("output", [PLAIN, MACHINE])
def test_everything_else_stays_linear(tmp_path, output) -> None:
    view = make_view(_steps(), output=output, log_path=tmp_path / "build.log")
    assert isinstance(view, PlainView)
    view.close(success=True)


# --------------------------------------------------------------------------
# The live frame
# --------------------------------------------------------------------------


def test_the_block_repaints_in_place_at_constant_height(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    for index in range(30):
        view.line(f"line {index}")
    view.close(success=True)
    # Height: step line + log pointer + frame top + window + frame bottom.
    height = 4 + buildview.WINDOW_LINES
    rewinds = stream.getvalue().count(f"\x1b[{height}F")
    assert rewinds >= 30  # every line repainted the same block in place


def test_a_step_without_output_shows_no_frame_at_all(tmp_path) -> None:
    """The frame belongs to the step producing output (PO 2026-08-16).

    Packing a context prints nothing, and an empty box over it reads as
    output that went missing.
    """
    view, stream = _live(tmp_path)
    view.step("compile")
    painted = stream.getvalue()
    assert "┌" not in painted
    assert "│" not in painted
    assert "build.log" not in painted  # nothing to point at yet
    assert "▶ compile (container r8)" in painted
    view.close(success=True)


def test_the_frame_closes_with_the_step_that_filled_it(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    view.line("compiler said this")
    assert "│" in stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    view.step("sign")
    after = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "│" not in after
    assert "compiler said this" not in after
    assert "▶ sign (local)" in after
    view.close(success=True)


def test_a_note_stays_above_the_repainted_block(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.note("  context   SDK 0.1.0")
    view.step("compile")
    for index in range(3):
        view.line(f"line {index}")
    view.close(success=True)
    written = stream.getvalue()
    # Written once and never rewound over: the block below it repaints,
    # the note scrolls up with the rest of the terminal.
    assert written.count("context   SDK 0.1.0") == 1
    assert "context   SDK 0.1.0" not in written.rsplit("\x1b[0J", 1)[-1]
    assert "SDK" not in (tmp_path / "build.log").read_text("utf-8")


def test_the_window_shows_the_newest_lines_and_the_file_keeps_all(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    for index in range(30):
        view.line(f"line {index}")
    last_frame = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "line 29" in last_frame
    assert "line 5" not in last_frame  # scrolled out of the 15-line window
    view.close(success=True)
    kept = (tmp_path / "build.log").read_text("utf-8").splitlines()
    assert kept == [f"line {index}" for index in range(30)]


def test_the_step_line_walks_the_states(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    frame = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "✓ validate" in frame
    assert "▶ compile (container r8)" in frame
    assert "· sign (local)" in frame
    view.step("sign")
    frame = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "✓ compile (container r8)" in frame


def test_closing_collapses_the_frame_to_the_step_line(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    view.line("some output")
    view.close(success=True)
    residue = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "│" not in residue  # the frame is gone
    assert "✓ sign (local)" in residue
    assert "build.log" in residue


def test_a_failure_marks_the_running_step(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    view.close(success=False)
    residue = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "✗ compile (container r8)" in residue


def test_a_failure_between_steps_still_shows_where_it_stopped(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.close(success=False)
    residue = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "✗ compile (container r8)" in residue


def test_foreign_escape_codes_cannot_break_the_frame(tmp_path) -> None:
    view, stream = _live(tmp_path)
    view.step("compile")
    view.line("colored \x1b[31mred\x1b[0m and\ttabbed")
    last_frame = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    assert "\x1b[31m" not in last_frame
    assert "\t" not in last_frame
    view.close(success=True)


# --------------------------------------------------------------------------
# The linear view
# --------------------------------------------------------------------------


def test_the_plain_view_passes_lines_through(tmp_path, capsys) -> None:
    view = PlainView(_steps(), output=PLAIN, log_path=tmp_path / "build.log")
    view.step("compile")
    view.line("west said this")
    view.close(success=True)
    assert "west said this" in capsys.readouterr().out
    assert "west said this" in (tmp_path / "build.log").read_text("utf-8")


def test_the_plain_view_prints_notes_but_a_machine_mode_does_not(tmp_path, capsys) -> None:
    """A machine mode carries the facts as ``progress`` data already."""
    view = PlainView(_steps(), output=PLAIN, log_path=tmp_path / "build.log")
    view.note("  context   SDK 0.1.0")
    view.close(success=True)
    assert "context   SDK 0.1.0" in capsys.readouterr().out

    view = PlainView(_steps(), output=MACHINE, log_path=tmp_path / "build.log")
    view.note("  context   SDK 0.1.0")
    view.close(success=True)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_under_a_machine_mode_lines_go_to_stderr(tmp_path, capsys) -> None:
    view = PlainView(_steps(), output=MACHINE, log_path=tmp_path / "build.log")
    view.line("west said this")
    view.close(success=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "west said this" in captured.err


# --------------------------------------------------------------------------
# The stream adapter, and the tail
# --------------------------------------------------------------------------


def test_the_stream_adapter_reassembles_lines(tmp_path) -> None:
    view, _stream = _live(tmp_path)
    adapter = ViewStream(view)
    adapter.write("first ha")
    adapter.write("lf\nsecond\nthird without newline")
    adapter.flush()
    view.close(success=True)
    kept = (tmp_path / "build.log").read_text("utf-8").splitlines()
    assert kept == ["first half", "second", "third without newline"]


def test_the_tail_is_the_last_lines_of_the_file(tmp_path) -> None:
    path = tmp_path / "build.log"
    path.write_text("\n".join(f"line {index}" for index in range(100)), "utf-8")
    tail = log_tail(path, lines=3)
    assert tail == "line 97\nline 98\nline 99"
    assert log_tail(tmp_path / "missing.log") == ""


def test_a_short_terminal_shrinks_the_window_instead_of_stacking(tmp_path, monkeypatch) -> None:
    """A block taller than the terminal cannot be rewound — the cursor
    stops at the top row and every repaint would stack a copy."""
    monkeypatch.setattr(buildview, "get_terminal_size", lambda: os.terminal_size((80, 10)))
    view, stream = _live(tmp_path)
    view.step("compile")
    for index in range(10):
        view.line(f"line {index}")
    last_frame = stream.getvalue().rsplit("\x1b[0J", 1)[-1]
    # 10 rows - 5 fixed lines = a 5-line window: the newest five lines.
    assert last_frame.count("│") == 2 * 5
    assert "line 9" in last_frame
    assert "line 4" not in last_frame
    view.close(success=True)


def test_a_closed_view_marks_every_open_step(tmp_path) -> None:
    view, _stream = _live(tmp_path)
    view.step("compile")
    view.close(success=True)
    assert [entry.state for entry in view.steps] == [DONE, DONE, DONE]
    view, _stream = _live(tmp_path)
    view.step("compile")
    view.close(success=False)
    assert [entry.state for entry in view.steps] == [DONE, FAILED, "pending"]
