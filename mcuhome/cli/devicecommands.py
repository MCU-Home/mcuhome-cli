# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome device`` — the life of a device, a build apart.

Everything a person does to a device that is not building it: writing
one (``new``), listing what a project holds (``list``), saying what one
resolves to (``info``, ``validate``), generating its application tree,
signing what a build produced, clearing a build directory (``clean``),
moving a device to another name (``rename``), removing it (``delete``),
and its Matter commissioning credentials — shown (``print-matter-
pairing``) and drawn (``create-matter-pairing``), two acts rather than
one flag, because drawing new codes makes every controller that knows
the device commission it again.

A device of a project is named by its folder, and every file MCUHome
writes for it is keyed on that one name: the build directory, the
secrets, the pairing credentials, the patches. The commands that read a
device take the name, or a path — a device folder, or a device file,
including one that lies outside any project; the three that act on the
*project's* record of it (``rename``, ``delete``, and ``new`` which has
nothing to point at yet) take the name, because that is what the
project knows it by.

Two of them ask before they act, and only where somebody is there to
answer: ``delete`` removes work, and ``create-matter-pairing`` over
existing credentials takes a commissioned device off the fabric it is
on. Non-interactively both do what they were told — the command was
typed — and a machine mode is never interactive.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mcuhome.workbench import api

from mcuhome.cli.errors import RetiredSpelling, UsageError
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, DIM, GREEN, RED, YELLOW, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_FAILURE, EXIT_OK

__all__ = [
    "ask_before_deleting",
    "ask_before_replacing_pairing",
    "device_clean",
    "device_create_matter_pairing",
    "device_delete",
    "device_generate_application",
    "device_info",
    "device_list",
    "device_new",
    "device_print_matter_pairing",
    "device_rename",
    "device_sign_firmware",
    "device_validate",
    "format_devices",
    "validate_clean",
    "validate_sign_firmware",
]


def device_new(invocation: Invocation) -> int:
    """``mcuhome device new <device> --board <board>``: write a new device."""
    output = invocation.output
    project = invocation.project()
    name = str(invocation.flag("device"))
    board = str(invocation.flag("board"))
    friendly_name = invocation.flag("friendly_name")
    friendly_name = None if friendly_name is None else str(friendly_name)
    invocation.start()
    if invocation.flag("dry_run", False):
        # Nothing is written, so there is no project entry to name — and
        # the refusals a board or a name earns are the same ones the
        # writing call gives, because the rendering raises them too.
        text = api.render_device_file(name, board=board, friendly_name=friendly_name)
        # The file and nothing else on stdout, because the obvious next
        # thing a person does with it is redirect it into one; the note
        # that nothing was written is for them and goes to stderr.
        output.human(text.rstrip("\n"))
        if not output.machine:
            output.log(output.muted(_("Nothing was written (--dry-run).")))
        output.result({"ok": True, "dry_run": True, "name": name, "board": board, "text": text})
        return EXIT_OK
    created = api.create_device(name, project=project, board=board, friendly_name=friendly_name)
    _print_new(created, output=output)
    output.result({"ok": True, "dry_run": False, **created.to_dict()})
    return EXIT_OK


def device_list(invocation: Invocation) -> int:
    """``mcuhome device list``: the project's devices with their state.

    ``ok`` is the verdict of the **listing**: a device with a broken
    configuration is a row with its own ``ok`` false, never a failed
    command — a client shows a list with a bad row rather than nothing.
    """
    output = invocation.output
    project = invocation.project()
    invocation.start()
    devices = api.find_devices(project)
    output.human(format_devices(devices, output=output))
    output.result(
        {
            "ok": True,
            "project": project.to_dict(),
            "devices": [device.to_dict() for device in devices],
        }
    )
    return EXIT_OK


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


