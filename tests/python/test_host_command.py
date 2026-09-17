# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome host check``, in all three modes.

The check talks to this machine: it runs the container runtime's version
command, asks the configured repositories what they publish and asks an
interpreter its version. None of that may happen inside a test suite, so
``check_build_host`` is stubbed at the **api seam** and answers real
:class:`~mcuhome.workbench.api.HostFinding` values — the shape is the
workbench's, the probing is not run.

Everything else is real: the configuration ladder the flags feed, the
project resolution (a project whose layout is older than this MCUHome is
what this command **reports**, so it must not refuse over one), the
cache walk, the documents and the exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcuhome.workbench import api

from mcuhome.cli.main import main


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


class FakeCheck:
    """``check_build_host`` without the probing: the findings it answers."""

    def __init__(self, *findings: api.HostFinding) -> None:
        self.findings = findings or (
            api.HostFinding(check="project", ok=True, detail="a project is here"),
            api.HostFinding(check="imgtool", ok=True, detail="/usr/bin/imgtool"),
        )
        self.options: Any = None
        self.imgtool: Any = None
        self.project: Any = None
        self.settings: Any = None

    def __call__(self, **kwargs: Any) -> api.HostCheckResult:
        self.options = kwargs["options"]
        self.imgtool = kwargs["imgtool"]
        self.project = kwargs["project"]
        self.settings = kwargs["settings"]
        return api.HostCheckResult(findings=self.findings)


@pytest.fixture
def checked(monkeypatch: pytest.MonkeyPatch) -> FakeCheck:
    fake = FakeCheck()
    monkeypatch.setattr(api, "check_build_host", fake)
    return fake


def _failing(monkeypatch: pytest.MonkeyPatch) -> FakeCheck:
    fake = FakeCheck(
        api.HostFinding(check="project", ok=True, detail="a project is here"),
        api.HostFinding(
            check="runtime",
            ok=False,
            detail="docker is not on this machine",
            hint="install it, or build as a child process",
        ),
    )
    monkeypatch.setattr(api, "check_build_host", fake)
    return fake


class TestHostCheck:
    def test_it_answers_the_host_and_the_cache(
        self, in_project: Path, checked: FakeCheck, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["host", "check", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "host", "cache"]
        assert document["ok"] is True
        assert list(document["host"]) == ["ok", "findings"]
        assert list(document["host"]["findings"][0]) == ["ok", "check", "detail", "hint"]
        for tier in document["cache"]:
            assert list(tier) == ["tier", "path", "size", "files"]

    def test_a_failing_finding_is_a_negative_answer_and_exit_one(
        self, in_project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A host that cannot build is the answer, not a refusal: the
        # command's own document, with the findings still in it.
        _failing(monkeypatch)
        assert main(["host", "check", "-o", "json"]) == 1
        document = _document(capsys)
        assert document["ok"] is False
        assert document["host"]["ok"] is False
        assert "errors" not in document
        assert [finding["check"] for finding in document["host"]["findings"]] == [
            "project",
            "runtime",
        ]

    def test_the_verdict_is_the_host_checks_own(
        self, in_project: Path, checked: FakeCheck, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["host", "check", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] == document["host"]["ok"]

    def test_the_build_option_flags_decide_what_is_examined(
        self, in_project: Path, checked: FakeCheck
    ) -> None:
        # A person asks what a build *would* need under a mode they have
        # not configured yet.
        assert main(["host", "check", "--build-mode", "container", "-o", "json"]) == 0
        assert checked.options.mode == "container"

    def test_the_signing_program_flag_reaches_the_check(
        self, in_project: Path, checked: FakeCheck
    ) -> None:
        assert main(["host", "check", "--signing-imgtool", "my-imgtool", "-o", "json"]) == 0
        assert checked.imgtool == "my-imgtool"

    def test_the_resolved_configuration_and_the_project_are_handed_over(
        self, in_project: Path, checked: FakeCheck
    ) -> None:
        # Without them the configuration, builder and secrets findings
        # are not reported at all.
        assert main(["host", "check", "-o", "json"]) == 0
        assert checked.settings is not None
        assert checked.project is not None
        assert checked.project.root == in_project

    def test_it_works_outside_a_project(
        self,
        tmp_path: Path,
        checked: FakeCheck,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["host", "check", "-o", "json"]) == 0
        assert _document(capsys)["ok"] is True
        assert checked.project is None

    def test_a_project_of_an_older_layout_is_reported_rather_than_refused(
        self, in_project: Path, checked: FakeCheck, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Every other command refuses over it, which is why this one must
        # not: it is what a person runs to find out what is wrong.
        marker = in_project / api.PROJECT_MARKER_FILE
        marker.write_text(
            marker.read_text(encoding="utf-8").replace("version = 2", "version = 1"),
            encoding="utf-8",
        )
        assert main(["secret", "list-scopes", "-o", "json"]) == 1
        assert set(_document(capsys)) == {"ok", "errors"}
        assert main(["host", "check", "-o", "json"]) == 0
        assert list(_document(capsys)) == ["ok", "host", "cache"]
        assert checked.project is not None

    def test_the_cache_is_read_for_every_configured_tier(
        self,
        in_project: Path,
        checked: FakeCheck,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cache_root = tmp_path / "ccache"
        (cache_root / "cache-local").mkdir(parents=True)
        (cache_root / "cache-local" / "a-file").write_bytes(b"0123456789")
        code = main(["host", "check", "--build-cache-root", str(cache_root), "-o", "json"])
        assert code == 0
        tiers = {tier["tier"]: tier for tier in _document(capsys)["cache"]}
        assert tiers["local"]["files"] == 1
        assert tiers["local"]["size"] == 10
        assert Path(tiers["local"]["path"]) == cache_root / "cache-local"

    def test_a_person_reads_one_line_per_finding(
        self, in_project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _failing(monkeypatch)
        assert main(["host", "check"]) == 1
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].endswith("project")
        assert "docker is not on this machine" in lines[1]
        # The fix of a finding that has one, under it.
        assert lines[2].strip().startswith("Fix:")
        assert "1 of 2 checks need attention." in "\n".join(lines)

    def test_a_finding_that_is_fine_states_its_detail_only_when_asked(
        self, in_project: Path, checked: FakeCheck, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["host", "check"]) == 0
        assert "/usr/bin/imgtool" not in capsys.readouterr().out
        assert main(["host", "check", "-v"]) == 0
        assert "/usr/bin/imgtool" in capsys.readouterr().out

    def test_it_reports_no_stages(
        self, in_project: Path, checked: FakeCheck, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["host", "check", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0] == {"verb": "start", "task": "host check"}
        assert messages[-1]["document"]["ok"] is True
