# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The examples of ``docs/cli.md`` under *In a pipeline*, run.

An example a script is told to copy is a promise, and a promise nobody
runs is a spelling that rots. This suite takes the invocations out of
that section (:func:`reference.pipeline_invocations`) and holds the
command line to them: every one of them parses with exactly the flags it
names, the stream loop reads the keys the text tells it to read, the
token really comes from standard input, and the exit code says what the
document's ``ok`` says.

The one example that would take a toolchain and minutes is stubbed the
way :mod:`test_build_command` stubs it — at the api seam — because what
is under test here is the invocation and what comes back out of it, not
the compiler. The placeholders a person fills in (``$KEY``,
``$SOURCES``, ``$IMAGE``, the device file) are filled in with real ones
from the fixtures, and nothing else about the argument list is touched.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import reference as ref
from mcuhome.workbench import api
from test_build_command import FakeBuild, FakeSigning

from mcuhome.cli.main import main
from mcuhome.cli.parser import build_parser

#: Where each example stands in the section, so a test can say which one
#: it is running rather than counting brackets.
INIT, SUBPROCESS, CONTAINER, REMOTE, STREAM = range(5)


def _examples() -> list[list[str]]:
    return ref.pipeline_invocations()


def _filled(argv: list[str], **values: str) -> list[str]:
    """The example's argument list with its placeholders filled in."""
    return [values.get(token, token) for token in argv]


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


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


@pytest.fixture
def sources(tmp_path: Path) -> dict[str, str]:
    """One directory per package kind, the way the examples name them."""
    made = {}
    for kind in ("sdk", "workspace", "tools"):
        directory = tmp_path / "sources" / kind
        directory.mkdir(parents=True)
        made[f"$SOURCES/{kind}"] = str(directory)
    return made


@pytest.fixture
def thermostat(device: Path) -> Path:
    """A device file at ``devices/thermostat.yaml``, as a job writes one.

    The positional of `device build` is a device of the project by name
    **or** a path to a device file, and the second form is what a job
    that generates the file it builds passes — which is exactly what the
    examples show.
    """
    project = api.read_project(device)
    entry = device / "devices" / "thermostat.yaml"
    entry.write_text(api.render_device_file("thermostat", board="nrf7002dk/nrf5340/cpuapp"))
    api.create_pairing(entry, project=project)
    return entry


class TestEveryExampleParses:
    """The argument spelling of every line in the section."""

    def test_the_section_shows_the_five_invocations_it_explains(self) -> None:
        # A guard on the extraction itself: a test that silently found
        # nothing would pass every assertion below.
        examples = _examples()
        assert len(examples) == 5
        assert examples[INIT][:2] == ["project", "init"]
        assert all(example[:2] == ["device", "build"] for example in examples[1:])

    @pytest.mark.parametrize("argv", _examples(), ids=lambda argv: " ".join(argv[:3]))
    def test_the_parser_accepts_it(self, argv: list[str]) -> None:
        # `parse_args` exits 2 on an unknown flag or a missing value, so
        # a retired spelling in the reference's own examples fails here.
        build_parser().parse_args(argv)

    def test_each_package_kind_is_named_by_its_own_flag(self) -> None:
        """The three kinds never fall back to one another, so all three are named."""
        for index in (SUBPROCESS, CONTAINER):
            argv = _examples()[index]
            assert "--build-sdk-sources" in argv
            assert "--build-workspace-sources" in argv
            assert "--build-tools-sources" in argv
            assert "--sdk-sources" not in argv


