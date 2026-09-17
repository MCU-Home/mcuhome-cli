# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``project init``, ``project info`` and ``project upgrade``.

The upgrade is driven against a project of the layout version before
this one. No api call creates such a project — making an old project is
exactly what MCUHome does not do — so the marker is written here, in the
format the workbench documents, and everything after that is the real
migration doing real work.
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


@pytest.fixture
def old_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project of layout version 1, with a secret in the old place."""
    root = tmp_path / "old"
    (root / "devices").mkdir(parents=True)
    (root / "secrets").mkdir()
    (root / "secrets" / "main.yaml").write_text("wifi_password: swordfish\n", encoding="utf-8")
    (root / "secrets").chmod(0o700)
    (root / "secrets" / "main.yaml").chmod(0o600)
    (root / "mcuhome.yaml").write_text("# project configuration\n", encoding="utf-8")
    (root / api.PROJECT_MARKER_FILE).write_text(
        'version = 1\nid = "01a0b2c3-d4e5-7f60-8a9b-0c1d2e3f4a5b"\n', encoding="utf-8"
    )
    monkeypatch.chdir(root)
    return root


class TestProjectInit:
    def test_it_creates_the_project_and_names_what_it_wrote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["project", "init", "thermostats", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "project", "created"]
        assert document["project"]["root"] == str(tmp_path / "thermostats")
        assert document["project"]["version"] == api.PROJECT_VERSION
        assert str(tmp_path / "thermostats" / api.PROJECT_MARKER_FILE) in document["created"]
        assert api.is_project_root(tmp_path / "thermostats")

    def test_the_working_directory_becomes_the_project_without_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["project", "init", "-o", "json"]) == 0
        assert _document(capsys)["project"]["root"] == str(tmp_path)

    def test_a_project_that_is_already_there_is_answered_not_refused(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A script may run init before every session.
        assert main(["project", "init", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert document["created"] == []

    def test_any_other_non_empty_directory_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "work.txt").write_text("mine", encoding="utf-8")
        assert main(["project", "init", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert "--force" in document["errors"][0]["hint"]

    def test_force_writes_into_it_anyway(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "work.txt").write_text("mine", encoding="utf-8")
        assert main(["project", "init", "--force", "-o", "json"]) == 0
        assert (tmp_path / "work.txt").read_text(encoding="utf-8") == "mine"
        assert api.is_project_root(tmp_path)

    def test_force_completes_a_project_that_is_already_there(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (in_project / ".gitignore").unlink()
        assert main(["project", "init", "--force", "-o", "json"]) == 0
        assert str(in_project / ".gitignore") in _document(capsys)["created"]

    def test_the_stream_starts_and_ends(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["project", "init", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0]["task"] == "project init"


class TestProjectInfo:
    def test_it_describes_the_project_it_is_in(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["project", "info", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "project", "upgrading", "devices", "plan"]
        assert document["project"]["root"] == str(in_project)
        assert document["upgrading"] is False
        assert document["devices"] == []
        assert document["plan"] == []

    def test_a_project_too_old_for_every_other_command_is_described(
        self, old_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # This is the command a person runs *because* something refused
        # them: the plan that is not empty is the "needs upgrading".
        assert main(["project", "info", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert document["project"]["version"] == 1
        assert [migration["name"] for migration in document["plan"]] == ["v2_secrets_layout"]

    def test_it_describes_a_stated_directory(
        self,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["project", "info", str(project), "-o", "json"]) == 0
        assert _document(capsys)["project"]["root"] == str(project)

    def test_outside_a_project_it_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["project", "info", "-o", "json"]) == 1
        assert _document(capsys)["errors"][0]["kind"] == "ConfigError"

    def test_a_person_reads_where_it_is_and_what_is_in_it(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["project", "info"]) == 0
        printed = capsys.readouterr().out
        assert str(in_project) in printed
        assert "No devices yet" in printed


class TestProjectUpgrade:
    def test_a_project_that_is_current_answers_the_shape_of_a_run(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["project", "upgrade", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == [
            "ok",
            "project",
            "dry_run",
            "from_version",
            "to_version",
            "applied",
            "stopped",
            "remaining",
        ]
        assert document["from_version"] == document["to_version"] == api.PROJECT_VERSION
        assert document["applied"] == [] and document["remaining"] == []

    def test_a_dry_run_shows_the_plan_and_changes_nothing(
        self, old_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["project", "upgrade", "--dry-run", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "project", "dry_run", "plan"]
        assert document["dry_run"] is True
        assert [migration["name"] for migration in document["plan"]] == ["v2_secrets_layout"]
        assert (old_project / "secrets" / "main.yaml").is_file()

    def test_without_a_terminal_it_refuses_and_names_the_id_to_pass(
        self, old_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["project", "upgrade", "-o", "json"]) == 2
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        # The command line's own condition, in its own vocabulary: a
        # `kind` that is not one of its three is a workbench exception.
        assert document["errors"][0]["kind"] == "UsageError"
        assert "--confirm" in document["errors"][0]["hint"]

    def test_a_confirmation_that_names_another_project_is_refused(
        self, old_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["project", "upgrade", "--confirm", "abcdef", "-o", "json"]) == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"

    def test_it_migrates_and_answers_what_it_applied(
        self, old_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        short = api.read_project(old_project, require_version=False).file.short_id
        assert main(["project", "upgrade", "--confirm", short, "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert document["from_version"] == 1
        assert document["to_version"] == api.PROJECT_VERSION
        assert [migration["name"] for migration in document["applied"]] == ["v2_secrets_layout"]
        assert document["remaining"] == []
        assert api.read_project(old_project).file.version == api.PROJECT_VERSION

    def test_the_stream_carries_the_plan_and_a_pair_per_migration(
        self, old_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        short = api.read_project(old_project, require_version=False).file.short_id
        assert main(["project", "upgrade", "--confirm", short, "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == [
            "start",
            "progress",
            "progress",
            "result",
        ]
        assert [migration["name"] for migration in messages[0]["plan"]] == ["v2_secrets_layout"]
        assert messages[1] == {
            "verb": "progress",
            "stage": "migration_started",
            "name": "v2_secrets_layout",
            "from_version": 1,
            "to_version": 2,
        }
        assert messages[2]["stage"] == "migration_done"
        assert messages[3]["document"]["ok"] is True

    def test_an_interactive_run_is_asked_and_a_no_changes_nothing(
        self,
        old_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt="": "no")
        assert main(["project", "upgrade", "--interactive"]) == 1
        printed = capsys.readouterr().out
        assert "Cancelled" in printed
        assert api.read_project(old_project, require_version=False).file.version == 1

    def test_an_interactive_yes_migrates(
        self,
        old_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
        assert main(["project", "upgrade", "--interactive"]) == 0
        assert "Project upgraded" in capsys.readouterr().out
        assert api.read_project(old_project).file.version == api.PROJECT_VERSION

    def test_a_stop_between_migrations_leaves_the_rest_for_the_next_run(
        self,
        old_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The stop predicate is asked before the first migration, which
        # is the boundary a stopped run ends at.
        monkeypatch.setattr(
            "mcuhome.cli.projectcommands._StopAfterCurrentMigration.requested",
            lambda self: True,
        )
        short = api.read_project(old_project, require_version=False).file.short_id
        assert main(["project", "upgrade", "--confirm", short, "-o", "json"]) == 1
        document = _document(capsys)
        assert document["ok"] is False
        assert document["stopped"] is True
        assert document["applied"] == []
        assert [migration["name"] for migration in document["remaining"]] == ["v2_secrets_layout"]