def device_validate(invocation: Invocation) -> int:
    """``mcuhome device validate <device>``: one pass, every problem.

    One of the commands that can answer negatively: a configuration with
    problems is this command's own document with ``ok`` false and the
    findings in ``diagnostics``, not a refusal — the run happened and
    the answer is no.
    """
    output = invocation.output
    project, entry = _device(invocation)
    invocation.start()
    validation = api.validate_device(
        entry, project=project, on_warning=lambda finding: output.finding(finding.to_dict())
    )
    _print_validation(
        validation,
        entry=entry,
        masked=not invocation.flag("show_sensitive", False),
        verbose=bool(invocation.flag("verbose", False)),
        output=output,
    )
    output.result(validation.to_dict())
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
    directory = path if path.is_dir() else path.parent
    what = _("a build directory") if path.is_dir() else _("a build report")
    return [
        RetiredSpelling(
            _("mcuhome device sign-firmware does not take {what} as its positional.").format(
                what=what
            ),
            hint=_(
                "name the device and, where the build is not in its own directory, say where:\n"
                "    mcuhome device sign-firmware <device> --out-dir {path}"
            ).format(path=directory),
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


def validate_clean(invocation: Invocation) -> list[api.MCUHomeError]:
    """One device or every one of them, and the invocation says which."""
    stated = invocation.flag("device")
    every = bool(invocation.flag("all", False))
    if stated is not None and every:
        return [
            UsageError(
                _("--all cleans every device of the project, so it takes no device."),
                hint=_(
                    "name the device, or ask for all of them:\n"
                    "    mcuhome device clean {device}\n"
                    "    mcuhome device clean --all"
                ).format(device=stated),
            )
        ]
    if stated is None and not every:
        return [
            UsageError(
                _("mcuhome device clean needs to know what to clean."),
                hint=_(
                    "name the device, or ask for all of them:\n"
                    "    mcuhome device clean <device>\n"
                    "    mcuhome device clean --all"
                ),
            )
        ]
    return []


def device_clean(invocation: Invocation) -> int:
    """``mcuhome device clean [<device>]``: remove what a build produced.

    Each directory is held under the ``clean`` operation for its own
    removal, which is the api's doing: a build or a signature running
    there refuses this one in words rather than losing its output.
    """
    output = invocation.output
    invocation.start()
    cleaned = [
        api.clean_build(out_dir, device=name) for name, out_dir in _directories_to_clean(invocation)
    ]
    _print_cleaned(cleaned, output=output)
    output.result({"ok": True, "cleaned": [result.to_dict() for result in cleaned]})
    return EXIT_OK


def device_rename(invocation: Invocation) -> int:
    """``mcuhome device rename <device> --to <name>``: rename a device."""
    output = invocation.output
    project = invocation.project()
    invocation.start()
    result = api.rename_device(
        str(invocation.flag("device")), project=project, to=str(invocation.flag("to"))
    )
    _print_changed(
        _("Renamed {device} to {to}:").format(device=result.device, to=result.to),
        result.changed,
        output=output,
    )
    output.result({"ok": True, **result.to_dict()})
    return EXIT_OK


def ask_before_deleting(invocation: Invocation) -> None:
    """The question ``delete`` asks, up front and only once.

    ``--force`` is what says the question has already been answered
    inside an interactive run; without a terminal nothing is asked at
    all, because the command was typed and a script is a person who
    already decided.
    """
    if invocation.flag("force", False):
        return
    name = str(invocation.flag("device"))
    output = invocation.output
    output.human(
        _("This removes the device {name}: its folder, its build directory{secrets}.").format(
            name=output.style(name, BOLD),
            secrets="" if invocation.flag("keep_secrets", False) else _(", and its secrets file"),
        )
    )
    if not invocation.flag("keep_secrets", False):
        output.human(
            output.style(
                _(
                    "  Its commissioning credentials go with it, and they cannot be drawn\n"
                    "  again — a controller that knows this device would lose it."
                ),
                YELLOW,
            )
        )
    invocation.args.confirmed = _answered_yes(_("Type yes to delete, anything else to cancel: "))


def device_delete(invocation: Invocation) -> int:
    """``mcuhome device delete <device>``: remove a device."""
    output = invocation.output
    project = invocation.project()
    if _was_declined(invocation):
        return _cancelled(_("Cancelled. Nothing was deleted."), output=output)
    invocation.start()
    result = api.delete_device(
        str(invocation.flag("device")),
        project=project,
        keep_secrets=bool(invocation.flag("keep_secrets", False)),
    )
    _print_changed(
        _("Deleted {device}:").format(device=result.device), result.removed, output=output
    )
    if result.kept_secrets:
        output.human(output.muted(_("Its secrets file was kept (--keep-secrets).")))
    output.result({"ok": True, **result.to_dict()})
    return EXIT_OK


def device_print_matter_pairing(invocation: Invocation) -> int:
    """``mcuhome device print-matter-pairing <device>``: the codes, as they are.

    The explicit ask, and the only place the codes are printed without
    being asked for in so many words: everywhere else they are masked.
    It reads — drawing them is a command of its own.
    """
    output = invocation.output
    project, entry = _device(invocation)
    invocation.start()
    credentials = api.read_pairing(entry, project=project)
    name = _name_of(entry)
    if credentials is None:
        output.human(
            _("{device} has no commissioning credentials.").format(device=name)
            + "\n"
            + output.muted(
                _("    mcuhome device create-matter-pairing {device}").format(device=name)
            )
        )
    else:
        output.human(_commissioning(credentials, output=output))
    output.result(
        {
            "ok": True,
            "device": name,
            "pairing": None if credentials is None else credentials.to_dict(),
        }
    )
    return EXIT_OK


def ask_before_replacing_pairing(invocation: Invocation) -> None:
    """The question ``create-matter-pairing`` asks, and only where it must.

    Only a replacement is asked about: a device that has no credentials
    yet has nothing to lose, and one that has them is refused by the api
    unless ``--force`` — so the question belongs to exactly the run that
    would go through.
    """
    if not invocation.flag("force", False):
        return
    try:
        project, entry = _device(invocation)
        if api.read_pairing(entry, project=project) is None:
            return
    except api.MCUHomeError:
        # Whatever is wrong with the device is the run's to refuse, in
        # the api's own words; this phase only decides whether to ask.
        return
    output = invocation.output
    output.human(
        output.style(
            _(
                "This draws new commissioning credentials. The codes this device has now\n"
                "stop working, and every controller it is commissioned on loses it."
            ),
            YELLOW,
        )
    )
    invocation.args.confirmed = _answered_yes(
        _("Type yes to replace them, anything else to cancel: ")
    )


def device_create_matter_pairing(invocation: Invocation) -> int:
    """``mcuhome device create-matter-pairing <device>``: draw the codes."""
    output = invocation.output
    project, entry = _device(invocation)
    if _was_declined(invocation):
        return _cancelled(_("Cancelled. Nothing was changed."), output=output)
    invocation.start()
    result = api.create_pairing(entry, project=project, force=bool(invocation.flag("force", False)))
    _print_new_pairing(result, output=output)
    output.result({"ok": True, "device": _name_of(entry), **result.to_dict()})
    return EXIT_OK


# -- what these commands share ------------------------------------------


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


def _build_dir(project: api.Project, name: str) -> Path:
    """Where a device's build output lies — the one place it ever does."""
    return project.root / api.BUILD_DIR / name


def _directories_to_clean(invocation: Invocation) -> list[tuple[str, Path]]:
    """The device build directories this run cleans, in the order it does.

    ``--all`` is the project's devices, in the order the listing gives
    them; a named device is that one, wherever it lies — including a
    device file outside any project, whose own directory stands in.
    """
    if invocation.flag("all", False):
        project = invocation.project()
        return [
            (device.name, _build_dir(project, device.name)) for device in api.find_devices(project)
        ]
    project, entry = _device(invocation)
    name = _name_of(entry)
    return [(name, _build_dir(project, name))]


def _answered_yes(question: str) -> bool:
    """One typed answer, and nothing but ``yes`` is one."""
    try:
        answer = input(question)
    except EOFError:
        answer = ""
    return answer.strip().lower() == "yes"


def _was_declined(invocation: Invocation) -> bool:
    """Whether the run was asked its question and said no.

    Absent means nobody was asked — a machine mode, ``--no-interactive``
    or a flag that says the answer is already given — and a command that
    was not asked does what it was told.
    """
    return invocation.flag("confirmed", None) is False


def _cancelled(sentence: str, *, output: Output) -> int:
    """A run the person in front of it called off.

    Only they ever reach this: a machine mode is never interactive, so
    there is no document to answer with and no reader waiting for one —
    the sentence is the whole answer, and the exit code says the command
    did not do what it was asked.
    """
    output.human(sentence)
    return EXIT_FAILURE


# -- the human renderings -----------------------------------------------


def format_devices(devices: Sequence[api.DeviceRecord], *, output: Output) -> str:
    """The project's devices as a table, or the line that says it has none.

    One rendering of a device record for the whole command line: ``device
    list`` answers these rows and ``project info`` shows the same ones,
    and two tables of one document would be two places to change.
    """
    if not devices:
        return _("No devices yet — mcuhome device new <name> writes one.")
    rows: list[list[str | Cell]] = [[_("device"), _("board"), _("problems"), _("build")]]
    for device in devices:
        rows.append(
            [
                device.name,
                device.board,
                Cell(str(device.problems), () if device.ok else (YELLOW,)),
                _build_state(device, output=output),
            ]
        )
    return format_table(rows, header=True, output=output)


def _build_state(device: api.DeviceRecord, *, output: Output) -> Cell:
    """What a row says about the device's build directory, in one word."""
    if device.busy:
        return Cell(_("working now"), (YELLOW,))
    if device.signed:
        return Cell(_("signed"), (GREEN,))
    if device.built:
        return Cell(_("built"), ())
    return Cell(_("—"), (DIM,))


def _print_new(created: api.NewDevice, *, output: Output) -> None:
    """A new device, and the two acts that come next."""
    output.human(
        f"{output.style('✓', GREEN, BOLD)} "
        + _("Wrote {path}.").format(path=output.path(created.entry))
    )
    output.human()
    output.human(output.heading(_("Next:")))
    output.human(
        _("  mcuhome device create-matter-pairing {name}    draw its commissioning codes").format(
            name=created.name
        )
    )
    output.human(
        _("  mcuhome device validate {name}        see what it resolves to").format(
            name=created.name
        )
    )
    output.human(_("  mcuhome device build {name}           compile it").format(name=created.name))
    output.human()
    output.human(
        output.muted(
            _(
                "The configuration has no hardware in it yet — the file carries a "
                "complete, commented\nexample to uncomment and adjust."
            )
        )
    )


def _print_validation(
    validation: api.ValidationResult,
    *,
    entry: Path,
    masked: bool,
    verbose: bool,
    output: Output,
) -> None:
    """What the configuration resolves to, and every problem it has."""
    if output.machine:
        return
    if validation.model is not None:
        output.human(_summary(validation.model, masked=masked, output=output))
        if verbose:
            output.human()
            output.human(validation.model.to_json().rstrip("\n"))
        output.human()
    for finding in validation.diagnostics():
        output.human(_finding_lines(finding, output=output))
    if validation.ok:
        output.human(
            f"{output.style('✓', GREEN, BOLD)} "
            + _("{path} is valid.").format(path=output.path(entry))
        )
        return
    output.human(
        _("{path}: {count} of these stop a build.").format(
            path=output.path(entry), count=len(validation.errors)
        )
    )


def _finding_lines(finding: dict[str, object], *, output: Output) -> str:
    """One finding as a person reads it: where, what, and the way out.

    The same two-line shape a refusal is rendered in — the fix on its own
    line rather than run into the sentence, because a hint is what a
    person acts on and a paragraph is what they skip.
    """
    warning = finding["severity"] == "warning"
    where = f"{finding['file']}:{finding['line']}: " if finding["file"] else ""
    severity = output.style(
        _("warning") if warning else _("error"), YELLOW if warning else RED, BOLD
    )
    lines = [f"{where}{severity}: {finding['message']}"]
    if finding["hint"]:
        hint = str(finding["hint"]).splitlines()
        lines.append(f"  {_('Fix:')} {hint[0]}")
        lines += [f"       {line}" for line in hint[1:]]
    return "\n".join(lines)


def _summary(model: api.DeviceModel, *, masked: bool, output: Output) -> str:
    """The picture of a resolved device a person reads.

    What the configuration became, not what was written: the board, the
    transport, what is on the buses and what the endpoints expose — and
    the commissioning block last, masked unless this run asked for it in
    so many words.
    """
    label = output.muted

    def row(name: str, value: str) -> str:
        return f"{label(name.ljust(9))}  {value}"

    device = model.device
    lines = [
        row(_("Device"), f"{output.style(device.name, BOLD)} ({device.friendly_name})"),
        row(_("Board"), device.board),
        row(_("Power"), device.power_source),
        row(_("Transport"), model.network.transport or _("none (standalone device)")),
        row(
            _("Matter"),
            output.style(_("enabled"), GREEN)
            if model.network.matter_enabled
            else label(_("disabled")),
        ),
        row(_("Zephyr"), model.toolchain.zephyr_line),
    ]
    if model.hardware.buses or model.hardware.peripherals:
        lines += ["", output.heading(_("Hardware"))]
        for bus in model.hardware.buses:
            lines.append(f"  {_('bus')} {bus.id} ({bus.kind})")
        for peripheral in model.hardware.peripherals:
            where = f" {_('on')} {peripheral.bus}" if peripheral.bus else ""
            lines.append(f"  {peripheral.id}: {peripheral.compatible}{where}")
    if model.endpoints:
        lines += ["", output.heading(_("Endpoints"))]
        for endpoint in model.endpoints:
            types = ", ".join(item.name for item in endpoint.device_types)
            lines.append(f"  {label(_('endpoint'))} {endpoint.id}: {types}")
            for cluster in endpoint.clusters:
                lines.append(f"    {cluster.name}")
    if model.network.pairing is not None:
        lines += [
            "",
            _commissioning(_credentials(model.network.pairing), output=output, masked=masked),
        ]
    return "\n".join(lines)


def _credentials(stated: api.PairingModel) -> api.Pairing:
    """The device model's commissioning values as the tuple that derives the codes.

    The manual code and the QR payload are arithmetic over these four
    values, and it is the model package's arithmetic: the canonical model
    states the inputs, this asks the same package what they come to.
    """
    return api.Pairing(
        discriminator=stated.discriminator,
        passcode=stated.passcode,
        salt=stated.salt,
        iterations=stated.iterations,
        test_credentials=stated.test_credentials,
    )


def _commissioning(credentials: api.Pairing, *, output: Output, masked: bool = False) -> str:
    """The two strings a person types into a controller.

    Masked wherever the codes merely pass by — a device that is shown or
    validated — because anyone holding them can commission the device;
    ``mcuhome device print-matter-pairing`` and ``--show-sensitive`` are
    the two asks that show them. The discriminator stays visible either
    way: the device broadcasts it in the clear.
    """
    if masked:
        hidden = output.muted(_("hidden — mcuhome device print-matter-pairing <device> shows it"))
        manual, qr = hidden, hidden
    else:
        manual = output.style(credentials.manual_code, BOLD)
        qr = output.style(credentials.qr_payload, BOLD)
    label = output.muted
    lines = [
        output.heading(_("Commissioning")),
        f"  {label(_('manual code   '))} {manual}",
        f"  {label(_('QR code       '))} {qr}",
        f"  {label(_('discriminator '))} {credentials.discriminator} "
        f"(0x{credentials.discriminator:03X})",
    ]
    if credentials.test_credentials:
        lines.append(
            output.style(
                _(
                    "  NOTE: these are the credentials published with the Matter SDK. "
                    "Anyone who\n        knows them can commission this device — bench "
                    "use only."
                ),
                YELLOW,
            )
        )
    return "\n".join(lines)


def _print_new_pairing(result: api.NewPairing, *, output: Output) -> None:
    """What was drawn, where the values went, and what that costs."""
    if output.machine:
        return
    verb = (
        _("Replaced the commissioning credentials of")
        if result.replaced
        else _("Drew commissioning credentials for")
    )
    output.human(f"{output.style('✓', GREEN, BOLD)} {verb} {output.path(result.entry)}.")
    output.human(
        _("The values live in {path}; the configuration carries !secret references.").format(
            path=output.path(result.secrets_file)
        )
    )
    output.human()
    output.human(_commissioning(result.pairing, output=output))
    output.human()
    output.human(
        output.style(
            _(
                "Keep the secrets file safe: it is the only copy. Anyone who has it — or "
                "the firmware\nbuilt from it — can commission this device."
            ),
            YELLOW,
        )
    )


def _print_cleaned(results: Sequence[api.CleanResult], *, output: Output) -> None:
    """What went, per directory, and the line for a directory where nothing did."""
    if output.machine:
        return
    for result in results:
        if not result.removed:
            output.human(_("Nothing to clean in {path}.").format(path=output.path(result.out_dir)))
            continue
        output.human(
            _("Cleaned {device} ({count} entries removed from {path}).").format(
                device=result.device, count=len(result.removed), path=output.path(result.out_dir)
            )
        )


def _print_changed(headline: str, paths: Sequence[Path], *, output: Output) -> None:
    """A headline and the paths an act moved or removed, in that order."""
    if output.machine:
        return
    output.human(f"{output.style('✓', GREEN, BOLD)} {headline}")
    for path in paths:
        output.human(f"  {output.path(path)}")


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