class TestABuildDrivenFromAScript:
    """The two local legs, run to the end against the stubbed build.

    This is the shape a continuous-integration job builds firmware in —
    a generated device file as the positional, the output directory and
    the key stated, the target and the mode stated, and one directory
    per package kind — so the spelling such a job copies out of the
    reference is proven here rather than in the first red pipeline.
    """

    def test_the_subprocess_leg_states_every_input_and_answers_true(
        self,
        thermostat: Path,
        sources: dict[str, str],
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        key = api.resolve_signing_key(env={}, project=api.read_project(thermostat.parents[1]))
        argv = _filled(_examples()[SUBPROCESS], **sources, **{"$KEY": str(key.path)})
        assert main(argv) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert document["build"]["out_dir"] == str(thermostat.parents[1] / "build" / "thermostat")
        options = built.request.options
        assert [str(path) for path in options.sdk_sources] == [sources["$SOURCES/sdk"]]
        assert [str(path) for path in options.workspace_sources] == [sources["$SOURCES/workspace"]]
        assert [str(path) for path in options.tools_sources] == [sources["$SOURCES/tools"]]
        assert options.mode == api.MODE_SUBPROCESS
        assert built.request.builder.target == "local"

    def test_the_container_leg_states_the_image_beside_the_three_directories(
        self,
        thermostat: Path,
        sources: dict[str, str],
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        key = api.resolve_signing_key(env={}, project=api.read_project(thermostat.parents[1]))
        image = "ghcr.io/mcu-home/build-environment:0.2.1-r1"
        argv = _filled(
            _examples()[CONTAINER], **sources, **{"$KEY": str(key.path), "$IMAGE": image}
        )
        assert main(argv) == 0
        assert _document(capsys)["ok"] is True
        # The image replaces the unpacking, not the pins that name it.
        assert built.request.container_image == image
        options = built.request.options
        assert [str(path) for path in options.sdk_sources] == [sources["$SOURCES/sdk"]]
        assert [str(path) for path in options.tools_sources] == [sources["$SOURCES/tools"]]

    def test_the_exit_code_says_what_the_document_says(
        self,
        thermostat: Path,
        sources: dict[str, str],
        signed: FakeSigning,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``test "$(jq -r .ok build.json)" = true`` and the exit code agree."""
        key = api.resolve_signing_key(env={}, project=api.read_project(thermostat.parents[1]))
        argv = _filled(_examples()[SUBPROCESS], **sources, **{"$KEY": str(key.path)})
        monkeypatch.setattr(api, "build_firmware", FakeBuild(ok=False))
        assert main(argv) == 1
        assert _document(capsys)["ok"] is False

    def test_the_token_is_read_from_standard_input(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``printf %s "$TOKEN" | mcuhome … --build-server-token -``."""
        monkeypatch.setattr("sys.stdin", io.StringIO("s3cret"))
        assert main(_examples()[REMOTE]) == 0
        assert _document(capsys)["ok"] is True
        assert built.request.builder.target == api.TARGET_REMOTE
        assert built.request.builder.server == "builds.example.org"
        assert built.request.builder.token == "s3cret"


class TestTheStreamTheExampleReads:
    """The `while read` loop of the section, applied to a real stream."""

    def test_it_reads_the_steps_the_stages_and_the_verdict(
        self,
        device: Path,
        built: FakeBuild,
        signed: FakeSigning,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = main(_examples()[STREAM])
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]

        # The loop, in Python: `jq -r .verb` picks the branch, and each
        # branch reads exactly one place of that message.
        steps: list[str] = []
        stages: list[str] = []
        verdict: object = None
        for message in messages:
            verb = message["verb"]
            if verb == "start":
                steps = message["steps"]
            elif verb == "progress":
                stages.append(message["stage"])
            elif verb == "result":
                verdict = message["document"]["ok"]

        assert steps, "the start message carries the steps the loop joins"
        assert steps == list(
            api.build_steps(target=built.request.builder.target, options=built.request.options)
        )
        assert stages, "a stage arrives as its own message"
        assert set(stages) <= set(steps), "a stage is one of the steps, never a new word"
        assert verdict is True
        assert messages[-1]["verb"] == "result", "the last line is always the result"
        assert code == 0, "the exit code says the same thing its ok does"

    def test_a_run_that_says_no_says_it_in_both_places(
        self,
        device: Path,
        signed: FakeSigning,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(api, "build_firmware", FakeBuild(ok=False))
        code = main(_examples()[STREAM])
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert messages[-1]["verb"] == "result"
        assert messages[-1]["document"]["ok"] is False
        assert code == 1
