# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome context``, in all three modes.

Writing a real context resolves an SDK release, a build workspace and a
build tools package — from the operator directories or, failing those,
from a registry over the network — and unpacks the first of them. That
is minutes and a machine this suite does not have, so the three calls
that reach for packages or read a context's files are stubbed at the
**api seam**: :class:`FakeContext` writes what a context directory holds
for the purposes of these tests and records what it was handed.

What the fakes cannot prove is what a real run proves — that the pins
resolve, that the bytes verify, that the identity matches the one a
build server computes from the same directory. That is the end-to-end
run, not this suite. What is real here is everything the command line
owns: the device and the key it resolves, the scratch directory it picks
and removes, the documents it prints and the exit codes it answers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcuhome.workbench import api

from mcuhome.cli.main import main

#: A locked context's facts, in the shape ``read_context_facts`` answers
#: them — display material, every key optional to a consumer.
FACTS = {
    "id": "sha256:0123456789abcdef",
    "sdk": "0.2.1",
    "sdk_sha256": "ab" * 32,
    "build_environment": "mcuhome-build-workspace 0.2.1 + mcuhome-build-tools 0.2.1",
    "board": "nrf7002dk/nrf5340/cpuapp",
    "files": 12,
    "patches": ["zephyr/0001-fix.patch"],
}


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def _manifest(identity: str) -> api.ContextManifest:
    """One manifest, as a locked context carries it."""
    pin = api.PackagePin
    return api.ContextManifest(
        sdk=api.SdkPin(constraint="~=0.2", version="0.2.1", url="https://x/sdk", sha256="ab" * 32),
        build_environment=api.EnvironmentPin(
            workspace=pin(
                name="mcuhome-build-workspace", version="0.2.1", url="u", sha256="cd" * 32
            ),
            tools=pin(name="mcuhome-build-tools", version="0.2.1", url="u", sha256="ef" * 32),
        ),
        board="nrf7002dk/nrf5340/cpuapp",
        files=(api.ContextFile(path="model/device-model.json", sha256="11" * 32),),
        id=identity,
    )


class FakeContext:
    """``create_context`` without the packages: the files and the seams.

    It writes what the commands below read back — the public key the
    caller handed it — and keeps everything it was given, so a test can
    say what the command resolved rather than what the fake did.
    """

    def __init__(self) -> None:
        self.out_dir: Path | None = None
        self.work_root: Path | None = None
        self.work_root_existed = False
        self.signing_pub = ""
        self.project_root: Path | None = None
        self.options: Any = None
        self.registries: Any = None

    def __call__(self, model: Any, **kwargs: Any) -> Any:
        self.out_dir = Path(kwargs["out_dir"])
        self.work_root = Path(kwargs["work_root"])
        self.work_root_existed = self.work_root.is_dir()
        self.signing_pub = kwargs["signing_pub"]
        self.project_root = kwargs["project_root"]
        self.options = kwargs["options"]
        self.registries = kwargs["registries"]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "key.pub").write_text(self.signing_pub, encoding="utf-8")
        kwargs["on_line"]("resolving mcuhome-sdk ~=0.2")
        return None


@pytest.fixture
def created(device: Path, monkeypatch: pytest.MonkeyPatch) -> FakeContext:
    """A project with a device and a key, and the three calls stubbed."""
    fake = FakeContext()
    monkeypatch.setattr(api, "create_context", fake)
    monkeypatch.setattr(api, "lock_context", lambda out_dir: _manifest(FACTS["id"]))
    monkeypatch.setattr(api, "read_context_facts", lambda root: dict(FACTS))
    return fake


