# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The command surface: exit codes, summary output, refusals."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
from conftest import EXAMPLES_DIR, FIXTURE_TREE, VALID_CONFIG, make_project
from mcuhome.compiler import container, localbuild, workspace
from mcuhome.compiler import localbackend as lb
from mcuhome.compiler.generate import APP_DIR
from mcuhome.model import __version__ as model_version
from mcuhome.model.manifest import MANIFEST_FILE
from mcuhome.model.model import MODEL_VERSION
from mcuhome.workbench import buildmethods, imgtool, sessionclient, signing
from mcuhome.workbench.project import Project

from mcuhome_cli import __version__ as cli_version
from mcuhome_cli import cli
from mcuhome_cli.cli import main
from mcuhome_cli.output import Output

EXAMPLE = EXAMPLES_DIR / "00-bmp180-two-endpoints.yaml"

#: The §7.2.1 build report a container build delivers (build-report.json):
#: the report version, the mandatory signing block, and the optional
#: memory table the linker enforced — no `inputs`/`outputs`/`signed`.
REPORT = {
    "report": 1,
    "signing": {
        "signature_type": "ecdsa-p256",
        "arguments": {
            "version": "0.1.0+0",
            "header-size": 512,
            "align": 4,
            "slot-size": 912 * 1024,
        },
    },
    "memory": [
        {"image": "mcuboot", "region": "FLASH", "used": 57344, "total": 65536, "percent": 87.5},
        {"image": "app", "region": "FLASH", "used": 859672, "total": 1048576, "percent": 82.0},
    ],
}


def _fake_local_run(model, **kwargs):
    """A stand-in for compose_local_build: the files a container delivers, no docker.

    Writes the unsigned firmware and the §7.2.1 build report into the
    per-invocation ``out`` the real backend would, and answers a successful
    :class:`~mcuhome.compiler.localbackend.LocalOutcome`. The private key is
    deliberately not among the arguments a build ever receives — the whole
    point of the local path — so this fake never sees one either.
    """
    work_root = Path(kwargs["work_root"])
    out = work_root / "backend" / "inv" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "firmware.hex").write_text(":00000001FF\n", "utf-8")
    (out / "firmware.bin").write_bytes(bytes(1024))
    (out / "bootloader.hex").write_text(":00000001FF\n", "utf-8")
    (out / "build-report.json").write_text(json.dumps(REPORT), "utf-8")
    roles = {
        "firmware.hex": "firmware",
        "firmware.bin": "firmware",
        "bootloader.hex": "bootloader",
        "build-report.json": "report",
    }
    artifacts = tuple(
        lb.Artifact(root="out", path=name, role=role, sha256="0" * 64)
        for name, role in roles.items()
    )
    outcome = lb.LocalOutcome(
        action="build",
        context_id="sha256:" + "1" * 64,
        exit_code=0,
        status="success",
        successful=True,
        artifacts=artifacts,
        out=out,
    )
    return localbuild.LocalBuildResult(
        outcome=outcome,
        out_dir=out,
        context_dir=work_root / "context",
        # `image` is None unless the build named one, exactly as the real
        # composition receives it before resolving the default.
        image=kwargs["image"] or container.IMAGE,
    )


def _local_failure(outcome: lb.LocalOutcome) -> buildmethods.BuildOutcome:
    """The dispatch's answer for a ``local`` build that ran and failed."""
    return buildmethods.BuildOutcome(
        method=buildmethods.LOCAL,
        successful=False,
        status=outcome.status,
        context_id=outcome.context_id,
        artifacts=(),
        out_dir=None,
        report=imgtool.BUILD_REPORT_FILE,
        detail=localbuild.LocalBuildResult(
            outcome=outcome, out_dir=Path("."), context_dir=Path("."), image=container.IMAGE
        ),
    )


def _fake_imgtool(monkeypatch, tmp_path):
    """Stub the one imgtool invocation and make discovery find a name.

    The same shape the detached-signing tests use: ``_run`` is replaced so
    no process starts, and ``MCUHOME_IMGTOOL`` names a script that is never
    executed because ``_run`` — the only thing that would — is stubbed.
    """
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> tuple[int, str]:
        commands.append(command)
        Path(command[-1]).write_bytes(b"signed")
        return 0, ""

    monkeypatch.setattr(imgtool, "_run", fake_run)
    monkeypatch.setenv(imgtool.IMGTOOL_VAR, str(tmp_path / "imgtool.py"))
    return commands


#: A sysbuild build log: one linker report per image, each preceded by the
#: only line that says whose output follows.
MEMORY_LOG = """\
[1/2] Performing build step for 'mcuboot'
Memory region         Used Size  Region Size  %age Used
           FLASH:       57344 B        64 KB      87.50%
             RAM:       29424 B       448 KB       6.41%
[2/2] Performing build step for 'app'
Memory region         Used Size  Region Size  %age Used
           FLASH:      859672 B         1 MB      81.99%
             RAM:      196296 B       448 KB      42.79%
        IDT_LIST:          0 GB        32 KB       0.00%
"""


def _planner(out_dir: Path):
    """A stand-in for plan_build that needs neither west nor a toolchain.

    Only the two things the tests cannot provide are replaced — the
    workspace and the prerequisite check. The command itself is assembled
    by the real code, because that is what the tests are looking at.
    """

    def plan(**kwargs) -> workspace.BuildPlan:
        app_dir = kwargs["out_dir"] / kwargs["app_subdir"]
        build_dir = kwargs["out_dir"] / workspace.BUILD_SUBDIR
        return workspace.BuildPlan(
            topdir=out_dir,
            app_dir=app_dir,
            build_dir=build_dir,
            command=workspace.west_build_command(
                app_dir=app_dir,
                build_dir=build_dir,
                board=kwargs["board"],
                snippets=kwargs["snippets"],
                bootloader_snippets=kwargs.get("bootloader_snippets", ()),
                signing_key=kwargs.get("signing_key"),
                detached_signing=kwargs.get("detached_signing", False),
                jobs=kwargs["jobs"],
            ),
            env={},
        )

    return plan


def test_version_reports_the_whole_stack(capsys) -> None:
    """One line per part (cli ADR 0002 §5), not the builder's number alone."""
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == f"mcuhome {cli_version}"
    parts = [line.split()[0] for line in lines]
    assert parts == ["mcuhome", "mcuhome-workbench", "mcuhome-compiler", "mcuhome-model"]
    assert lines[3] == f"mcuhome-model {model_version}"


def test_no_arguments_prints_help(capsys) -> None:
    assert main([]) == 0
    assert "usage: mcuhome" in capsys.readouterr().out


def test_validate_by_device_name_with_project_dir(capsys) -> None:
    assert main(["validate", "bench-node", "--project-dir", str(FIXTURE_TREE)]) == 0
    out = capsys.readouterr().out
    assert "Device     bench-node (Bench Node)" in out
    assert "Board      nrf7002dk/nrf5340/cpuapp" in out
    assert "Transport  Thread, end device" in out
    assert "Zephyr     4.4" in out
    assert "is valid." in out


def test_validate_by_file_path(capsys) -> None:
    assert main(["validate", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "endpoint 1 [temperature]: temperature_sensor (0x0302 rev 3)" in out
    assert "endpoint 2 [pressure]: pressure_sensor (0x0305 rev 2)" in out
    assert "temperature_measurement (0x0402 rev 4, 3 attributes)" in out
    assert "baro.temperature -> endpoint 1 0x0402/0x0000, every 10 s" in out
    assert "report on 0.1 °C change" in out
    assert "snippets: debug-rtt, matter, boot-mode" in out


def test_validate_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["validate", str(EXAMPLE)]) == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    [["-v", "validate", str(EXAMPLE)], ["validate", "-v", str(EXAMPLE)]],
)
def test_verbose_prints_the_model(capsys, argv: list[str]) -> None:
    assert main(argv) == 0
    out = capsys.readouterr().out
    payload = out[out.index("{") : out.rindex("}") + 1]
    assert json.loads(payload)["model_version"] == 1


def test_validate_reports_problems_and_exits_one(tmp_path, capsys) -> None:
    entry = tmp_path / "main.yaml"
    entry.write_text(VALID_CONFIG.replace("device_role: ftd", "device_role: sed"), "utf-8")
    assert main(["validate", str(entry)]) == 1
    captured = capsys.readouterr()
    assert "Sleepy end devices" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_unknown_device_exits_one(capsys) -> None:
    assert main(["validate", "nope", "--project-dir", str(FIXTURE_TREE)]) == 1
    assert "no device called" in capsys.readouterr().err


