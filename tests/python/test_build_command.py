# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome device build``, in all three modes.

A firmware build takes minutes, a container and a toolchain, so the one
call that does it is stubbed at the **api seam** — the name the command
line calls — by :class:`FakeBuild`, which drives the callbacks the way
the api reference says a build drives them and delivers the files a build
delivers. Everything around it is real: the project, the device, the
signing key, the configuration ladder, the lock, the report reader and
the footprint.

What the fake cannot prove is what a real build proves — that a build
environment exists, that west compiles, that the delivered image is
flashable. That is the end-to-end run, not this suite.
"""

from __future__ import annotations

import io
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest
from mcuhome.workbench import api

from mcuhome.cli import buildcommand
from mcuhome.cli.main import main

#: The build report a build environment delivers beside the unsigned
#: image, in the shape the build-environment specification states — its
#: format, not MCUHome's, which is why the version is a literal here.
REPORT = {
    "report": 1,
    "signing": {
        "signature_type": "ecdsa-p256",
        "arguments": {"version": "1.2.3+4", "header-size": 512, "align": 4, "slot-size": 933888},
    },
    "memory": [
        {"image": "app", "region": "FLASH", "used": 524288, "total": 1048576, "percent": 50.0},
        {"image": "app", "region": "RAM", "used": 65536, "total": 262144, "percent": 25.0},
        {"image": "app", "region": "IDT_LIST", "used": 0, "total": 32768, "percent": 0.0},
    ],
}

#: What the fake's compile step prints, as west and the compiler would.
LINES = ("-- west build: building application", "[1/2] Building C object app.c.obj")


class FakeBuild:
    """One build, without a build: the callbacks and the delivery.

    The order is the reference's: a stage arrives once when it starts and
    a second time with facts where it has something to state, the
    predicate is polled while the work runs, and only a build that
    succeeded delivers.
    """

    def __init__(
        self,
        *,
        ok: bool = True,
        waits: int = 0,
        interrupts: int = 0,
        diagnostics: tuple[api.Diagnostic, ...] = (),
    ) -> None:
        self.ok = ok
        self.waits = waits
        self.interrupts = interrupts
        self.diagnostics = diagnostics
        self.request: api.BuildRequest | None = None
        self.target: Any = None
        self.polls = 0

    async def __call__(self, request: api.BuildRequest, *, target: Any = None) -> api.BuildResult:
        self.request = request
        self.target = target
        for attempt in range(1, self.waits + 1):
            request.on_wait(
                api.SeatWait(retry_after=2.0, waited=2.0 * (attempt - 1), attempt=attempt)
            )
        request.on_step("context")
        request.on_step("context", id="sha256:0123456789abcdef", sdk="0.2.1", files=12, patches=[])
        request.on_step("environment")
        request.on_step(
            "environment",
            build_environment="ghcr.io/mcu-home/build-environment@sha256:fedcba9876543210",
            zephyr="4.4.0",
            found_under="0.2.1-r1",
            fetched=False,
        )
        request.on_step("compile")
        for line in LINES:
            request.on_line(line)
        for _ in range(self.interrupts):
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.01)
        for _ in range(50):
            self.polls += 1
            if request.should_stop is not None and request.should_stop():
                return self._result(request, ok=False, stopped=True)
            time.sleep(0.001)
        if not self.ok:
            return self._result(request, ok=False, stopped=False)
        deliver(request.out_dir)
        return self._result(request, ok=True, stopped=False)

    def _result(self, request: api.BuildRequest, *, ok: bool, stopped: bool) -> api.BuildResult:
        artifacts = (
            (
                api.Artifact(root=api.ROOT_OUT, path="firmware.bin", role="firmware", sha256="ab"),
                api.Artifact(
                    root=api.ROOT_OUT, path="build-report.json", role="report", sha256="cd"
                ),
            )
            if ok
            else ()
        )
        return api.BuildResult(
            ok=ok,
            target=api.TARGET_REMOTE if request.builder.target == api.TARGET_REMOTE else "local",
            device=request.model.device.name,
            context_id="sha256:0123456789abcdef",
            artifacts=artifacts,
            out_dir=request.out_dir,
            report=api.BUILD_REPORT_FILE,
            container_image="",
            stopped=stopped,
            diagnostics=self.diagnostics,
        )


def deliver(out_dir: Path) -> None:
    """What a build that succeeded leaves at the top of its directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "firmware.bin").write_bytes(bytes(1024))
    (out_dir / api.BUILD_REPORT_FILE).write_text(json.dumps(REPORT), encoding="utf-8")


