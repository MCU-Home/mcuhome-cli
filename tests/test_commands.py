# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The commands the vocabulary step added (cli ADR 0003 §2).

``config`` (print/get/set/unset over the five layers of ADR 0022),
``device list``, ``doctor``, the honest stubs, and ``device new
--name``. The build-selection rungs have their own section in
``test_cli.py``; here it is the new surface itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import VALID_CONFIG, make_project
from mcuhome.model.errors import BuildError

from mcuhome_cli import cli
from mcuhome_cli.cli import main

BOARD = "nrf7002dk/nrf5340/cpuapp"


def _project_with_device(tmp_path: Path) -> Path:
    project = make_project(tmp_path / "project")
    (project / "devices" / "bench-node").mkdir(parents=True)
    (project / "devices" / "bench-node" / "main.yaml").write_text(VALID_CONFIG, encoding="utf-8")
    return project


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_config_set_get_print_unset_round_trip(tmp_path, capsys, monkeypatch) -> None:
    """One value through all four verbs, in the project scope (the default)."""
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)

    assert main(["config", "set", "jobs", "4"]) == 0
    out = capsys.readouterr().out
    assert "Set jobs = 4" in out
    assert "mcuhome.yaml" in out
    assert "jobs: 4" in (project / "mcuhome.yaml").read_text(encoding="utf-8")

    assert main(["config", "get", "jobs"]) == 0
    assert capsys.readouterr().out.strip() == "4"

    assert main(["config", "print"]) == 0
    printed = capsys.readouterr().out
    assert "option" in printed and "origin" in printed
    assert "jobs" in printed
    assert "project" in printed

    assert main(["config", "unset", "jobs"]) == 0
    assert "Removed jobs" in capsys.readouterr().out
    assert "jobs" not in (project / "mcuhome.yaml").read_text(encoding="utf-8")

    assert main(["config", "unset", "jobs"]) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_config_print_answers_json_with_origins(tmp_path, capsys, monkeypatch) -> None:
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)
    (project / "mcuhome.yaml").write_text("jobs: 3\n", encoding="utf-8")
    assert main(["config", "print", "-o", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["config"]["jobs"] == {
        "value": 3,
        "origin": "project",
        "source": str(project / "mcuhome.yaml"),
    }


def test_config_set_writes_the_user_scope_file(tmp_path, capsys, monkeypatch) -> None:
    """--user writes configuration.yaml under XDG_CONFIG_HOME, project or not."""
    monkeypatch.chdir(tmp_path)
    assert main(["config", "set", "--user", "default_builder", "attic", "-o", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True and document["scope"] == "user"
    file = Path(document["file"])
    assert file.name == "configuration.yaml"
    assert "default_builder: attic" in file.read_text(encoding="utf-8")


def test_config_set_in_the_project_scope_needs_a_project(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["config", "set", "jobs", "4"]) == 1
    err = capsys.readouterr().err
    assert "no project here" in err
    assert "mcuhome project init" in err


def test_config_refuses_what_a_file_may_not_carry(tmp_path, capsys, monkeypatch) -> None:
    """The channel rules and the structured-value rule, through the command."""
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)
    assert main(["config", "set", "signing_key", "/k"]) == 1
    assert "cannot be set from a configuration file" in capsys.readouterr().err
    assert main(["config", "set", "builders", "attic"]) == 1
    assert "structured configuration" in capsys.readouterr().err
    assert main(["config", "get", "jobz"]) == 1
    assert "no option called 'jobz'" in capsys.readouterr().err
    before = (project / "mcuhome.yaml").read_text(encoding="utf-8")
    assert main(["config", "unset", "jobz"]) == 1
    assert "no option called 'jobz'" in capsys.readouterr().err
    assert (project / "mcuhome.yaml").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# device list
# --------------------------------------------------------------------------


def test_list_outside_a_project_refuses_toward_init(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["device", "list"]) == 1
    err = capsys.readouterr().err
    assert "mcuhome project init" in err


def test_list_an_empty_project_names_the_next_step(tmp_path, capsys, monkeypatch) -> None:
    project = make_project(tmp_path / "project")
    monkeypatch.chdir(project)
    assert main(["device", "list"]) == 0
    out = capsys.readouterr().out
    assert "No devices yet" in out
    assert "mcuhome device new" in out


def test_list_shows_validation_and_build_state(tmp_path, capsys, monkeypatch) -> None:
    project = _project_with_device(tmp_path)
    (project / "devices" / "broken-node").mkdir()
    (project / "devices" / "broken-node" / "main.yaml").write_text(
        "device:\n  name: broken-node\n", encoding="utf-8"
    )
    # bench-node has a signed container delivery, broken-node no build.
    build_dir = project / "build" / "bench-node"
    build_dir.mkdir(parents=True)
    (build_dir / "build-report.json").write_text("{}", encoding="utf-8")
    (build_dir / "firmware.signed.bin").write_bytes(b"\x00")
    monkeypatch.chdir(project)

    assert main(["device", "list"]) == 0
    out = capsys.readouterr().out
    assert "bench-node" in out
    assert BOARD in out
    assert "signed" in out
    assert "broken-node" in out
    assert "problem" in out

    assert main(["device", "list", "-o", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    by_name = {entry["name"]: entry for entry in document["devices"]}
    assert by_name["bench-node"]["ok"] is True
    assert by_name["bench-node"]["board"] == BOARD
    assert by_name["bench-node"]["built"] is True
    assert by_name["bench-node"]["signed"] is True
    assert by_name["broken-node"]["ok"] is False
    assert by_name["broken-node"]["problems"] >= 1
    assert by_name["broken-node"]["built"] is False


def test_list_calls_an_unsigned_delivery_unsigned(tmp_path, capsys, monkeypatch) -> None:
    project = _project_with_device(tmp_path)
    build_dir = project / "build" / "bench-node"
    build_dir.mkdir(parents=True)
    (build_dir / "build-report.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(project)
    assert main(["device", "list", "-o", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    entry = document["devices"][0]
    assert entry["built"] is True
    assert entry["signed"] is False


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def test_doctor_reports_every_check_and_exits_zero_when_nothing_fails(
    tmp_path, capsys, monkeypatch
) -> None:
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli.container, "preflight", lambda *a, **k: None)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    for check in ("stack", "project", "configuration", "builders", "container", "secrets"):
        assert check in out
    assert str(project) in out
    assert "mcuhome-workbench" in out


def test_doctor_marks_a_missing_container_runtime_and_exits_one(
    tmp_path, capsys, monkeypatch
) -> None:
    """One broken thing never hides the next: every check still reports."""
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)

    def refuse(*args, **kwargs):
        raise BuildError("MCUHome compiles in a container and cannot find docker.")

    monkeypatch.setattr(cli.container, "preflight", refuse)
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "fail" in out
    assert "cannot find docker" in out
    assert "secrets" in out  # the checks after the failure still ran


def test_doctor_json_carries_the_checks(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    def refuse(*args, **kwargs):
        raise BuildError("no docker here")

    monkeypatch.setattr(cli.container, "preflight", refuse)
    assert main(["doctor", "-o", "json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    by_check = {entry["check"]: entry for entry in document["checks"]}
    assert by_check["container"]["status"] == "fail"
    assert by_check["project"]["status"] == "warn"  # not inside a project


def test_doctor_warns_about_exposed_secrets(tmp_path, capsys, monkeypatch) -> None:
    project = _project_with_device(tmp_path)
    secrets_file = project / "secrets" / "main.yaml"
    secrets_file.write_text("wifi_password: hunter2\n", encoding="utf-8")
    secrets_file.chmod(0o644)
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli.container, "preflight", lambda *a, **k: None)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "warn" in out
    assert "main.yaml" in out


# --------------------------------------------------------------------------
# stubs, and device new --name
# --------------------------------------------------------------------------


def test_flash_and_first_time_setup_refuse_in_words(tmp_path, capsys, monkeypatch) -> None:
    """cli ADR 0003: honest stubs — a refusal naming the plan, exit 1."""
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)
    assert main(["device", "flash", "bench-node"]) == 1
    err = capsys.readouterr().err
    assert "not implemented yet" in err
    assert "recovery" in err
    assert main(["device", "first-time-setup", "bench-node"]) == 1
    err = capsys.readouterr().err
    assert "not implemented yet" in err
    assert "bootloader" in err


def test_device_new_takes_a_friendly_name(tmp_path, capsys, monkeypatch) -> None:
    project = make_project(tmp_path / "project")
    monkeypatch.chdir(project)
    argv = ["device", "new", "bench-node", "--board", BOARD, "--name", "Workbench Node"]
    assert main(argv) == 0
    capsys.readouterr()
    text = (project / "devices" / "bench-node" / "main.yaml").read_text(encoding="utf-8")
    assert 'friendly_name: "Workbench Node"' in text


def test_sign_firmware_takes_a_device_name_and_refuses_without_a_build(
    tmp_path, capsys, monkeypatch
) -> None:
    """The device form resolves to the last build — and says so when there is none."""
    project = _project_with_device(tmp_path)
    monkeypatch.chdir(project)
    assert main(["device", "sign-firmware", "bench-node"]) == 1
    err = capsys.readouterr().err
    assert "has no build to sign" in err
    assert "mcuhome device build bench-node" in err


def test_sign_firmware_on_an_unrelated_directory_names_what_it_expected(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["device", "sign-firmware", str(empty)]) == 1
    err = capsys.readouterr().err
    assert "build-report.json" in err


# --------------------------------------------------------------------------
# device boards / init rendering (PO 2026-08-15)
# --------------------------------------------------------------------------


def test_device_boards_lists_supported_and_planned(capsys) -> None:
    """The registry's answer, plus the stable docs link."""
    assert main(["device", "boards"]) == 0
    out = capsys.readouterr().out
    assert BOARD in out
    assert "Planned, not usable yet:" in out
    assert "https://t.mcuhome.org/cli/docs/device-supported-boards/" in out


def test_device_boards_as_a_document(capsys) -> None:
    assert main(["device", "boards", "-o", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    names = [entry["name"] for entry in document["boards"]]
    assert BOARD in names
    assert all("status" in entry for entry in document["planned"])


def test_init_lists_files_first_and_marks_directories(tmp_path, capsys, monkeypatch) -> None:
    """Files, then directories with a trailing slash (PO 2026-08-15)."""
    monkeypatch.chdir(tmp_path)
    assert main(["project", "init"]) == 0
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    dirs = [line for line in lines if line.endswith("/")]
    assert "devices/" in dirs
    assert "secrets/" in dirs
    file_positions = [lines.index(name) for name in (".gitignore", "mcuhome.yaml")]
    assert max(file_positions) < min(lines.index(d) for d in dirs)
    assert "Getting started: https://t.mcuhome.org/cli/docs/getting-started/" in "\n".join(lines)