def test_build_compiles_what_it_generated(tmp_path, capsys, monkeypatch) -> None:
    """The whole local-dev command, unsigned build then the host signing step.

    Since E56 local-dev builds unsigned and the host signs afterwards, so
    the compiler stub leaves no signed file — the imgtool stub produces
    ``zephyr.signed.*``, exactly as it does for a detached build.
    """
    seen: dict[str, object] = {}

    def fake_run(plan, stream=None) -> tuple[int, str]:
        seen["command"] = plan.command
        seen["cwd"] = plan.topdir
        for image in ("mcuboot", APP_DIR):
            output = plan.build_dir / image / "zephyr"
            output.mkdir(parents=True)
            (output / "zephyr.elf").write_text("", "utf-8")
            (output / "zephyr.hex").write_text(":00000001FF\n", "utf-8")
            (output / "zephyr.bin").write_bytes(bytes(1024))
        app_output = plan.build_dir / APP_DIR / "zephyr"
        app_output.joinpath(".config").write_text(
            'CONFIG_ROM_START_OFFSET=0x200\nCONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION="0.0.0+0"\n',
            "utf-8",
        )
        # Sysbuild's combined hex falls back to the unsigned application; the
        # build drops it, and no signed file exists until the host signs.
        (plan.build_dir / "merged_nrf7002dk_nrf5340_cpuapp.hex").write_text("", "utf-8")
        return 0, MEMORY_LOG

    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", fake_run)
    _fake_imgtool(monkeypatch, tmp_path)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--method",
                "local-dev",
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Generated 12 files for bmp180-node" in out
    assert "app/src/mcuhome_config.c" in out
    assert "app/sysbuild.conf" in out
    # The application it generated is the application it built.
    assert str(tmp_path / "app") in " ".join(seen["command"])  # type: ignore[arg-type]
    assert "--board nrf7002dk/nrf5340/cpuapp" in " ".join(seen["command"])  # type: ignore[arg-type]
    # No private key in the west command — the bootloader gets the public
    # half, the host signing step keeps the private one (E56).
    assert "-DMCUHOME_DETACHED_SIGNING=y" in " ".join(seen["command"])  # type: ignore[arg-type]
    assert "Built bmp180-node." in out
    # Both images, each under its own sysbuild sub-directory, and the
    # host-signed application next to the raw one.
    assert "mcuboot (bootloader)  1.0 KiB" in out
    assert "app (application)  1.0 KiB" in out
    assert str(tmp_path / "build" / "mcuboot" / "zephyr" / "zephyr.hex") in out
    assert str(tmp_path / "build" / APP_DIR / "zephyr" / "zephyr.signed.bin") in out
    # No combined hex: an unsigned build's is dropped, uniform with the
    # container path, which never has one.
    assert "merged_nrf7002dk_nrf5340_cpuapp.hex" not in out
    assert "memory: FLASH 56.0 KiB of 64.0 KiB (87.5%)" in out
    assert "memory: FLASH 839.5 KiB of 1024.0 KiB (82.0%)" in out
    assert "memory: RAM 191.7 KiB of 448.0 KiB (42.8%)" in out
    # And the layout those images were built against.
    assert "Flash layout (class A, MCUboot swap-using-offset, staging: external-flash)" in out
    assert "image-0  internal 0x014000..0x0f8000   912 KiB" in out
    # The delivery path: a class-A board with Matter gets a Matter OTA file
    # wrapped around the freshly signed image (ADR 0015 decision 5).
    assert "Matter OTA image (version 0.1.0, SoftwareVersion 65536)" in out
    ota_file = tmp_path / "bmp180-node-0.1.0.ota"
    assert ota_file.is_file()
    manifest = json.loads((tmp_path / "build-manifest.json").read_text(encoding="utf-8"))
    # E56: no build signs itself — the manifest records the host signature.
    assert manifest["signing"]["signed_by_the_build"] is False
    assert manifest["signing"]["signed"] is True
    assert manifest["ota"]["path"] == "bmp180-node-0.1.0.ota"
    assert manifest["ota"]["software_version"] == 65536
    assert manifest["ota"]["size"] == ota_file.stat().st_size


def test_build_passes_the_configurations_snippets_and_then_the_extra_ones(
    tmp_path, monkeypatch
) -> None:
    """Per image, because sysbuild would otherwise give them to both.

    ``-S debug-rtt`` collapses into the always-included log transport
    instead of applying the snippet twice; a new snippet appends.
    """
    seen: list[str] = []

    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(
        workspace,
        "run_build",
        lambda plan, stream=None: (seen.extend(plan.command), (0, ""))[1],
    )
    # --no-sign isolates the west command from the host signing step, which
    # is identical for every snippet set: the command carries the snippets
    # either way.
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--method", "local-dev"]
    argv += ["--no-sign", "--public-key", str(_public_key(tmp_path))]
    argv += ["-S", "debug-rtt", "-S", "thread-sed"]
    assert main(argv) == 0
    assert "--snippet" not in seen
    assert f"-D{APP_DIR}_SNIPPET=debug-rtt;matter;boot-mode;thread-sed" in seen
    assert "-Dmcuboot_SNIPPET=boot-mode" in seen


def test_a_relative_build_dir_still_names_an_absolute_application(tmp_path, monkeypatch) -> None:
    """The build runs from the workspace top, not from the user's cwd."""
    seen: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(
        workspace,
        "run_build",
        lambda plan, stream=None: (seen.extend(plan.command), (0, ""))[1],
    )
    argv = ["build", str(EXAMPLE), "--build-dir", "out", "--method", "local-dev"]
    argv += ["--no-sign", "--public-key", str(_public_key(tmp_path))]
    assert main(argv) == 0
    assert str((tmp_path / "out" / APP_DIR).resolve()) in seen


def test_build_uses_the_jobs_flag_and_reports_its_source(tmp_path, capsys, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(
        workspace,
        "run_build",
        lambda plan, stream=None: (seen.extend(plan.command), (0, ""))[1],
    )
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path)]
    argv += ["--method", "local-dev", "--jobs", "3"]
    argv += ["--no-sign", "--public-key", str(_public_key(tmp_path))]
    assert main(argv) == 0
    assert "-o=-j3" in seen
    assert "jobs 3 (arguments)" in capsys.readouterr().out