class TestContextCreate:
    def test_it_writes_the_context_and_answers_what_it_holds(
        self, device: Path, created: FakeContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "ctx"
        code = main(["context", "create", "kitchen", "--out-dir", str(out_dir), "-o", "json"])
        assert code == 0
        document = _document(capsys)
        assert list(document) == ["ok", "out_dir", "context"]
        assert document["ok"] is True
        assert Path(document["out_dir"]) == out_dir
        # What `mcuhome context print` answers for the directory just written.
        assert document["context"] == FACTS
        assert created.out_dir == out_dir

    def test_the_scratch_area_is_beside_the_context_and_does_not_survive(
        self, device: Path, created: FakeContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "ctx"
        assert main(["context", "create", "kitchen", "--out-dir", str(out_dir), "-o", "json"]) == 0
        assert created.work_root is not None
        assert created.work_root.parent == out_dir.parent
        # It is there while the call runs and gone when the run is over.
        assert created.work_root_existed is True
        assert not created.work_root.exists()

    def test_a_refusal_takes_the_scratch_area_with_it(
        self, device: Path, created: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work_roots: list[Path] = []

        def refuse(model: Any, **kwargs: Any) -> Any:
            work_roots.append(Path(kwargs["work_root"]))
            raise api.BuildError("no package here")

        monkeypatch.setattr(api, "create_context", refuse)
        out_dir = device / "ctx"
        assert main(["context", "create", "kitchen", "--out-dir", str(out_dir), "-o", "json"]) == 1
        assert not work_roots[0].exists()

    def test_it_locks_what_it_created(
        self, device: Path, created: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The identity a build server checks its copy against is written
        # by whoever builds the context, which here is this command.
        locked: list[Path] = []
        monkeypatch.setattr(
            api, "lock_context", lambda out_dir: locked.append(Path(out_dir)) or _manifest("x")
        )
        out_dir = device / "ctx"
        assert main(["context", "create", "kitchen", "--out-dir", str(out_dir), "-o", "json"]) == 0
        assert locked == [out_dir]

    def test_the_context_carries_the_public_half_of_the_resolved_key(
        self, device: Path, created: FakeContext
    ) -> None:
        out_dir = device / "ctx"
        assert main(["context", "create", "kitchen", "--out-dir", str(out_dir), "-o", "json"]) == 0
        private = (device / "secrets" / "signing" / "key.pem").read_text(encoding="utf-8")
        assert created.signing_pub == api.public_key_pem(private)
        assert "PRIVATE KEY" not in created.signing_pub

    def test_the_public_key_flag_names_the_file_to_write_in(
        self, device: Path, created: FakeContext, tmp_path: Path
    ) -> None:
        public = tmp_path / "other.pub"
        public.write_text(api.public_key_pem(api.generate_key_pem()), encoding="utf-8")
        out_dir = device / "ctx"
        code = main(
            [
                "context",
                "create",
                "kitchen",
                "--out-dir",
                str(out_dir),
                "--public-key",
                str(public),
                "-o",
                "json",
            ]
        )
        assert code == 0
        assert created.signing_pub == public.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "what",
        ["missing", "directory", "private", "not-a-key"],
    )
    def test_a_public_key_that_is_not_one_is_a_wrong_invocation(
        self,
        device: Path,
        created: FakeContext,
        tmp_path: Path,
        what: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Every shape of "that is not a public key", before anything runs.

        A file that is not there, a directory, a **private** key, and
        text that is no key at all: each of them is the invocation being
        wrong — exit 2, a document in the machine modes, and the pins
        never resolved.
        """
        stated = tmp_path / what
        if what == "directory":
            stated.mkdir()
        elif what == "private":
            stated.write_text(api.generate_key_pem(), encoding="utf-8")
        elif what == "not-a-key":
            stated.write_text("hello", encoding="utf-8")
        code = main(
            [
                "context",
                "create",
                "kitchen",
                "--out-dir",
                str(device / "ctx"),
                "--public-key",
                str(stated),
                "-o",
                "json",
            ]
        )
        assert code == 2
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "UsageError"
        # The act never started: no context, and no scratch directory.
        assert created.out_dir is None
        assert not (device / "ctx").exists()
        assert not list(device.glob(".mcuhome-*-work"))

    def test_a_private_key_is_refused_by_name(
        self,
        device: Path,
        created: FakeContext,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The whole point of the flag is that the private half stays
        # where it is, so the refusal says which half was handed over.
        private = tmp_path / "key.pem"
        private.write_text(api.generate_key_pem(), encoding="utf-8")
        code = main(
            [
                "context",
                "create",
                "kitchen",
                "--out-dir",
                str(device / "ctx"),
                "--public-key",
                str(private),
                "-o",
                "json",
            ]
        )
        assert code == 2
        entry = _document(capsys)["errors"][0]
        assert "is a private key" in entry["message"]
        assert "mcuhome signing print-public-key" in entry["hint"]

    def test_the_stream_still_ends_with_one_result(
        self,
        device: Path,
        created: FakeContext,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A run that refuses before it starts owes its reader a document
        # like every other one.
        code = main(
            [
                "context",
                "create",
                "kitchen",
                "--out-dir",
                str(device / "ctx"),
                "--public-key",
                str(tmp_path / "missing"),
                "-o",
                "json-stream",
            ]
        )
        assert code == 2
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["error", "result"]
        assert messages[-1]["document"]["ok"] is False

    def test_a_build_option_flag_reaches_the_call(
        self, device: Path, created: FakeContext, tmp_path: Path
    ) -> None:
        sources = tmp_path / "packages"
        sources.mkdir()
        out_dir = device / "ctx"
        code = main(
            [
                "context",
                "create",
                "kitchen",
                "--out-dir",
                str(out_dir),
                "--build-sdk-sources",
                str(sources),
                "-o",
                "json",
            ]
        )
        assert code == 0
        assert created.options.sdk_sources == (sources,)

    def test_the_log_lines_go_to_stderr_in_every_mode(
        self, device: Path, created: FakeContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "ctx"
        for mode in ("human", "json", "json-stream"):
            assert (
                main(
                    [
                        "context",
                        "create",
                        "kitchen",
                        "--out-dir",
                        str(out_dir / mode),
                        "-o",
                        mode,
                    ]
                )
                == 0
            )
            printed = capsys.readouterr()
            assert "resolving mcuhome-sdk" in printed.err
            assert "resolving mcuhome-sdk" not in printed.out

    def test_it_reports_no_stages(
        self, device: Path, created: FakeContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "ctx"
        code = main(
            ["context", "create", "kitchen", "--out-dir", str(out_dir), "-o", "json-stream"]
        )
        assert code == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0] == {"verb": "start", "task": "context create"}

    def test_a_person_reads_where_it_went_and_what_it_holds(
        self, device: Path, created: FakeContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_dir = device / "ctx"
        assert main(["context", "create", "kitchen", "--out-dir", str(out_dir)]) == 0
        printed = capsys.readouterr().out
        assert str(out_dir) in printed
        assert FACTS["id"] in printed
        assert FACTS["board"] in printed


class TestContextVerify:
    def test_a_context_that_still_holds_what_it_declares(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        verification = api.ContextVerification(
            root=tmp_path / "ctx",
            manifest=_manifest("sha256:aa"),
            actual_id="sha256:aa",
            mismatches=(),
        )
        monkeypatch.setattr(api, "verify_context", lambda root: verification)
        assert main(["context", "verify", str(tmp_path / "ctx"), "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "root", "context_id", "actual_id", "mismatches"]
        assert document["ok"] is True
        assert document["context_id"] == "sha256:aa"
        assert document["mismatches"] == []

    def test_bytes_that_no_longer_match_are_a_negative_answer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The run happened and the answer is no: the command's own
        # document with `ok` false, never a refusal.
        verification = api.ContextVerification(
            root=tmp_path / "ctx",
            manifest=_manifest("sha256:aa"),
            actual_id="sha256:bb",
            mismatches=(
                api.FileMismatch(
                    path="model/device-model.json",
                    declared_sha256="11" * 32,
                    actual_sha256="22" * 32,
                ),
            ),
        )
        monkeypatch.setattr(api, "verify_context", lambda root: verification)
        assert main(["context", "verify", str(tmp_path / "ctx"), "-o", "json"]) == 1
        document = _document(capsys)
        assert document["ok"] is False
        assert "errors" not in document
        assert document["actual_id"] == "sha256:bb"
        assert document["mismatches"][0]["path"] == "model/device-model.json"

    def test_a_person_reads_every_disagreement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        verification = api.ContextVerification(
            root=tmp_path / "ctx",
            manifest=_manifest("sha256:aa"),
            actual_id="sha256:bb",
            mismatches=(
                api.FileMismatch(
                    path="model/device-model.json",
                    declared_sha256="11" * 32,
                    actual_sha256=None,
                ),
            ),
        )
        monkeypatch.setattr(api, "verify_context", lambda root: verification)
        assert main(["context", "verify", str(tmp_path / "ctx")]) == 1
        printed = capsys.readouterr().out
        assert "model/device-model.json" in printed
        assert "sha256:bb" in printed

    def test_a_directory_that_is_no_context_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Nothing to verify against is the one thing verify_context
        # raises over, and a refusal carries none of this command's keys.
        assert main(["context", "verify", str(tmp_path), "-o", "json"]) == 1
        assert set(_document(capsys)) == {"ok", "errors"}


class TestContextPrint:
    @pytest.fixture
    def printed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(api, "read_context_facts", lambda root: dict(FACTS))
        monkeypatch.setattr(
            api,
            "read_generator_chain",
            lambda path: (api.GeneratorEntry("mcuhome-workbench", "0.1.0"),),
        )

    def test_it_answers_the_facts_under_one_key(
        self, tmp_path: Path, printed: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["context", "print", str(tmp_path), "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "context"]
        assert document["context"] == FACTS

    def test_a_person_reads_the_facts_and_the_generator_chain(
        self, tmp_path: Path, printed: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["context", "print", str(tmp_path)]) == 0
        text = capsys.readouterr().out
        assert FACTS["id"] in text
        assert FACTS["board"] in text
        assert "zephyr/0001-fix.patch" in text
        assert "mcuhome-workbench:0.1.0" in text

    def test_a_context_that_is_not_locked_yet_has_no_identity(
        self,
        tmp_path: Path,
        printed: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        base = {key: value for key, value in FACTS.items() if key != "id"}
        monkeypatch.setattr(api, "read_context_facts", lambda root: dict(base))
        assert main(["context", "print", str(tmp_path)]) == 0
        printed_text = capsys.readouterr().out
        assert "not locked yet" in printed_text

    def test_the_stream_ends_with_one_result(
        self, tmp_path: Path, printed: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["context", "print", str(tmp_path), "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[-1]["document"]["context"] == FACTS

    def test_a_directory_that_is_no_context_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["context", "print", str(tmp_path), "-o", "json"]) == 1
        assert set(_document(capsys)) == {"ok", "errors"}
