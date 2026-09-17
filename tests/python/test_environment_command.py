# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome environment provision``, in all three modes.

Provisioning acquires a package — from an operator directory or from a
registry over the network — verifies its hash and unpacks hundreds of
megabytes under a bound. That is the one call this suite stubs, at the
**api seam**, and what it stubs is exactly the part that leaves this
machine. Everything the command line owns is real: the configuration
ladder the build option flags feed, the project whose trust anchor
decides which registry may be asked, the log channel and the document.

What only a real run proves is the acquisition itself: that a registry
answers, that the hash matches, that the unpacked tree is usable.
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


class FakeProvision:
    """``provision_environment`` without the package: the seams it is given."""

    def __init__(self, *, store: Path | None = None) -> None:
        self.package: Any = None
        self.options: Any = None
        self.project: Any = None
        self.registries: Any = None
        self.store = store

    def __call__(self, package: Any, **kwargs: Any) -> api.StoreEntry:
        self.package = package
        self.options = kwargs["options"]
        self.project = kwargs["project"]
        self.registries = kwargs["registries"]
        kwargs["on_line"]("unpacking mcuhome-build-tools 0.2.1")
        base = self.store or Path("/store")
        return api.StoreEntry(
            kind="tools",
            name="mcuhome-build-tools_x86-64",
            version="0.2.1",
            sha256="ab" * 32,
            path=base / "tools" / "mcuhome-build-tools_x86-64" / "0.2.1",
        )


@pytest.fixture
def provisioned(monkeypatch: pytest.MonkeyPatch) -> FakeProvision:
    fake = FakeProvision()
    monkeypatch.setattr(api, "provision_environment", fake)
    return fake


class TestEnvironmentProvision:
    def test_it_answers_the_store_entry(
        self, in_project: Path, provisioned: FakeProvision, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["environment", "provision", "mcuhome-build-tools", "-o", "json"])
        assert code == 0
        document = _document(capsys)
        assert list(document) == ["ok", "kind", "name", "version", "sha256", "path"]
        assert document["ok"] is True
        assert document["name"] == "mcuhome-build-tools_x86-64"
        assert document["version"] == "0.2.1"

    def test_a_package_name_travels_as_it_was_written(
        self, in_project: Path, provisioned: FakeProvision
    ) -> None:
        # The grammar for a constraint and a hash is the workbench's to
        # parse, so what a person wrote is what it is handed.
        assert main(["environment", "provision", "mcuhome-sdk:~=0.2", "-o", "json"]) == 0
        assert provisioned.package == "mcuhome-sdk:~=0.2"

    def test_a_package_file_travels_as_an_absolute_path(
        self, in_project: Path, provisioned: FakeProvision, tmp_path: Path
    ) -> None:
        # Which file is read must not depend on the directory the
        # workbench happens to be called from.
        package = tmp_path / "mcuhome-build-workspace-0.2.0.tar.zst"
        package.write_bytes(b"not really a package")
        assert main(["environment", "provision", str(package), "-o", "json"]) == 0
        assert provisioned.package == package

    def test_it_provisions_for_the_project_it_stands_in(
        self, in_project: Path, provisioned: FakeProvision
    ) -> None:
        assert main(["environment", "provision", "mcuhome-build-tools", "-o", "json"]) == 0
        assert provisioned.project is not None
        assert provisioned.project.root == in_project

    def test_it_works_outside_a_project(
        self,
        tmp_path: Path,
        provisioned: FakeProvision,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A machine being set up has no project yet; the operator
        # directories are still where a package is looked for.
        monkeypatch.chdir(tmp_path)
        assert main(["environment", "provision", "mcuhome-build-tools", "-o", "json"]) == 0
        assert _document(capsys)["ok"] is True
        assert provisioned.project is None

    def test_a_build_option_flag_reaches_the_call(
        self, in_project: Path, provisioned: FakeProvision, tmp_path: Path
    ) -> None:
        store = tmp_path / "store"
        code = main(
            [
                "environment",
                "provision",
                "mcuhome-build-tools",
                "--build-env-store",
                str(store),
                "--build-tools-max-bytes",
                "1024",
                "-o",
                "json",
            ]
        )
        assert code == 0
        assert provisioned.options.env_store == store
        assert provisioned.options.tools_max_bytes == 1024

    def test_a_value_of_the_wrong_shape_is_a_wrong_invocation(
        self, in_project: Path, provisioned: FakeProvision, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "environment",
                "provision",
                "mcuhome-build-tools",
                "--build-cpus",
                "many",
                "-o",
                "json",
            ]
        )
        assert code == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"
        assert provisioned.options is None

    def test_the_log_lines_go_to_stderr_in_every_mode(
        self, in_project: Path, provisioned: FakeProvision, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for mode in ("human", "json", "json-stream"):
            assert main(["environment", "provision", "mcuhome-build-tools", "-o", mode]) == 0
            printed = capsys.readouterr()
            assert "unpacking mcuhome-build-tools" in printed.err
            assert "unpacking mcuhome-build-tools" not in printed.out

    def test_it_reports_no_stages(
        self, in_project: Path, provisioned: FakeProvision, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["environment", "provision", "mcuhome-build-tools", "-o", "json-stream"])
        assert code == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0] == {"verb": "start", "task": "environment provision"}

    def test_a_person_reads_what_is_in_the_store_and_where(
        self, in_project: Path, provisioned: FakeProvision, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["environment", "provision", "mcuhome-build-tools"]) == 0
        printed = capsys.readouterr().out
        assert "mcuhome-build-tools_x86-64 0.2.1" in printed
        assert "ab" * 32 in printed

    def test_a_package_nobody_publishes_is_refused(
        self, in_project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def refuse(package: Any, **kwargs: Any) -> Any:
            raise api.BuildEnvironmentError("there is no such package")

        monkeypatch.setattr(api, "provision_environment", refuse)
        assert main(["environment", "provision", "mcuhome-nothing", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "BuildEnvironmentError"
