# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome device`` — describing a device, generating it, signing it.

Three acts that sit around a build without being one. ``info`` says what
a device resolves to and what its build directory holds; ``generate-
application`` writes the standalone Zephyr application the model
describes and stops there — a build generates inside its build
environment, from the model its context carries, so this is for whoever
wants the tree for its own sake; ``sign-firmware`` applies the signature
where the private key is, which is what makes "build here, sign there" a
workflow rather than a compromise.

A device of a project is named by its folder, and every file MCUHome
writes for it is keyed on that one name: the build directory, the
secrets, the pairing credentials, the patches. These commands take the
name, or a path — a device folder, or a device file, including one that
lies outside any project.
"""

from __future__ import annotations

from pathlib import Path

from mcuhome.workbench import api

from mcuhome.cli.errors import RetiredSpelling
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, GREEN, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_FAILURE, EXIT_OK

__all__ = [
    "device_generate_application",
    "device_info",
    "device_sign_firmware",
    "validate_sign_firmware",
]


def device_info(invocation: Invocation) -> int:
    """``mcuhome device info <device>``: one device in full."""
    output = invocation.output
    project, entry = _device(invocation)
    invocation.start()
    validation = api.validate_device(
        entry, project=project, on_warning=lambda finding: output.finding(finding.to_dict())
    )
    name = validation.model.device.name if validation.model is not None else _name_of(entry)
    out_dir = project.root / api.BUILD_DIR / name
    record = api.read_build(out_dir)
    footprint = _footprint(out_dir, record)
    _print_info(name, validation, record, footprint, out_dir=out_dir, output=output)
    output.result(
        {
            "ok": validation.ok,
            "device": name,
            "validation": validation.to_dict(),
            "build": None if record is None else record.to_dict(),
            "footprint": [region.to_dict() for region in footprint],
        }
    )
    return EXIT_OK if validation.ok else EXIT_FAILURE


def device_generate_application(invocation: Invocation) -> int:
    """``mcuhome device generate-application <device> --out-dir <directory>``."""
    output = invocation.output
    project, entry = _device(invocation)
    invocation.start()
    model = api.load_model(
        entry, project=project, on_warning=lambda finding: output.finding(finding.to_dict())
    )
    result = api.generate_application(model, out_dir=_stated_out_dir(invocation))
    output.human(
        _("Generated {count} files for {device} in {path}:").format(
            count=len(result.files), device=result.device, path=output.path(result.out_dir)
        )
    )
    for file in result.files:
        output.human(f"  {file}")
    output.result({"ok": True, **result.to_dict()})
    return EXIT_OK


def validate_sign_firmware(invocation: Invocation) -> list[api.MCUHomeError]:
    """The positional of ``sign-firmware`` is a device, and was not always.

    A build directory, or the build report inside one, used to be what
    this command was pointed at. Both are refused by name with
    ``--out-dir`` in the message: the device is what the signature is
    *for* — it decides the key, and whether an over-the-air image is
    written at all — and a directory is where it lies.
    """
    stated = invocation.flag("device")
    if stated is None:
        return []
    path = Path(str(stated))
    if not _is_build_target(path):
        return []
    return [
        RetiredSpelling(
            _("mcuhome device sign-firmware does not take a build directory any more.").format(),
            hint=_(
                "name the device and, where the build is not in its own directory, say where:\n"
                "    mcuhome device sign-firmware <device> --out-dir {path}"
            ).format(path=path if path.is_dir() else path.parent),
        )
    ]


def device_sign_firmware(invocation: Invocation) -> int:
    """``mcuhome device sign-firmware <device>``: sign a finished build."""
    output = invocation.output
    project, entry = _device(invocation)
    settings = invocation.settings(project=project)
    model = api.load_model(
        entry, project=project, on_warning=lambda finding: output.finding(finding.to_dict())
    )
    out_dir = _stated_out_dir(invocation) or project.root / api.BUILD_DIR / model.device.name
    invocation.start()
    key = settings.value("signing.key")
    imgtool = settings.value("signing.imgtool")
    # Held for the whole run under its own operation, so a build of the
    # same device refuses in words rather than racing the signature.
    with api.open_build_lock(out_dir, device=model.device.name, operation="sign"):
        if invocation.flag("dry_run", False):
            plan = api.plan_signing(
                out_dir, env=invocation.env, key=key, project=project, imgtool=imgtool, model=model
            )
            _print_plan(plan, output=output)
            output.result({"ok": True, "dry_run": True, **plan.to_dict()})
            return EXIT_OK
        result = api.sign_firmware(
            out_dir, env=invocation.env, key=key, project=project, imgtool=imgtool, model=model
        )
    _print_signed(result, output=output)
    output.result(result.to_dict())
    return EXIT_OK if result.ok else EXIT_FAILURE


# -- what these three commands share ------------------------------------


def _device(invocation: Invocation) -> tuple[api.Project, Path]:
    """The device this invocation is about, and the project it lies in."""
    return api.resolve_device(
        str(invocation.flag("device")),
        env=invocation.env,
        cwd=invocation.cwd,
        project_dir=invocation.project_dir,
    )


def _name_of(entry: Path) -> str:
    """The device's name as its path gives it.

    A device of a project is its folder; a bare device file outside one
    is not subject to that rule and is named by the file.
    """
    return entry.parent.name if entry.name == api.DEVICE_FILE else entry.stem


def _stated_out_dir(invocation: Invocation) -> Path | None:
    """``--out-dir`` as an absolute path, or ``None`` where it was not used."""
    stated = invocation.flag("out_dir")
    if stated is None:
        return None
    path = api.expand_user_path(str(stated), env=invocation.env)
    if not path.is_absolute():
        path = invocation.cwd / path
    return path.resolve()


def _is_build_target(path: Path) -> bool:
    """Whether *path* names a finished build rather than a device."""
    if path.is_file():
        return path.name == api.BUILD_REPORT_FILE
    return path.is_dir() and (path / api.BUILD_REPORT_FILE).is_file()


def _footprint(out_dir: Path, record: api.BuildRecord | None) -> tuple[api.MemoryRegion, ...]:
    """What the build report measured, where there is a report to read."""
    report = out_dir / (api.BUILD_REPORT_FILE if record is None else record.report)
    if not report.is_file():
        return ()
    return api.memory_footprint(api.read_build_report(report))


# -- the human renderings -----------------------------------------------


def _print_info(
    name: str,
    validation: api.ValidationResult,
    record: api.BuildRecord | None,
    footprint: tuple[api.MemoryRegion, ...],
    *,
    out_dir: Path,
    output: Output,
) -> None:
    """One device, for a person: what it is, and what has been built of it."""
    if output.machine:
        return
    output.human(f"{output.style(name, BOLD)}  {output.path(validation.entry)}")
    model = validation.model
    if model is not None:
        output.human(f"  {output.muted(_('board'))} {model.device.board}")
    for finding in validation.diagnostics():
        output.human(f"  {finding['severity']}: {finding['message']}")
    output.human()
    if record is None:
        output.human(
            _("No build in {path}.").format(path=output.path(out_dir))
            if not api.is_busy(out_dir)
            else _("Something is working in {path} right now.").format(path=output.path(out_dir))
        )
        return
    output.human(output.heading(_("Build")))
    output.human(f"  {output.path(record.out_dir)}")
    for artifact in record.artifacts:
        output.human(f"  {artifact.path}  {output.muted(artifact.role)}")
    for entry in record.signed:
        output.human(f"  {Path(entry.path).name}  {output.style(_('(signed)'), GREEN)}")
    if record.busy:
        output.human(output.muted(_("  something is working in this directory right now")))
    if footprint:
        output.human()
        output.human(_memory_table(footprint, output=output))


def _memory_table(footprint: tuple[api.MemoryRegion, ...], *, output: Output) -> str:
    """The regions the build report measured, one row each."""
    rows: list[list[str | Cell]] = [[_("image"), _("region"), _("used"), _("total")]]
    for region in footprint:
        rows.append([region.image, region.region, str(region.used), str(region.total)])
    return (
        output.heading(_("Memory"))
        + "\n"
        + format_table(rows, align="llrr", header=True, indent="  ", output=output)
    )


def _print_plan(plan: api.SignPlan, *, output: Output) -> None:
    """What signing would run, and what it would remove first."""
    if output.machine:
        return
    output.human(
        _("Signing {path} with {key} would run:").format(
            path=output.path(plan.out_dir), key=output.path(plan.key)
        )
    )
    for _form, argv, _destination in plan.commands:
        output.human(f"  {output.muted(' '.join(argv))}")
    output.human()
    output.human(output.heading(_("Writes")))
    for path in plan.outputs:
        output.human(f"  {output.path(path)}")
    if plan.removes:
        output.human()
        output.human(output.heading(_("Removes first")))
        for path in plan.removes:
            output.human(f"  {output.path(path)}")
    output.human()
    output.human(output.muted(_("Nothing was changed (--dry-run).")))


def _print_signed(result: api.SigningResult, *, output: Output) -> None:
    """What the signature produced, for a person."""
    if output.machine:
        return
    output.human(
        f"{output.style('✓', GREEN, BOLD)} "
        + _("Signed the image of {path} with {key}:").format(
            path=output.path(result.out_dir), key=output.path(result.key)
        )
    )
    for entry in result.signed:
        output.human(f"  {output.path(entry.path)}")
    if result.ota is not None:
        output.human(f"  {output.path(result.ota)}  {output.muted(_('(over-the-air image)'))}")
