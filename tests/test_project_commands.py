# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome project``: init, info, upgrade — and the confirmation.

The upgrade is the one command in the CLI that can damage a project, so
what is pinned here is mostly *refusing to run*: without a terminal and
without ``--confirm-upgrade`` it stops at exit 2, with the wrong id it
stops at exit 2, and a "no" at the prompt leaves the project exactly as
it was.
"""

from __future__ import annotations

import builtins
import json
import signal
from pathlib import Path

import pytest
from conftest import VALID_CONFIG
from mcuhome.workbench import api

from mcuhome_cli import output as output_module
from mcuhome_cli.cli import _StopAfterCurrentMigration, _wait_for_builds, main

MARKER = ".mcuhome-project-root"


def legacy_project(root: Path, *, devices: tuple[str, ...] = ()) -> Path:
    """A project from before the project file had content: version 0."""
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("# This file marks the root of an MCUHome project.\n", "utf-8")
    for name in devices:
        folder = root / "devices" / name
        folder.mkdir(parents=True)
        (folder / "main.yaml").write_text(VALID_CONFIG, "utf-8")
    return root


def document(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# --- init -------------------------------------------------------------


def test_init_gives_the_new_project_a_version_and_an_id(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["project", "init", "-o", "json"]) == 0
    project = document(capsys)["project"]
    assert project["version"] == api.PROJECT_VERSION
    assert project["short_id"] == project["id"][-6:]
    assert "version = 1" in (tmp_path / MARKER).read_text("utf-8")


def test_init_creates_missing_directories(tmp_path, capsys) -> None:
    target = tmp_path / "deep" / "down"
    assert main(["project", "init", str(target)]) == 0
    assert api.is_project_root(target)


# --- the version gate -------------------------------------------------


def test_an_outdated_project_stops_every_command(tmp_path, capsys, monkeypatch) -> None:
    root = legacy_project(tmp_path / "old", devices=("bench-node",))
    monkeypatch.chdir(root)
    assert main(["device", "validate", "bench-node"]) == 1
    err = capsys.readouterr().err
    assert "needs an upgrade" in err
    assert f"mcuhome project upgrade {root}" in err


def test_a_project_version_refusal_carries_the_troubleshooting_link(
    tmp_path, capsys, monkeypatch
) -> None:
    """ "Restore your backup" needs paragraphs; a hint has one sentence."""
    root = legacy_project(tmp_path / "old", devices=("bench-node",))
    monkeypatch.chdir(root)
    assert main(["device", "validate", "bench-node"]) == 1
    assert "https://t.mcuhome.org/cli/docs/project-upgrade/" in capsys.readouterr().err


def test_the_link_stays_out_of_the_machine_document(tmp_path, capsys, monkeypatch) -> None:
    root = legacy_project(tmp_path / "old", devices=("bench-node",))
    monkeypatch.chdir(root)
    assert main(["device", "validate", "bench-node", "-o", "json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is False
    assert "t.mcuhome.org" not in captured.err


def test_an_interrupted_upgrade_is_told_apart_from_a_missing_project(
    tmp_path, capsys, monkeypatch
) -> None:
    """The leftover file is what makes a killed upgrade legible."""
    root = legacy_project(tmp_path / "old")
    (root / MARKER).rename(root / api.UPGRADE_FILE)
    monkeypatch.chdir(root)
    assert main(["project", "info"]) == 1
    err = capsys.readouterr().err
    assert "was interrupted" in err
    assert "Restore the backup" in err
    assert "https://t.mcuhome.org/cli/docs/project-upgrade/" in err


def test_info_still_answers_for_an_outdated_project(tmp_path, capsys, monkeypatch) -> None:
    """The command a person reaches for *after* being refused."""
    root = legacy_project(tmp_path / "old")
    monkeypatch.chdir(root)
    assert main(["project", "info", "-o", "json"]) == 0
    body = document(capsys)
    assert body["upgrade_required"] is True
    assert body["project"]["version"] == 0
    assert body["project"]["id"] is None
    assert [step["to_version"] for step in body["plan"]] == [api.PROJECT_VERSION]


def test_info_names_the_project_and_its_short_id(tmp_path, capsys, monkeypatch) -> None:
    root = api.init_project(tmp_path / "fresh").project.root
    monkeypatch.chdir(root)
    assert main(["project", "info"]) == 0
    out = capsys.readouterr().out
    file = api.project_at(root).file
    assert file is not None and file.id is not None
    assert file.id in out
    assert file.short_id in out
    assert "(current)" in out


# --- the confirmation -------------------------------------------------


def test_without_a_terminal_the_upgrade_needs_the_project_id(tmp_path, capsys) -> None:
    root = legacy_project(tmp_path / "old")
    assert main(["project", "upgrade", str(root), "--no-interactive"]) == 2
    err = capsys.readouterr().err
    assert "no terminal" in err
    assert "--confirm-upgrade" in err
    # Nothing was touched — not even the rename.
    assert (root / MARKER).is_file()
    assert not (root / api.UPGRADE_FILE).exists()


def test_the_wrong_id_refuses_before_anything_moves(tmp_path, capsys) -> None:
    """The point of the flag: a script in the wrong directory stops."""
    root = legacy_project(tmp_path / "old")
    assert main(["project", "upgrade", str(root), "--confirm-upgrade", "somewhere-else"]) == 2
    err = capsys.readouterr().err
    assert "does not name the project" in err
    assert api.project_at(root, require_version=False).file.version == 0


def test_the_id_confirms_the_upgrade(tmp_path, capsys) -> None:
    root = legacy_project(tmp_path / "old")
    token = api.project_at(root, require_version=False).file.token
    assert main(["project", "upgrade", str(root), "--confirm-upgrade", token]) == 0
    out = capsys.readouterr().out
    assert "Project upgraded: version 0 → 1" in out
    assert "https://t.mcuhome.org/cli/docs/project-upgrade/" in out
    assert api.project_at(root).file.version == api.PROJECT_VERSION


def test_a_current_project_short_circuits(tmp_path, capsys) -> None:
    root = api.init_project(tmp_path / "fresh").project.root
    assert main(["project", "upgrade", str(root), "--no-interactive"]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert not (root / api.UPGRADE_FILE).exists()


def test_a_declined_prompt_changes_nothing(tmp_path, capsys, monkeypatch) -> None:
    root = legacy_project(tmp_path / "old")
    monkeypatch.setattr(builtins, "input", lambda *_: "y")  # not "yes"
    assert main(["project", "upgrade", str(root), "--interactive"]) == 1
    assert "Cancelled" in capsys.readouterr().out
    assert api.project_at(root, require_version=False).file.version == 0
    assert (root / MARKER).is_file()


def test_a_typed_yes_runs_it(tmp_path, capsys, monkeypatch) -> None:
    root = legacy_project(tmp_path / "old")
    monkeypatch.setattr(builtins, "input", lambda *_: " YES \n")
    assert main(["project", "upgrade", str(root), "--interactive"]) == 0
    assert api.project_at(root).file.version == api.PROJECT_VERSION


def test_the_prompt_warns_before_it_asks(tmp_path, capsys, monkeypatch) -> None:
    root = legacy_project(tmp_path / "old")
    monkeypatch.setattr(builtins, "input", lambda *_: "no")
    main(["project", "upgrade", str(root), "--interactive"])
    out = capsys.readouterr().out
    assert "cannot be undone" in out
    assert "backup" in out
    assert "nothing else is" in out


# --- dry run ----------------------------------------------------------


def test_dry_run_explains_and_changes_nothing(tmp_path, capsys) -> None:
    root = legacy_project(tmp_path / "old")
    assert main(["project", "upgrade", str(root), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Give the project a version" in out
    assert "Do not edit it by" in out, "the long explanation, not only the one-liner"
    assert "--dry-run" in out
    assert api.project_at(root, require_version=False).file.version == 0


def test_dry_run_needs_no_confirmation(tmp_path, capsys) -> None:
    root = legacy_project(tmp_path / "old")
    assert main(["project", "upgrade", str(root), "--dry-run", "--no-interactive"]) == 0


# --- waiting for what is still running --------------------------------


class _FakeSession:
    """A session whose build directories stop being busy after two looks."""

    def __init__(self, busy) -> None:
        self._answers = list(busy)

    def running_builds(self):
        return self._answers.pop(0) if self._answers else ()


def test_the_upgrade_waits_for_a_running_build(monkeypatch, capsys) -> None:
    monkeypatch.setattr("mcuhome_cli.cli.time.sleep", lambda _seconds: None)
    busy = (api.RunningBuild(directory=Path("/b/porch"), device="porch", operation="build"),)
    session = _FakeSession([busy, busy, ()])
    _wait_for_builds(session, output=output_module.Output())
    out = capsys.readouterr().out
    assert out.count("Waiting for porch") == 1, "announced once, not once per poll"
    assert "Done waiting" in out


# --- signals ----------------------------------------------------------


def test_the_first_ctrl_c_stops_after_the_current_migration(capsys) -> None:
    stopper = _StopAfterCurrentMigration(output_module.Output())
    assert not stopper.requested()
    stopper._handle(signal.SIGINT, None)
    assert stopper.requested()
    err = capsys.readouterr().err
    assert "after the current migration" in err
    assert "3 times within 3 seconds" in err


def test_sigterm_stops_the_same_way_without_the_hint(capsys) -> None:
    stopper = _StopAfterCurrentMigration(output_module.Output())
    stopper._handle(signal.SIGTERM, None)
    assert stopper.requested()
    assert "Stopping after the current migration." in capsys.readouterr().err


def test_three_ctrl_c_in_the_window_abort_now(capsys) -> None:
    stopper = _StopAfterCurrentMigration(output_module.Output())
    stopper._handle(signal.SIGINT, None)
    stopper._handle(signal.SIGINT, None)
    with pytest.raises(KeyboardInterrupt):
        stopper._handle(signal.SIGINT, None)
    assert "will most likely be broken" in capsys.readouterr().err


def test_presses_outside_the_window_only_warn_again(monkeypatch, capsys) -> None:
    """Two presses now and one in a minute must not add up to an abort."""
    clock = [100.0]
    monkeypatch.setattr("mcuhome_cli.cli.time.monotonic", lambda: clock[0])
    stopper = _StopAfterCurrentMigration(output_module.Output())
    stopper._handle(signal.SIGINT, None)
    stopper._handle(signal.SIGINT, None)
    clock[0] += 60.0
    stopper._handle(signal.SIGINT, None)  # window expired: counting restarts
    stopper._handle(signal.SIGINT, None)
    assert "after the current migration" in capsys.readouterr().err
