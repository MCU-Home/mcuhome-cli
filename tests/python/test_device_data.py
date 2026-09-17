# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``device print-schema``, ``list-boards`` and ``list-supported``.

Everything here is the real workbench: the schema and the hardware and
Matter table are pure answers, so there is nothing to stub. Two of the
three commands answer data and carry no ``ok``; ``list-boards`` is a
projection of the same table and carries one like every other command's
document.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcuhome.workbench import api

from mcuhome.cli.main import main


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


class TestPrintSchema:
    """The JSON Schema of a device file, as data."""

    def test_it_answers_the_schema_alone_with_no_ok(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "print-schema", "-o", "json"]) == 0
        document = _document(capsys)
        assert "ok" not in document
        assert document == api.device_schema()

    def test_the_stream_ends_with_one_result_carrying_the_same_document(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "print-schema", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages].count("result") == 1
        result = next(message for message in messages if message["verb"] == "result")
        assert result["document"] == api.device_schema()

    def test_a_person_reads_the_schema_as_valid_json(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "print-schema", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert json.loads(printed) == api.device_schema()

    def test_it_needs_no_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A client that builds a form from the schema has not created a
        # project yet, and the data is the tool's rather than a project's.
        monkeypatch.chdir(tmp_path)
        assert main(["device", "print-schema", "-o", "json"]) == 0
        assert _document(capsys) == api.device_schema()
        assert main(["device", "list-boards", "-o", "json"]) == 0
        assert _document(capsys)["ok"] is True
        assert main(["device", "list-supported", "-o", "json"]) == 0
        assert "ok" not in _document(capsys)


class TestListBoards:
    """A projection of the hardware and Matter table: two lists, taken whole."""

    def test_it_answers_the_two_board_lists_as_a_projection(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list-boards", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "boards", "planned_boards"]
        assert document["ok"] is True
        registry = api.device_registry()
        # A projection and not a rebuild: the entries are the workbench's
        # own, unchanged key by key.
        assert document["boards"] == registry["boards"]
        assert document["planned_boards"] == registry["planned_boards"]

    def test_the_stream_ends_with_one_result_carrying_the_same_document(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list-boards", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages].count("result") == 1
        result = next(message for message in messages if message["verb"] == "result")
        assert result["document"]["ok"] is True
        assert result["document"]["boards"] == api.device_registry()["boards"]

    def test_a_person_reads_a_table_of_boards(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list-boards", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        board = api.device_registry()["boards"][0]
        assert board["name"] in printed
        assert board["transports"][0] in printed


class TestListSupported:
    """Everything MCUHome knows about hardware and Matter, as data."""

    def test_it_answers_the_whole_document_alone_with_no_ok(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list-supported", "-o", "json"]) == 0
        document = _document(capsys)
        assert "ok" not in document
        assert document == api.device_registry()

    def test_the_stream_ends_with_one_result_carrying_the_same_document(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list-supported", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages].count("result") == 1
        result = next(message for message in messages if message["verb"] == "result")
        assert result["document"] == api.device_registry()

    def test_a_person_reads_the_counts(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list-supported", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        registry = api.device_registry()
        assert "boards" in printed
        assert str(len(registry["boards"])) in printed
        assert "-o json" in printed