class FakeSigning:
    """The host-side signing step, without the signing program."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.model: Any = None

    def __call__(self, out_dir: Path, **kwargs: Any) -> api.SigningResult:
        self.model = kwargs.get("model")
        signed = out_dir / "firmware.signed.bin"
        signed.write_bytes(bytes(1024))
        return api.SigningResult(
            ok=self.ok,
            out_dir=out_dir,
            report_path=out_dir / api.BUILD_REPORT_FILE,
            key=Path(kwargs.get("key") or out_dir / "key.pem"),
            signed=(api.SignedArtifact(format="bin", path=signed),),
            ota=None,
        )


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> FakeBuild:
    """``build_firmware`` replaced at the seam the command line calls."""
    fake = FakeBuild()
    monkeypatch.setattr(api, "build_firmware", fake)
    return fake


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> FakeSigning:
    """``sign_firmware`` replaced: the signing program is a child process."""
    fake = FakeSigning()
    monkeypatch.setattr(api, "sign_firmware", fake)
    return fake


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


class TestTheDocument:
    """``{ok, build, signing, footprint}`` and nothing beside it."""

    def test_a_build_that_delivered_and_signed(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "build", "signing", "footprint"]
        assert document["ok"] is True
        assert document["build"]["out_dir"] == str(device / "build" / "kitchen")
        assert [entry["path"] for entry in document["build"]["artifacts"]] == [
            "firmware.bin",
            "build-report.json",
        ]
        assert document["signing"]["signed"][0]["format"] == "bin"
        # The device's own model reaches signing, which is what the OTA
        # image needs and what a client must never assemble itself.
        assert signed.model is not None and signed.model.device.name == "kitchen"

    def test_the_footprint_is_what_the_report_measured(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        footprint = _document(capsys)["footprint"]
        assert footprint == [
            {"image": "app", "region": "FLASH", "used": 524288, "total": 1048576},
            {"image": "app", "region": "RAM", "used": 65536, "total": 262144},
            {"image": "app", "region": "IDT_LIST", "used": 0, "total": 32768},
        ]

    def test_without_signing_the_key_is_the_document_is_null(
        self, device: Path, built: FakeBuild, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "build", "kitchen", "--no-sign", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert document["signing"] is None

    def test_the_delivery_lands_at_the_top_of_the_build_directory(
        self, device: Path, built: FakeBuild, signed: FakeSigning
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        out_dir = device / "build" / "kitchen"
        assert (out_dir / "firmware.bin").is_file()
        assert (out_dir / api.BUILD_REPORT_FILE).is_file()

    def test_out_dir_is_taken_where_it_is_stated(
        self, device: Path, tmp_path: Path, built: FakeBuild, capsys: pytest.CaptureFixture[str]
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        code = main(
            ["device", "build", "kitchen", "--no-sign", "--out-dir", str(elsewhere), "-o", "json"]
        )
        assert code == 0
        assert _document(capsys)["build"]["out_dir"] == str(elsewhere)


class TestTheStream:
    """The verbs, the stages, and exactly one result."""

    def test_start_names_the_steps_this_target_will_report(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        options = api.resolve_build_options(
            api.resolve_settings(project=api.read_project(device), env={}, args=())
        )
        assert messages[0] == {
            "verb": "start",
            "task": "device build",
            "steps": list(api.build_steps(target="local", options=options)),
        }

    def test_a_stage_arrives_once_and_a_second_time_with_its_facts(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        stages = [message["stage"] for message in messages if message["verb"] == "progress"]
        assert stages == ["context", "context", "environment", "environment", "compile"]
        first = next(message for message in messages if message.get("stage") == "context")
        # The first message carries no facts; the second is where they are.
        assert set(first) == {"verb", "stage"}
        second = [message for message in messages if message.get("stage") == "context"][1]
        assert second["sdk"] == "0.2.1"

    def test_the_run_ends_with_exactly_one_result(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages].count("result") == 1
        assert messages[-1]["verb"] == "result"
        assert messages[-1]["document"]["ok"] is True

    def test_a_refused_turn_is_a_wait_and_never_a_stage(
        self,
        device: Path,
        monkeypatch: pytest.MonkeyPatch,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(api, "build_firmware", FakeBuild(waits=2))
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 0
        waits = [message for message in _stream(capsys) if message["verb"] == "wait"]
        assert [wait["attempt"] for wait in waits] == [1, 2]
        assert set(waits[0]) == {"verb", "retry_after", "waited", "attempt"}

    def test_the_build_log_is_not_in_the_stream(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 0
        captured = capsys.readouterr()
        assert LINES[0] not in captured.out
        assert LINES[0] in captured.err


class TestTheLog:
    """``build.log`` is written in every mode."""

    @pytest.mark.parametrize("mode", ["human", "json", "json-stream"])
    def test_every_line_the_build_produced_is_in_the_file(
        self, device: Path, built: FakeBuild, signed: FakeSigning, mode: str
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", mode]) == 0
        log = (device / "build" / "kitchen" / "build.log").read_text(encoding="utf-8")
        assert log.splitlines() == list(LINES)

    def test_the_lines_leave_on_stderr_and_stdout_keeps_the_document(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["ok"] is True
        assert LINES[1] in captured.err


class TestSayingNo:
    """A build that ran and failed is the command's own document."""

    def test_a_failed_build_is_a_negative_answer_and_not_a_refusal(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        finding = api.Diagnostic(
            severity=api.SEVERITY_ERROR, message="the compile step exited 1", kind="BuildError"
        )
        monkeypatch.setattr(api, "build_firmware", FakeBuild(ok=False, diagnostics=(finding,)))
        assert main(["device", "build", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        assert list(document) == ["ok", "build", "signing", "footprint"]
        assert document["ok"] is False
        assert document["build"]["diagnostics"][0]["message"] == "the compile step exited 1"
        assert "errors" not in document

    def test_a_person_reads_the_narration_where_the_summary_would_have_been(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A negative answer is rendered on stdout, like a positive one.

        Whatever the answer, it is the command's own document being
        rendered — stderr carries what happened while the run was going
        (the build log, the live frame) and rendered refusals, and a
        build that ran and failed is neither.
        """
        finding = api.Diagnostic(
            severity=api.SEVERITY_ERROR, message="the compile step exited 1", kind="BuildError"
        )
        monkeypatch.setattr(api, "build_firmware", FakeBuild(ok=False, diagnostics=(finding,)))
        assert main(["device", "build", "kitchen", "--color", "never"]) == 1
        printed = capsys.readouterr()
        assert "The firmware did not build." in printed.out
        assert "the compile step exited 1" in printed.out
        assert "The firmware did not build." not in printed.err

    def test_a_stopped_build_says_so_on_stdout_too(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(api, "build_firmware", FakeBuild(interrupts=1))
        assert main(["device", "build", "kitchen", "--color", "never"]) == 1
        printed = capsys.readouterr()
        assert "The build was ended before it produced anything." in printed.out
        # The bound, though, is stated while the run is still going.
        assert "Stopping the build" in printed.err

    def test_a_missing_key_is_the_workbench_refusal_that_names_the_command(
        self, in_project: Path, built: FakeBuild, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project = api.read_project(in_project)
        new = api.create_device("kitchen", project=project, board="nrf7002dk/nrf5340/cpuapp")
        api.create_pairing(Path(new.entry), project=project)
        assert main(["device", "build", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert "mcuhome signing create-key" in document["errors"][0]["hint"]
        # Nothing ran: the refusal is before the build, not around it.
        assert built.request is None


class TestTheInvocationsItRefuses:
    """The rules of the flags, checked before anything runs."""

    def test_a_device_and_a_model_are_two_inputs(self, device: Path) -> None:
        assert main(["device", "build", "kitchen", "--model", "model.json"]) == 2

    def test_neither_is_none(self, device: Path) -> None:
        assert main(["device", "build"]) == 2

    def test_a_public_key_without_no_sign(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        key = device / "public.pem"
        resolved = api.resolve_signing_key(env=dict(os.environ), project=api.read_project(device))
        key.write_text(api.public_key_pem(resolved.pem), encoding="utf-8")
        code = main(["device", "build", "kitchen", "--public-key", str(key), "-o", "json"])
        assert code == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"

    def test_a_public_key_that_is_the_private_half(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        key = api.resolve_signing_key(env=dict(os.environ), project=api.read_project(device))
        code = main(
            ["device", "build", "kitchen", "--no-sign", "--public-key", str(key.path), "-o", "json"]
        )
        assert code == 2
        document = _document(capsys)
        assert document["errors"][0]["kind"] == "UsageError"
        assert "private key" in document["errors"][0]["message"]

    def test_a_token_that_names_no_server(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["device", "build", "kitchen", "--build-server-token", "t", "-o", "json"])
        assert code == 2
        assert "--build-server" in _document(capsys)["errors"][0]["message"]

    def test_an_image_a_subprocess_build_cannot_honour_is_the_workbench_refusal(
        self, device: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No stub here: the refusal happens in the dispatch, before the
        # lock and before any work — and it is the workbench's rule, so
        # the command line does not state a second one beside it.
        code = main(
            [
                "device",
                "build",
                "kitchen",
                "--build-mode",
                "subprocess",
                "--container-image",
                "ghcr.io/mcu-home/build-environment:0.2.1-r1",
                "-o",
                "json",
            ]
        )
        assert code == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert "container" in document["errors"][0]["message"]


class TestWhereItRuns:
    """The ladder, and the credential that never stands in a process list."""

    def test_a_named_build_server_is_remote_by_statement(
        self, device: Path, built: FakeBuild, signed: FakeSigning
    ) -> None:
        code = main(
            ["device", "build", "kitchen", "--build-server", "builds.example.org", "-o", "json"]
        )
        assert code == 0
        assert built.request.builder.target == api.TARGET_REMOTE
        assert built.request.builder.server == "builds.example.org"
        assert built.target == api.TARGET_REMOTE

    def test_the_token_is_read_from_standard_input(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("s3cret\n"))
        code = main(
            [
                "device",
                "build",
                "kitchen",
                "--build-server",
                "builds.example.org",
                "--build-server-token",
                "-",
                "-o",
                "json",
            ]
        )
        assert code == 0
        assert built.request.builder.token == "s3cret"

    def test_nothing_piped_in_is_a_wrong_invocation_rather_than_a_wait(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A run sitting on a value nobody is going to type looks like a
        # run that hung, which is the one thing this must not do.
        class Terminal(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr("sys.stdin", Terminal())
        code = main(
            [
                "device",
                "build",
                "kitchen",
                "--build-server",
                "builds.example.org",
                "--build-server-token",
                "-",
                "-o",
                "json",
            ]
        )
        assert code == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"

    def test_an_empty_read_is_not_an_empty_credential(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
        code = main(
            [
                "device",
                "build",
                "kitchen",
                "--build-server",
                "builds.example.org",
                "--build-server-token",
                "-",
                "-o",
                "json",
            ]
        )
        assert code == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"

    def test_a_stated_target_beats_a_configured_builder(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["config", "set", "builder.shed.target", "remote"]) == 0
        assert main(["config", "set", "builder.shed.server", "shed.example.org"]) == 0
        assert main(["config", "set", "build.builder", "shed"]) == 0
        capsys.readouterr()
        code = main(["device", "build", "kitchen", "--build-target", "local", "-o", "json"])
        assert code == 0
        assert built.request.builder.target == api.TARGET_LOCAL
        assert built.request.builder.builder is None

    def test_a_configured_builder_answers_where_nothing_was_stated(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["config", "set", "builder.shed.target", "remote"]) == 0
        assert main(["config", "set", "builder.shed.server", "shed.example.org"]) == 0
        assert main(["config", "set", "build.builder", "shed"]) == 0
        capsys.readouterr()
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        assert built.request.builder.builder.name == "shed"
        assert built.request.builder.server == "shed.example.org"

    def test_the_image_reaches_the_request_as_the_field_it_is(
        self, device: Path, built: FakeBuild, signed: FakeSigning
    ) -> None:
        code = main(
            [
                "device",
                "build",
                "kitchen",
                "--container-image",
                "ghcr.io/mcu-home/build-environment:0.2.1-r1",
                "-o",
                "json",
            ]
        )
        assert code == 0
        assert built.request.container_image == "ghcr.io/mcu-home/build-environment:0.2.1-r1"


class TestStopping:
    """``Ctrl-C`` reaches the predicate, and the bound is stated once."""

    def test_a_stop_is_heard_and_the_build_answers_that_it_was_stopped(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeBuild(interrupts=1)
        monkeypatch.setattr(api, "build_firmware", fake)
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 1
        messages = _stream(capsys)
        verbs = [message["verb"] for message in messages]
        assert "stopping" in verbs
        assert verbs[-1] == "result"
        stopping = next(message for message in messages if message["verb"] == "stopping")
        assert stopping["seconds"] == api.resolve_shutdown_seconds(cancel_grace_seconds=0)
        document = messages[-1]["document"]
        assert document["ok"] is False
        assert document["build"]["stopped"] is True
        assert document["build"]["artifacts"] == []
        assert document["signing"] is None

    def test_the_stop_reaches_the_predicate_rather_than_the_process(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeBuild(interrupts=1)
        monkeypatch.setattr(api, "build_firmware", fake)
        assert main(["device", "build", "kitchen", "-o", "json"]) == 1
        capsys.readouterr()
        # The build was asked, and it answered rather than being killed.
        assert fake.polls >= 1
        assert fake.request.should_stop is not None

    def test_a_second_press_leaves_whatever_it_leaves(
        self, device: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class NeverStops(FakeBuild):
            async def __call__(self, request: api.BuildRequest, *, target: Any = None) -> Any:
                self.request = request
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.01)
                os.kill(os.getpid(), signal.SIGINT)
                for _ in range(1000):  # pragma: no cover - the second press wins
                    time.sleep(0.001)
                raise AssertionError("the second press did not leave")

        monkeypatch.setattr(api, "build_firmware", NeverStops())
        assert main(["device", "build", "kitchen", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "Interrupted"

    def test_the_handlers_are_put_back_when_the_build_is_over(
        self, device: Path, built: FakeBuild, signed: FakeSigning
    ) -> None:
        before = signal.getsignal(signal.SIGINT)
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        assert signal.getsignal(signal.SIGINT) is before


class TestTheReportItCannotRead:
    """A build that delivered is answered whatever its report turns out to be."""

    @staticmethod
    def _break(device: Path, *, text: str | None) -> Path:
        """Replace what the fake delivers with a report nobody can read."""
        out_dir = device / "build" / "kitchen"

        async def fake(request: api.BuildRequest, *, target: Any = None) -> api.BuildResult:
            built = FakeBuild()
            result = await built(request, target=target)
            report = request.out_dir / api.BUILD_REPORT_FILE
            if text is None:
                report.unlink()
            else:
                report.write_text(text, encoding="utf-8")
            return result

        return out_dir, fake  # type: ignore[return-value]

    def test_a_missing_report_is_a_finding_and_not_a_refusal(
        self,
        device: Path,
        monkeypatch: pytest.MonkeyPatch,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out_dir, fake = self._break(device, text=None)
        monkeypatch.setattr(api, "build_firmware", fake)
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        captured = capsys.readouterr()
        document = json.loads(captured.out)
        # The build ran, delivered and was signed; only the footprint is
        # missing, and the document still names the report.
        assert list(document) == ["ok", "build", "signing", "footprint"]
        assert document["ok"] is True
        assert document["footprint"] == []
        assert document["build"]["report"] == api.BUILD_REPORT_FILE
        assert document["signing"]["ok"] is True
        assert str(out_dir / api.BUILD_REPORT_FILE) in captured.err

    def test_a_report_of_another_version_is_a_finding_too(
        self,
        device: Path,
        monkeypatch: pytest.MonkeyPatch,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _out_dir, fake = self._break(device, text=json.dumps({"report": 99, "signing": {}}))
        monkeypatch.setattr(api, "build_firmware", fake)
        assert main(["device", "build", "kitchen", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert document["footprint"] == []

    def test_it_arrives_as_one_diagnostic_message_in_the_stream(
        self,
        device: Path,
        monkeypatch: pytest.MonkeyPatch,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _out_dir, fake = self._break(device, text=None)
        monkeypatch.setattr(api, "build_firmware", fake)
        assert main(["device", "build", "kitchen", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        findings = [message for message in messages if message["verb"] == "diagnostic"]
        assert len(findings) == 1
        assert findings[0]["diagnostic"]["severity"] == "error"
        assert findings[0]["diagnostic"]["kind"] == "BuildError"
        assert messages[-1]["verb"] == "result"
        assert messages[-1]["document"]["footprint"] == []

    def test_a_person_reads_it_as_an_error_on_stderr(
        self,
        device: Path,
        monkeypatch: pytest.MonkeyPatch,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _out_dir, fake = self._break(device, text=None)
        monkeypatch.setattr(api, "build_firmware", fake)
        assert main(["device", "build", "kitchen", "--color", "never"]) == 0
        captured = capsys.readouterr()
        # A finding carries its severity, and the line says which it is.
        assert "Error: MCUHome cannot read the build report" in captured.err
        assert "Warning:" not in captured.err
        assert "Built kitchen." in captured.out


class TestAPersonReadsIt:
    """The human rendering, which nobody parses."""

    def test_it_names_the_device_the_artifacts_and_the_footprint(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert "Built kitchen." in printed
        assert "firmware.bin" in printed
        assert "Memory" in printed
        assert "FLASH" in printed
        # A region that is not memory on the device is not in the table.
        assert "IDT_LIST" not in printed

    def test_the_header_says_which_half_of_the_key_was_read(
        self, device: Path, built: FakeBuild, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The whole difference between the two ways to run this.
        assert main(["device", "build", "kitchen", "--no-sign", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert "signing key" in printed
        assert "only its public half reaches the build" in printed

        public = device / "public.pem"
        resolved = api.resolve_signing_key(env=dict(os.environ), project=api.read_project(device))
        public.write_text(api.public_key_pem(resolved.pem), encoding="utf-8")
        code = main(
            [
                "device",
                "build",
                "kitchen",
                "--no-sign",
                "--public-key",
                str(public),
                "--color",
                "never",
            ]
        )
        assert code == 0
        printed = capsys.readouterr().out
        assert "public key" in printed
        assert "no private key is anywhere near this build" in printed

    def test_verbose_prints_the_commands_signing_will_run(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # `-v` asks the real `plan_signing`, which resolves the key and
        # the signing program and writes nothing.
        assert main(["device", "build", "kitchen", "-v", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert "Signing" in printed
        assert "imgtool" in printed
        assert "sign" in printed
        assert "--slot-size" in printed

    def test_without_verbose_no_command_is_printed(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "--color", "never"]) == 0
        assert "imgtool" not in capsys.readouterr().out

    def test_verbose_in_a_machine_mode_changes_no_document(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["device", "build", "kitchen", "-v", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "build", "signing", "footprint"]

    def test_an_unsigned_build_says_what_to_do_with_it(
        self, device: Path, built: FakeBuild, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["device", "build", "kitchen", "--no-sign", "--color", "never"]) == 0
        printed = capsys.readouterr().out
        assert "UNSIGNED" in printed
        assert "mcuhome device sign-firmware kitchen" in printed


class TestAModelBuild:
    """``--model`` builds what another machine resolved."""

    def test_it_touches_no_project_and_builds_beside_the_working_directory(
        self,
        device: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        built: FakeBuild,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        project = api.read_project(device)
        model = api.load_model(project.device_file("kitchen"), project=project)
        written = tmp_path / "model.json"
        written.write_text(model.to_json(), encoding="utf-8")
        here = tmp_path / "elsewhere"
        here.mkdir()
        monkeypatch.chdir(here)
        key = here / "public.pem"
        key.write_text(
            api.public_key_pem(api.resolve_signing_key(env=dict(os.environ), project=project).pem),
            encoding="utf-8",
        )
        code = main(
            [
                "device",
                "build",
                "--model",
                str(written),
                "--no-sign",
                "--public-key",
                str(key),
                "-o",
                "json",
            ]
        )
        assert code == 0
        assert _document(capsys)["build"]["out_dir"] == str(here / "build" / "kitchen")
        assert built.request.project_root is None


def test_the_command_is_no_longer_a_placeholder() -> None:
    assert buildcommand.build.__module__ == "mcuhome.cli.buildcommand"
