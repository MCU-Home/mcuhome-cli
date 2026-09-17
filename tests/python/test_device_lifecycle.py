# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The life of a device: ``new``, ``list``, ``validate``, ``clean``,
``rename``, ``delete`` and the two Matter pairing commands.

Every test drives ``main([...])`` against a real project and lets the
workbench do the work — a mock of a call this cheap would be a test of
the mock. What is stubbed is the one thing a person supplies and a test
cannot: the answer typed at a question.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
from conftest import BOARD
from mcuhome.workbench import api

from mcuhome.cli.main import main


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def _answer(monkeypatch: pytest.MonkeyPatch, typed: str) -> list[str]:
    """What the person at the terminal types, and the questions they saw."""
    asked: list[str] = []

    def ask(question: str = "") -> str:
        asked.append(question)
        return typed

    monkeypatch.setattr(builtins, "input", ask)
    return asked


def _never_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that asks anything at all fails the test that says it does not."""

    def refuse(question: str = "") -> str:
        raise AssertionError(f"the command asked {question!r}")

    monkeypatch.setattr(builtins, "input", refuse)


def _break_device(project: Path, name: str) -> None:
    """Leave *name* with a configuration that cannot resolve."""
    file = project / "devices" / name / "main.yaml"
    file.write_text(file.read_text(encoding="utf-8") + "\nnonsense: 1\n", encoding="utf-8")


class TestDeviceNew:
    """A new device, and the two refusals that are the workbench's."""

    def test_it_writes_the_device_and_answers_where(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "new", "kitchen", "--board", BOARD, "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "dry_run", "project", "entry", "name", "board"]
        assert document["ok"] is True
        assert document["dry_run"] is False
        assert document["name"] == "kitchen"
        assert document["board"] == BOARD
        assert document["project"]["root"] == str(in_project)
        assert Path(document["entry"]) == in_project / "devices" / "kitchen" / "main.yaml"
        assert Path(document["entry"]).is_file()

    def test_a_dry_run_answers_the_text_and_writes_nothing(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "new", "kitchen", "--board", BOARD, "--dry-run", "-o", "json"]) == 0
        document = _document(capsys)
        # No project entry to name: nothing was written.
        assert list(document) == ["ok", "dry_run", "name", "board", "text"]
        assert document["dry_run"] is True
        assert "name: kitchen" in document["text"]
        assert list((in_project / "devices").iterdir()) == []

    def test_a_dry_run_prints_the_file_and_nothing_else(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # What a person does with it is redirect it into a file.
        assert main(["device", "new", "kitchen", "--board", BOARD, "--dry-run"]) == 0
        printed = capsys.readouterr()
        assert printed.out == api.render_device_file("kitchen", board=BOARD).rstrip("\n") + "\n"
        assert "Nothing was written" in printed.err

    def test_the_friendly_name_is_what_a_controller_shows(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            main(
                [
                    "device",
                    "new",
                    "kitchen",
                    "--board",
                    BOARD,
                    "--friendly-name",
                    "Kitchen sensor",
                    "-o",
                    "json",
                ]
            )
            == 0
        )
        entry = Path(_document(capsys)["entry"])
        assert '"Kitchen sensor"' in entry.read_text(encoding="utf-8")

    def test_a_board_nobody_brought_up_is_the_workbenchs_refusal(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "new", "kitchen", "--board", "nonesuch", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "ConfigError"
        assert "not a board MCUHome knows" in document["errors"][0]["message"]
        assert list((in_project / "devices").iterdir()) == []

    def test_a_dry_run_refuses_that_board_the_same_way(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Showing the file before it is written must meet the same answer
        # as writing it, or --dry-run would be the one way to be told
        # nothing at all.
        assert (
            main(["device", "new", "kitchen", "--board", "nonesuch", "--dry-run", "-o", "json"])
            == 1
        )
        assert _document(capsys)["errors"][0]["kind"] == "ConfigError"

    def test_a_device_that_is_already_there_is_refused(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = (device / "devices" / "kitchen" / "main.yaml").read_bytes()
        assert main(["device", "new", "kitchen", "--board", BOARD, "-o", "json"]) == 1
        assert "already a device" in _document(capsys)["errors"][0]["message"]
        assert (device / "devices" / "kitchen" / "main.yaml").read_bytes() == before

    def test_the_board_is_required(self, in_project: Path) -> None:
        assert main(["device", "new", "kitchen"]) == 2

    def test_a_person_reads_what_was_written_and_what_comes_next(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "new", "kitchen", "--board", BOARD]) == 0
        printed = capsys.readouterr().out
        assert "devices/kitchen/main.yaml" in printed
        assert "mcuhome device create-matter-pairing kitchen" in printed


class TestDeviceList:
    """The listing's verdict is the listing's, never a row's."""

    def test_it_answers_the_project_and_one_record_per_device(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "project", "devices"]
        assert document["ok"] is True
        assert list(document["devices"][0]) == [
            "ok",
            "name",
            "file",
            "board",
            "problems",
            "built",
            "signed",
            "busy",
        ]
        assert document["devices"][0]["name"] == "kitchen"
        assert document["devices"][0]["board"] == BOARD

    def test_a_device_with_a_problem_is_a_row_and_not_a_failed_command(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api.create_device("bath", project=api.read_project(device), board=BOARD)
        _break_device(device, "bath")
        assert main(["device", "list", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True, "one device with a typo is not a failed listing"
        rows = {row["name"]: row for row in document["devices"]}
        assert rows["kitchen"]["ok"] is True
        assert rows["bath"]["ok"] is False
        assert rows["bath"]["problems"] > 0

    def test_a_project_with_no_devices_says_so(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list"]) == 0
        assert "No devices yet" in capsys.readouterr().out

    def test_a_person_reads_a_table_with_the_state(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "list"]) == 0
        printed = capsys.readouterr().out
        assert "kitchen" in printed
        assert BOARD in printed

    def test_outside_a_project_it_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["device", "list", "-o", "json"]) == 1
        assert "No MCUHome project found here." in _document(capsys)["errors"][0]["message"]


class TestDeviceValidate:
    """One pass, every problem, nothing written."""

    def test_a_configuration_that_resolves_answers_the_model(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "validate", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "file", "diagnostics", "model"]
        assert document["ok"] is True
        assert document["diagnostics"] == []
        assert document["model"]["device"]["name"] == "kitchen"

    def test_a_configuration_with_problems_is_a_negative_answer(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _break_device(device, "kitchen")
        assert main(["device", "validate", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        # The command's own document, not a refusal: the run happened.
        assert list(document) == ["ok", "file", "diagnostics", "model"]
        assert document["ok"] is False
        assert "errors" not in document
        assert document["diagnostics"]
        assert document["diagnostics"][0]["severity"] == "error"

    def test_the_document_is_the_same_whether_the_codes_are_shown(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "validate", "kitchen", "-o", "json"]) == 0
        masked = _document(capsys)
        assert main(["device", "validate", "kitchen", "--show-sensitive", "-o", "json"]) == 0
        assert _document(capsys) == masked, "--show-sensitive is a rendering decision"

    def test_a_person_sees_the_codes_only_when_they_ask(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entry = device / "devices" / "kitchen" / "main.yaml"
        codes = api.read_pairing(entry, project=api.read_project(device))
        assert codes is not None

        assert main(["device", "validate", "kitchen"]) == 0
        hidden = capsys.readouterr().out
        assert codes.manual_code not in hidden
        assert "hidden" in hidden
        assert str(codes.discriminator) in hidden, "the device broadcasts it in the clear"

        assert main(["device", "validate", "kitchen", "--show-sensitive"]) == 0
        assert codes.manual_code in capsys.readouterr().out

    def test_the_stream_starts_and_ends_with_one_result(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "validate", "kitchen", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert messages[0]["verb"] == "start"
        assert messages[0]["task"] == "device validate"
        assert [message["verb"] for message in messages].count("result") == 1
        assert messages[-1]["verb"] == "result"
        assert messages[-1]["document"]["ok"] is True

    def test_a_device_nobody_has_is_a_refusal(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "validate", "nope", "-o", "json"]) == 1
        assert set(_document(capsys)) == {"ok", "errors"}


class TestDeviceClean:
    """One directory or twenty, the shape is the same."""

    @staticmethod
    def _built(project: Path, name: str) -> Path:
        out_dir = project / "build" / name
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "firmware.bin").write_bytes(b"x")
        return out_dir

    def test_it_answers_one_result_per_directory(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = self._built(device, "kitchen")
        assert main(["device", "clean", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "cleaned"]
        assert document["ok"] is True
        assert list(document["cleaned"][0]) == ["device", "out_dir", "removed"]
        assert document["cleaned"][0]["device"] == "kitchen"
        assert document["cleaned"][0]["out_dir"] == str(out_dir)
        assert str(out_dir / "firmware.bin") in document["cleaned"][0]["removed"]
        assert not (out_dir / "firmware.bin").exists()
        assert out_dir.is_dir(), "the directory itself stays"

    def test_all_cleans_every_device_of_the_project(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api.create_device("bath", project=api.read_project(device), board=BOARD)
        self._built(device, "kitchen")
        self._built(device, "bath")
        assert main(["device", "clean", "--all", "-o", "json"]) == 0
        document = _document(capsys)
        assert [entry["device"] for entry in document["cleaned"]] == ["bath", "kitchen"]
        for entry in document["cleaned"]:
            assert entry["removed"]

    def test_a_directory_with_nothing_in_it_is_answered_not_refused(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "clean", "kitchen", "-o", "json"]) == 0
        assert _document(capsys)["cleaned"][0]["removed"] == []

    def test_a_device_and_all_together_are_a_wrong_invocation(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "clean", "kitchen", "--all", "-o", "json"]) == 2
        document = _document(capsys)
        assert document["errors"][0]["kind"] == "UsageError"
        assert "--all" in document["errors"][0]["message"]

    def test_neither_of_them_is_a_wrong_invocation(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "clean", "-o", "json"]) == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"

    def test_what_a_build_did_not_write_stays(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = self._built(device, "kitchen")
        (out_dir / "notes.txt").write_text("mine", encoding="utf-8")
        assert main(["device", "clean", "kitchen", "-o", "json"]) == 0
        assert (out_dir / "notes.txt").read_text(encoding="utf-8") == "mine"


class TestDeviceRename:
    """The folder, the name inside it, the secrets — and the build output goes."""

    def test_it_moves_the_device_and_answers_what_changed(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (device / "build" / "kitchen").mkdir(parents=True)
        (device / "build" / "kitchen" / "firmware.bin").write_bytes(b"x")
        assert main(["device", "rename", "kitchen", "--to", "bath", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "device", "to", "changed"]
        assert document["ok"] is True
        assert document["device"] == "kitchen"
        assert document["to"] == "bath"
        entry = device / "devices" / "bath" / "main.yaml"
        assert entry.is_file()
        assert not (device / "devices" / "kitchen").exists()
        assert "name: bath" in entry.read_text(encoding="utf-8")
        assert (device / "secrets" / "device" / "bath.yaml").is_file()
        assert not (device / "build" / "kitchen").exists(), "build output names the old device"

    def test_the_new_name_is_required(self, device: Path) -> None:
        assert main(["device", "rename", "kitchen"]) == 2

    def test_a_device_nobody_has_is_a_refusal(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "rename", "nope", "--to", "bath", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert "no device called" in document["errors"][0]["message"]


class TestDeviceDelete:
    """It removes work, so it asks — where there is somebody to ask."""

    def test_a_run_nobody_can_ask_deletes_what_it_was_told_to(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "delete", "kitchen", "--no-interactive", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "device", "kept_secrets", "removed"]
        assert document["kept_secrets"] is False
        assert not (device / "devices" / "kitchen").exists()
        assert not (device / "secrets" / "device" / "kitchen.yaml").exists()

    def test_keep_secrets_leaves_the_credentials_that_cannot_be_drawn_again(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            main(
                [
                    "device",
                    "delete",
                    "kitchen",
                    "--keep-secrets",
                    "--no-interactive",
                    "-o",
                    "json",
                ]
            )
            == 0
        )
        document = _document(capsys)
        assert document["kept_secrets"] is True
        assert (device / "secrets" / "device" / "kitchen.yaml").is_file()
        assert str(device / "secrets" / "device" / "kitchen.yaml") not in document["removed"]

    def test_an_interactive_run_is_asked_first_and_a_no_removes_nothing(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        asked = _answer(monkeypatch, "no")
        assert main(["device", "delete", "kitchen", "--interactive"]) == 1
        assert asked, "an interactive delete asks before it removes work"
        assert "Cancelled" in capsys.readouterr().out
        assert (device / "devices" / "kitchen" / "main.yaml").is_file()

    def test_a_yes_deletes(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _answer(monkeypatch, "yes")
        assert main(["device", "delete", "kitchen", "--interactive"]) == 0
        assert not (device / "devices" / "kitchen").exists()

    def test_force_says_the_same_thing_inside_an_interactive_run(
        self, device: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _never_asked(monkeypatch)
        assert main(["device", "delete", "kitchen", "--force", "--interactive"]) == 0
        assert not (device / "devices" / "kitchen").exists()


class TestPrintMatterPairing:
    """The one explicit ask for the codes — and it only reads."""

    def test_it_answers_the_credentials_document(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = (device / "devices" / "kitchen" / "main.yaml").read_bytes()
        assert main(["device", "print-matter-pairing", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "device", "pairing"]
        assert document["device"] == "kitchen"
        assert list(document["pairing"]) == [
            "discriminator",
            "passcode",
            "salt",
            "iterations",
            "test_credentials",
            "manual_code",
            "qr_payload",
        ]
        assert (device / "devices" / "kitchen" / "main.yaml").read_bytes() == before

    def test_a_device_with_no_credentials_answers_none(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api.create_device("kitchen", project=api.read_project(in_project), board=BOARD)
        assert main(["device", "print-matter-pairing", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["pairing"] is None
        assert document["ok"] is True

    def test_a_person_reads_the_codes_without_asking_twice(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        codes = api.read_pairing(
            device / "devices" / "kitchen" / "main.yaml", project=api.read_project(device)
        )
        assert codes is not None
        assert main(["device", "print-matter-pairing", "kitchen"]) == 0
        printed = capsys.readouterr().out
        assert codes.manual_code in printed
        assert codes.qr_payload in printed


class TestCreateMatterPairing:
    """Drawing them is its own act, and replacing them is asked about."""

    @pytest.fixture
    def fresh(self, in_project: Path) -> Path:
        """A device with no credentials yet."""
        api.create_device("kitchen", project=api.read_project(in_project), board=BOARD)
        return in_project

    def test_it_draws_the_credentials_and_says_where_they_went(
        self, fresh: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "create-matter-pairing", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "device", "entry", "secrets_file", "pairing", "replaced"]
        assert document["device"] == "kitchen"
        assert document["replaced"] is False
        assert Path(document["secrets_file"]).is_file()
        assert document["pairing"]["manual_code"]
        assert "!secret" in Path(document["entry"]).read_text(encoding="utf-8")

    def test_the_document_is_the_one_print_matter_pairing_answers(
        self, fresh: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "create-matter-pairing", "kitchen", "-o", "json"]) == 0
        drawn = _document(capsys)["pairing"]
        assert main(["device", "print-matter-pairing", "kitchen", "-o", "json"]) == 0
        assert _document(capsys)["pairing"] == drawn

    def test_credentials_that_are_already_there_are_refused(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = (device / "devices" / "kitchen" / "main.yaml").read_bytes()
        assert main(["device", "create-matter-pairing", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert "already has commissioning credentials" in document["errors"][0]["message"]
        assert (device / "devices" / "kitchen" / "main.yaml").read_bytes() == before

    def test_force_replaces_them(self, device: Path, capsys: pytest.CaptureFixture[str]) -> None:
        old = api.read_pairing(
            device / "devices" / "kitchen" / "main.yaml", project=api.read_project(device)
        )
        assert old is not None
        assert main(["device", "create-matter-pairing", "kitchen", "--force", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["replaced"] is True
        assert document["pairing"]["passcode"] != old.passcode

    def test_an_interactive_replacement_is_asked_first(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old = (device / "devices" / "kitchen" / "main.yaml").read_bytes()
        asked = _answer(monkeypatch, "no")
        assert main(["device", "create-matter-pairing", "kitchen", "--force", "--interactive"]) == 1
        assert asked, "a commissioned device stops being reachable"
        assert "Cancelled" in capsys.readouterr().out
        assert (device / "devices" / "kitchen" / "main.yaml").read_bytes() == old

    def test_a_first_draw_is_not_asked_about(
        self, fresh: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # There is nothing to lose, so there is nothing to ask.
        _never_asked(monkeypatch)
        assert main(["device", "create-matter-pairing", "kitchen", "--force", "--interactive"]) == 0