def test_build_uses_the_environment_variable_when_no_flag_is_given(
    tmp_path, capsys, monkeypatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(
        workspace,
        "run_build",
        lambda plan, stream=None: (seen.extend(plan.command), (0, ""))[1],
    )
    monkeypatch.setenv(workspace.JOBS_VAR, "5")
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--method", "local-dev"]
    argv += ["--no-sign", "--public-key", str(_public_key(tmp_path))]
    assert main(argv) == 0
    assert "-o=-j5" in seen
    assert "jobs 5 (environment)" in capsys.readouterr().out


def test_build_auto_detects_when_neither_flag_nor_environment_is_given(
    tmp_path, capsys, monkeypatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(
        workspace,
        "run_build",
        lambda plan, stream=None: (seen.extend(plan.command), (0, ""))[1],
    )
    monkeypatch.delenv(workspace.JOBS_VAR, raising=False)
    monkeypatch.setattr(workspace, "detect_jobs", lambda: 7)
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--method", "local-dev"]
    argv += ["--no-sign", "--public-key", str(_public_key(tmp_path))]
    assert main(argv) == 0
    assert "-o=-j7" in seen
    assert "jobs 7 (auto)" in capsys.readouterr().out


def test_jobs_zero_is_a_plain_language_refusal(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["build", str(EXAMPLE), "--jobs", "0"])
    assert caught.value.code == 2
    assert "--jobs must be at least 1" in capsys.readouterr().err


def test_jobs_garbage_is_a_plain_language_refusal(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["build", str(EXAMPLE), "--jobs", "nope"])
    assert caught.value.code == 2
    assert "whole number of parallel build jobs" in capsys.readouterr().err


def test_build_reports_a_failed_compile_and_points_at_the_build_directory(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", lambda plan, stream=None: (2, "boom\n"))
    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--method",
                "local-dev",
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "The firmware did not compile (west build exited with 2)." in err
    assert str(tmp_path / "build") in err
    assert "Traceback" not in err


def test_build_without_a_flag_builds_in_the_container_and_signs_on_the_host(
    tmp_path, capsys, monkeypatch
) -> None:
    """E54's default, as the command line sees it.

    No flag drives the local ABI path: the container delivers an unsigned
    image plus the §7.2.1 report, and the host signs it — one command to a
    flashable image, with the private key never in a container.
    """
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    monkeypatch.setattr(
        workspace, "plan_build", lambda **kwargs: pytest.fail("the local-dev path ran by default")
    )
    commands = _fake_imgtool(monkeypatch, tmp_path)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"image {container.IMAGE}" in out
    assert "Built bmp180-node." in out
    # The unsigned image the container delivered was copied up, and the
    # host signed it beside it.
    assert (tmp_path / "firmware.bin").is_file()
    assert (tmp_path / "firmware.hex").is_file()
    assert (tmp_path / "build-report.json").is_file()
    assert (tmp_path / "firmware.signed.bin").is_file()
    assert (tmp_path / "firmware.signed.hex").is_file()
    assert len(commands) == 2  # one imgtool sign per firmware encoding
    for command in commands:
        assert command[command.index("--slot-size") + 1] == str(912 * 1024)
        assert command[command.index("--header-size") + 1] == "512"
    # No host-side stage-4 tree: the container generates from the model.
    assert not (tmp_path / "app").exists()
    # The .ota wraps the freshly signed image, in the same step.
    assert "Matter OTA image (version 0.1.0" in out
    assert (tmp_path / "bmp180-node-0.1.0.ota").is_file()


def test_the_local_build_prints_the_footprint_from_the_report(
    tmp_path, capsys, monkeypatch
) -> None:
    """The memory table comes from the container's report, not a host log."""
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    _fake_imgtool(monkeypatch, tmp_path)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "mcuboot: FLASH 56.0 KiB of 64.0 KiB (87.5%)" in out
    assert "app: FLASH 839.5 KiB of 1024.0 KiB (82.0%)" in out


def test_the_image_can_be_named_per_build(tmp_path, capsys, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def capture(model, **kwargs):
        seen["image"] = kwargs["image"]
        return _fake_local_run(model, **kwargs)

    monkeypatch.setattr(buildmethods, "compose_local_build", capture)
    _fake_imgtool(monkeypatch, tmp_path)
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--image", "localhost/b:wip"]
    argv += ["--signing-key", str(_private_key(tmp_path))]
    assert main(argv) == 0
    assert seen["image"] == "localhost/b:wip"
    assert "image localhost/b:wip" in capsys.readouterr().out


def test_a_missing_image_is_a_plain_refusal_not_a_traceback(tmp_path, capsys, monkeypatch) -> None:
    """The local path's image-not-found is a typed refusal, not a crash."""

    def refuse(model, **kwargs):
        raise localbuild.lb.BuildError(
            f"No build container on this host answers to {kwargs['image']}.",
            hint="pull or build the image, or point --image at one",
        )

    monkeypatch.setattr(buildmethods, "compose_local_build", refuse)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "answers to" in captured.err
    assert "Traceback" not in captured.err


def test_a_missing_sdk_source_is_a_clean_refusal(tmp_path, capsys, monkeypatch) -> None:
    """No --sdk-sources through any channel: a typed refusal, no docker.

    The image resolves (a stubbed inspect), and then the SDK pin cannot,
    which is the refusal E54 asks be surfaced cleanly rather than as a
    traceback. Docker is never reached for a build.
    """

    def inspect_only(argv, on_line=None):
        if argv[1:3] == ["image", "inspect"]:
            # The coupling labels are part of resolving: an image
            # carrying no `org.mcuhome.zephyr` serves no line and is
            # refused before the SDK is ever looked for (§2.1.1), which
            # would be a different refusal than the one under test.
            facts = {
                "RepoDigests": [f"{container.IMAGE}@sha256:{'1' * 64}"],
                "Config": {
                    "Labels": {
                        "org.mcuhome.contract": "1",
                        "org.mcuhome.zephyr": "4.4.0",
                        "org.mcuhome.toolchain": "zephyr-sdk-1.0.1",
                    }
                },
            }
            return lb.Completed(0, json.dumps(facts))
        raise AssertionError(f"no container should start: {argv}")

    monkeypatch.setattr(lb, "_run_command", inspect_only)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "SDK source" in err
    assert "Traceback" not in err


def test_build_generate_only_succeeds(tmp_path, capsys) -> None:
    assert main(["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--generate-only"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert str(tmp_path) in captured.out
    assert (tmp_path / "device-model.json").is_file()
    assert (tmp_path / "app" / "boards" / "nrf7002dk_nrf5340_cpuapp.overlay").is_file()


def test_build_defaults_to_a_build_dir_at_the_project_root(tmp_path, capsys) -> None:
    project = make_project(tmp_path / "config")
    (project / "devices" / "bench-node").mkdir(parents=True)
    (project / "devices" / "bench-node" / "main.yaml").write_text(VALID_CONFIG, "utf-8")

    assert main(["build", "bench-node", "--project-dir", str(project), "--generate-only"]) == 0
    assert (project / "build" / "bench-node" / APP_DIR / "prj.conf").is_file()
    # Build output stays out of the device configuration proper.
    assert list((project / "devices" / "bench-node").iterdir()) == [
        project / "devices" / "bench-node" / "main.yaml"
    ]
    assert "Generated 12 files for bench-node" in capsys.readouterr().out


def test_build_reports_configuration_problems_and_writes_nothing(tmp_path, capsys) -> None:
    entry = tmp_path / "main.yaml"
    entry.write_text(VALID_CONFIG.replace("device_role: ftd", "device_role: sed"), "utf-8")
    out_dir = tmp_path / "out"
    assert main(["build", str(entry), "--build-dir", str(out_dir), "--generate-only"]) == 1
    assert "Sleepy end devices" in capsys.readouterr().err
    assert not out_dir.exists()


def test_clean_refuses_cleanly(capsys) -> None:
    assert main(["clean", "--all"]) == 1
    assert "mcuhome clean is not implemented yet" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Commissioning codes
# --------------------------------------------------------------------------


def test_validate_prints_the_pairing_codes(capsys) -> None:
    assert main(["validate", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "Commissioning" in out
    assert "manual code    34970112332" in out
    assert "QR code        MT:Y.K90AFN00KA0648G00" in out
    assert "discriminator  3840 (0xF00)" in out


def test_the_published_test_credentials_are_called_out(capsys) -> None:
    assert main(["validate", "bench-node", "--project-dir", str(FIXTURE_TREE)]) == 0
    own = capsys.readouterr().out
    assert "published with the Matter SDK" not in own, "this fixture has credentials of its own"

    assert main(["validate", str(EXAMPLE)]) == 0
    assert "published with the Matter SDK" in capsys.readouterr().out


def test_build_prints_the_pairing_codes_last(tmp_path, capsys) -> None:
    assert main(["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--generate-only"]) == 0
    out = capsys.readouterr().out
    assert out.index("Commissioning") > out.index("Generated 12 files")
    assert "MT:Y.K90AFN00KA0648G00" in out


def test_init_pairing_writes_the_codes_and_prints_them(tmp_path, capsys) -> None:
    project = make_project(tmp_path / "config")
    (project / "devices" / "bench-node").mkdir(parents=True)
    entry = project / "devices" / "bench-node" / "main.yaml"
    entry.write_text(VALID_CONFIG.replace("    use_test_pairing: true\n", ""), "utf-8")

    assert main(["init-pairing", "bench-node", "--project-dir", str(project)]) == 0
    out = capsys.readouterr().out
    assert str(entry) in out
    assert "Commissioning" in out
    assert "manual code" in out
    assert "QR code        MT:" in out

    # The device now builds, and the codes it reports are the ones just
    # written — the loop that makes the credentials ordinary input.
    assert main(["validate", "bench-node", "--project-dir", str(project)]) == 0
    assert out.splitlines()[3] in capsys.readouterr().out


def test_init_pairing_refuses_a_second_time(tmp_path, capsys) -> None:
    entry = tmp_path / "main.yaml"
    entry.write_text(VALID_CONFIG, "utf-8")
    assert main(["init-pairing", str(entry)]) == 1
    captured = capsys.readouterr()
    assert "already has commissioning credentials" in captured.err
    assert "--force" in captured.err
    assert entry.read_text("utf-8") == VALID_CONFIG


def test_init_pairing_with_secrets_writes_two_files(tmp_path, capsys) -> None:
    entry = tmp_path / "main.yaml"
    entry.write_text(VALID_CONFIG.replace("    use_test_pairing: true\n", ""), "utf-8")
    assert main(["init-pairing", str(entry), "--secrets"]) == 0

    out = capsys.readouterr().out
    # A bare file's stand-in project is its own directory (ADR 0022), so
    # the values land in secrets/main.yaml next to it.
    assert str(tmp_path / "secrets" / "main.yaml") in out
    assert "!secret bench_node_passcode" in entry.read_text("utf-8")
    assert main(["validate", str(entry)]) == 0


# --------------------------------------------------------------------------
# -o json (cli ADR 0004 §2)
# --------------------------------------------------------------------------


def test_validate_json_prints_the_resolved_model(capsys) -> None:
    assert main(["validate", str(EXAMPLE), "-o", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["errors"] == []
    assert document["model"]["device"]["name"] == "bmp180-node"
    assert document["file"] == EXAMPLE.name


def test_validate_json_suppresses_the_human_summary(capsys) -> None:
    main(["validate", str(EXAMPLE), "-o", "json"])
    out = capsys.readouterr().out
    assert "Commissioning" not in out
    assert "is valid." not in out


def test_validate_json_reports_every_problem_with_a_relative_path(tmp_path, capsys) -> None:
    tree = make_project(tmp_path / "config")
    entry = tree / "devices" / "bench-node" / "main.yaml"
    entry.parent.mkdir(parents=True)
    entry.write_text(VALID_CONFIG.replace("nrf7002dk/nrf5340/cpuapp", "nrf99dk"), "utf-8")

    assert main(["validate", "bench-node", "--project-dir", str(tree), "-o", "json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["model"] is None
    problem = document["errors"][0]
    assert problem["file"] == "devices/bench-node/main.yaml"
    assert problem["kind"] == "ConfigError"
    assert problem["line"] and problem["hint"]


def test_a_refusal_before_the_tree_is_resolved_is_still_json(capsys) -> None:
    """Exit codes do not change with -o json, and neither does the stream."""
    assert main(["validate", "no-such-device", "--project-dir", "/nope", "-o", "json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["errors"][0]["kind"] == "ConfigError"


def test_build_json_mirrors_the_manifest(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", _fake_build_run)
    _fake_imgtool(monkeypatch, tmp_path)

    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path),
        "--method",
        "local-dev",
        "-o",
        "json",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    assert main(argv) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["device"] == "bmp180-node"
    # E56: no build signs itself, and the host step then signs it — so the
    # manifest the document mirrors says "not signed by the build" and yet
    # "signed".
    assert document["manifest"]["signing"]["signed_by_the_build"] is False
    assert document["manifest"]["signing"]["signed"] is True
    assert json.loads((tmp_path / MANIFEST_FILE).read_text("utf-8")) == document["manifest"]


def test_generate_only_json_says_there_is_no_manifest(tmp_path, capsys) -> None:
    assert (
        main(["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--generate-only", "-o", "json"])
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["manifest"] is None
    assert f"{APP_DIR}/prj.conf" in document["generated"]


# --------------------------------------------------------------------------
# The build manifest lands in the build directory
# --------------------------------------------------------------------------


def _fake_build_run(plan, stream=None) -> tuple[int, str]:
    """Stage 5 without a compiler: the files a real build leaves behind."""
    for image in ("mcuboot", APP_DIR):
        output = plan.build_dir / image / "zephyr"
        output.mkdir(parents=True, exist_ok=True)
        (output / "zephyr.elf").write_bytes(b"\x7fELF")
        (output / "zephyr.hex").write_text(":00000001FF\n", "utf-8")
        (output / "zephyr.bin").write_bytes(bytes(1024))
    app_output = plan.build_dir / APP_DIR / "zephyr"
    app_output.joinpath(".config").write_text(
        'CONFIG_ROM_START_OFFSET=0x200\nCONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION="0.0.0+0"\n', "utf-8"
    )
    if "-DMCUHOME_DETACHED_SIGNING=y" not in plan.command:
        app_output.joinpath("zephyr.signed.bin").write_bytes(bytes(1600))
        app_output.joinpath("zephyr.signed.hex").write_text(":00000001FF\n", "utf-8")
    (plan.build_dir / "merged_nrf7002dk_nrf5340_cpuapp.hex").write_text(":00000001FF\n", "utf-8")
    return 0, MEMORY_LOG


def test_a_build_writes_its_manifest(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", _fake_build_run)
    _fake_imgtool(monkeypatch, tmp_path)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path),
                "--method",
                "local-dev",
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )
    assert str(tmp_path / MANIFEST_FILE) in capsys.readouterr().out
    document = json.loads((tmp_path / MANIFEST_FILE).read_text("utf-8"))
    assert document["device"]["name"] == "bmp180-node"
    assert document["build"]["snippets"] == ["debug-rtt", "matter", "boot-mode"]
    assert document["signing"]["arguments"]["slot-size"] == 912 * 1024


# --------------------------------------------------------------------------
# Detached signing (ADR 0015 decision 8)
# --------------------------------------------------------------------------


def _private_key(tmp_path: Path) -> Path:
    """A throwaway private key, stated explicitly wherever a build signs.

    Since ADR 0015 §8 the default signing key lives *inside the project*
    (``secrets/firmware/mcuboot.yaml``, generated on first need) — and a
    bare example file's stand-in project is its own directory, so a test
    that signs without stating a key would generate one into the
    repository checkout (the conftest guard fails the test that tries).
    """
    key = tmp_path / "signing.key"
    if not key.exists():
        key.write_text(signing.generate_key_pem(0x1234567890ABCDEF), "utf-8")
        key.chmod(0o600)  # key material: anything wider is a refusal (ADR 0022 §5)
    return key


def _public_key(tmp_path: Path) -> Path:
    private = _private_key(tmp_path)
    public = tmp_path / "signing.pub"
    public.write_text(signing.public_key_pem(private.read_text("utf-8")), "utf-8")
    return public


def test_no_sign_without_a_public_key_is_an_exit_2_refusal(tmp_path, capsys) -> None:
    """The validate phase (cli ADR 0004 §3/§4): a missing required input is
    a usage refusal — exit 2, and the build never starts."""
    assert main(["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--no-sign"]) == 2
    err = capsys.readouterr().err
    assert "mcuhome public-key" in err


def test_no_sign_refuses_a_private_key_as_the_public_one(tmp_path, capsys) -> None:
    """The one mistake the feature exists to prevent — caught in the
    validate phase, so it is an exit-2 refusal and the build never runs."""
    private = tmp_path / "signing.key"
    private.write_text(signing.generate_key_pem(0x1234567890ABCDEF), "utf-8")
    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path / "out"),
                "--no-sign",
                "--public-key",
                str(private),
            ]
        )
        == 2
    )
    assert "is a private key" in capsys.readouterr().err


def test_no_sign_builds_unsigned_and_says_what_is_next(tmp_path, capsys, monkeypatch) -> None:
    out_dir = tmp_path / "out"
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", _fake_build_run)

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(out_dir),
                "--method",
                "local-dev",
                "--no-sign",
                "--public-key",
                str(_public_key(tmp_path)),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "public key" in out
    assert f"mcuhome sign {out_dir}" in out

    document = json.loads((out_dir / MANIFEST_FILE).read_text("utf-8"))
    assert document["signing"]["signed_by_the_build"] is False
    assert document["signing"]["signed"] is False
    assert document["signing"]["arguments"]["header-size"] == 512
    # Nothing in the directory may look bootable: no signed image, and no
    # combined hex, which sysbuild fills with the *unsigned* application.
    assert not (out_dir / "build" / APP_DIR / "zephyr" / "zephyr.signed.bin").exists()
    assert not list((out_dir / "build").glob(workspace.MERGED_IMAGE_GLOB))
    assert document["merged"] is None
    # And no .ota either: it wraps the SIGNED image. What the manifest does
    # carry is the parameters, which is what lets `mcuhome sign` write one
    # on a machine that has no device configuration (ADR 0015 decision 8).
    assert document["ota"]["path"] is None
    assert document["ota"]["version"] == "0.1.0"
    assert not list(out_dir.glob("*.ota"))


def test_the_detached_build_command_carries_the_public_key(tmp_path, monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))

    def capture(plan, stream=None):
        seen["command"] = plan.command
        return _fake_build_run(plan, stream)

    monkeypatch.setattr(workspace, "run_build", capture)
    public = _public_key(tmp_path)
    main(
        [
            "build",
            str(EXAMPLE),
            "--build-dir",
            str(tmp_path / "out"),
            "--method",
            "local-dev",
            "--no-sign",
            "--public-key",
            str(public),
        ]
    )
    command = seen["command"]
    assert f'-D{workspace.SIGNING_KEY_OPTION}="{public}"' in command
    assert "-DMCUHOME_DETACHED_SIGNING=y" in command


def test_sign_applies_the_manifests_parameters(tmp_path, capsys, monkeypatch) -> None:
    out_dir = tmp_path / "out"
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", _fake_build_run)
    main(
        [
            "build",
            str(EXAMPLE),
            "--build-dir",
            str(out_dir),
            "--method",
            "local-dev",
            "--no-sign",
            "--public-key",
            str(_public_key(tmp_path)),
        ]
    )
    capsys.readouterr()

    commands: list[list[str]] = []

    def fake_imgtool(command: list[str]) -> tuple[int, str]:
        commands.append(command)
        Path(command[-1]).write_bytes(b"signed")
        return 0, ""

    monkeypatch.setattr(imgtool, "_run", fake_imgtool)
    # Discovery would refuse on machines without a west workspace or an
    # installed imgtool; the override names a script that never runs,
    # because _run — the only thing that would run it — is replaced.
    monkeypatch.setenv(imgtool.IMGTOOL_VAR, str(tmp_path / "imgtool.py"))
    assert main(["sign", str(out_dir), "--signing-key", str(tmp_path / "signing.key")]) == 0

    out = capsys.readouterr().out
    assert "Signed the application image" in out
    assert len(commands) == 2  # one per artifact format
    for command in commands:
        assert command[command.index("--slot-size") + 1] == str(912 * 1024)
        assert command[command.index("--header-size") + 1] == "512"
        assert command[command.index("--align") + 1] == "4"

    document = json.loads((out_dir / MANIFEST_FILE).read_text("utf-8"))
    assert document["signing"]["signed"] is True
    assert document["signing"]["signed_by_the_build"] is False
    application = next(image for image in document["images"] if image["name"] == APP_DIR)
    assert any(entry["path"].endswith("zephyr.signed.bin") for entry in application["files"])

    # The delivery path closes here rather than at build time: the .ota is
    # written where the key is, wrapping the image that was just signed.
    assert "Matter OTA image (version 0.1.0" in out
    ota_file = out_dir / "bmp180-node-0.1.0.ota"
    assert ota_file.is_file()
    assert document["ota"]["path"] == "bmp180-node-0.1.0.ota"
    assert document["ota"]["size"] == ota_file.stat().st_size


# --------------------------------------------------------------------------
# The default local path: auto-sign, --no-sign, the sign verb's two shapes,
# and the security invariant (E54/E55)
# --------------------------------------------------------------------------


def test_no_sign_local_stops_at_the_unsigned_image(tmp_path, capsys, monkeypatch) -> None:
    """--no-sign on the default path leaves the container's delivery untouched.

    No signature, no combined-flash lookalike, no .ota — the same "nothing
    flashable yet" state the detached workflow expects. The build report is
    what a second machine signs from.
    """
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    out_dir = tmp_path / "out"

    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(out_dir),
                "--no-sign",
                "--public-key",
                str(_public_key(tmp_path)),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "public key" in out
    assert f"mcuhome sign {out_dir}" in out
    assert (out_dir / "firmware.bin").is_file()
    assert (out_dir / "build-report.json").is_file()
    # Nothing signed, so nothing to wrap and nothing that looks bootable.
    assert not (out_dir / "firmware.signed.bin").exists()
    assert not list(out_dir.glob("*.ota"))


def test_no_sign_local_still_needs_a_public_key(tmp_path, capsys, monkeypatch) -> None:
    """The bootloader needs the public key even when the build never signs."""
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    assert main(["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--no-sign"]) == 2
    assert "mcuhome public-key" in capsys.readouterr().err


def test_a_no_sign_rebuild_drops_a_prior_signed_image_and_ota(
    tmp_path, capsys, monkeypatch
) -> None:
    """A --no-sign rebuild in a signed directory leaves nothing flashable behind.

    The local path copies the container's UNSIGNED firmware into the
    persistent build directory. A prior signed build's ``firmware.signed.*``
    and its ``.ota`` would otherwise survive there, next to fresh unrelated
    unsigned firmware, while the CLI reports nothing flashable — a
    boot-bricking lookalike. The rebuild drops them.
    """
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    out_dir = tmp_path / "out"

    # A first build that signs on the host: firmware.signed.* and an .ota land.
    _fake_imgtool(monkeypatch, tmp_path)
    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(out_dir),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (out_dir / "firmware.signed.bin").is_file()
    assert (out_dir / "firmware.signed.hex").is_file()
    assert list(out_dir.glob("*.ota"))

    # A --no-sign rebuild in the SAME directory.
    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(out_dir),
                "--no-sign",
                "--public-key",
                str(_public_key(tmp_path)),
            ]
        )
        == 0
    )

    # Only the fresh unsigned firmware remains; the stale signed lookalikes
    # and the .ota are gone.
    assert (out_dir / "firmware.bin").is_file()
    assert not (out_dir / "firmware.signed.bin").exists()
    assert not (out_dir / "firmware.signed.hex").exists()
    assert not list(out_dir.glob("*.ota"))


def test_sign_reads_a_build_report_directory(tmp_path, capsys, monkeypatch) -> None:
    """`mcuhome sign` on the container backend's delivery uses the §7.2.1 report."""
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    out_dir = tmp_path / "out"
    main(
        [
            "build",
            str(EXAMPLE),
            "--build-dir",
            str(out_dir),
            "--no-sign",
            "--public-key",
            str(_public_key(tmp_path)),
        ]
    )
    capsys.readouterr()
    # A build-report directory holds no build-manifest.json — the report
    # shape is the one chosen.
    assert (out_dir / "build-report.json").is_file()
    assert not (out_dir / MANIFEST_FILE).exists()

    commands = _fake_imgtool(monkeypatch, tmp_path)
    key = tmp_path / "signing.key"
    key.write_text(signing.generate_key_pem(0x1234567890ABCDEF), "utf-8")
    assert main(["sign", str(out_dir), "--signing-key", str(key)]) == 0

    out = capsys.readouterr().out
    assert "Signed the application image" in out
    assert (out_dir / "firmware.signed.bin").is_file()
    assert (out_dir / "firmware.signed.hex").is_file()
    assert len(commands) == 2
    for command in commands:
        assert command[command.index("--slot-size") + 1] == str(912 * 1024)
        assert command[command.index("--header-size") + 1] == "512"
        assert command[command.index("--align") + 1] == "4"


def test_sign_chooses_the_report_shape_by_which_file_is_present(tmp_path) -> None:
    """One verb, two report shapes: build-manifest wins, else build-report."""
    manifest_dir = tmp_path / "local-dev"
    manifest_dir.mkdir()
    (manifest_dir / MANIFEST_FILE).write_text("{}", "utf-8")
    assert cli._target_is_build_report(manifest_dir) is False

    report_dir = tmp_path / "container"
    report_dir.mkdir()
    (report_dir / imgtool.BUILD_REPORT_FILE).write_text(json.dumps(REPORT), "utf-8")
    assert cli._target_is_build_report(report_dir) is True

    # A directory that carries both keeps the richer manifest path.
    both = tmp_path / "both"
    both.mkdir()
    (both / MANIFEST_FILE).write_text("{}", "utf-8")
    (both / imgtool.BUILD_REPORT_FILE).write_text(json.dumps(REPORT), "utf-8")
    assert cli._target_is_build_report(both) is False

    # The report file named directly is unambiguous.
    assert cli._target_is_build_report(report_dir / imgtool.BUILD_REPORT_FILE) is True
    assert cli._target_is_build_report(manifest_dir / MANIFEST_FILE) is False


def test_sign_on_a_directory_with_no_build_output_is_a_clean_refusal(tmp_path, capsys) -> None:
    """`mcuhome sign` on a directory holding neither a manifest nor a report refuses cleanly.

    Neither file present means the manifest path is taken (a manifest would
    win if it existed), so the refusal is the missing build-manifest.json —
    a typed message, not a traceback.
    """
    key = _private_key(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["sign", str(empty), "--signing-key", str(key)]) == 1
    err = capsys.readouterr().err
    assert MANIFEST_FILE in err
    assert "Traceback" not in err


def test_the_private_key_never_reaches_the_local_backend(tmp_path, capsys, monkeypatch) -> None:
    """E55 security invariant, at the command boundary.

    The container gets the **public** key and nothing else: the private key
    is never one of the arguments the build backend is handed, and what it
    *is* handed for the key is a public key. Run inside a real project, so
    the key under test is the per-project one (ADR 0015 §8) — generated as
    ``secrets/firmware/mcuboot.pem`` by this very build, and still
    absent from everything the backend received, path and value alike.
    The docker-argv half of the same invariant is asserted against the
    real backend in the workbench's own ``test_localbuild.py``.
    """
    handed: dict[str, object] = {}

    def record(model, **kwargs):
        handed.update(kwargs)
        return _fake_local_run(model, **kwargs)

    monkeypatch.setattr(buildmethods, "compose_local_build", record)
    _fake_imgtool(monkeypatch, tmp_path)

    project = make_project(tmp_path / "proj")
    device_dir = project / "devices" / "bmp180-node"
    device_dir.mkdir(parents=True)
    (device_dir / "main.yaml").write_text(EXAMPLE.read_text("utf-8"), "utf-8")

    argv = ["build", "bmp180-node", "--project-dir", str(project)]
    argv += ["--build-dir", str(tmp_path / "out")]
    assert main(argv) == 0

    # The per-project key was generated on first need — the human header
    # says so loudly, naming the key file itself — and yet nothing the
    # backend received names or contains it. Two files since the !file
    # shape (draft ADR 0015 §8): mcuboot.pem is the material,
    # mcuboot.yaml references it.
    out = capsys.readouterr().out
    key_home = project / "secrets" / "firmware" / "mcuboot.pem"
    assert key_home.is_file()
    assert (project / "secrets" / "firmware" / "mcuboot.yaml").is_file()
    assert str(key_home) in out
    assert "NEW" in out
    everything = " ".join(str(value) for value in handed.values())
    assert str(key_home) not in everything
    assert "PRIVATE KEY" not in everything
    # What the backend WAS given for the key is the public half — of
    # exactly the key that lives in the project's secrets.
    assert signing.looks_like_p256_public_key(str(handed["signing_pub"]))
    assert not signing.looks_like_p256_key(str(handed["signing_pub"]))
    resolved = signing.signing_key(
        None, env={}, project=Project(root=project, discovered=True), create=False
    )
    assert signing.public_key_pem(resolved.pem) == handed["signing_pub"]


# --------------------------------------------------------------------------
# new, public-key, schema
# --------------------------------------------------------------------------


def test_init_then_new_is_the_whole_start(tmp_path, capsys, monkeypatch) -> None:
    """The fresh-machine loop: init creates the project, new the device."""
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "Created an MCUHome project" in out
    assert ".mcuhome-project-root" in out
    assert (tmp_path / "secrets").stat().st_mode & 0o777 == 0o700
    assert "secrets/" in (tmp_path / ".gitignore").read_text("utf-8")
    assert "build/" in (tmp_path / ".gitignore").read_text("utf-8")

    assert main(["new", "bench-node", "--board", "nrf7002dk/nrf5340/cpuapp"]) == 0
    out = capsys.readouterr().out
    assert "mcuhome init-pairing bench-node" in out
    assert (tmp_path / "devices" / "bench-node" / "main.yaml").is_file()


def test_init_on_an_existing_project_is_a_no_op(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["init"]) == 0
    assert "already an MCUHome project" in capsys.readouterr().out


def test_init_refuses_a_directory_with_existing_work(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("mine\n", "utf-8")
    assert main(["init"]) == 1
    err = capsys.readouterr().err
    assert "not empty" in err
    assert "notes.txt" in err
    assert "--force" in err
    assert not (tmp_path / ".mcuhome-project-root").exists()
    assert main(["init", "--force"]) == 0
    assert (tmp_path / ".mcuhome-project-root").is_file()
    assert (tmp_path / "notes.txt").read_text("utf-8") == "mine\n"


def test_new_outside_any_project_points_at_init(tmp_path, capsys, monkeypatch) -> None:
    """The old implicit tree creation is gone: a device needs a project."""
    monkeypatch.chdir(tmp_path)
    assert main(["new", "bench-node", "--board", "nrf7002dk/nrf5340/cpuapp"]) == 1
    err = capsys.readouterr().err
    assert "No MCUHome project" in err
    assert "mcuhome init" in err
    assert not (tmp_path / "devices").exists()


def test_new_refuses_an_existing_device(tmp_path, capsys) -> None:
    project = make_project(tmp_path / "proj")
    argv = ["new", "bench-node", "--board", "nrf7002dk/nrf5340/cpuapp"]
    argv += ["--project-dir", str(project)]
    assert main(argv) == 0
    capsys.readouterr()
    assert main(argv) == 1
    assert "already a device" in capsys.readouterr().err


def test_new_refuses_a_board_nobody_brought_up(tmp_path, capsys) -> None:
    project = make_project(tmp_path / "proj")
    argv = ["new", "bench-node", "--board", "nrf99dk", "--project-dir", str(project)]
    assert main(argv) == 1
    assert "nrf7002dk/nrf5340/cpuapp" in capsys.readouterr().err


def test_public_key_writes_the_public_half(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv(signing.KEY_VAR, str(_private_key(tmp_path)))

    assert main(["public-key"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("-----BEGIN PUBLIC KEY-----")
    assert signing.looks_like_p256_public_key(printed)

    assert main(["public-key", "-o", str(tmp_path / "signing.pub")]) == 0
    assert (tmp_path / "signing.pub").read_text("utf-8") == printed


def test_public_key_outside_a_project_names_every_way_to_a_key(
    tmp_path, capsys, monkeypatch
) -> None:
    """No project, no override: a refusal in words, never a generated key."""
    monkeypatch.chdir(tmp_path)
    assert main(["public-key"]) == 1
    err = capsys.readouterr().err
    assert "secrets/firmware/mcuboot.yaml" in err
    assert "--signing-key" in err
    assert list(tmp_path.iterdir()) == []


def test_public_key_never_creates_a_key(tmp_path, capsys, monkeypatch) -> None:
    """Inside a project without a key it still refuses rather than generates."""
    project = make_project(tmp_path / "proj")
    monkeypatch.chdir(project)
    assert main(["public-key"]) == 1
    err = capsys.readouterr().err
    assert "no such file" in err
    assert "mcuboot.yaml" in err
    assert not (project / "secrets" / "firmware").exists()


def test_schema_prints_the_configuration_schema(capsys) -> None:
    assert main(["schema"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["$id"].endswith("main.schema.json")
    assert "device" in document["properties"]


def test_schema_registry_prints_the_registry(tmp_path, capsys) -> None:
    assert main(["schema", "registry", "-o", str(tmp_path / "registry.json")]) == 0
    assert "registry" in capsys.readouterr().out
    document = json.loads((tmp_path / "registry.json").read_text("utf-8"))
    assert document["boards"][0]["name"] == "nrf7002dk/nrf5340/cpuapp"


# --------------------------------------------------------------------------
# build --model: the build server's entry point (dashboard ADR 0007 §4)
# --------------------------------------------------------------------------


def test_build_from_a_model_generates_the_same_tree_as_from_the_yaml(
    tmp_path, capsys, monkeypatch
) -> None:
    """The property the remote-build contract rests on.

    The canonical model is the wire format (builder-pipeline.md §6): the
    dashboard resolves stages 1-3 and sends the model, a build server runs
    stages 4-5 on it and never sees the configuration tree. That is only a
    contract if both routes produce the *same* application — including the
    "generated from" line in every file header, which is why the source
    file name is a field of the model rather than an argument of stage 4.
    """
    direct = tmp_path / "direct"
    assert main(["build", str(EXAMPLE), "--build-dir", str(direct), "--generate-only"]) == 0
    capsys.readouterr()

    from_model = tmp_path / "from-model"
    assert (
        main(
            [
                "build",
                "--model",
                str(direct / "device-model.json"),
                "--build-dir",
                str(from_model),
                "--generate-only",
            ]
        )
        == 0
    )
    capsys.readouterr()

    left = sorted(path.relative_to(direct) for path in direct.rglob("*") if path.is_file())
    right = sorted(path.relative_to(from_model) for path in from_model.rglob("*") if path.is_file())
    assert left == right
    for relative in left:
        assert (direct / relative).read_bytes() == (from_model / relative).read_bytes(), relative


def test_build_from_a_model_reads_no_configuration_tree(tmp_path, monkeypatch) -> None:
    """A build server holds neither the tree nor the secrets file.

    Enforced by making the resolution the other path uses explode: if
    --model touched it, this test would fail instead of quietly passing on
    a machine that happens to have a tree.
    """
    direct = tmp_path / "direct"
    main(["build", str(EXAMPLE), "--build-dir", str(direct), "--generate-only"])

    def explode(*arguments, **keywords):  # pragma: no cover - must not run
        raise AssertionError("--model must not resolve a configuration tree")

    monkeypatch.setattr(cli.api, "find_device", explode)
    assert (
        main(
            [
                "build",
                "--model",
                str(direct / "device-model.json"),
                "--build-dir",
                str(tmp_path / "out"),
                "--generate-only",
            ]
        )
        == 0
    )


def test_build_help_advertises_the_model_flag(capsys) -> None:
    """The build server feature-probes the help text for it."""
    with pytest.raises(SystemExit):
        main(["build", "--help"])
    assert "--model" in capsys.readouterr().out


def test_build_refuses_a_device_and_a_model_at_once(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["build", str(EXAMPLE), "--model", "model.json"])
    assert "not allowed with" in capsys.readouterr().err


def test_build_needs_one_of_the_two(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["build"])
    assert "required" in capsys.readouterr().err


def test_a_model_of_the_wrong_version_names_both_numbers(tmp_path, capsys) -> None:
    path = tmp_path / "device-model.json"
    path.write_text(json.dumps({"model_version": 99}), encoding="utf-8")
    assert main(["build", "--model", str(path), "--generate-only"]) == 1
    err = capsys.readouterr().err
    assert "version 99" in err
    assert f"implements version {MODEL_VERSION}" in err


def test_a_model_that_is_not_json_is_refused(tmp_path, capsys) -> None:
    path = tmp_path / "device-model.json"
    path.write_text("{not json", encoding="utf-8")
    assert main(["build", "--model", str(path), "--generate-only"]) == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_a_truncated_model_says_it_is_truncated(tmp_path, capsys) -> None:
    path = tmp_path / "device-model.json"
    path.write_text(json.dumps({"model_version": MODEL_VERSION}), encoding="utf-8")
    assert main(["build", "--model", str(path), "--generate-only"]) == 1
    assert "missing something this builder needs" in capsys.readouterr().err


def test_a_missing_model_file_says_where_one_comes_from(tmp_path, capsys) -> None:
    assert main(["build", "--model", str(tmp_path / "nope.json"), "--generate-only"]) == 1
    assert "device-model.json" in capsys.readouterr().err


def test_model_errors_serialize_in_json_mode(tmp_path, capsys) -> None:
    """-o json stays a document even when the model was the problem."""
    path = tmp_path / "device-model.json"
    path.write_text(json.dumps({"model_version": 99}), encoding="utf-8")
    assert main(["build", "--model", str(path), "--generate-only", "-o", "json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["errors"][0]["message"].startswith("The device model")


def test_a_failed_container_build_quotes_the_programs_own_account():
    """§5.4's reason, error message and details reach the user verbatim.

    A program that refuses before it runs anything writes only the result
    document and no build log — so the document is the entire diagnosis,
    and dropping it once reduced a precise in-container refusal to
    "status 'failure'; exited 1" in CI.
    """
    outcome = lb.LocalOutcome(
        action="build",
        context_id="sha256:" + "1" * 64,
        exit_code=1,
        status="failure",
        successful=False,
        problems=("the program reported status 'failure'", "the program exited 1"),
        result={
            "result": 1,
            "action": "build",
            "status": "failure",
            "reason": "error.context.mismatch",
            "error": {
                "retryable": False,
                "message": "the context disagrees with its integrity list",
                "details": {"paths": ["model/device-model.json"]},
            },
        },
    )
    failure = cli._delivered_build_failed(_local_failure(outcome))
    assert "error.context.mismatch" in failure.message
    assert "disagrees with its integrity list" in failure.message
    assert "model/device-model.json" in failure.message
    # The hint names where the build ran as a complete phrase, not as a
    # noun the branch below it has to finish.
    assert "the build ran in the MCUHome build container," in (failure.hint or "")
    assert "--method local-dev compiles on the host instead" in (failure.hint or "")


def test_a_failed_container_build_without_a_document_stays_terse():
    """No result document, no invented account — the judgement stands alone."""
    outcome = lb.LocalOutcome(
        action="build",
        context_id="",
        exit_code=137,
        status="failure",
        successful=False,
        problems=("no result document was written at the path the request named",),
        result=None,
    )
    failure = cli._delivered_build_failed(_local_failure(outcome))
    assert "The program said" not in failure.message
    assert "no result document" in failure.message


def test_a_failed_remote_build_quotes_the_verdicts_error_envelope():
    """The same renderer, the other method: a verdict instead of a document.

    ``remote`` has no result document to quote — the server relays the
    program's account in the verdict's error envelope — and the refusal a
    user reads must carry it just the same, out of the one renderer.
    """
    outcome = buildmethods.BuildOutcome(
        method=buildmethods.REMOTE,
        successful=False,
        status="failure",
        context_id="sha256:" + "2" * 64,
        artifacts=(),
        out_dir=None,
        report=imgtool.BUILD_REPORT_FILE,
        detail=sessionclient.RemoteBuildResult(
            action="build",
            context_id="sha256:" + "2" * 64,
            status="failure",
            successful=False,
            artifacts=(),
            out=None,
            error={"message": "the compiler ran out of memory", "details": {"image": "app"}},
        ),
    )
    failure = cli._delivered_build_failed(outcome)
    assert "ran out of memory" in failure.message
    assert '"app"' in failure.message
    assert "the build ran on a build server," in (failure.hint or "")


# --------------------------------------------------------------------------
# Choosing a build method: the ladder and the one signing step
# (ADR 0020 decision 6, E53, E54, E56, E62)
# --------------------------------------------------------------------------


def _build_args(**overrides) -> argparse.Namespace:
    """The parsed ``build`` arguments the ladder reads, and nothing else."""
    values = {"method": None, "server": None, "token": None}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_the_method_ladder_takes_the_flag_first() -> None:
    """Rung 1: an explicit --method beats anything in the environment."""
    env = {cli.METHOD_VAR: buildmethods.LOCAL}
    assert cli._resolve_method(_build_args(method="remote"), env) == buildmethods.REMOTE


def test_the_method_ladder_falls_to_the_environment() -> None:
    """Rung 2: no flag, so the variable decides."""
    env = {cli.METHOD_VAR: buildmethods.LOCAL_DEV}
    assert cli._resolve_method(_build_args(), env) == buildmethods.LOCAL_DEV


def test_the_method_ladder_ends_at_the_local_container() -> None:
    """Rung 3 is the default, not a configuration file (E54).

    The configured file (E63) names build *servers*; no decision says a
    build method may be read out of it, so this ladder stops one rung
    short rather than inventing a key — an empty environment gets the
    decided default.
    """
    assert cli._resolve_method(_build_args(), {}) == buildmethods.LOCAL


def test_native_is_gone_and_is_now_an_unknown_argument(tmp_path, capsys) -> None:
    """E62: ``--method local-dev`` is the only spelling of the host method.

    The alias is not deprecated, it is absent — argparse refuses it as an
    unknown argument with exit code 2, the same as any typo. The project
    is not public, so no invocation outside these repositories can have
    depended on it.
    """
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--native"]
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 2
    assert "unrecognized arguments: --native" in capsys.readouterr().err


def test_an_unknown_method_is_a_refusal_listing_the_real_ones(tmp_path, capsys) -> None:
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path), "--method", "cloud"]
    assert main(argv) == 1
    err = capsys.readouterr().err
    for name in buildmethods.METHODS:
        assert name in err
    assert "Traceback" not in err


def test_the_server_ladder_takes_the_flag_then_the_variable() -> None:
    """E53, verbatim: --server/--token, then MCUHOME_BUILD_SERVER/_TOKEN."""
    quiet = Output()
    env = {cli.SERVER_VAR: "ws://from-env/ws", cli.TOKEN_VAR: "env-token"}
    assert cli._remote_server(_build_args(), env, quiet) == ("ws://from-env/ws", "env-token")
    args = _build_args(server="ws://from-flag/ws", token="flag-token")
    assert cli._remote_server(args, env, quiet) == ("ws://from-flag/ws", "flag-token")


def test_the_server_ladder_falls_to_the_file_and_then_to_nothing(tmp_path) -> None:
    """Rung 3 exists now (E63), and the rung below it is still "nothing".

    This test used to pin "the ladder ends at the flag and the variable",
    which was true only while ``$XDG_CONFIG_HOME/mcuhome/`` had no decided
    file format. It has one: ``build-servers.toml`` plus a token file per
    label. An environment that names neither still resolves to nothing —
    that is what makes ``RemoteNotConfigured`` the refusal a user sees
    rather than a server nobody chose.
    """
    config_home = tmp_path / "xdg"
    directory = config_home / "mcuhome"
    (directory / "tokens").mkdir(parents=True)
    (directory / "build-servers.toml").write_text(
        'default = "home"\n\n[server.home]\nurl = "wss://build.lan:8443/ws"\n',
        encoding="utf-8",
    )
    (directory / "tokens" / "home").write_text("file-token\n", encoding="utf-8")
    (directory / "tokens" / "home").chmod(0o600)

    quiet = Output()
    env = {"XDG_CONFIG_HOME": str(config_home)}
    assert cli._remote_server(_build_args(), env, quiet) == (
        "wss://build.lan:8443/ws",
        "file-token",
    )

    # The rung above still wins, and a label reaches the file from it.
    with_flag = _build_args(server="ws://from-flag/ws", token="flag-token")
    assert cli._remote_server(with_flag, env, quiet) == ("ws://from-flag/ws", "flag-token")
    assert cli._remote_server(_build_args(server="home"), env, quiet) == (
        "wss://build.lan:8443/ws",
        "file-token",
    )

    # And with no file anywhere, the ladder ends where it always did.
    assert cli._remote_server(_build_args(), {}, quiet) == (None, None)


def test_a_configured_label_reaches_the_remote_method_with_its_token(
    tmp_path, capsys, monkeypatch
) -> None:
    """The rung, end to end: ``--server home`` builds against home's url.

    The file rung is only worth anything if what it resolves arrives in
    the request the method receives, so that is what is asserted — the
    url out of the table and the token out of ``tokens/home``, neither of
    which appears anywhere on the command line.
    """
    config_home = tmp_path / "xdg"
    directory = config_home / "mcuhome"
    (directory / "tokens").mkdir(parents=True)
    (directory / "build-servers.toml").write_text(
        '[server.home]\nurl = "wss://build.lan:8443/ws"\n', encoding="utf-8"
    )
    (directory / "tokens" / "home").write_text("file-token\n", encoding="utf-8")
    (directory / "tokens" / "home").chmod(0o600)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    seen: list[buildmethods.BuildRequest] = []

    async def refuse(request, *, method):
        seen.append(request)
        raise buildmethods.RemoteNotConfigured("stopped here on purpose", hint="nothing to fix")

    monkeypatch.setattr(cli.api, "run_build", refuse)
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path / "out"),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    argv += ["--server", "home", "--sdk-sources", str(tmp_path)]
    assert main(argv) == 1
    capsys.readouterr()
    assert (seen[0].server, seen[0].token) == ("wss://build.lan:8443/ws", "file-token")


def test_an_unknown_label_is_a_refusal_listing_the_configured_ones(
    tmp_path, capsys, monkeypatch
) -> None:
    """A label is looked up, never dialled — so a typo names the real ones."""
    directory = tmp_path / "xdg" / "mcuhome"
    directory.mkdir(parents=True)
    (directory / "build-servers.toml").write_text(
        '[server.home]\nurl = "wss://build.lan:8443/ws"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path / "out"),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    assert main(argv + ["--server", "hoem"]) == 1
    err = capsys.readouterr().err
    assert "hoem" in err
    assert "home" in err
    assert "Traceback" not in err


def test_a_broken_server_file_stops_a_remote_build_and_no_other(
    tmp_path, capsys, monkeypatch
) -> None:
    """The file is the remote method's configuration, and only its.

    A local build compiles in a container on this machine and never opens
    a socket, so a typo in ``build-servers.toml`` has no business stopping
    it — the file is not even read there.
    """
    directory = tmp_path / "xdg" / "mcuhome"
    directory.mkdir(parents=True)
    (directory / "build-servers.toml").write_text("this is not TOML {{{\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path / "out"),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "not valid TOML" in err
    assert "build-servers.toml" in err

    signed = _capture_signing(monkeypatch)
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(tmp_path / "local"),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )
    assert [call["device"] for call in signed] == ["bmp180-node"]


def test_a_group_readable_token_warns_on_stderr(tmp_path, capsys, monkeypatch) -> None:
    """Loudly, and never on stdout — in the machine modes that belongs to the document."""
    config_home = tmp_path / "xdg"
    directory = config_home / "mcuhome"
    (directory / "tokens").mkdir(parents=True)
    (directory / "build-servers.toml").write_text(
        '[server.home]\nurl = "wss://build.lan:8443/ws"\n', encoding="utf-8"
    )
    (directory / "tokens" / "home").write_text("file-token\n", encoding="utf-8")
    (directory / "tokens" / "home").chmod(0o644)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    async def refuse(request, *, method):
        raise buildmethods.RemoteNotConfigured("stopped here on purpose", hint="nothing to fix")

    monkeypatch.setattr(cli.api, "run_build", refuse)
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path / "out"),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    argv += ["--server", "home", "--sdk-sources", str(tmp_path), "-o", "json"]
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert "Warning:" in captured.err
    assert "chmod 600" in captured.err
    assert "Warning" not in captured.out
    json.loads(captured.out)


def test_remote_without_a_server_refuses_in_the_builder_vocabulary(
    tmp_path, capsys, monkeypatch
) -> None:
    """Selectable, and honest about what it is missing.

    The refusal is the workbench's, and since ADR 0023 its hint speaks
    the named-builder vocabulary — the configuration that outlives the
    transitional ``--server`` rung this command still carries until the
    vocabulary step (cli ADR 0003) lands the matching flags.
    """
    monkeypatch.delenv(cli.SERVER_VAR, raising=False)
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "type: remote" in err
    assert "--builder" in err
    assert "default_builder" in err
    assert "--build-mode remote --build-server" in err
    assert "Traceback" not in err


def test_remote_with_a_server_but_no_sdk_source_names_that_knob(
    tmp_path, capsys, monkeypatch
) -> None:
    """The one thing left that ``remote`` cannot invent: which SDK package.

    E65 closed the gap — this method creates its own build context now —
    and left exactly one input a user must supply, the same one the
    default ``local`` method needs: a directory holding the hash-pinned
    SDK package. The refusal names ``--sdk-source`` and the variable, and
    does **not** offer another method as the workaround, because there is
    nothing to work around any more.
    """
    monkeypatch.setenv(cli.SERVER_VAR, "ws://build.example/ws")
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "--sdk-sources" in err
    assert "MCUHOME_SDK_SOURCES" in err
    assert "sdk_sources:" in err
    assert "Traceback" not in err


def test_the_sdk_source_variable_reaches_a_remote_request_too(
    tmp_path, capsys, monkeypatch
) -> None:
    """E65: the ladder's second rung serves ``remote``, not only ``local``.

    ``MCUHOME_SDK_SOURCES`` is the registry option's environment channel
    (ADR 0022) and serves the ``remote`` method exactly as it serves
    ``local``, so the variable is asserted against the request the remote
    method actually receives, rather than against the resolution helper,
    which would prove only that the helper works.
    """
    seen: list[buildmethods.BuildRequest] = []

    async def refuse(request, *, method):
        seen.append(request)
        raise buildmethods.RemoteNotConfigured("stopped here on purpose", hint="nothing to fix")

    monkeypatch.setattr(cli.api, "run_build", refuse)
    monkeypatch.setenv(
        "MCUHOME_SDK_SOURCES", os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    )
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(tmp_path / "out"),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    argv += ["--server", "ws://build.example/ws"]
    assert main(argv) == 1
    capsys.readouterr()
    assert seen[0].sdk_sources == (tmp_path / "a", tmp_path / "b")


def _capture_signing(monkeypatch) -> list[dict]:
    """Replace the one signing step and record every call it receives."""
    calls: list[dict] = []

    def record(model, out_dir, *, key, env, report, project=None):
        calls.append({"device": model.device.name, "out_dir": out_dir, "report": report})
        return cli._Signed(signed=[], ota=None, manifest=None)

    monkeypatch.setattr(cli, "_sign_after_build", record)
    return calls


def test_every_method_reaches_the_one_signing_step(tmp_path, capsys, monkeypatch) -> None:
    """E56: one host-side signing step, reached identically by all three.

    Each method is stubbed at its own backend seam — the west build for
    ``local-dev``, ``compose_local_build`` for ``local``, and the dispatch
    itself for ``remote``, whose real composition (a session against a
    build server) is tested in the library's own suite. What is asserted
    is the same thing for all three: the signing step ran exactly once, on
    the build directory, for this device — and the only value that
    differs is the *name of the report* it was told to read, which is
    E55's "both shapes" rule and not a second code path.

    The ``remote`` leg also carries an ``--sdk-source``, because since E65
    it needs one for the same reason ``local`` does: it creates its own
    build context and the SDK pin is part of that context's identity. The
    request it produced is checked below.
    """
    calls = _capture_signing(monkeypatch)
    requests: list[buildmethods.BuildRequest] = []

    # local-dev, through the real dispatch onto a stubbed west build.
    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", _fake_build_run)
    local_dev_dir = tmp_path / "local-dev"
    argv = ["build", str(EXAMPLE), "--build-dir", str(local_dev_dir), "--method", "local-dev"]
    argv += ["--signing-key", str(_private_key(tmp_path))]
    assert main(argv) == 0

    # local, through the real dispatch onto a stubbed container backend.
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    local_dir = tmp_path / "local"
    assert (
        main(
            [
                "build",
                str(EXAMPLE),
                "--build-dir",
                str(local_dir),
                "--signing-key",
                str(_private_key(tmp_path)),
            ]
        )
        == 0
    )

    # remote: stubbed at the dispatch, which is this method's seam from
    # here — see the module the refusal above points at.
    remote_dir = tmp_path / "remote"

    async def delivered(request, *, method):
        requests.append(request)
        out = tmp_path / "delivery"
        out.mkdir(exist_ok=True)
        (out / "firmware.bin").write_bytes(bytes(16))
        (out / imgtool.BUILD_REPORT_FILE).write_text(json.dumps(REPORT), "utf-8")
        return buildmethods.BuildOutcome(
            method=buildmethods.REMOTE,
            successful=True,
            status="success",
            context_id="sha256:" + "3" * 64,
            artifacts=(
                lb.Artifact(root="out", path="firmware.bin", role="firmware", sha256="0" * 64),
                lb.Artifact(
                    root="out", path=imgtool.BUILD_REPORT_FILE, role="report", sha256="1" * 64
                ),
            ),
            out_dir=out,
            report=imgtool.BUILD_REPORT_FILE,
        )

    monkeypatch.setattr(cli.api, "run_build", delivered)
    sdk_source = tmp_path / "packages"
    sdk_source.mkdir()
    argv = [
        "build",
        str(EXAMPLE),
        "--build-dir",
        str(remote_dir),
        "--method",
        "remote",
        "--signing-key",
        str(_private_key(tmp_path)),
    ]
    argv += ["--server", "ws://build.example/ws", "--sdk-sources", str(sdk_source)]
    assert main(argv) == 0
    capsys.readouterr()

    # What the command line handed the remote method: the server, the
    # token rung left empty, and the SDK source — the same field the
    # `local` method reads, which is the point of E65 (one resolver, one
    # knob, two methods).
    assert [request.server for request in requests] == ["ws://build.example/ws"]
    assert requests[0].sdk_sources == (sdk_source,)

    assert [call["device"] for call in calls] == ["bmp180-node"] * 3
    assert [call["out_dir"] for call in calls] == [
        local_dev_dir.resolve(),
        local_dir.resolve(),
        remote_dir.resolve(),
    ]
    assert [call["report"] for call in calls] == [
        MANIFEST_FILE,
        imgtool.BUILD_REPORT_FILE,
        imgtool.BUILD_REPORT_FILE,
    ]


def test_no_sign_reaches_the_signing_step_on_no_method(tmp_path, capsys, monkeypatch) -> None:
    """--no-sign skips the one step uniformly, rather than per method (E56)."""
    calls = _capture_signing(monkeypatch)
    public = _public_key(tmp_path)

    monkeypatch.setattr(workspace, "plan_build", _planner(tmp_path))
    monkeypatch.setattr(workspace, "run_build", _fake_build_run)
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    for extra in (["--method", "local-dev"], []):
        argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path / "out")]
        argv += ["--no-sign", "--public-key", str(public), *extra]
        assert main(argv) == 0
    capsys.readouterr()
    assert calls == []


def test_the_build_help_advertises_the_three_methods(capsys) -> None:
    """The command surface is the one machine-facing promise this package makes."""
    with pytest.raises(SystemExit):
        main(["build", "--help"])
    out = capsys.readouterr().out
    for name in buildmethods.METHODS:
        assert name in out
    assert "--method" in out
    assert "--server" in out
    # E62: one spelling per method, and the old alias is not one of them.
    assert "--native" not in out


# --------------------------------------------------------------------------
# -o json-stream: the NDJSON contract (cli ADR 0004 §2)
# --------------------------------------------------------------------------


def _stream_lines(out: str) -> list[dict]:
    lines = [json.loads(line) for line in out.splitlines() if line]
    assert all("verb" in line for line in lines)
    return lines


def test_validate_json_stream_ends_in_the_json_document(capsys) -> None:
    """`result` carries the same document `-o json` prints — one contract."""
    assert main(["validate", str(EXAMPLE), "-o", "json"]) == 0
    single = json.loads(capsys.readouterr().out)

    assert main(["validate", str(EXAMPLE), "-o", "json-stream"]) == 0
    lines = _stream_lines(capsys.readouterr().out)
    assert lines[0]["verb"] == "start"
    assert lines[0]["task"] == "validate"
    assert lines[-1]["verb"] == "result"
    assert lines[-1]["document"] == single


def test_validate_json_stream_streams_each_error(tmp_path, capsys) -> None:
    entry = tmp_path / "main.yaml"
    entry.write_text(VALID_CONFIG.replace("device_role: ftd", "device_role: sed"), "utf-8")
    assert main(["validate", str(entry), "-o", "json-stream"]) == 1
    lines = _stream_lines(capsys.readouterr().out)
    verbs = [line["verb"] for line in lines]
    assert "error" in verbs
    error = next(line for line in lines if line["verb"] == "error")
    assert "Sleepy end devices" in error["error"]["message"]
    assert lines[-1]["verb"] == "result"
    assert lines[-1]["document"]["ok"] is False


def test_build_json_stream_reports_honest_stages(tmp_path, capsys) -> None:
    """start, the stages that really ran, result — and stdout carries
    nothing else; the machine modes force the log onto stderr."""
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path)]
    argv += ["--generate-only", "-o", "json-stream"]
    assert main(argv) == 0
    lines = _stream_lines(capsys.readouterr().out)
    verbs = [line["verb"] for line in lines]
    assert verbs[0] == "start"
    assert "progress" in verbs
    stages = [line["stage"] for line in lines if line["verb"] == "progress"]
    assert stages == ["generate"]
    assert lines[-1]["verb"] == "result"
    assert lines[-1]["document"]["ok"] is True


def test_a_full_build_streams_the_compile_and_sign_stages(tmp_path, capsys, monkeypatch) -> None:
    """The stage names are machine-facing vocabulary, pinned like the verbs.

    A consumer keys its progress display on them, so renaming one is a
    breaking change to the append-only stream contract — this is the
    end-to-end pin over the default (container) build path.
    """
    monkeypatch.setattr(buildmethods, "compose_local_build", _fake_local_run)
    _fake_imgtool(monkeypatch, tmp_path)
    argv = ["build", str(EXAMPLE), "--build-dir", str(tmp_path / "out")]
    argv += ["--signing-key", str(_private_key(tmp_path)), "-o", "json-stream"]
    assert main(argv) == 0
    lines = _stream_lines(capsys.readouterr().out)
    assert lines[0]["verb"] == "start"
    assert lines[0] == {
        "verb": "start",
        "task": "build",
        "device": "bmp180-node",
        "method": "local",
    }
    stages = [line["stage"] for line in lines if line["verb"] == "progress"]
    assert stages == ["compile", "sign"]
    assert lines[-1]["verb"] == "result"
    assert lines[-1]["document"]["ok"] is True
    assert lines[-1]["document"]["signed"] is True


def test_a_failure_in_json_stream_is_error_verbs_plus_the_failure_document(
    tmp_path, capsys
) -> None:
    entry = tmp_path / "main.yaml"
    entry.write_text(VALID_CONFIG.replace("device_role: ftd", "device_role: sed"), "utf-8")
    argv = ["build", str(entry), "--build-dir", str(tmp_path / "out")]
    argv += ["--generate-only", "-o", "json-stream"]
    assert main(argv) == 1
    lines = _stream_lines(capsys.readouterr().out)
    assert [line["verb"] for line in lines if line["verb"] == "error"]
    assert lines[-1]["verb"] == "result"
    assert lines[-1]["document"]["ok"] is False
    assert lines[-1]["document"]["errors"]
