# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``device info``, ``generate-application`` and ``sign-firmware``.

Everything here is the real workbench against a real project. Two seams
are stubbed and no more: the code generator, which is a separate package
this repository does not depend on, and the signing program, which is a
child process. ``--dry-run`` needs neither — a plan is decided before
anything runs — so the one test that proves the disk is untouched proves
it against the real call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcuhome.workbench import api
from test_build_command import FakeSigning, deliver

from mcuhome.cli.main import main


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Every file under *directory* with its bytes, the lock aside.

    ``.mcuhome-build.lock`` is the lock the command is holding while it
    looks — the reference says it holds the directory for the whole run,
    dry or not — and it is the one file a run that changed nothing still
    leaves.
    """
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != api.BUILD_LOCK_FILE
    }


class TestDeviceInfo:
    """One device in full, and a negative answer when it does not resolve."""

    def test_it_answers_the_device_its_validation_and_its_build(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        deliver(device / "build" / "kitchen")
        assert main(["device", "info", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "device", "validation", "build", "footprint"]
        assert document["ok"] is True
        assert document["device"] == "kitchen"
        assert list(document["validation"]) == ["ok", "file", "diagnostics", "model"]
        assert document["build"]["out_dir"] == str(device / "build" / "kitchen")
        assert document["footprint"][0]["region"] == "FLASH"

    def test_a_device_with_no_build_says_so_rather_than_refusing(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "info", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["build"] is None
        assert document["footprint"] == []

    def test_an_invalid_configuration_is_a_negative_answer(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        file = device / "devices" / "kitchen" / "main.yaml"
        file.write_text(file.read_text(encoding="utf-8") + "\nnonsense: 1\n", encoding="utf-8")
        assert main(["device", "info", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        # The command's own document with `ok: false` and its findings in
        # `diagnostics` — never a refusal document.
        assert list(document) == ["ok", "device", "validation", "build", "footprint"]
        assert document["ok"] is False
        assert "errors" not in document
        assert document["validation"]["diagnostics"][0]["severity"] == "error"

    def test_a_device_nobody_has_is_a_refusal(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "info", "cellar", "-o", "json"]) == 1
        assert set(_document(capsys)) == {"ok", "errors"}

    def test_the_stream_starts_and_ends(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "info", "kitchen", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert messages[0] == {"verb": "start", "task": "device info"}
        assert [message["verb"] for message in messages].count("result") == 1

    def test_a_person_reads_the_device_and_its_build(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        deliver(device / "build" / "kitchen")
        assert main(["device", "info", "kitchen", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert "kitchen" in printed
        assert "Build" in printed
        assert "FLASH" in printed


class TestGenerateApplication:
    """The standalone tree, and nothing else."""

    @pytest.fixture
    def generated(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        """``generate_application`` without the code generator package."""
        written: list[Path] = []

        def generate(model: Any, *, out_dir: Path) -> api.GenerationResult:
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in ("CMakeLists.txt", "prj.conf"):
                (out_dir / name).write_text("", encoding="utf-8")
                written.append(out_dir / name)
            return api.GenerationResult(
                device=model.device.name, out_dir=out_dir, files=("CMakeLists.txt", "prj.conf")
            )

        monkeypatch.setattr(api, "generate_application", generate)
        return written

    def test_it_answers_the_device_the_directory_and_the_files(
        self,
        device: Path,
        tmp_path: Path,
        generated: list[Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out_dir = tmp_path / "tree"
        code = main(
            ["device", "generate-application", "kitchen", "--out-dir", str(out_dir), "-o", "json"]
        )
        assert code == 0
        document = _document(capsys)
        assert list(document) == ["ok", "device", "out_dir", "files"]
        assert document["device"] == "kitchen"
        assert document["out_dir"] == str(out_dir)
        assert document["files"] == ["CMakeLists.txt", "prj.conf"]

    def test_the_directory_is_required(self, device: Path) -> None:
        assert main(["device", "generate-application", "kitchen"]) == 2

    def test_a_device_that_does_not_resolve_is_a_refusal(
        self, device: Path, tmp_path: Path, generated: list[Path]
    ) -> None:
        assert (
            main(["device", "generate-application", "cellar", "--out-dir", str(tmp_path / "x")])
            == 1
        )


class TestSignFirmware:
    """The signature where the private key is."""

    def test_it_answers_the_signing_result(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        deliver(device / "build" / "kitchen")
        monkeypatch.setattr(api, "sign_firmware", FakeSigning())
        assert main(["device", "sign-firmware", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "out_dir", "report_path", "key", "signed", "ota"]
        assert document["ok"] is True
        assert document["out_dir"] == str(device / "build" / "kitchen")

    def test_a_signing_that_says_no_is_a_negative_answer(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        deliver(device / "build" / "kitchen")
        monkeypatch.setattr(api, "sign_firmware", FakeSigning(ok=False))
        assert main(["device", "sign-firmware", "kitchen", "-o", "json"]) == 1
        assert _document(capsys)["ok"] is False

    def test_out_dir_names_a_build_that_is_not_the_device_s_own(
        self,
        device: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        elsewhere = tmp_path / "delivered"
        deliver(elsewhere)
        monkeypatch.setattr(api, "sign_firmware", FakeSigning())
        code = main(
            ["device", "sign-firmware", "kitchen", "--out-dir", str(elsewhere), "-o", "json"]
        )
        assert code == 0
        assert _document(capsys)["out_dir"] == str(elsewhere)

    def test_a_dry_run_prints_the_plan_and_touches_nothing(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "build" / "kitchen"
        deliver(out_dir)
        # A signature that is already there is what `removes` names, and
        # what a dry run has to leave exactly as it found it.
        (out_dir / "firmware.signed.bin").write_bytes(b"old")
        before = _snapshot(out_dir)
        assert main(["device", "sign-firmware", "kitchen", "--dry-run", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == [
            "ok",
            "dry_run",
            "out_dir",
            "report_path",
            "key",
            "commands",
            "outputs",
            "removes",
        ]
        assert document["dry_run"] is True
        assert str(out_dir / "firmware.signed.bin") in document["outputs"]
        assert str(out_dir / "firmware.signed.bin") in document["removes"]
        assert _snapshot(out_dir) == before

    def test_a_person_reads_the_plan_with_what_it_would_remove(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "build" / "kitchen"
        deliver(out_dir)
        (out_dir / "firmware.signed.bin").write_bytes(b"old")
        assert main(["device", "sign-firmware", "kitchen", "--dry-run", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert "imgtool" in printed
        assert "Removes first" in printed
        assert "Nothing was changed" in printed

    def test_a_directory_that_holds_no_build_is_a_refusal(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "sign-firmware", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "BuildError"

    def test_the_build_directory_is_held_under_the_sign_operation(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "build" / "kitchen"
        deliver(out_dir)
        held: list[bool] = []

        def signing(directory: Path, **kwargs: Any) -> api.SigningResult:
            held.append(api.is_busy(directory))
            return FakeSigning()(directory, **kwargs)

        monkeypatch.setattr(api, "sign_firmware", signing)
        assert main(["device", "sign-firmware", "kitchen", "-o", "json"]) == 0
        capsys.readouterr()
        assert held == [True]
        # And released again when the command is over.
        assert api.is_busy(out_dir) is False


class TestTheRetiredPositional:
    """A build directory was the positional once; the device is now."""

    def test_a_build_directory_is_refused_with_out_dir(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "build" / "kitchen"
        deliver(out_dir)
        assert main(["device", "sign-firmware", str(out_dir), "-o", "json"]) == 2
        document = _document(capsys)
        assert document["errors"][0]["kind"] == "RetiredSpelling"
        assert "a build directory" in document["errors"][0]["message"]
        assert "--out-dir" in document["errors"][0]["hint"]
        assert str(out_dir) in document["errors"][0]["hint"]

    def test_a_build_report_file_is_refused_the_same_way(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "build" / "kitchen"
        deliver(out_dir)
        report = out_dir / api.BUILD_REPORT_FILE
        assert main(["device", "sign-firmware", str(report), "-o", "json"]) == 2
        document = _document(capsys)
        assert document["errors"][0]["kind"] == "RetiredSpelling"
        # Each form is named as what it is, and the hint points at the
        # directory either way.
        assert "a build report" in document["errors"][0]["message"]
        assert str(out_dir) in document["errors"][0]["hint"]

    def test_a_device_folder_is_not_a_retired_spelling(
        self, device: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deliver(device / "build" / "kitchen")
        monkeypatch.setattr(api, "sign_firmware", FakeSigning())
        assert main(["device", "sign-firmware", "devices/kitchen", "-o", "json"]) == 0
