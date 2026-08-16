# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The ``mcuhome`` command line (cli ADR 0002: the thin shell).

This is the thin command shell of the MCUHome repo family: it parses
arguments, calls into ``mcuhome.workbench.api``, and renders the result
for a human (or, with ``-o json``/``-o json-stream``, for a machine).
All actual building, validation and signing logic lives in the
workbench — this package adds no behavior of its own beyond the command
surface.

::

    mcuhome project init     [dir]       # create a project directory
    mcuhome project info     [dir]       # where, which id, which version
    mcuhome project upgrade  [dir]       # migrate it to the current layout
    mcuhome config           <verb>      # print/get/set/unset configuration
    mcuhome device new       <device>    # scaffold a device folder
    mcuhome device validate  <device>    # stages 1-3, prints a summary
    mcuhome device build     <device>    # stages 1-5
    mcuhome device sign-firmware <t>     # apply the signature afterwards
    mcuhome device flash     <device>    # stub (cli ADR 0003)
    mcuhome device first-time-setup <d>  # stub (cli ADR 0003)
    mcuhome device matter-pairing <dev>  # pairing codes; --new draws credentials
    mcuhome device list                  # the project's devices, with state
    mcuhome device boards                # what MCUHome can build for
    mcuhome public-key                   # the public half of the signing key
    mcuhome schema           [what]      # the schema and the registry, as JSON
    mcuhome doctor                       # environment diagnosis
    mcuhome clean            <device|--all>   # stub

This is the decided vocabulary of cli ADR 0003: device-scoped
operations under the ``device`` noun, project- and environment-scoped
commands top-level, names deliberately explicit (``sign-firmware``, not
``sign`` — a future ``sign-ota-update`` may join). The stubs refuse in
words rather than being missing, because both wait on platform work
(our MCUboot serial recovery, vendor provisioning). The old flat
spellings, ``--json``, ``--method``/``--server``/``--token``,
``MCUHOME_BUILD_*`` and ``build-servers.toml`` retired with the same
step, without aliases (pre-1.0, the E62 rule).

``device build`` selects **where** to build through ADR 0023's ladder,
most explicit wins: fully manual — ``--build-mode`` plus its
mode-specific flags (``--build-server``/``--build-token`` for
``remote``, ``--workspace`` for ``local-dev``, ``--container-image`` for
``local``) — bypassing the builder list entirely; a named builder
(``--builder NAME``); or the configured ``default_builder``, falling
back to a plain ``local`` build. Builders are configuration (any layer
of ADR 0022, merged by name), their credentials live in
``secrets/build-server/<name>.yaml``, and the method vocabulary
underneath (``local``/``local-dev``/``remote``) stays the workbench's:
a builder is configuration *about* a method, never a fourth method.
Whichever ran, what comes back is an **unsigned** image plus a build
report, and one host-side step signs it (:func:`_sign_after_build`,
E56) — so the private key is absent from every build on every method,
not merely from the ones that happen to run elsewhere.

``--sdk-sources`` serves ``local`` and ``remote`` alike (E65). Both
create a build context, a context is content-addressed over the SDK
package's hash, and the pin is therefore resolved *here* on either
method — what differs is only who fetches the bytes afterwards and
checks them against it. The flag is an option of the ADR 0022 registry:
``MCUHOME_SDK_SOURCES`` and the configuration files (project, user,
system) set it too, through one resolution
(:func:`mcuhome.workbench.api.resolve_settings`), and so is ``--jobs``.
``config`` edits the same registry the build reads — ``print`` shows
every effective value with its origin layer, ``set``/``unset`` edit one
scope's file (``--project`` by default) through the round-trip editor,
so comments and ``!file`` references survive.

``device validate`` and ``device build`` take ``-o json`` and ``-o json-stream``
(cli ADR 0004): one machine-readable document on stdout — the resolved
model or the build manifest on success, ``{"ok": false, "errors":
[...]}`` on failure — or the same document at the end of an NDJSON
stream of ``start``/``progress``/``error`` messages. Exit codes are the
same either way, and the build log goes to stderr in both, so
redirecting stdout into a file leaves both halves intact.

``device validate -o json`` carries the **whole** canonical model,
commissioning credentials included, exactly as ``device-model.json``
does: it is the output of stages 1-3 and a caller that asked for the
model gets the model. ``device build -o json`` carries the build
manifest, which has none — a manifest describes artifacts. Neither
prints the human commissioning block, which exists for a person holding
a device they just built.

**This module is not an API.** Programs embed
:mod:`mcuhome.workbench.api`, which is the supported surface;
everything here is a command line, free to change its internals between
releases. The one stable machine-facing promise this package makes is
the command surface itself — a machine driving it feature-probes
``mcuhome device build --help`` for ``--model``,
``--no-sign``/``--public-key`` and ``-o``.

``device validate`` writes nothing at all. ``device build`` writes only
into its build directory, which is deliberately outside the project
directory's configuration (ADR 0022 — ``init`` puts ``build/`` in the
project's ``.gitignore``): ``<project>/build/<device>/`` unless
``--build-dir`` says otherwise. Inside it, the generated application is
``app/`` and the compiler's working tree is ``build/`` — everything a
human is meant to read on one side, machine spoil on the other.
``device matter-pairing --new`` is the one command that writes into the
project's *configuration*, and it writes into exactly two files: the
device's own ``main.yaml`` (``!secret`` references) and the device's
``secrets/devices/<name>.yaml`` with the values
(:mod:`mcuhome.workbench.provision`).
``config set``/``unset`` write one configuration file per invocation —
the named scope's — and nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import shutil
import signal
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcuhome.compiler import container, workspace
from mcuhome.compiler.generate import write_tree
from mcuhome.model import __version__ as model_version
from mcuhome.model import export, ota, pairing, registry
from mcuhome.model import manifest as manifest_module
from mcuhome.model.errors import BuildError, ConfigError, MCUHomeError
from mcuhome.model.model import DeviceModel, PairingModel
from mcuhome.model.userpaths import expand
from mcuhome.workbench import (
    api,
    configschema,
    imgtool,
    otafile,
    provision,
    scaffold,
    signing,
)
from mcuhome.workbench.loader import load_yaml_file
from mcuhome.workbench.project import check_secret_file

from mcuhome_cli import __version__ as cli_version
from mcuhome_cli import buildview, phases
from mcuhome_cli import output as output_module
from mcuhome_cli.i18n import _
from mcuhome_cli.output import Output

__all__ = [
    "BUILD_DIR",
    "format_build_summary",
    "format_commissioning",
    "format_summary",
    "load_device_model",
    "main",
]

#: Directory the per-device build trees are created in, at the project
#: root. A sibling of ``devices/``, never inside it — build output must
#: not turn up in the user's config diffs, and ``mcuhome project init`` writes
#: it into the project's ``.gitignore`` (ADR 0022).
BUILD_DIR = "build"


def _process_env() -> dict[str, str]:
    """This process's environment, as the library wants to be handed it.

    The library takes the environment as an argument everywhere and reads
    :data:`os.environ` nowhere, because one library process serves several
    callers (a dashboard, a build server) and "the environment" is then
    nobody's in particular. A command line is the caller for which the
    process environment *is* the user's answer, so this is where that
    conversion happens — once, by name, instead of as a default nobody
    can see.
    """
    return dict(os.environ)


def load_device_model(
    entry: Path, *, project: api.Project, output: Output | None = None
) -> DeviceModel:
    """Run stages 1-3 on one device configuration.

    Kept as a name because the CLI is written in terms of it; the
    implementation is :func:`mcuhome.workbench.api.load_model`, which is
    the supported one. Non-fatal findings — today the secrets-file
    permission warning of ADR 0022 §5 — go to *output* as warnings.
    """
    on_warning = None if output is None else output.warn
    return api.load_model(entry, project=project, on_warning=on_warning)


def _optional_project(args: argparse.Namespace) -> api.Project | None:
    """The project this invocation is in, or None outside of one.

    For the commands that *can* work without a project — ``sign`` and
    ``public-key`` run wherever the key is, and an explicit
    ``--signing-key`` needs no project at all. The ladder still binds:
    an explicit ``--project-dir`` or a set ``MCUHOME_PROJECT_DIR`` that
    names a non-project is an error, exactly as everywhere else; only
    the *search* rung is allowed to come home empty-handed here.
    """
    env = _process_env()
    explicit = getattr(args, "project_dir", None)
    if explicit is None and not env.get(api.PROJECT_DIR_VAR):
        found = api.find_project_root(Path.cwd())
        # project_at, not a bare Project: a project that was found still
        # has to be one these tools speak, and skipping the version check
        # here would let exactly the commands that work without a project
        # work *inside an outdated one* instead.
        return None if found is None else api.project_at(found)
    return api.resolve_project(explicit, env=env, cwd=Path.cwd())


def option_env_var(name: str) -> str:
    """The ``MCUHOME_*`` spelling of a registry option — one source, ADR 0022."""
    return next(declared for declared in api.OPTIONS if declared.name == name).env_var


def _settings(args: argparse.Namespace, project: api.Project | None) -> api.Settings:
    """The resolved option registry, with only what the user actually gave.

    The five layers of ADR 0022 in one call: defaults, system and user
    configuration, the project's ``mcuhome.yaml``, ``MCUHOME_*``
    variables — and, on top, the flags of this invocation. An unset flag
    is *absent* here, not None: "not given" and "given as empty" are
    different statements and only this caller can tell them apart.
    """
    env = _process_env()
    given: dict[str, object] = {}
    sources = getattr(args, "sdk_sources", None)
    if sources:
        given["sdk_sources"] = tuple(expand(entry, env) for entry in sources)
    if getattr(args, "jobs", None):
        given["jobs"] = args.jobs
    return api.resolve_settings(project=project, env=env, args=given)


def _resolve_jobs(settings: api.Settings) -> tuple[int, str]:
    """Parallel build jobs and where the number came from.

    The ``jobs`` option resolves through the registry like any other;
    only its *default* is special — no layer said anything, so the
    auto-detection (CPU count against available RAM) answers instead of
    the declared ``1``, exactly as before the registry existed.
    """
    setting = settings.setting("jobs")
    if setting.origin == "default":
        resolved = workspace.resolve_jobs(env={}, cli_jobs=None)
        return resolved.value, resolved.source
    value = int(settings.value("jobs"))
    if value < 1:
        source = setting.source or setting.origin
        raise ConfigError(
            f"jobs must be at least 1 ({value}, from {source}, would build nothing at all).",
            hint="one job is a serial build; more parallelize the compile",
        )
    return value, setting.origin


# --------------------------------------------------------------------------
# Summary rendering
# --------------------------------------------------------------------------


def _format_duration(milliseconds: int) -> str:
    if milliseconds % 60_000 == 0 and milliseconds >= 60_000:
        return f"{milliseconds // 60_000} min"
    if milliseconds % 1_000 == 0:
        return f"{milliseconds // 1_000} s"
    return f"{milliseconds} ms"


def _cluster_unit(cluster_id: int) -> tuple[str, float]:
    for definition in registry.CLUSTERS.values():
        if definition.id == cluster_id:
            return definition.unit, float(definition.raw_per_unit)
    return "", 1.0  # pragma: no cover - every generated cluster is known


def format_commissioning(credentials: PairingModel, *, output: Output, masked: bool = False) -> str:
    """The two strings a human needs to add the device to a controller.

    Printed, never written: the builder keeps no record of a device's
    codes beyond the configuration file the user owns and the firmware it
    compiles. Anyone holding either of those holds the passcode — which
    is why output that merely passes by (validate, build) masks the
    codes by default (PO 2026-08-15) and only the explicit ask shows
    them: ``mcuhome device matter-pairing``, or ``--show-sensitive``.
    The discriminator stays visible either way — the device broadcasts
    it in the clear.
    """
    if masked:
        hidden = output.muted(_("hidden — mcuhome device matter-pairing <device> shows it"))
        manual, qr = hidden, hidden
    else:
        tuple_ = pairing.Pairing(
            discriminator=credentials.discriminator,
            passcode=credentials.passcode,
            salt=credentials.salt,
            iterations=credentials.iterations,
        )
        manual = output.style(tuple_.manual_code, output_module.BOLD)
        qr = output.style(tuple_.qr_payload, output_module.BOLD)
    label = output.muted
    lines = [
        output.heading("Commissioning"),
        f"  {label('manual code   ')} {manual}",
        f"  {label('QR code       ')} {qr}",
        f"  {label('discriminator ')} {credentials.discriminator} "
        f"(0x{credentials.discriminator:03X})",
    ]
    if credentials.test_credentials:
        lines.append(
            output.style(
                "  NOTE: these are the credentials published with the Matter SDK. Anyone "
                "who\n        knows them can commission this device — bench use only.",
                output_module.YELLOW,
            )
        )
    return "\n".join(lines)


def format_summary(model: DeviceModel, *, output: Output, masked: bool = True) -> str:
    """The human-readable picture of a resolved device.

    *masked* hides the pairing codes (PO 2026-08-15) — ``validate
    --show-sensitive`` is the one caller that turns it off.
    """
    lines: list[str] = []
    device = model.device
    label = output.muted

    def row(name: str, value: str) -> str:
        return f"{label(name.ljust(9))}  {value}"

    lines.append(
        row("Device", f"{output.style(device.name, output_module.BOLD)} ({device.friendly_name})")
    )
    lines.append(row("Board", device.board))
    lines.append(row("Power", device.power_source))

    network = model.network
    if network.transport == "thread" and network.thread is not None:
        role = {"ftd": "router", "mtd": "end device"}.get(
            network.thread.device_role, network.thread.device_role
        )
        lines.append(row("Transport", f"Thread, {role}"))
    elif network.transport:
        lines.append(row("Transport", network.transport))
    else:
        lines.append(row("Transport", "none (standalone device)"))
    matter = network.matter_enabled
    lines.append(
        row(
            "Matter",
            output.style("enabled", output_module.GREEN) if matter else label("disabled"),
        )
    )
    lines.append(row("Zephyr", model.toolchain.zephyr_line))
    blobs = ", ".join(f"{name}: {value}" for name, value in model.toolchain.blobs.items())
    usage = label(f"(blob_usage: {model.toolchain.blob_usage})")
    lines.append(row("Blobs", f"{blobs or 'none integrated yet'} {usage}"))

    if model.hardware.buses or model.hardware.peripherals:
        lines.append("")
        lines.append(output.heading("Hardware"))
        for bus in model.hardware.buses:
            detail = f" via {bus.controller}" if bus.controller else ""
            frequency = f", {bus.frequency_hz // 1000} kHz" if bus.frequency_hz else ""
            lines.append(f"  bus {bus.id} ({bus.kind}{detail}{frequency})")
        for peripheral in model.hardware.peripherals:
            where = f" on {peripheral.bus}" if peripheral.bus else ""
            address = f" @ {peripheral.reg:#04x}" if peripheral.reg is not None else ""
            lines.append(f"  {peripheral.id}: {peripheral.compatible}{address}{where}")

    if model.endpoints:
        lines.append("")
        lines.append(output.heading("Endpoints"))
        for endpoint in model.endpoints:
            types = ", ".join(
                f"{item.name} {label(f'({item.id:#06x} rev {item.revision})')}"
                for item in endpoint.device_types
            )
            alias = f" [{endpoint.alias}]" if endpoint.alias else ""
            lines.append(f"  {label('endpoint')} {endpoint.id}{alias}: {types}")
            for cluster in endpoint.clusters:
                lines.append(
                    f"    {cluster.name} "
                    + label(
                        f"({cluster.id:#06x} rev {cluster.cluster_revision}, "
                        f"{len(cluster.attrs)} attributes)"
                    )
                )

    if model.channels:
        lines.append("")
        lines.append(output.heading("Channels"))
        for channel in model.channels:
            unit, raw_per_unit = _cluster_unit(channel.cluster_id)
            if channel.report_delta:
                natural = channel.report_delta / raw_per_unit
                delta = f"report on {natural:g} {unit} change"
            else:
                delta = "report every sample"
            lines.append(
                f"  {channel.source.channel} {label('->')} endpoint {channel.endpoint_id} "
                f"{label(f'{channel.cluster_id:#06x}/{channel.attr_id:#06x}')}, every "
                f"{_format_duration(channel.sample_period_ms)}, {delta}"
            )

    if model.network.pairing is not None:
        lines.append("")
        lines.append(format_commissioning(model.network.pairing, output=output, masked=masked))

    if model.build.snippets or model.build.kconfig:
        lines.append("")
        lines.append(output.heading("Build"))
        if model.build.snippets:
            lines.append(f"  {label('snippets:')} {', '.join(model.build.snippets)}")
        lines.append(f"  {len(model.build.kconfig)} {label('Kconfig settings')}")

    return "\n".join(lines)


def _built_line(name: str, *, output: Output) -> str:
    """The one line that says the build worked, and for what."""
    return (
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} Built "
        f"{output.style(name, output_module.BOLD)}."
    )


def format_build_summary(
    name: str,
    *,
    images: list[workspace.ImageArtifacts],
    memory: dict[str, list[workspace.MemoryRegion]],
    output: Output,
    merged: Path | None = None,
) -> str:
    """What came out of stage 5: which images, where, and what they cost.

    Two images, not one, since ADR 0015: a bootloader and an application
    signed for it. Both are reported, because "the firmware" is now both
    of them and a user installing only the second one has a brick.
    """
    lines = [_built_line(name, output=output)]
    for image in images:
        lines.append("")
        lines.append(output.heading(image.describe()))
        for path in image.files:
            lines.append(f"  {output.path(path)}")
    footprint = [
        Footprint(image=image.name, region=region.name, used=region.used, total=region.total)
        for image in images
        for region in memory.get(image.name, [])
    ]
    table = format_memory(footprint, output=output)
    if table:
        lines.append("")
        lines.append(table)
    if merged is not None:
        lines.append("")
        lines.append(output.heading("Combined"))
        lines.append(output.muted("  every image at its own offset, for a full-chip flash"))
        lines.append(f"  {output.path(merged)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Footprint:
    """One image's use of one memory region, in bytes."""

    image: str
    region: str
    used: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 * self.used / self.total if self.total else 0.0


#: Regions the linker reports that are not memory on the device at all.
#: Zephyr collects the interrupt-table metadata in a bogus ``IDT_LIST``
#: region at a made-up address and the final link discards it
#: (``include/zephyr/linker/intlist.ld``), so it holds nothing on any
#: board and never could. Printing "0 of 32 KiB" invites a reader to
#: compare it with FLASH, where that number would mean something.
#: Filtering is by name and never by value: a genuinely empty region is
#: a fact about the build and stays in the table.
LINKER_ONLY_REGIONS = frozenset({"IDT_LIST"})

#: Where a fill level stops being unremarkable. Below the first, no
#: color: an image that fits is not news.
_TIGHT_PERCENT = 75.0
_CRITICAL_PERCENT = 90.0


def _fill_codes(percent: float) -> tuple[str, ...]:
    if percent >= _CRITICAL_PERCENT:
        return (output_module.RED, output_module.BOLD)
    if percent >= _TIGHT_PERCENT:
        return (output_module.YELLOW,)
    return ()


def format_memory(entries: Sequence[Footprint], *, output: Output) -> str:
    """The footprint table: one row per image, one column per region.

    Rounded to whole KiB and whole percent on purpose — a tenth of a KiB
    is noise in a number a person reads to answer "will the next feature
    still fit". The exact bytes stay in ``-o json`` and in the build
    report, which is where anything that computes with them looks.
    """
    entries = [entry for entry in entries if entry.region not in LINKER_ONLY_REGIONS]
    if not entries:
        return ""
    images = list(dict.fromkeys(entry.image for entry in entries))
    regions = list(dict.fromkeys(entry.region for entry in entries))
    by_key = {(entry.image, entry.region): entry for entry in entries}

    def kib(value: int) -> str:
        return f"{round(value / 1024)}"

    # Column widths inside a cell, per region: the used/total numbers of
    # one region line up with each other, which is what makes two rows
    # comparable at a glance.
    used_width = {
        region: max(len(kib(entry.used)) for entry in entries if entry.region == region)
        for region in regions
    }
    total_width = {
        region: max(len(kib(entry.total)) for entry in entries if entry.region == region)
        for region in regions
    }
    rows: list[list[str | output_module.Cell]] = [["Image", *regions]]
    for image in images:
        row: list[str | output_module.Cell] = [image]
        for region in regions:
            entry = by_key.get((image, region))
            if entry is None:
                row.append("")
                continue
            percent = entry.percent
            text = (
                f"{kib(entry.used):>{used_width[region]}} / "
                f"{kib(entry.total):>{total_width[region]}} KiB  {round(percent):>3}%"
            )
            row.append(output_module.Cell(text, _fill_codes(percent)))
        rows.append(row)
    return (
        output.heading("Memory")
        + "\n"
        + output_module.format_table(rows, header=True, indent="  ", output=output)
    )


def format_flash_layout(board: str, *, output: Output) -> str:
    """The partition table the images were built against (ADR 0015)."""
    definition = registry.BOARDS.get(board)
    if definition is None or definition.update_scheme is None:
        return ""
    scheme = definition.update_scheme
    rows: list[list[str | output_module.Cell]] = [["Partition", "Device", "Range", "Size"]]
    for entry in scheme.partitions:
        rows.append(
            [
                entry.fixed_label,
                entry.device or "internal",
                f"{entry.offset:#08x} – {entry.end:#08x}",
                f"{entry.size // 1024} KiB",
            ]
        )
    heading = (
        output.heading("Flash layout")
        + "  "
        + output.muted(
            f"class {scheme.board_class} · MCUboot {scheme.mcuboot_mode} · staging {scheme.staging}"
        )
    )
    table = output_module.format_table(rows, align="lllr", header=True, indent="  ", output=output)
    return f"{heading}\n{table}"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace, output: Output) -> int:
    project, entry = api.find_device(
        args.device, env=_process_env(), cwd=Path.cwd(), project_dir=args.project_dir
    )
    args.json_root = project.root
    output.start("validate", device=args.device)
    result = api.validate_device(entry, project=project, on_warning=output.warn)
    if output.machine:
        document = result.to_dict()
        for entry_dict in document["errors"]:
            output.error(entry_dict)
        output.result(document)
        return phases.EXIT_OK if result.ok else phases.EXIT_FAILURE
    if not result.ok:
        result.raise_errors()
    assert result.model is not None  # noqa: S101 - ok means there is one
    print(format_summary(result.model, output=output, masked=not args.show_sensitive))
    if args.verbose:
        print()
        print(result.model.to_json(), end="")
    print()
    print(
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} "
        f"{output.path(entry)} is valid."
    )
    return phases.EXIT_OK


def _positive_int(text: str) -> int:
    """``--jobs``'s type=: a whole number of parallel build jobs, at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--jobs wants a whole number of parallel build jobs, not {text!r}."
        ) from None
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--jobs must be at least 1 ({value} would build nothing at all)."
        )
    return value


def _snippets_for(model: DeviceModel, extra: list[str] | None) -> tuple[str, ...]:
    """The configuration's own snippets, then anything the caller added.

    Order matters to Zephyr (later fragments override earlier ones), so
    ``--snippet`` deliberately appends: a development transport must be
    able to override what the configuration asks for, not the reverse.
    Duplicates are dropped rather than refused — asking twice for the
    snippet a device already needs is not a mistake worth stopping for.
    """
    ordered: dict[str, None] = {}
    for snippet in [*model.build.snippets, *(extra or [])]:
        ordered.setdefault(snippet, None)
    return tuple(ordered)


def _validate_build(args: argparse.Namespace, output: Output) -> list[MCUHomeError]:
    """``device build``'s validate phase: the argument shapes only it can check.

    Two rule sets, both read-only and instant, which is what the
    validate phase is for (cli ADR 0004 §3): the builder-selection flag
    pairing (:func:`_validate_build_selection`), and the detached pair —
    ``--no-sign`` needs ``--public-key``, and what that names has to
    *be* a public key (ADR 0015 decision 8). A missing or unusable
    input is an exit-2 refusal a user gets in a second, not ten minutes
    into a Matter compile, and the build never starts.
    """
    problems = _validate_build_selection(args)
    if not args.no_sign:
        return problems
    if args.public_key is None:
        return [
            *problems,
            BuildError(
                "--no-sign needs the public half of your signing key (--public-key).",
                hint=(
                    "MCUboot verifies against a public key compiled into the "
                    "bootloader, so a build that does not sign still has to be told "
                    "which key the signature will come from. Write yours out and pass "
                    "it:\n"
                    f"    mcuhome public-key > {signing.PUBLIC_KEY_FILE}\n"
                    "    mcuhome device build <device> --no-sign --public-key "
                    f"{signing.PUBLIC_KEY_FILE}\n"
                    "The private half stays where it is; mcuhome device "
                    "sign-firmware applies the signature afterwards."
                ),
            ),
        ]
    del output  # questions were asked upstream; this phase only checks
    path = Path(args.public_key).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return [
            *problems,
            BuildError(
                f"MCUHome cannot read the public key {path}: {error.strerror}.",
                hint=f"write one with: mcuhome public-key > {path}",
            ),
        ]
    except UnicodeDecodeError:
        return [
            *problems,
            BuildError(
                f"{path} is not a PEM public key.",
                hint=f"write one with: mcuhome public-key > {path}",
            ),
        ]
    if signing.looks_like_p256_key(text):
        return [
            *problems,
            BuildError(
                f"{path} is a private key, and --public-key wants the public half.",
                hint=(
                    "the whole point of --no-sign is that the private key never "
                    "reaches the machine that builds. Write the "
                    "public half out and pass that:\n"
                    f"    mcuhome public-key --signing-key {path} > {signing.PUBLIC_KEY_FILE}"
                ),
            ),
        ]
    if not signing.looks_like_p256_public_key(text):
        return [
            *problems,
            BuildError(
                f"{path} is not an ECDSA P-256 public key in PEM form.",
                hint=(
                    "MCUHome signs with ECDSA P-256. Write the "
                    "public half of your key with: mcuhome public-key > <file>"
                ),
            ),
        ]
    return problems


def _resolve_build_key(
    args: argparse.Namespace, project: api.Project | None
) -> tuple[Path | None, signing.SigningKey | None]:
    """Which key the build gets: ``(public key file, None)`` or ``(None, key)``.

    Two shapes of the same argument (ADR 0015 decision 8). Normally it
    is the user's own private key — ``--signing-key``/``MCUHOME_SIGNING_KEY``
    as a plain PEM file, else the project's
    ``secrets/firmware/mcuboot.yaml``, generated there on first need.
    With ``--no-sign`` it is the **public** half, which is all the
    bootloader needs and all a machine that must not be able to sign may
    have; the validate phase (:func:`_validate_build`) already proved
    the file is one.
    """
    if not args.no_sign:
        key = signing.signing_key(args.signing_key, env=_process_env(), project=project)
        return None, key
    return Path(args.public_key).expanduser(), None


def _build_input(
    args: argparse.Namespace, output: Output
) -> tuple[DeviceModel, Path, api.Project | None]:
    """The model, the build directory and the project, from either input form.

    Two ways in, one result. The normal one runs stages 1-3 on a device
    configuration; ``--model`` takes a canonical model that some other
    machine already resolved and starts at stage 4 (builder-pipeline.md
    §6). The second path deliberately never touches a project directory —
    a build server has no business holding one, and no business holding
    the secrets next to it — which is also why it answers ``None`` for
    the project: with no project there is no per-project signing key and
    no project configuration layer, and both callers act on that.
    """
    if args.model is not None:
        model = api.read_model(Path(args.model))
        # No project to hang the default on, so the build directory is
        # relative to where the command was run. A caller that cares —
        # every build server does — passes --build-dir.
        return model, args.build_dir or Path.cwd() / BUILD_DIR / model.device.name, None
    project, entry = api.find_device(
        args.device, env=_process_env(), cwd=Path.cwd(), project_dir=args.project_dir
    )
    args.json_root = project.root
    model = load_device_model(entry, project=project, output=output)
    return model, args.build_dir or project.root / BUILD_DIR / model.device.name, project


#: The manual rung's mode-specific flags, and the mode each belongs to
#: (ADR 0023 §2). The single source :func:`_validate_build_selection`
#: enforces: a mode flag without ``--build-mode`` — or beside the wrong
#: mode — is an exit-2 refusal, never a silently ignored word.
_MODE_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("build_server", "--build-server", api.REMOTE),
    ("build_token", "--build-token", api.REMOTE),
    ("workspace", "--workspace", api.LOCAL_DEV),
    ("container_image", "--container-image", api.LOCAL),
)


def _validate_build_selection(args: argparse.Namespace) -> list[MCUHomeError]:
    """The builder-selection flag rules, checked before anything runs.

    ADR 0023 §2 has three rungs and they do not mix: ``--build-mode``
    is the fully manual one and owns the mode-specific flags;
    ``--builder`` and the configured default take a builder *whole*.
    Everything here is argument shape — read-only, instant, exit 2.
    """
    problems: list[MCUHomeError] = []
    mode = getattr(args, "build_mode", None)
    if mode is not None and getattr(args, "builder", None) is not None:
        problems.append(
            ConfigError(
                "--builder selects a configured builder and --build-mode builds "
                "fully manually — one or the other, not both.",
                hint=(
                    "a named builder brings its own server, workspace or image with "
                    "it; to override one of those, use --build-mode with "
                    "the mode's own flags instead"
                ),
            )
        )
    for attribute, flag, wanted in _MODE_FLAGS:
        if getattr(args, attribute, None) is None:
            continue
        if mode is None:
            problems.append(
                ConfigError(
                    f"{flag} belongs to the fully manual rung: it needs --build-mode {wanted}.",
                    hint=(
                        "without --build-mode the build uses a configured builder "
                        "(--builder NAME, or the default_builder), and a builder "
                        "carries these values itself"
                    ),
                )
            )
        elif mode != wanted:
            problems.append(
                ConfigError(
                    f"{flag} is a --build-mode {wanted} flag, and this build's mode is {mode}.",
                    hint="drop the flag, or change the mode it belongs to",
                )
            )
    if mode == api.REMOTE and getattr(args, "build_server", None) is None:
        problems.append(
            ConfigError(
                "--build-mode remote needs --build-server.",
                hint=(
                    "name the build server's address (IP or hostname[:port]) — or "
                    "configure a remote builder once and select it with --builder"
                ),
            )
        )
    return problems


def _select_build(
    args: argparse.Namespace,
    settings: api.Settings,
    project: api.Project | None,
    output: Output,
) -> api.SelectedBuilder:
    """Where this build runs: the three rungs of ADR 0023 §2.

    The fully manual rung (``--build-mode`` plus its mode-specific
    flags) bypasses the builder list entirely; otherwise the workbench
    resolves an explicit ``--builder`` name, the configured
    ``default_builder``, or the built-in ``local`` fallback — the remote
    builder's token read from ``secrets/build-server/<name>.yaml`` on
    the way. The flag pairing rules ran in the validate phase
    (:func:`_validate_build_selection`), so this function only selects.
    """
    env = _process_env()
    mode = getattr(args, "build_mode", None)
    if mode is not None:
        selected_workspace = (
            None if args.workspace is None else expand(str(args.workspace), env).resolve()
        )
        return api.SelectedBuilder(
            method=api.resolve_method(mode),
            server=args.build_server,
            token=args.build_token,
            workspace=selected_workspace,
            image=args.container_image,
        )
    return api.resolve_builder(
        settings,
        name=getattr(args, "builder", None),
        project=project,
        env=env,
        on_warning=output.warn,
    )


def _print_log_tail(view: object, log_path: Path) -> None:
    """After a live frame collapsed on failure, restore the log's tail.

    The linear views scrolled the whole log past already, so only the
    live frame owes the reader this. It runs inside an exception
    handler, so its own trouble (a closed stdout, say) must never
    replace the build failure it is decorating.
    """
    try:
        if not isinstance(view, buildview.LiveView):
            return
        tail = buildview.log_tail(log_path)
        if tail:
            print(tail)
            print()
    except Exception:  # noqa: BLE001 - deliberately silent, see docstring
        pass


def _run_method(request: api.BuildRequest, *, method: str) -> api.BuildOutcome:
    """Run one build method and wait for it.

    The one place the command line crosses the async boundary (E53's
    lead-engineer note): the three build methods are awaitable because
    ``remote`` drives a socket and the other two block for minutes, and a
    command line owns its event loop, so it wraps the whole build in a
    single :func:`asyncio.run` and its user sees no difference.
    """
    return asyncio.run(api.run_build(request, method=method))


def _cmd_build(args: argparse.Namespace, output: Output) -> int:
    model, out_dir, project = _build_input(args, output)
    settings = _settings(args, project)
    selection = _select_build(args, settings, project, output)
    method = selection.method
    output.start("build", device=model.device.name, method=method)
    # The whole command holds the build directory, not just the compile:
    # generating the tree, collecting the artifacts and signing the image
    # all write files a second run would be overwriting underneath.
    with api.build_lock(out_dir, device=model.device.name, operation="build"):
        return _build_holding_the_directory(
            args,
            model,
            out_dir,
            project=project,
            settings=settings,
            selection=selection,
            method=method,
            output=output,
        )


def _build_holding_the_directory(
    args: argparse.Namespace,
    model: DeviceModel,
    out_dir: Path,
    *,
    project: api.Project | None,
    settings: api.Settings,
    selection: api.SelectedBuilder,
    method: str,
    output: Output,
) -> int:
    # Host-side stage 4 runs for --generate-only (which stops after it) and
    # for local-dev (which compiles the tree it produces). The two
    # container-shaped methods generate *inside* the build container from
    # the device model the context carries (build-container-contract §6.1),
    # so they write no application on the host — the SDK does, out of reach
    # of the private key.
    if args.generate_only or method == api.LOCAL_DEV:
        # The configuration's file name comes out of the model rather than
        # out of the path this command was given: the generated files name
        # it in their header, and stage 4 has to be a function of the model
        # alone or a --model build could not reproduce a direct build byte
        # for byte.
        output.progress("generate", device=model.device.name)
        written = write_tree(model, out_dir=out_dir, config_name=model.device.source)
        generated = [str(path.relative_to(out_dir)) for path in written]
        if not output.machine:
            print(f"Generated {len(written)} files for {model.device.name} in {out_dir}:")
            for name in generated:
                print(f"  {name}")
        if args.generate_only:
            if output.machine:
                output.result(
                    {
                        "ok": True,
                        "device": model.device.name,
                        "build_dir": str(out_dir),
                        "generated": generated,
                        "manifest": None,
                    }
                )
                return phases.EXIT_OK
            _print_commissioning(model, output=output)
            return phases.EXIT_OK
        return _build_local_dev(
            args,
            model,
            out_dir,
            project=project,
            settings=settings,
            selection=selection,
            generated=generated,
            output=output,
        )

    return _build_delivered(
        args, model, out_dir, project=project, settings=settings, selection=selection, output=output
    )


def _build_local_dev(
    args: argparse.Namespace,
    model: DeviceModel,
    out_dir: Path,
    *,
    project: api.Project | None,
    settings: api.Settings,
    selection: api.SelectedBuilder,
    generated: list[str],
    output: Output,
) -> int:
    """The ``local-dev`` method: compile on the host, then host-sign (E56).

    The local-dev escape hatch (ADR 0007) for a contributor who already has
    a west workspace — selected manually (``--build-mode local-dev``) or
    through a configured builder whose ``workspace:`` then names where to
    compile. Since E56 it is not a special case: like the two
    container-shaped methods it produces an **unsigned** image plus the
    signing parameters (in ``build-manifest.json``), and the private key
    never reaches the west build — the bootloader gets the public half, and
    the one shared host-side step (:func:`_sign_after_build`) signs
    afterwards. ``--no-sign`` skips that step, uniformly with every method.

    The build itself goes through the one dispatch over the three methods
    (:func:`mcuhome.workbench.api.run_build`), so what is left
    here is what a command line owns: which key, which snippets, and how
    to render the answer.
    """
    env = _process_env()
    out_dir = out_dir.resolve()
    snippets = _snippets_for(model, args.snippet)
    scheme = _update_scheme_of(model)
    public_key, key = _resolve_build_key(args, project)
    # Where the west workspace is looked for. A selected workspace — the
    # builder's `workspace:` or the manual rung's --workspace — answers
    # alone; otherwise the two discovery starts of E18 (this package's
    # install location, then where the command was run).
    if selection.workspace is not None:
        module_dir = selection.workspace
        started_in = selection.workspace
    else:
        module_dir = workspace.installed_module_dir()
        started_in = Path.cwd()
    # E56: no build signs inline, so the west build gets the PUBLIC key for
    # the bootloader and nothing more — the private key stays for the host
    # signing step below, exactly as the container path keeps it off the
    # container.
    bootloader_key = public_key if key is None else _bootloader_public_key(key, out_dir)
    jobs, jobs_source = _resolve_jobs(settings)
    bootloader_snippets = () if scheme is None else scheme.bootloader_snippets

    steps = [
        buildview.BuildStep("validate", "validate", state=buildview.DONE),
        buildview.BuildStep("generate", "generate", state=buildview.DONE),
        buildview.BuildStep("compile", "compile (local west)"),
    ]
    if not args.no_sign:
        steps.append(buildview.BuildStep("sign", "sign (local)"))
    view = buildview.make_view(steps, output=output, log_path=out_dir / buildview.LOG_FILE)
    view.note(_validate_note(model, output=output))

    def on_step(stage: str, **facts: Any) -> None:
        output.progress(stage, device=model.device.name, **facts)
        view.step(stage)
        note = _step_note(stage, facts, output=output)
        if note is not None:
            view.note(note)

    def announce(plan: workspace.BuildPlan) -> None:
        """Say what is about to run — after every pre-flight refusal, before it."""
        if not output.machine:
            print()
            print(
                f"{output.heading('Building')} "
                f"{output.style(model.device.name, output_module.BOLD)} for "
                f"{model.device.board} {output.muted('in')} {output.path(plan.topdir)}"
            )
            print(f"  {output.muted('jobs')} {jobs} {output.muted(f'({jobs_source})')}")
            print(_key_note(key, public_key, output))
            print(f"  {output.muted(' '.join(plan.command))}")
            print()
        # The build log is teed to the same terminal; flush so the header
        # above it is not still sitting in this process's buffer.
        sys.stdout.flush()

    try:
        outcome = _run_method(
            api.BuildRequest(
                model=model,
                # Absolute, because the build runs with the workspace top
                # directory as its working directory (that is how west finds
                # the manifest): a relative --build-dir would land somewhere
                # else entirely for anyone who invoked mcuhome from a
                # subdirectory.
                out_dir=out_dir,
                env=env,
                jobs=jobs,
                bootloader_key=bootloader_key,
                snippets=snippets,
                bootloader_snippets=bootloader_snippets,
                # The library states neither of these for itself: `mcuhome` on
                # a command line *is* the local-dev case (E18), and a command
                # line is the one caller entitled to say "where I am installed"
                # and "where I was run" — or, above, which workspace was chosen.
                module_dir=module_dir,
                started_in=started_in,
                on_plan=announce,
                on_step=on_step,
                # The compiler tees its subprocess output into this stream
                # in-process, so the view receives it line by line — into
                # the live frame, the linear passthrough (stdout for a
                # human, stderr under a machine mode where stdout is the
                # document), and always into build.log.
                stream=buildview.ViewStream(view),
            ),
            method=api.LOCAL_DEV,
        )
        result = outcome.detail
        plan, log = result.plan, result.log
        images, merged = result.images, result.merged
        manifest_path = result.manifest_path

        # The one shared host-side signing step (E56), unless --no-sign.
        ota_image = None
        if not args.no_sign:
            on_step("sign")
            ota_image = _sign_after_build(
                model,
                out_dir,
                key=args.signing_key,
                env=env,
                report=outcome.report,
                project=project,
            ).ota
            # Re-read so the artifact list now includes the freshly signed image.
            images = workspace.build_images(plan.build_dir, app_image=plan.app_dir.name)
    except BaseException:
        view.close(success=False)
        _print_log_tail(view, out_dir / buildview.LOG_FILE)
        raise
    view.close(success=True)

    if output.machine:
        output.result(
            {
                "ok": True,
                "device": model.device.name,
                "build_dir": str(out_dir),
                "generated": generated,
                "manifest_path": str(manifest_path),
                "manifest": manifest_module.read_manifest(Path(manifest_path)),
            }
        )
        return phases.EXIT_OK

    print()
    print(
        format_build_summary(
            model.device.name,
            images=images,
            memory=workspace.parse_image_memory_report(
                log, images=[image.name for image in images]
            ),
            output=output,
            merged=merged,
        )
    )
    print(f"  {output.path(manifest_path)}")
    if ota_image is not None:
        print()
        print(_ota_note(ota_image, output=output))
    layout = format_flash_layout(model.device.board, output=output)
    if layout:
        print()
        print(layout)
    if args.no_sign:
        print()
        print(_detached_next_step(out_dir))
    _print_commissioning(model, output=output)
    return 0


# --------------------------------------------------------------------------
# What a build step says about itself (cli ADR 0004, PO 2026-08-16)
# --------------------------------------------------------------------------
#
# A step line says how far a build is; these lines say what it found on
# the way. They stay on the terminal after the run — "which SDK was this
# firmware built from", "was Matter on", "did anything patch the build
# environment" are questions a person asks about a build that finished
# hours ago, and answering them in passing costs one line each.


def _note(label: str, parts: Sequence[str], *, output: Output) -> str:
    """One step's line: which step, then what it established."""
    return "  " + output.muted(label.ljust(10)) + output.muted(" · ").join(parts)


def _count(number: int, thing: str) -> str:
    return f"{number} {thing}" if number == 1 else f"{number} {thing}s"


def _transport_note(model: DeviceModel) -> str:
    network = model.network
    if network.transport == "thread" and network.thread is not None:
        role = {"ftd": "router", "mtd": "end device"}.get(
            network.thread.device_role, network.thread.device_role
        )
        return f"Thread {role}"
    return network.transport or "no transport"


def _validate_note(model: DeviceModel, *, output: Output) -> str:
    """What validating the configuration established, in one line."""
    return _note(
        "validate",
        [
            model.device.board,
            _transport_note(model),
            "Matter on" if model.network.matter_enabled else "Matter off",
            _count(len(model.endpoints), "endpoint"),
            _count(len(model.channels), "channel"),
        ],
        output=output,
    )


def _step_note(stage: str, facts: dict[str, Any], *, output: Output) -> str | None:
    """The line a step's facts make, or None when they make none.

    Facts are append-only display material (``BuildRequest.on_step``):
    what is rendered here is what this version recognizes, and a fact it
    does not know is carried by the machine modes and ignored here
    rather than guessed at.
    """
    if stage != "context" or not facts:
        return None
    # Every value is read defensively: a line that describes the build
    # must never be the thing that ends it.
    parts = [f"SDK {facts.get('sdk', '?')}", f"Zephyr {facts.get('zephyr', '?')}"]
    patches = facts.get("patches") or []
    parts.append(f"patches: {', '.join(patches)}" if patches else "no patches")
    if facts.get("files"):
        parts.append(_count(int(facts["files"]), "file"))
    identity = str(facts.get("id") or "")
    if identity:
        # The full ID is in the manifest and in `-o json`; twelve hex
        # digits are what a person compares two builds with.
        parts.append(f"id {identity.partition(':')[2][:12]}")
    return _note("context", parts, output=output)


def _bootloader_public_key(key: signing.SigningKey, out_dir: Path) -> Path:
    """The PUBLIC key file west compiles into the bootloader (E56).

    Every build is unsigned now, so west never receives the private key —
    it gets the public half, which is all MCUboot needs and is useless for
    signing. With ``--no-sign`` the user already wrote that file out
    (``--public-key``) and this is never called; otherwise the half is
    derived from the resolved key — which may live inside the project's
    ``mcuboot.yaml`` rather than as a file, so it is derived from the PEM
    in memory, never by re-reading a path — into the build directory,
    where the host signing step then uses the private half.
    """
    public = out_dir / ".mcuhome-signing.pub"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(signing.public_key_pem(key.pem), encoding="utf-8")
    return public


def _build_delivered(
    args: argparse.Namespace,
    model: DeviceModel,
    out_dir: Path,
    *,
    project: api.Project | None,
    settings: api.Settings,
    selection: api.SelectedBuilder,
    output: Output,
) -> int:
    """The two container-shaped methods: build elsewhere, sign on the host.

    ``local`` (the default, E54) and ``remote`` are one function because
    from here they are one thing: a build environment receives the device
    model and the **public** signing key, generates and compiles from them
    through the build-container ABI, and *delivers* an unsigned image plus
    the §7.2.1 build report. Whether that environment was a container this
    machine started or one a build server started is
    :func:`mcuhome.workbench.api.run_build`'s business, and it
    answers both in one shape.

    This command then signs on the host, where the private key already is
    (ADR 0015 decision 8), so ``mcuhome build`` still gets to one flashable
    image in one step while the private key never goes near a container or
    a socket — the §9.2 violation the old inline-signing container path
    carried. ``--no-sign`` stops at the unsigned image for the
    detached-from-another-machine workflow.
    """
    env = _process_env()
    out_dir = out_dir.resolve()
    method = selection.method
    public_key, key = _resolve_build_key(args, project)
    # Only the public half ever reaches the build environment. Derived from
    # the private key on the signing path, taken verbatim from --public-key
    # on the detached one — never the private half, on either.
    signing_pub = _public_pem_for_context(public_key, key)
    jobs, jobs_source = _resolve_jobs(settings)
    remote = method == api.REMOTE
    reference = "" if remote else container.image_reference(env, override=selection.image)
    server, token = (selection.server, selection.token) if remote else (None, None)

    if not output.machine:
        print()
        where = "on a build server" if remote else "in the build container"
        print(
            f"{output.heading('Building')} {output.style(model.device.name, output_module.BOLD)} "
            f"for {model.device.board} {output.muted(where)}"
        )
        if selection.builder is not None:
            print(
                f"  {output.muted('builder')} {selection.builder.name} "
                f"{output.muted(f'({selection.builder.type})')}"
            )
        if remote:
            # Only when there is one: a run that is about to be refused for
            # the lack of an address should not print "server None" first.
            if server:
                print(f"  {output.muted('server')} {server}")
        else:
            print(f"  {output.muted('image')} {reference}")
        print(f"  {output.muted('jobs')} {jobs} {output.muted(f'({jobs_source})')}")
        print(_key_note(key, public_key, output))
        print()
    sys.stdout.flush()

    # The step line of the live view (cli ADR 0004, PO 2026-08-15): each
    # label carries where that step runs. Validation already happened —
    # this function starts with a resolved model — so it opens settled.
    where = f"remote {selection.builder.name}" if remote and selection.builder else None
    if remote and where is None:
        where = f"remote {server}" if server else "remote"
    if not remote:
        where = f"container {reference.rsplit(':', 1)[-1]}"
    steps = [
        buildview.BuildStep("validate", "validate", state=buildview.DONE),
        buildview.BuildStep("context", "context"),
        buildview.BuildStep("compile", f"compile ({where})"),
        buildview.BuildStep("artifacts", "artifacts"),
    ]
    if not args.no_sign:
        steps.append(buildview.BuildStep("sign", "sign (local)"))
    view = buildview.make_view(steps, output=output, log_path=out_dir / buildview.LOG_FILE)
    view.note(_validate_note(model, output=output))

    def on_step(stage: str, **facts: Any) -> None:
        # One seam, three consumers: the machine modes get the honest
        # `progress` verb with whatever facts came with it, a live human
        # run gets the repainted step line, and a step that established
        # something worth stating leaves a line saying what.
        output.progress(stage, device=model.device.name, **facts)
        view.step(stage)
        note = _step_note(stage, facts, output=output)
        if note is not None:
            view.note(note)

    try:
        # A hidden scratch area under the build directory: the context and
        # the session tree live here and are rebuilt each run; the durable
        # artifacts are copied up into out_dir below.
        outcome = _run_method(
            api.BuildRequest(
                model=model,
                out_dir=out_dir,
                env=env,
                jobs=jobs,
                signing_pub=signing_pub,
                sdk_sources=settings.value("sdk_sources"),
                image=None if remote else reference,
                server=server,
                token=token,
                on_line=view.line,
                on_step=on_step,
            ),
            method=method,
        )
        if not outcome.successful:
            raise _delivered_build_failed(outcome)

        on_step("artifacts")
        copied = _collect_delivered_artifacts(outcome, out_dir)
        report = imgtool.read_build_report(out_dir / outcome.report)

        # Whatever an earlier build of this directory signed is
        # (re)produced only on the signing branch below; drop any stale
        # signed image and .ota first so a --no-sign run cannot leave a
        # flashable lookalike beside the fresh unsigned firmware.
        _drop_signed_lookalikes(out_dir)

        signed: list[Path] = []
        ota_image = None
        if not args.no_sign:
            on_step("sign")
            result = _sign_after_build(
                model,
                out_dir,
                key=args.signing_key,
                env=env,
                report=outcome.report,
                project=project,
            )
            signed, ota_image = result.signed, result.ota
    except BaseException:
        # Collapse the frame before whatever renders the failure, and put
        # the log tail back on the terminal: the frame's scrollback is
        # gone with it, and the tail is what a person diagnoses from.
        view.close(success=False)
        _print_log_tail(view, out_dir / buildview.LOG_FILE)
        raise
    view.close(success=True)

    if output.machine:
        output.result(
            {
                "ok": True,
                "device": model.device.name,
                "build_dir": str(out_dir),
                "image": outcome.image,
                "signed": not args.no_sign,
                "artifacts": [{"role": role, "path": name} for role, name, _ in copied],
                "signed_artifacts": [str(path) for path in signed],
                "report": report,
                "ota": None if ota_image is None else str(ota_image.path),
            }
        )
        return phases.EXIT_OK

    print()
    print(_format_local_summary(model.device.name, copied, signed, report, output=output))
    if ota_image is not None:
        print()
        print(_ota_note(ota_image, output=output))
    layout = format_flash_layout(model.device.board, output=output)
    if layout:
        print()
        print(layout)
    if args.no_sign:
        print()
        print(_detached_next_step(out_dir))
    _print_commissioning(model, output=output)
    return 0


def _public_pem_for_context(public_key: Path | None, key: signing.SigningKey | None) -> str:
    """The **public** key PEM that goes into the context — never the private half.

    On the signing path *key* is the resolved private key — possibly
    living inside the project's ``mcuboot.yaml`` rather than as a file —
    and the public half is derived from its PEM in memory; with
    ``--no-sign`` *key* is None and *public_key* points at the public key
    the user wrote out. Either way what leaves this function is a public
    key, which is all a build container may ever hold (ADR 0015
    decision 8).
    """
    if key is not None:
        return signing.public_key_pem(key.pem)
    assert public_key is not None  # noqa: S101 - _resolve_build_key returns one or the other
    return public_key.read_text(encoding="utf-8")


def _collect_delivered_artifacts(
    outcome: api.BuildOutcome, out_dir: Path
) -> list[tuple[str, str, Path]]:
    """Copy the verified delivered artifacts up into *out_dir*.

    A build environment delivers into a per-invocation directory that is
    wiped on the next build; the durable copies a user flashes and signs
    belong in the build directory itself. Only the artifacts the method
    declared and verified (:attr:`…api.BuildOutcome.artifacts`)
    are copied — nothing undeclared rides along, on either method.
    """
    copied: list[tuple[str, str, Path]] = []
    if outcome.out_dir is None:
        return copied
    for artifact in outcome.artifacts:
        source = outcome.out_dir / artifact.path
        destination = out_dir / artifact.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append((artifact.role, artifact.path, destination))
    return copied


def _delivered_build_failed(outcome: api.BuildOutcome) -> BuildError:
    """A build whose result was not a conforming deliverable.

    Two voices speak here and both are quoted. ``problems`` is the
    *backend's* judgement — which §5.3 condition failed. The result
    document (or, on the remote method, the verdict's error envelope) is
    the *program's* account of itself: ``reason``, the §5.4 error message
    and its details. A program that refuses before it runs anything writes
    only that document and not a line of build log, so dropping it here
    left "status 'failure'; exited 1" as the entire diagnosis of a failure
    the program had explained precisely.
    """
    # The local method's detail wraps the backend outcome; the remote
    # method's *is* the outcome. Both carry the same §5.4 vocabulary.
    inner = getattr(outcome.detail, "outcome", outcome.detail)
    problems = "; ".join(getattr(inner, "problems", ()) or ()) or (
        f"the build reported {outcome.status!r} and no usable result"
    )
    said = []
    document = getattr(inner, "result", None) or {}
    if not document and getattr(inner, "error", None):
        document = {"error": inner.error}
    error = document.get("error")
    error = error if isinstance(error, dict) else {}
    if document.get("reason"):
        said.append(str(document["reason"]))
    if error.get("message"):
        said.append(str(error["message"]))
    details = error.get("details")
    if details:
        said.append(json.dumps(details, sort_keys=True))
    account = f" The program said: {' — '.join(said)}" if said else ""
    where = (
        "on a build server" if outcome.method == api.REMOTE else "in the MCUHome build container"
    )
    return BuildError(
        f"The firmware did not build: {problems}.{account}",
        hint=(
            f"the build ran {where}. The build log above carries what west and the "
            "compiler said; --build-mode local-dev compiles on the host instead."
        ),
    )


def _report_footprint(report: dict) -> list[Footprint]:
    """The §7.2.1 ``memory`` entries as the one footprint shape.

    What ``format_build_summary`` reads out of a host build log, read
    here out of the report the build environment measured — the two
    paths render one table because they answer one question.
    """
    memory = report.get("memory")
    if not isinstance(memory, list):
        return []
    entries = []
    for region in memory:
        if not isinstance(region, dict):
            continue
        entries.append(
            Footprint(
                image=str(region.get("image", "")),
                region=str(region.get("region", "")),
                used=int(region.get("used", 0)),
                total=int(region.get("total", 0)),
            )
        )
    return entries


def _format_local_summary(
    name: str,
    copied: list[tuple[str, str, Path]],
    signed: list[Path],
    report: dict,
    *,
    output: Output,
) -> str:
    """What the container delivered, what the host signed, and the footprint."""
    lines = [_built_line(name, output=output)]
    lines.append("")
    lines.append(output.heading("Artifacts"))
    for role, _name, destination in copied:
        lines.append(f"  {output.path(destination)}  {output.muted(f'({role})')}")
    for path in signed:
        lines.append(f"  {output.path(path)}  {output.style('(signed)', output_module.GREEN)}")
    table = format_memory(_report_footprint(report), output=output)
    if table:
        lines.append("")
        lines.append(table)
    return "\n".join(lines)


def _write_ota(model: DeviceModel, *, out_dir: Path, signed: Path) -> ota.OtaImage | None:
    """Wrap the signed image in a Matter OTA file, when this device can take one.

    Returns None — silently, and correctly — for a board whose update
    scheme has no staging slot and for a device without a Matter stack:
    neither can be updated over the air, so an .ota file for it would be a
    file nothing can deliver.
    """
    parameters = manifest_module.ota_parameters(model)
    if parameters is None or not signed.is_file():
        return None
    return otafile.write_ota_image(
        payload=signed,
        output=out_dir / otafile.ota_file_name(model.device.name, parameters.version),
        vendor_id=parameters.vendor_id,
        product_id=parameters.product_id,
        version=parameters.version,
    )


def _ota_note(image: ota.OtaImage, *, output: Output) -> str:
    return (
        output.heading("Matter OTA image")
        + "  "
        + output.muted(f"version {image.version}, SoftwareVersion {image.software_version}")
        + f"\n  {output.path(image.path)}\n"
        + output.muted(
            "  Put it where your controller's OTA provider looks for images; the device\n"
            "  downloads it over Thread once a controller announces the provider or the\n"
            "  next periodic query runs."
        )
    )


@dataclass(frozen=True)
class _Signed:
    """What the one host-side signing step produced."""

    #: The files imgtool wrote.
    signed: list[Path]
    #: The Matter OTA image wrapped around the freshly signed binary, or
    #: None for a device that cannot be updated over the air.
    ota: ota.OtaImage | None
    #: The updated ``build-manifest.json`` content, on the shape that has
    #: one to fold a signature back into.
    manifest: dict | None


def _sign_after_build(
    model: DeviceModel,
    out_dir: Path,
    *,
    key: Path | None,
    env: dict[str, str],
    report: str,
    project: api.Project | None = None,
) -> _Signed:
    """The one host-side signing step, for every build method (E56).

    Every method delivers an **unsigned** image, and exactly one place
    turns it into a flashable one — this function. ``local-dev``,
    ``local`` and ``remote`` all reach it with the same four arguments,
    and the private key enters the story here and nowhere earlier, which
    is what makes "the signing key never reaches the thing that builds" a
    property of the code rather than of three code paths agreeing.

    *report* is :attr:`…api.BuildOutcome.report`: the two report
    shapes E55 says must both be read. A ``build-manifest.json`` is a host
    build's, and its signature and ``.ota`` are folded back into it; a
    §7.2.1 ``build-report.json`` is a delivery from a build environment,
    which has no manifest to fold anything into, so the ``.ota`` is
    written from the device model this command still holds. Which one
    applies is decided by what the build said it wrote — never by looking
    around the directory, which is how ``mcuhome sign`` has to do it (see
    :func:`_target_is_build_report`) because it arrives without a build.
    """
    if report == manifest_module.MANIFEST_FILE:
        plan, data, ota_image = _apply_manifest_signature(
            out_dir, key=key, env=env, project=project
        )
        return _Signed(signed=list(plan.outputs), ota=ota_image, manifest=data)
    plan = imgtool.sign_report(out_dir, key=key, env=env, project=project)
    signed = list(plan.outputs)
    signed_bin = next((path for path in signed if path.suffix == ".bin"), None)
    ota_image = (
        None if signed_bin is None else _write_ota(model, out_dir=out_dir, signed=signed_bin)
    )
    return _Signed(signed=signed, ota=ota_image, manifest=None)


def _drop_signed_lookalikes(out_dir: Path) -> None:
    """Remove signed firmware and OTA files an earlier build of this dir left.

    The local path copies the container's UNSIGNED firmware into the
    persistent *out_dir* and signs on the host only afterwards. A ``--no-sign``
    run — or a repeat build — writes no signed image, but a
    ``firmware.signed.*`` and a matching ``*.ota`` from an earlier signed
    build of the same directory would otherwise survive next to the fresh
    unsigned firmware: a flashable, boot-bricking lookalike that belongs to
    no build now here and that the summary does not mention. Dropping them
    before the signing branch means that branch is the only thing that ever
    creates them, so what a build leaves behind is always what that build
    produced — the promise the ``local-dev`` path keeps through
    :func:`mcuhome.compiler.devbuild.drop_unsigned_lookalikes`. The signed names come from
    :data:`imgtool.REPORT_FIRMWARE`, so this and the signer cannot disagree
    about what a signed image is called.
    """
    for _source_name, signed_name in imgtool.REPORT_FIRMWARE:
        (out_dir / signed_name).unlink(missing_ok=True)
    for ota_path in out_dir.glob("*.ota"):
        ota_path.unlink(missing_ok=True)


def _detached_next_step(out_dir: Path) -> str:
    return (
        "This build is UNSIGNED, and MCUboot boots nothing it cannot verify.\n"
        "Sign it where your private key is:\n"
        f"    mcuhome device sign-firmware {out_dir}"
    )


def _update_scheme_of(model: DeviceModel) -> registry.UpdateSchemeDef | None:
    board = registry.BOARDS.get(model.device.board)
    return None if board is None else board.update_scheme


def _key_note(key: signing.SigningKey | None, public_key: Path | None, output: Output) -> str:
    """The key line of the build header, for either shape of the argument."""
    if key is not None:
        return _signing_key_note(key, output)
    assert public_key is not None  # noqa: S101 - _resolve_build_key returns one or the other
    return _detached_key_note(public_key, output)


def _signing_key_note(key: signing.SigningKey, output: Output) -> str:
    """Where the signing key is, and — loudly — when it is brand new.

    A new key is not a detail: MCUboot verifies against the public half
    compiled into the bootloader already on the device, so firmware
    signed with a key that was just generated is firmware an already
    bootstrapped device will refuse.
    """
    if not key.created:
        return f"  {output.muted('signing key')} {output.path(key.path)}"
    return (
        f"  {output.muted('signing key')} {output.path(key.path)}\n"
        f"               {output.style('NEW', output_module.YELLOW, output_module.BOLD)}"
        " — MCUHome had none and generated one just now. Keep it: every\n"
        "               device bootstrapped with it only accepts firmware signed "
        "with it,\n"
        "               and replacing it means bootstrapping those devices again."
    )


def _detached_key_note(path: Path, output: Output) -> str:
    """Where the *public* key came from, and what it does not let happen."""
    return f"  {output.muted('public key ')} {output.path(path)}\n" + output.muted(
        "              --no-sign: the bootloader gets this, the application is\n"
        "              left unsigned, and no private key is anywhere near this build."
    )


def _print_commissioning(model: DeviceModel, *, output: Output) -> None:
    """A masked pairing reminder, last, where a freshly built device needs it.

    Masked on purpose (PO 2026-08-15): a build log is output that merely
    passes by, and the explicit ask — mcuhome device matter-pairing —
    shows the codes.
    """
    if model.network.pairing is None:
        return
    print()
    print(format_commissioning(model.network.pairing, output=output, masked=True))


def _validate_matter_pairing(args: argparse.Namespace, output: Output) -> list[MCUHomeError]:
    del output
    if args.force and not args.new:
        return [
            BuildError(
                "--force replaces credentials, and only --new draws any.",
                hint="mcuhome device matter-pairing --new <device> --force",
            )
        ]
    return []


def _cmd_matter_pairing(args: argparse.Namespace, output: Output) -> int:
    """``mcuhome device matter-pairing``: show the codes, or draw new ones.

    The bare command is the one *explicit* ask for the pairing codes —
    everywhere else (validate, build) they are masked (PO 2026-08-15).
    ``--new`` draws fresh credentials through the provision module:
    ``!secret`` references into main.yaml, values into the device's own
    secrets file.
    """
    project, entry = api.find_device(
        args.device, env=_process_env(), cwd=Path.cwd(), project_dir=args.project_dir
    )
    if not args.new:
        model = api.load_model(entry, project=project, on_warning=output.warn)
        if not model.network.matter_enabled or model.network.pairing is None:
            raise BuildError(
                f"Matter is off for {args.device}, so it has no pairing.",
                hint=(
                    "switch it on under network: —\n    matter:\n      enabled: true\n"
                    "  and draw credentials: mcuhome device matter-pairing --new "
                    f"{args.device}"
                ),
            )
        print(format_commissioning(model.network.pairing, output=output))
        return phases.EXIT_OK
    result = provision.init_pairing(entry, secrets_file=project.secrets_file, force=args.force)
    verb = "Replaced the commissioning credentials for" if result.replaced else "Wrote"
    print(
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} {verb} "
        f"{output.path(result.entry)}."
    )
    print(
        f"The values live in {output.path(result.secrets_file)}; the configuration carries "
        "!secret references."
    )
    print()
    print(format_commissioning(_pairing_model(result.pairing), output=output))
    print()
    print(
        output.style(
            "Keep the secrets file safe: it is the only copy. Anyone who has it — or the "
            "firmware\nbuilt from it — can commission this device.",
            output_module.YELLOW,
        )
    )
    return 0


def _pairing_model(credentials: pairing.Pairing) -> PairingModel:
    return PairingModel(
        discriminator=credentials.discriminator,
        passcode=credentials.passcode,
        salt=credentials.salt,
        iterations=credentials.iterations,
        test_credentials=credentials.test_credentials,
    )


# --- the project noun (cli ADR 0003) ----------------------------------


def _project_document(project: api.Project) -> dict[str, Any]:
    """One project, as the machine modes describe it."""
    file = project.file
    return {
        "root": str(project.root),
        "id": None if file is None else file.id,
        "short_id": None if file is None else file.short_id,
        "version": None if file is None else file.version,
        "current_version": api.PROJECT_VERSION,
    }


def _project_for(args: argparse.Namespace, *, require_version: bool) -> api.Project:
    """The project a ``project`` command works on.

    An explicit directory — the positional argument, or ``--project-dir``
    — names *that* directory and disables the upward search, the same
    rule the flag follows everywhere else. Without one the search runs,
    because a person standing in ``devices/porch`` means their project.
    """
    explicit = args.directory or args.project_dir
    return api.resolve_project(
        None if explicit is None else Path(explicit),
        env=_process_env(),
        cwd=Path.cwd(),
        require_version=require_version,
    )


def _cmd_project_init(args: argparse.Namespace, output: Output) -> int:
    """``mcuhome project init``: the durable part of a project (ADR 0022).

    The target is the positional argument, or ``--project-dir`` when only
    that was given — the one command where that flag may name a directory
    that is *not* a project yet, because making it one is the job. Missing
    directories are created on the way.
    """
    target = Path(args.directory) if args.directory is not None else Path.cwd()
    if args.directory is None and args.project_dir is not None:
        target = args.project_dir
    target = target.resolve()
    if api.is_project_root(target) and not args.force:
        project = api.project_at(target, require_version=False)
        output.human(
            _("{path} is already an MCUHome project; nothing to do.").format(
                path=output.path(target)
            )
        )
        output.result({"ok": True, "created": [], "project": _project_document(project)})
        return phases.EXIT_OK
    result = api.init_project(target, force=args.force)
    output.human(
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} "
        + _("Created an MCUHome project in {path}:").format(path=output.path(result.project.root))
    )
    # Files first, then directories with a trailing slash and ls's blue
    # (PO 2026-08-15) — what was created is legible at a glance.
    for path in sorted(result.created, key=lambda p: (p.is_dir(), str(p))):
        shown = str(path.relative_to(result.project.root))
        if path.is_dir():
            shown = output.style(f"{shown}/", output_module.BLUE)
        output.human(f"  {shown}")
    output.human()
    output.human(output.heading(_("Next:")))
    output.human(_("  mcuhome device new --help    how to describe your first device"))
    output.human(_("  mcuhome --help               every command, and the workflow"))
    output.human()
    output.human(_("Getting started: {url}").format(url=_docs_url("getting-started")))
    output.result(
        {
            "ok": True,
            "created": [str(path) for path in result.created],
            "project": _project_document(result.project),
        }
    )
    return phases.EXIT_OK


def _cmd_project_info(args: argparse.Namespace, output: Output) -> int:
    """``mcuhome project info``: which project this is, and which version.

    Deliberately readable on an **outdated** project: this is the command
    a person runs when another one just refused, and answering "upgrade
    first" to that question would be a circle.
    """
    project = _project_for(args, require_version=False)
    file = project.file
    assert file is not None  # a resolved project always has one
    devices = project.device_names()
    plan = api.upgrade_plan(file.version)

    rows: list[list[str | output_module.Cell]] = [
        [output.muted(_("Project")), output.path(project.root)],
        [
            output.muted(_("Id")),
            f"{file.id}  {output.muted(_('(short: {short})').format(short=file.short_id))}"
            if file.id
            else output.muted(_("none yet — this project predates project ids")),
        ],
        [
            output.muted(_("Version")),
            f"{file.version}  "
            + (
                output.style(_("(current)"), output_module.GREEN)
                if not plan
                else output.style(
                    _("(needs an upgrade to {version})").format(version=api.PROJECT_VERSION),
                    output_module.YELLOW,
                )
            ),
        ],
        [
            output.muted(_("Devices")),
            ", ".join(devices) if devices else output.muted(_("none yet")),
        ],
    ]
    output.human(output_module.format_table(rows, output=output))
    if plan:
        output.human()
        output.human(
            _("This project needs an upgrade before it can be used:\n    {command}").format(
                command=f"mcuhome project upgrade {project.root}"
            )
        )
    output.result(
        {
            "ok": True,
            "project": _project_document(project),
            "devices": devices,
            "upgrade_required": bool(plan),
            "plan": [_migration_document(migration) for migration in plan],
        }
    )
    return phases.EXIT_OK


def _migration_document(migration: api.Migration) -> dict[str, Any]:
    return {
        "name": migration.name,
        "from_version": migration.from_version,
        "to_version": migration.to_version,
        "description": migration.description,
        "details": migration.details,
    }


def _print_upgrade_plan(
    project: api.Project,
    plan: Sequence[api.Migration],
    *,
    output: Output,
) -> None:
    """What the upgrade would do — the text a user approves."""
    file = project.file
    assert file is not None
    named = (
        f"  {output.muted(_('(id {short})').format(short=file.short_id))}" if file.short_id else ""
    )
    where = _("Upgrading the project in {path}").format(path=output.path(project.root))
    output.human(where + named)
    output.human(
        _("from project version {old} to {new}:").format(old=file.version, new=plan[-1].to_version)
    )
    output.human()
    for number, migration in enumerate(plan, start=1):
        output.human(f"  {number}. {migration.description}")


def _print_upgrade_details(applied: Sequence[api.Migration], *, output: Output) -> None:
    """The long form: what changed, and what to watch out for from now on."""
    for migration in applied:
        output.human()
        output.human(output.heading(migration.description))
        for line in migration.details.splitlines():
            output.human(f"  {line}" if line else "")


def _wait_for_builds(session: api.UpgradeSession, *, output: Output) -> None:
    """Wait until nothing is working in this project's build directories.

    No lock is taken: the project file is already renamed, so no build can
    *start* — this only waits out the ones that began before the upgrade
    did. It runs before the confirmation on purpose (PO 2026-08-16): a
    person who types "yes" must see the upgrade begin, not a wait they
    might interrupt at the very moment it ends.
    """
    announced: set[str] = set()
    while True:
        busy = session.running_builds()
        if not busy:
            if announced:
                output.human(_("Done waiting — nothing is working in this project any more."))
            return
        for entry in busy:
            if entry.name in announced:
                continue
            announced.add(entry.name)
            output.progress("waiting", device=entry.name, process=entry.process)
            output.human(
                _("Waiting for {device}: {what} (process {process}, started {started}).").format(
                    device=entry.name,
                    what=entry.operation or _("a run"),
                    process=entry.process or "?",
                    started=entry.started or "?",
                )
            )
        time.sleep(1.0)


def _confirm_upgrade(
    args: argparse.Namespace,
    session: api.UpgradeSession,
    *,
    output: Output,
) -> bool:
    """The point of no return: a typed ``yes``, or the project's own id.

    Not a ``--force``-shaped flag, deliberately: ``--confirm-upgrade``
    takes the id of the project it is about, so a script that ends up in
    the wrong directory refuses instead of migrating something else.
    """
    file = session.file
    if args.confirm_upgrade is not None:
        return file.matches(args.confirm_upgrade)
    output.human()
    output.human(
        output.style(_("This cannot be undone."), output_module.YELLOW, output_module.BOLD)
    )
    output.human(
        _(
            "  Make sure you have a backup of this project, and that nothing else is\n"
            "  working on it (a build, a flash, an editor writing files)."
        )
    )
    output.human()
    try:
        answer = input(_("Type yes to upgrade, anything else to cancel: "))
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


class _StopAfterCurrentMigration:
    """Ctrl+C and SIGTERM stop the upgrade — between migrations, not inside one.

    Cutting a migration in half is what leaves a project broken, so the
    signal only sets a flag and the run ends at the next clean boundary.
    A person who really wants out now is told how (three times within
    three seconds) and what it costs; SIGKILL cannot be caught at all,
    and that case is what the renamed project file makes visible
    afterwards.
    """

    #: How many presses, and how close together, mean "now".
    PRESSES = 3
    WINDOW = 3.0

    def __init__(self, output: Output) -> None:
        self._output = output
        self._stop = False
        self._presses = 0
        self._first = 0.0
        self._previous: dict[int, Any] = {}

    def requested(self) -> bool:
        return self._stop

    @property
    def stopping(self) -> bool:
        return self._stop

    def __enter__(self) -> _StopAfterCurrentMigration:
        for number in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous[number] = signal.signal(number, self._handle)
            except ValueError:  # pragma: no cover - not the main thread
                self._previous.clear()
                break
        return self

    def __exit__(self, *_exc: object) -> None:
        for number, handler in self._previous.items():
            signal.signal(number, handler)

    def _handle(self, number: int, _frame: object) -> None:
        self._stop = True
        if number != signal.SIGINT:
            self._output.log(_("Stopping after the current migration."))
            return
        now = time.monotonic()
        if now - self._first > self.WINDOW:
            self._first = now
            self._presses = 0
        self._presses += 1
        if self._presses >= self.PRESSES:
            self._output.log(
                _(
                    "Aborting now. The project is left mid-upgrade and will most "
                    "likely be broken — restore your backup."
                )
            )
            signal.signal(signal.SIGINT, self._previous.get(signal.SIGINT, signal.SIG_DFL))
            raise KeyboardInterrupt
        self._output.log(
            _(
                "Stopping after the current migration — interrupting one half-way "
                "would break the project.\nPress Ctrl+C {presses} times within "
                "{window:.0f} seconds to abort immediately anyway."
            ).format(presses=self.PRESSES, window=self.WINDOW)
        )


def _validate_upgrade(args: argparse.Namespace, output: Output) -> list[MCUHomeError]:
    """Argument rules of ``project upgrade``, checked before anything moves.

    The confirmation is the whole rule: without a terminal to type
    ``yes`` at, ``--confirm-upgrade`` is required and must name *this*
    project. Problems resolving the project itself are left to the run —
    they are refusals (exit 1), not usage errors.
    """
    try:
        project = _project_for(args, require_version=False)
    except MCUHomeError:
        return []
    file = project.file
    if file is None or args.dry_run or not api.upgrade_plan(file.version):
        return []
    command = f"mcuhome project upgrade {project.root} --confirm-upgrade {file.token}"
    if args.confirm_upgrade is None:
        if output.interactive:
            return []
        return [
            ConfigError(
                _("An upgrade has to be confirmed, and there is no terminal to confirm at."),
                hint=_(
                    "back the project up first, then name the project you mean:\n    {command}"
                ).format(command=command),
            )
        ]
    if not file.matches(args.confirm_upgrade):
        return [
            ConfigError(
                _('"{given}" does not name the project in {path}.').format(
                    given=args.confirm_upgrade, path=project.root
                ),
                hint=_("pass this project's id — the short form is enough:\n    {command}").format(
                    command=command
                ),
            )
        ]
    return []


def _cmd_project_upgrade(args: argparse.Namespace, output: Output) -> int:
    """``mcuhome project upgrade``: migrate a project to the current layout.

    The order is the design (PO 2026-08-16): take the project — which
    renames its file, so nothing else can start work on it — wait for
    whatever was already running, *then* ask, then migrate.
    """
    project = _project_for(args, require_version=False)
    file = project.file
    assert file is not None
    output.start("project-upgrade", project=str(project.root))
    plan = api.upgrade_plan(file.version)
    if not plan:
        output.human(
            _("The project in {path} is up to date (version {version}); nothing to do.").format(
                path=output.path(project.root), version=file.version
            )
        )
        output.result({"ok": True, "project": _project_document(project), "applied": []})
        return phases.EXIT_OK

    if args.dry_run:
        _print_upgrade_plan(project, plan, output=output)
        _print_upgrade_details(plan, output=output)
        output.human()
        output.human(output.muted(_("Nothing was changed (--dry-run).")))
        output.result(
            {
                "ok": True,
                "project": _project_document(project),
                "dry_run": True,
                "plan": [_migration_document(migration) for migration in plan],
            }
        )
        return phases.EXIT_OK

    try:
        with api.upgrade_session(project.root) as session:
            _print_upgrade_plan(project, session.plan, output=output)
            output.human()
            _wait_for_builds(session, output=output)
            if not _confirm_upgrade(args, session, output=output):
                output.human(_("Cancelled. Nothing was changed."))
                return phases.EXIT_FAILURE
            output.human()
            result = _apply_upgrade(session, output=output)
    except KeyboardInterrupt:
        output.human()
        output.human(_("Cancelled. Nothing was changed."))
        return phases.EXIT_FAILURE

    document = {
        "ok": not result.stopped,
        "project": _project_document(api.project_at(project.root, require_version=False)),
        "from_version": result.from_version,
        "to_version": result.to_version,
        "stopped": result.stopped,
        "applied": [_migration_document(migration) for migration in result.applied],
    }
    if result.stopped:
        output.human()
        output.human(
            _("Stopped at project version {version}. Run the upgrade again to continue.").format(
                version=result.to_version
            )
        )
        output.result(document)
        return phases.EXIT_FAILURE
    output.human()
    output.human(
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} "
        + _("Project upgraded: version {old} → {new}.").format(
            old=result.from_version, new=result.to_version
        )
    )
    _print_upgrade_details(result.applied, output=output)
    output.human()
    output.human(
        _("What changed and what to do if something looks wrong: {url}").format(
            url=_docs_url("project-upgrade")
        )
    )
    output.result(document)
    return phases.EXIT_OK


def _apply_upgrade(session: api.UpgradeSession, *, output: Output) -> api.UpgradeResult:
    def report(kind: str, migration: api.Migration) -> None:
        if kind == "start":
            output.progress("migration", name=migration.name, to_version=migration.to_version)
            output.human(f"  {migration.description} …")
        else:
            output.human(
                f"  {output.style('✓', output_module.GREEN)} "
                + _("version {version}").format(version=migration.to_version)
            )

    with _StopAfterCurrentMigration(output) as stopper:
        return session.apply(on_event=report, should_stop=stopper.requested)


def _cmd_new(args: argparse.Namespace, output: Output) -> int:
    created = scaffold.new_device(
        args.device,
        board=args.board,
        env=_process_env(),
        cwd=Path.cwd(),
        project_dir=args.project_dir,
        friendly_name=args.name,
    )
    print(
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} Wrote "
        f"{output.path(created.entry)}."
    )
    print()
    print(output.heading(_("Next:")))
    print(f"  mcuhome device matter-pairing --new {created.name}    draw its commissioning codes")
    print(f"  mcuhome device validate {created.name}        see what it resolves to")
    print(f"  mcuhome device build {created.name}           compile it")
    print()
    print(
        output.muted(
            _(
                "The configuration has no hardware in it yet — the file carries a complete, "
                "commented\nexample to uncomment and adjust."
            )
        )
    )
    return 0


def _apply_manifest_signature(
    target: Path,
    *,
    key: Path | None,
    env: dict[str, str],
    project: api.Project | None = None,
) -> tuple[imgtool.SignPlan, dict, ota.OtaImage | None]:
    """Sign a build-manifest build and fold the signature and .ota back in.

    The one host-side signing step for a build described by
    ``build-manifest.json`` — the ``local-dev`` build's report shape. Shared
    (E56) by ``mcuhome sign`` on such a directory and by ``mcuhome build``
    right after ``local-dev`` compiles an **unsigned** image: neither build
    signs itself, and this is the single place a private key turns an
    unsigned image into a flashable one. The .ota wraps the image that was
    just signed, and the manifest records both — all from the manifest's
    own parameters, so the machine that runs this needs neither the device
    configuration nor the Matter SDK.
    """
    plan = imgtool.sign_build(target, key=key, env=env, project=project)
    data = manifest_module.record_signature(
        manifest_module.read_manifest(plan.manifest_path), out_dir=plan.out_dir, files=plan.outputs
    )
    ota_image = _sign_ota(data, out_dir=plan.out_dir, outputs=plan.outputs)
    if ota_image is not None:
        manifest_module.record_ota(data, ota_image, out_dir=plan.out_dir)
    manifest_module.dump_manifest(data, out_dir=plan.out_dir)
    return plan, data, ota_image


def _is_sign_target(path: Path) -> bool:
    """Whether *path* is something the signing step can work on directly."""
    if path.is_file():
        return path.name in (manifest_module.MANIFEST_FILE, imgtool.BUILD_REPORT_FILE)
    return path.is_dir() and (
        (path / manifest_module.MANIFEST_FILE).is_file()
        or (path / imgtool.BUILD_REPORT_FILE).is_file()
    )


def _resolve_sign_target(args: argparse.Namespace) -> tuple[Path, api.Project | None]:
    """``device sign-firmware``'s argument: a device name, or a build's path.

    Inside a project, a device name signs that device's last build
    (``<project>/build/<device>/``). The path form exists for the
    detached workflow (ADR 0015 decision 8): the machine holding the
    private key may hold nothing but the key and a delivered build
    directory — no project, no device configuration — and an explicit
    ``--signing-key``/``MCUHOME_SIGNING_KEY`` then needs no project at
    all. A path that *is* a build wins over a device that happens to
    share its spelling, because the path is the more explicit statement.
    """
    spec = Path(args.target)
    if _is_sign_target(spec):
        return spec, _optional_project(args)
    if spec.exists() and not (spec.is_dir() and (spec / api.DEVICE_ENTRY).is_file()):
        # An existing path that is neither a build nor a device folder:
        # say what a sign target must hold, not what a device folder is.
        raise BuildError(
            f"{spec} holds neither a {manifest_module.MANIFEST_FILE} nor a "
            f"{imgtool.BUILD_REPORT_FILE}.",
            hint=(
                "point at a finished build directory, one of those two files, or "
                "a device name inside a project (signs its last build)"
            ),
        )
    project, entry = api.find_device(
        args.target, env=_process_env(), cwd=Path.cwd(), project_dir=args.project_dir
    )
    build_dir = project.root / BUILD_DIR / entry.parent.name
    if not _is_sign_target(build_dir):
        raise BuildError(
            f"{entry.parent.name} has no build to sign.",
            hint=(
                f"expected {build_dir} to hold a {manifest_module.MANIFEST_FILE} or "
                f"{imgtool.BUILD_REPORT_FILE} — build first:\n"
                f"    mcuhome device build {entry.parent.name} --no-sign "
                "--public-key <file>\n"
                "or pass the path of a delivered build directory."
            ),
        )
    return build_dir, project


def _cmd_sign(args: argparse.Namespace, output: Output) -> int:
    target, project = _resolve_sign_target(args)
    # Signing replaces the application image in a build directory, which
    # is exactly what a build running there would be rewriting underneath
    # it — so it holds the directory too, and says what is in the way
    # when it cannot.
    directory = target if target.is_dir() else target.parent
    with api.build_lock(directory, device=directory.name, operation="sign"):
        return _sign_holding_the_directory(args, target, project, output)


def _sign_holding_the_directory(
    args: argparse.Namespace, target: Path, project: api.Project | None, output: Output
) -> int:
    # One verb, two report shapes, chosen by which file the directory
    # holds. A local-dev build dir carries build-manifest.json (the manifest
    # path below); a container build dir carries the leaner §7.2.1
    # build-report.json (the local backend's delivery), which has no
    # manifest to fold a signature back into and no OTA parameters to wrap.
    if _target_is_build_report(target):
        return _cmd_sign_report(args, target, project)
    plan, data, ota_image = _apply_manifest_signature(
        target, key=args.signing_key, env=_process_env(), project=project
    )
    print(
        f"{output.style('✓', output_module.GREEN, output_module.BOLD)} Signed the application "
        f"image of {output.path(plan.out_dir)} with {output.path(plan.key)}:"
    )
    for path in plan.outputs:
        print(f"  {output.path(path)}")
    if ota_image is not None:
        print()
        print(_ota_note(ota_image, output=output))
    print()
    print(
        output.muted(
            f"imgtool sign --version {plan.parameters.version} "
            f"--header-size {plan.parameters.header_size} "
            f"--slot-size {plan.parameters.slot_size} --align {plan.parameters.align}\n"
            "  — the parameters the build manifest states, which are the ones the build "
            "would have\n    used itself."
        )
    )
    if data.get("merged") is None:
        print()
        print(
            "There is no combined hex for a full-chip flash: a --no-sign build does "
            "not write\none, because sysbuild would fill it with the unsigned "
            "application. Install the\nbootloader and the signed application above "
            "separately, or build with signing on."
        )
    return 0


def _target_is_build_report(target: Path) -> bool:
    """Whether *target* is a §7.2.1 build-report directory, not a manifest one.

    A build-manifest.json — the ``local-dev`` shape — wins when both are
    present, so a directory that ever carried a manifest keeps its richer
    path (signatures folded back, OTA written). A directory holding only
    build-report.json is the container backend's delivery, and the report
    file named directly is unambiguous.
    """
    if target.is_file():
        return target.name == imgtool.BUILD_REPORT_FILE
    if (target / manifest_module.MANIFEST_FILE).is_file():
        return False
    return (target / imgtool.BUILD_REPORT_FILE).is_file()


def _cmd_sign_report(args: argparse.Namespace, target: Path, project: api.Project | None) -> int:
    """Sign the firmware a container build delivered, from its §7.2.1 report.

    The detached-signing tail of the default build path: the report carries
    the imgtool parameters and the unsigned ``firmware.*`` sit beside it, so
    a machine that has only the delivery and the private key produces the
    flashable images. There is no manifest to record the signature in and
    no OTA block to wrap — those belong to ``mcuhome build``, which does
    both in one step, or to the machine that holds the device model.
    """
    plan = imgtool.sign_report(target, key=args.signing_key, env=_process_env(), project=project)
    print(f"Signed the application image of {plan.out_dir} with {plan.key}:")
    for path in plan.outputs:
        print(f"  {path}")
    print()
    print(
        f"imgtool sign --version {plan.parameters.version} "
        f"--header-size {plan.parameters.header_size} "
        f"--slot-size {plan.parameters.slot_size} --align {plan.parameters.align}\n"
        "  — the parameters the build report states, which are the ones the build "
        "container linked\n    the image for."
    )
    return 0


def _sign_ota(data: dict, *, out_dir: Path, outputs: list[Path]) -> ota.OtaImage | None:
    """The .ota file for a build that was signed detached, if it can have one.

    Everything needed comes from the manifest: the OTA block is present
    exactly when the device can be updated over the air, and it carries the
    version and the identifiers the header needs. The device name comes
    from the manifest too, so the file lands under the same name an inline
    build would have given it.
    """
    block = data.get("ota")
    if not isinstance(block, dict):
        return None
    signed = next((path for path in outputs if path.suffix == ".bin"), None)
    if signed is None:  # pragma: no cover - the builder writes both forms
        return None
    parameters = manifest_module.OtaEntry.from_dict(block)
    device = str((data.get("device") or {}).get("name") or "device")
    return otafile.write_ota_image(
        payload=signed,
        output=out_dir / otafile.ota_file_name(device, parameters.version),
        vendor_id=parameters.vendor_id,
        product_id=parameters.product_id,
        version=parameters.version,
    )


def _cmd_public_key(args: argparse.Namespace, output: Output) -> int:
    """The public half, on stdout — the document channel is the file API.

    The old ``-o PATH`` spelling retired with the vocabulary step:
    ``-o`` selects the output *format* everywhere (cli ADR 0004), and a
    file is a shell redirect — ``mcuhome public-key > signing.pub``.
    """
    del output
    key = signing.signing_key(
        args.signing_key, env=_process_env(), project=_optional_project(args), create=False
    )
    print(signing.public_key_pem(key.pem), end="")
    return phases.EXIT_OK


#: What ``mcuhome schema`` can emit, and what produces it.
SCHEMA_EXPORTS = {
    "config": configschema.config_json_schema,
    "registry": export.registry_data,
}


def _cmd_schema(args: argparse.Namespace, output: Output) -> int:
    del output
    print(export.to_json(SCHEMA_EXPORTS[args.what]()), end="")
    return phases.EXIT_OK


def _cmd_clean(args: argparse.Namespace, output: Output) -> int:
    """``mcuhome clean`` — an honest stub (cli ADR 0003).

    Deleting a build directory is the third operation that must hold it
    (``operation="clean"``): removing files a running build is writing
    leaves that build failing in ways that name no cause.
    """
    del args, output
    raise BuildError(
        "mcuhome clean is not implemented yet.",
        hint=(
            f"build output is self-contained: delete the {BUILD_DIR}/ directory, or "
            "the one --build-dir pointed at, and nothing else is affected"
        ),
    )


def _cmd_flash(args: argparse.Namespace, output: Output) -> int:
    """``device flash`` — an honest stub (cli ADR 0003).

    ``--flash-mode recovery`` will be our own MCUboot serial recovery
    over USB CDC — no vendor tools, the bootloader presents itself as a
    plain serial port and accepts DFU. That waits on platform work
    (phase 3: CDC-ACM recovery in our bootloader), so the command
    refuses in words rather than being missing.

    When it stops being a stub it takes the build directory for the
    duration, like every command that touches one::

        with api.build_lock(out_dir, device=name, operation="flash"):

    A build rewriting ``firmware.signed.hex`` while this reads it would
    put half of one image and half of another on the device, and neither
    side could notice.
    """
    del output
    raise BuildError(
        f"mcuhome device flash is not implemented yet ({args.device} was not touched).",
        hint=(
            "planned: --flash-mode recovery will flash over our MCUboot's USB serial "
            "recovery, with no vendor tools. Until then, flash the built images with "
            "your board's own tooling — the build summary names every file and its "
            "offset."
        ),
    )


def _cmd_first_time_setup(args: argparse.Namespace, output: Output) -> int:
    """``device first-time-setup`` — an honest stub (cli ADR 0003).

    Takes the device's build directory when it becomes real, for the
    reason ``_cmd_flash`` states: it writes what a build may be
    rewriting.
    """
    del output
    raise BuildError(
        f"mcuhome device first-time-setup is not implemented yet ({args.device} was not touched).",
        hint=(
            "planned: one-time board provisioning — build and "
            "flash our MCUboot bootloader with the vendor's own tooling, the one "
            "deliberate exception to 'nothing toolchain-shaped on the host' "
            "(cli ADR 0002). Which tools per vendor is analyzed later."
        ),
    )


# --------------------------------------------------------------------------
# config (ADR 0022; cli ADR 0003)
# --------------------------------------------------------------------------


def _config_value_text(value: object) -> str:
    """One option's value as the human table shows it."""
    if value is None:
        return "(unset)"
    if isinstance(value, list):
        if not value:
            return "(none)"
        if isinstance(value[0], dict):
            # Builders carry their defining layer (merge-by-name makes
            # origin a per-builder fact, ADR 0023).
            return ", ".join(
                f"{entry['name']} ({entry['type']}, {entry['layer']})" for entry in value
            )
        return os.pathsep.join(str(item) for item in value)
    return str(value)


def _cmd_config_print(args: argparse.Namespace, output: Output) -> int:
    """Every effective option, with the layer it came from (ADR 0022 §3)."""
    project = _optional_project(args)
    settings = api.resolve_settings(project=project, env=_process_env(), args={})
    data = settings.print_data()
    output.start("config-print")
    if output.machine:
        output.result({"ok": True, "config": data})
        return phases.EXIT_OK
    rows: list[list[str | output_module.Cell]] = [["option", "value", "origin"]]
    for name, entry in data.items():
        origin = str(entry["origin"])
        if entry["source"] and origin != "default":
            origin = f"{origin} ({entry['source']})"
        rows.append(
            [
                name,
                _config_value_text(entry["value"]),
                output_module.Cell(origin, (output_module.DIM,)),
            ]
        )
    print(output_module.format_table(rows, header=True, output=output))
    return phases.EXIT_OK


def _config_setting_or_refuse(settings: api.Settings, name: str) -> api.Setting:
    if name not in settings:
        known = ", ".join(opt.name for opt in api.OPTIONS if not opt.bootstrap)
        raise ConfigError(
            f"There is no option called {name!r}.",
            hint=f"the declared options are: {known}",
        )
    return settings.setting(name)


def _cmd_config_get(args: argparse.Namespace, output: Output) -> int:
    project = _optional_project(args)
    settings = api.resolve_settings(project=project, env=_process_env(), args={})
    setting = _config_setting_or_refuse(settings, args.name)
    output.start("config-get", name=args.name)
    if output.machine:
        entry = settings.print_data()[args.name]
        output.result({"ok": True, "name": args.name, **entry})
        return phases.EXIT_OK
    value = setting.value
    if isinstance(value, tuple) and not isinstance(value, str):
        for item in value:
            print(_config_value_text(item) if isinstance(item, dict) else str(item))
    elif value is not None:
        print(value)
    return phases.EXIT_OK


def _cmd_config_set(args: argparse.Namespace, output: Output) -> int:
    env = _process_env()
    file = api.scope_config_file(args.scope, project=_optional_project(args), env=env)
    written = api.set_config_value(file, args.name, args.value, env=env)
    output.start("config-set", name=args.name, scope=args.scope)
    if output.machine:
        document = {"ok": True, "name": args.name, "value": written, "scope": args.scope}
        output.result({**document, "file": str(file)})
        return phases.EXIT_OK
    shown = os.pathsep.join(written) if isinstance(written, list) else written
    print(_("Set {name} = {value} in {file}.").format(name=args.name, value=shown, file=file))
    return phases.EXIT_OK


def _cmd_config_unset(args: argparse.Namespace, output: Output) -> int:
    env = _process_env()
    file = api.scope_config_file(args.scope, project=_optional_project(args), env=env)
    removed = api.unset_config_value(file, args.name)
    output.start("config-unset", name=args.name, scope=args.scope)
    if output.machine:
        document = {"ok": True, "name": args.name, "removed": removed, "scope": args.scope}
        output.result({**document, "file": str(file)})
        return phases.EXIT_OK
    if removed:
        print(_("Removed {name} from {file}.").format(name=args.name, file=file))
    else:
        print(_("{name} was not set in {file}; nothing changed.").format(name=args.name, file=file))
    return phases.EXIT_OK


# --------------------------------------------------------------------------
# device list / doctor (cli ADR 0003)
# --------------------------------------------------------------------------


def _build_state(build_dir: Path) -> tuple[bool, bool]:
    """Whether this device has a build, and whether that build is signed.

    Both report shapes count (E55): a ``build-manifest.json`` records its
    own signing state; a container delivery is signed exactly when the
    signed images sit beside its ``build-report.json`` — the same names
    :data:`imgtool.REPORT_FIRMWARE` gives the signer, so the two cannot
    disagree about what a signed image is called.
    """
    manifest_file = build_dir / manifest_module.MANIFEST_FILE
    report_file = build_dir / imgtool.BUILD_REPORT_FILE
    if not manifest_file.is_file() and not report_file.is_file():
        return False, False
    if any((build_dir / signed_name).is_file() for _source, signed_name in imgtool.REPORT_FIRMWARE):
        return True, True
    if manifest_file.is_file():
        try:
            data = manifest_module.read_manifest(manifest_file)
        except MCUHomeError:
            return True, False
        block = data.get("signing")
        return True, bool(isinstance(block, dict) and block.get("signed"))
    return True, False


def _board_of(entry: Path) -> str | None:
    """The board straight out of the YAML, for a device that does not validate.

    A configuration can be one drawn credential away from valid and still
    name its board perfectly well; the listing should say what it can.
    Anything unreadable here simply answers None — the status column
    already says the device has problems.
    """
    try:
        data = load_yaml_file(entry)
    except MCUHomeError:
        return None
    device = data.get("device") if isinstance(data, dict) else None
    board = device.get("board") if isinstance(device, dict) else None
    return board if isinstance(board, str) else None


def _cmd_device_list(args: argparse.Namespace, output: Output) -> int:
    """The project's devices, each with its validation and build state."""
    project = api.resolve_project(args.project_dir, env=_process_env(), cwd=Path.cwd())
    args.json_root = project.root
    output.start("list", project=str(project.root))
    devices: list[dict[str, object]] = []
    for name in project.device_names():
        entry = project.device_entry(name)
        result = api.validate_device(entry, project=project, on_warning=output.warn)
        built, signed = _build_state(project.root / BUILD_DIR / name)
        board = result.model.device.board if result.model is not None else _board_of(entry)
        devices.append(
            {
                "name": name,
                "board": board,
                "ok": result.ok,
                "problems": len(result.errors),
                "built": built,
                "signed": signed,
            }
        )
    if output.machine:
        output.result({"ok": True, "project": str(project.root), "devices": devices})
        return phases.EXIT_OK
    if not devices:
        print(_("No devices yet."))
        print(_("  mcuhome device new <name> --board <target>    scaffold the first one"))
        return phases.EXIT_OK
    rows: list[list[str | output_module.Cell]] = [["device", "board", "status", "build"]]
    for device in devices:
        problems = int(device["problems"])  # type: ignore[arg-type]
        status = (
            _("ok")
            if device["ok"]
            else (_("1 problem") if problems == 1 else _("{n} problems").format(n=problems))
        )
        build = _("signed") if device["signed"] else (_("unsigned") if device["built"] else "-")
        rows.append(
            [
                str(device["name"]),
                str(device["board"] or "?"),
                output_module.Cell(
                    status, (output_module.GREEN,) if device["ok"] else (output_module.RED,)
                ),
                output_module.Cell(build, () if device["signed"] else (output_module.DIM,)),
            ]
        )
    print(output_module.format_table(rows, header=True, output=output))
    return phases.EXIT_OK


def _cmd_device_boards(args: argparse.Namespace, output: Output) -> int:
    """The boards MCUHome can build for, from the registry (PO 2026-08-15).

    The registry is the authority — a board entry carries MCUHome's own
    bring-up knowledge (partitions, entropy, radio), so this list is
    deliberately not "what Zephyr supports".
    """
    del args
    supported = [
        {"name": board.name, "transports": sorted(board.transports)}
        for _name, board in sorted(registry.BOARDS.items())
    ]
    planned = [
        {"name": name, "status": status} for name, status in sorted(registry.PLANNED_BOARDS.items())
    ]
    if output.machine:
        output.result({"ok": True, "boards": supported, "planned": planned})
        return phases.EXIT_OK
    print(output.heading(_("Boards MCUHome builds for:")))
    rows = [[entry["name"], ", ".join(entry["transports"])] for entry in supported]
    print(output_module.format_table(rows, indent="  ", output=output))
    print()
    print(output.heading(_("Planned, not usable yet:")))
    planned_rows: list[list[str | output_module.Cell]] = [
        [entry["name"], output_module.Cell(entry["status"], (output_module.DIM,))]
        for entry in planned
    ]
    print(output_module.format_table(planned_rows, indent="  ", output=output))
    print()
    print(_("Details: {url}").format(url=_docs_url("device-supported-boards")))
    return phases.EXIT_OK


#: How ``doctor`` paints each status word.
_DOCTOR_STYLES = {
    "ok": output_module.GREEN,
    "warn": output_module.YELLOW,
    "fail": output_module.RED,
}


def _cmd_doctor(args: argparse.Namespace, output: Output) -> int:
    """Environment diagnosis — the "why does nothing work" command (cli ADR 0003).

    Every check reports rather than raises, so one broken thing never
    hides the next: the stack's versions, the project, the resolved
    configuration, the builders, the container runtime and the secrets
    permissions each get their own verdict. Any ``fail`` makes the exit
    code 1; warnings alone leave it 0.
    """
    env = _process_env()
    checks: list[dict[str, str]] = []

    def record(check: str, status: str, detail: str) -> None:
        checks.append({"check": check, "status": status, "detail": detail})

    output.start("doctor")
    record("stack", "ok", "; ".join(_stack_version().splitlines()))

    project: api.Project | None = None
    try:
        project = _optional_project(args)
    except MCUHomeError as error:
        record("project", "fail", error.message)
    else:
        if project is None:
            record("project", "warn", _("not inside a project — mcuhome project init creates one"))
        else:
            file = project.file
            version = "" if file is None else f" (project version {file.version}"
            identity = "" if file is None or not file.short_id else f", id {file.short_id}"
            record("project", "ok", f"{project.root}{version}{identity}{')' if version else ''}")

    settings: api.Settings | None = None
    try:
        settings = api.resolve_settings(project=project, env=env, args={})
    except MCUHomeError as error:
        record("configuration", "fail", error.message)
    else:
        configured = sum(
            1 for entry in settings.print_data().values() if entry["origin"] != "default"
        )
        record(
            "configuration",
            "ok",
            _("resolves; {n} option(s) set beyond the defaults").format(n=configured),
        )

    if settings is not None:
        complaints: list[str] = []
        try:
            api.resolve_builder(
                settings, name=None, project=project, env=env, on_warning=complaints.append
            )
        except MCUHomeError as error:
            record("builders", "fail", error.message)
        else:
            configured_builders = settings.value("builders")
            if not configured_builders:
                detail = _("none configured — a plain build uses the local build container")
            else:
                listed = ", ".join(
                    f"{item.name} ({item.type}, {item.layer})" for item in configured_builders
                )
                default = settings.value("default_builder")
                detail = f"{listed}; default: {default or _('built-in local')}"
            if complaints:
                detail += "\n" + "\n".join(complaints)
            record("builders", "warn" if complaints else "ok", detail)

    docker = container.docker_program(env)
    reference = container.image_reference(env)
    try:
        container.preflight(docker, reference, env=env)
    except MCUHomeError as error:
        record("container", "fail", error.message)
    else:
        record(
            "container",
            "ok",
            _("{docker} answers and the image {image} is present").format(
                docker=docker, image=reference
            ),
        )

    if project is not None and project.secrets_dir.is_dir():
        complaints = []
        for file in sorted(project.secrets_dir.rglob("*")):
            if file.is_file():
                check_secret_file(file, key_material=False, on_warning=complaints.append)
        if complaints:
            record("secrets", "warn", "\n".join(complaints))
        else:
            tight = _("{dir} permissions are tight").format(dir=project.secrets_dir)
            record("secrets", "ok", tight)

    failed = any(entry["status"] == "fail" for entry in checks)
    if output.machine:
        output.result({"ok": not failed, "checks": checks})
        return phases.EXIT_FAILURE if failed else phases.EXIT_OK
    for entry in checks:
        status = output.style(
            entry["status"].ljust(5), _DOCTOR_STYLES[entry["status"]], output_module.BOLD
        )
        first, *rest = entry["detail"].splitlines() or [""]
        print(f"{status} {entry['check'].ljust(14)} {first}")
        for line in rest:
            print(" " * 21 + line)
    return phases.EXIT_FAILURE if failed else phases.EXIT_OK


def _stack_version() -> str:
    """``mcuhome --version``: the whole stack, one line per part (ADR 0002 §5)."""
    try:
        compiler = importlib.metadata.version("mcuhome-compiler")
    except importlib.metadata.PackageNotFoundError:
        compiler = "not installed"
    return "\n".join(
        [
            f"mcuhome {cli_version}",
            f"mcuhome-workbench {api.VERSION}",
            f"mcuhome-compiler {compiler}",
            f"mcuhome-model {model_version}",
        ]
    )


class _StackVersion(argparse.Action):
    """``--version``, printed verbatim: argparse's own version action runs
    the text through the help formatter, which re-flows the one-line-per-
    part shape ADR 0002 §5 asks for into a paragraph."""

    def __init__(self, option_strings: list[str], dest: str, **kwargs: object) -> None:
        del kwargs
        super().__init__(
            option_strings, dest, nargs=0, help="show the version of every part of the stack"
        )

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        del namespace, values, option_string
        print(_stack_version())
        parser.exit()


def _show_help(parser: argparse.ArgumentParser):  # noqa: ANN202 - argparse callback
    """A noun without a verb prints the noun's own help and succeeds."""

    def show(args: argparse.Namespace, output: Output) -> int:
        del args, output
        parser.print_help()
        return phases.EXIT_OK

    return show


def _docs_url(page: str) -> str:
    """A stable documentation link (PO 2026-08-15).

    ``t.mcuhome.org`` is the project's target host: one path per page a
    shipped binary links to, under the scheme
    ``/<source-repo>/<target-area>/<target-detail>/<version>`` — the
    linking tool is part of a link's identity (the dashboard's
    getting-started page is not this CLI's), and the version is this
    CLI's major.minor. Paths are kept alive for the binary's lifetime
    (github.com/mcu-home/t.mcuhome.org).
    """
    return f"https://t.mcuhome.org/cli/docs/{page}/{'.'.join(cli_version.split('.')[:2])}"


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Usage lines that identify, not enumerate (PO 2026-08-15).

    The usage line carries what an invocation *must* say — positionals
    and required flags — and folds everything optional into
    ``[options]``; the grouped option list below has the detail. The
    raw-description base keeps the line breaks of the top-level
    workflow epilog.
    """

    def _format_usage(self, usage, actions, groups, prefix):  # noqa: ANN001, ANN202
        if usage is None:
            shown = [action for action in actions if not action.option_strings or action.required]
            if len(shown) < len(actions):
                text = super()._format_usage(None, shown, groups, prefix)
                return text.rstrip("\n") + " [options]\n\n"
        return super()._format_usage(usage, actions, groups, prefix)


class _Parser(argparse.ArgumentParser):
    """argparse, with the project's help contract wired in.

    Every parser in the tree renders through :class:`_HelpFormatter`
    and registers ``-h`` itself (leaf commands list it under *general
    options*), and a usage error points at ``--help`` instead of
    re-listing every option.
    """

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        kwargs.setdefault("formatter_class", _HelpFormatter)
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str):  # noqa: ANN202 - argparse contract
        self.print_usage(sys.stderr)
        hint = _("Run {prog} --help for the full option list.").format(prog=self.prog)
        self.exit(phases.EXIT_USAGE, f"{self.prog}: error: {message}\n{hint}\n")


def _help_parser(parser: argparse.ArgumentParser, tokens: list[str]) -> argparse.ArgumentParser:
    """The parser whose help ``-h`` anywhere in *tokens* shows.

    ``-h``/``--help`` wins wherever it stands (PO 2026-08-15) — even
    where argparse would read it as a flag's missing value, as in
    ``mcuhome device new --board -h``. The walk descends the command
    tree for as long as tokens name subcommands and ignores everything
    else.
    """
    current = parser
    for token in tokens:
        actions = current._actions
        sub = next(
            (action for action in actions if isinstance(action, argparse._SubParsersAction)),
            None,
        )
        if sub is None:
            break
        if token in sub.choices:
            current = sub.choices[token]
    return current


#: Refusals a page can say more about than a hint ever should. The
#: project-version family is the whole list today: "restore your backup"
#: needs paragraphs, and a hint is one sentence and a command.
_TROUBLESHOOTING = (
    (api.UpgradeInterrupted, "project-upgrade"),
    (api.UpgradeInProgress, "project-upgrade"),
    (api.MigrationFailed, "project-upgrade"),
    (api.ProjectUpgradeRequired, "project-upgrade"),
    (api.ProjectVersionUnsupported, "project-upgrade"),
    (api.ProjectFileError, "project-upgrade"),
)


def _troubleshooting_page(error: MCUHomeError) -> str | None:
    return next((page for kind, page in _TROUBLESHOOTING if isinstance(error, kind)), None)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="mcuhome",
        description="Build Zephyr firmware from an MCUHome YAML device configuration.",
        epilog=(
            "the workflow:\n"
            "  mcuhome project init         create a project directory\n"
            "  mcuhome device new NAME      describe a device (device boards lists the targets)\n"
            "  mcuhome device build NAME    compile its firmware\n"
            "  mcuhome device flash NAME    put it on the board\n"
            "\n"
            f"getting started: {_docs_url('getting-started')}"
        ),
    )
    parser.add_argument("-h", "--help", action="help", help="show this help message and exit")
    parser.add_argument("--version", action=_StackVersion)
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print the resolved device model",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_bare_help(noun_parser: argparse.ArgumentParser) -> None:
        noun_parser.add_argument(
            "-h", "--help", action="help", help="show this help message and exit"
        )

    def finish_options(subparser: argparse.ArgumentParser, *, output: bool = False) -> None:
        """The options every command shares, apart from the command's own.

        Called *after* a command's own arguments, so the help shows the
        command-specific options first and the shared ones as their own
        group below (PO 2026-08-15).
        """
        general = subparser.add_argument_group("general options")
        general.add_argument("-h", "--help", action="help", help="show this help message and exit")
        if output:
            general.add_argument(
                "-o",
                "--output",
                dest="output_mode",
                choices=output_module.MODES,
                default=output_module.HUMAN,
                metavar="FORMAT",
                help=(
                    "output format: human (the default), json — one "
                    "machine-readable document on stdout after the run (failure form "
                    '{"ok": false, "errors": [...]}) — or json-stream, NDJSON with the verbs '
                    "start/progress/error/result as the run progresses. Exit codes do not "
                    "change, logs go to stderr, and both machine forms force "
                    "--no-interactive"
                ),
            )
        general.add_argument(
            "--project-dir",
            type=Path,
            default=None,
            metavar="PATH",
            help=(
                f"the MCUHome project directory (default: {api.PROJECT_DIR_VAR}, or the "
                f"nearest directory upward carrying {api.MARKER_FILE}); it must be one — "
                "mcuhome project init creates it"
            ),
        )
        # Also accepted after the subcommand, where people reach for it.
        # SUPPRESS so that leaving it out here does not overwrite the
        # value given before the subcommand.
        general.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="also print the resolved device model",
        )
        general.add_argument(
            "--color",
            choices=("auto", "always", "never"),
            default="auto",
            help="color in human output (auto: only on a terminal; NO_COLOR is respected)",
        )
        general.add_argument(
            "--interactive",
            dest="interactive",
            action="store_true",
            default=None,
            help="ask questions up front even when not attached to a terminal",
        )
        general.add_argument(
            "--no-interactive",
            dest="interactive",
            action="store_false",
            help="never ask; a missing required input is then an exit-2 refusal",
        )

    # ---- project-scoped, top-level (cli ADR 0003 §1) ---------------------

    project_parser = subparsers.add_parser(
        "project",
        help="create, inspect and upgrade a project directory",
    )
    project_sub = project_parser.add_subparsers(dest="project_command")
    add_bare_help(project_parser)
    project_parser.set_defaults(func=_show_help(project_parser))

    init_project_parser = project_sub.add_parser(
        "init",
        help="create an MCUHome project directory (marker, mcuhome.yaml, devices/, secrets/)",
    )
    init_project_parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="where to create the project, created if missing (default: here)",
    )
    init_project_parser.add_argument(
        "--force",
        action="store_true",
        help="proceed in a non-empty directory (existing files may be overwritten)",
    )
    finish_options(init_project_parser, output=True)
    init_project_parser.set_defaults(func=_cmd_project_init)

    info_parser = project_sub.add_parser(
        "info",
        help="what this project is: where, which id, which version",
    )
    info_parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="the project (default: the one this directory is in)",
    )
    finish_options(info_parser, output=True)
    info_parser.set_defaults(func=_cmd_project_info)

    upgrade_parser = project_sub.add_parser(
        "upgrade",
        help="migrate a project to the layout this MCUHome speaks",
    )
    upgrade_parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="the project to upgrade (default: the one this directory is in)",
    )
    upgrade_parser.add_argument(
        "--confirm-upgrade",
        metavar="ID",
        default=None,
        help=(
            "confirm without being asked, by naming the project: its id, or the "
            "short form mcuhome project info prints. Required without a terminal"
        ),
    )
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what the upgrade would change and stop; nothing is touched",
    )
    finish_options(upgrade_parser, output=True)
    upgrade_parser.set_defaults(func=_cmd_project_upgrade, validate_input=_validate_upgrade)

    config_parser = subparsers.add_parser(
        "config",
        help="read and write MCUHome configuration",
    )
    config_sub = config_parser.add_subparsers(dest="config_command")
    add_bare_help(config_parser)
    config_parser.set_defaults(func=_show_help(config_parser))

    def add_scope_flags(subparser: argparse.ArgumentParser) -> None:
        scope = subparser.add_mutually_exclusive_group()
        scope.add_argument(
            "--project",
            dest="scope",
            action="store_const",
            const="project",
            help="edit the project's mcuhome.yaml (the default)",
        )
        scope.add_argument(
            "--user",
            dest="scope",
            action="store_const",
            const="user",
            help="edit the user configuration (configuration.yaml)",
        )
        scope.add_argument(
            "--system",
            dest="scope",
            action="store_const",
            const="system",
            help="edit the system configuration (usually needs administrator rights)",
        )
        subparser.set_defaults(scope="project")

    config_print_parser = config_sub.add_parser(
        "print", help="every effective option, with the layer each value came from"
    )
    finish_options(config_print_parser, output=True)
    config_print_parser.set_defaults(func=_cmd_config_print)

    config_get_parser = config_sub.add_parser("get", help="one option's effective value")
    config_get_parser.add_argument(
        "name", help="the option, spelled as its configuration key (e.g. jobs)"
    )
    finish_options(config_get_parser, output=True)
    config_get_parser.set_defaults(func=_cmd_config_get)

    config_set_parser = config_sub.add_parser(
        "set", help="set an option in one scope's configuration file"
    )
    config_set_parser.add_argument(
        "name", help="the option, spelled as its configuration key (e.g. default_builder)"
    )
    config_set_parser.add_argument(
        "value",
        help=(
            "the value to write; list-valued options (sdk_sources) take several "
            f"entries separated by {os.pathsep!r}, like their environment variable"
        ),
    )
    add_scope_flags(config_set_parser)
    finish_options(config_set_parser, output=True)
    config_set_parser.set_defaults(func=_cmd_config_set)

    config_unset_parser = config_sub.add_parser(
        "unset", help="remove an option from one scope's configuration file"
    )
    config_unset_parser.add_argument("name", help="the option to remove")
    add_scope_flags(config_unset_parser)
    finish_options(config_unset_parser, output=True)
    config_unset_parser.set_defaults(func=_cmd_config_unset)

    # ---- the device noun (cli ADR 0003 §1/§2) ----------------------------

    device_parser = subparsers.add_parser(
        "device", help="device-scoped commands: new, validate, build, sign-firmware, ..."
    )
    device_sub = device_parser.add_subparsers(dest="device_command")
    add_bare_help(device_parser)
    device_parser.set_defaults(func=_show_help(device_parser))

    new_parser = device_sub.add_parser(
        "new", help="create a new device folder with a starter configuration"
    )
    new_parser.add_argument("device", help="device name; it becomes the folder and the hostname")
    new_parser.add_argument(
        "--board",
        required=True,
        metavar="TARGET",
        help=(
            "Zephyr board target this device runs on, verbatim — "
            "mcuhome device boards lists what MCUHome can build for"
        ),
    )
    new_parser.add_argument(
        "--name",
        default=None,
        metavar="NAME",
        help=(
            "human-readable name, destined for the device's Matter identity "
            "(default: the device name, title-cased)"
        ),
    )
    finish_options(new_parser)
    new_parser.set_defaults(func=_cmd_new)

    validate_parser = device_sub.add_parser(
        "validate", help="check a device configuration and print what it resolves to"
    )
    validate_parser.add_argument(
        "device", help="device folder name, or the path of a device folder or YAML file"
    )
    validate_parser.add_argument(
        "--show-sensitive",
        action="store_true",
        help=(
            "print the pairing codes in the clear (they are masked by default; "
            "mcuhome device matter-pairing shows them too)"
        ),
    )
    finish_options(validate_parser, output=True)
    validate_parser.set_defaults(func=_cmd_validate)

    build_parser_ = device_sub.add_parser("build", help="build firmware for a device")
    # Two ways to say what to build, and exactly one of them per run. The
    # second exists for the build server (dashboard ADR 0007 decision 4):
    # the canonical model is the wire format, so a machine that receives
    # one starts at stage 4 and never sees the configuration tree — or the
    # secrets file next to it.
    build_input = build_parser_.add_mutually_exclusive_group(required=True)
    build_input.add_argument("device", nargs="?", help="device folder name or path")
    build_input.add_argument(
        "--model",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "build a canonical device model (device-model.json) that has already "
            "been resolved elsewhere, skipping load/validate/resolve — no "
            "configuration tree and no secrets are read"
        ),
    )
    build_parser_.add_argument(
        "--build-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            f"where to generate the application (default: <project>/{BUILD_DIR}/<device>, "
            f"or ./{BUILD_DIR}/<device> with --model)"
        ),
    )
    build_parser_.add_argument(
        "--generate-only",
        action="store_true",
        help="stop after writing the generated application, and succeed",
    )
    build_parser_.add_argument(
        "-S",
        "--snippet",
        action="append",
        metavar="NAME",
        help=(
            "Zephyr snippet to apply on top of the ones the configuration needs "
            "(repeatable); the debug-rtt log transport is always already among them"
        ),
    )
    # ADR 0023: where a build runs. Three rungs, most explicit wins —
    # fully manual (--build-mode plus its mode flags), a named builder,
    # the configured default. A builder is configuration about a method,
    # never a fourth method.
    build_parser_.add_argument(
        "--builder",
        metavar="NAME",
        default=None,
        help=(
            "build through this configured builder (`builders:` in any "
            "configuration layer). Default: the configured default_builder "
            f"({option_env_var('default_builder')} sets it too), else the local "
            "build container"
        ),
    )
    build_parser_.add_argument(
        "--build-mode",
        metavar="MODE",
        choices=api.METHODS,
        default=None,
        help=(
            "build fully manually in this mode, bypassing the builders "
            "configuration: "
            + ", ".join(api.METHODS)
            + " — local compiles in a build container on this machine, local-dev "
            "in your own west workspace, remote on a build server; each mode has "
            "its own flags below"
        ),
    )
    build_parser_.add_argument(
        "--build-server",
        metavar="ADDRESS",
        default=None,
        help=(
            "--build-mode remote: the build server's address, IP or "
            "hostname[:port] (a configured builder carries its own)"
        ),
    )
    build_parser_.add_argument(
        "--build-token",
        metavar="TOKEN",
        default=None,
        help=(
            "--build-mode remote: bearer token for that server (a configured "
            "builder reads secrets/build-server/<name>.yaml instead)"
        ),
    )
    build_parser_.add_argument(
        "--workspace",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "--build-mode local-dev: the west workspace to compile in (default: "
            "discovered from the install location and the working directory)"
        ),
    )
    build_parser_.add_argument(
        "--container-image",
        metavar="REF",
        default=None,
        help=(
            f"--build-mode local: container image to compile in (default: {container.IMAGE}; "
            f"the {container.IMAGE_VAR} environment variable sets it too)"
        ),
    )
    build_parser_.add_argument(
        "--sdk-sources",
        action="append",
        metavar="DIR",
        help=(
            "directory holding the hash-pinned MCUHome SDK package this build is "
            "pinned to (repeatable; searched in order). Needed by the local and "
            "remote modes alike — both create a build context, and the pin is "
            "part of its identity. An option of the configuration registry: "
            f"{option_env_var('sdk_sources')} is a PATH-style list of "
            "them, the configuration files take a `sdk_sources:` list, and "
            "local-dev needs none"
        ),
    )
    build_parser_.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "ECDSA P-256 private key to sign the firmware with, as a plain PEM file "
            f"(the {signing.KEY_VAR} environment variable sets it too). Default: the "
            f"project's secrets/firmware/{signing.PRIVATE_KEY_FILE}, generated on "
            "first use and referenced from mcuboot.yaml"
        ),
    )
    build_parser_.add_argument(
        "--no-sign",
        action="store_true",
        help=(
            "build the application UNSIGNED and record the signing parameters in "
            "the build manifest, so that the private key never has to be on the "
            "machine that compiles; needs --public-key, and "
            "mcuhome device sign-firmware applies the signature afterwards"
        ),
    )
    build_parser_.add_argument(
        "--public-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "public half of the signing key, compiled into the bootloader "
            "(required with --no-sign; write one with mcuhome public-key)"
        ),
    )
    build_parser_.add_argument(
        "--jobs",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "parallel build jobs (default: auto-detected from CPU count and "
            "available RAM). An option of the configuration registry: "
            f"{option_env_var('jobs')} and the configuration files set it too, "
            "--jobs beats them all"
        ),
    )
    finish_options(build_parser_, output=True)
    build_parser_.set_defaults(func=_cmd_build, validate_input=_validate_build)

    sign_parser = device_sub.add_parser(
        "sign-firmware", help="sign the application image of a finished build"
    )
    sign_parser.add_argument(
        "target",
        help=(
            "a device name (signs its last build), a build directory, or the "
            f"{manifest_module.MANIFEST_FILE}/{imgtool.BUILD_REPORT_FILE} inside one"
        ),
    )
    sign_parser.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "ECDSA P-256 private key to sign with, as a plain PEM file (the "
            f"{signing.KEY_VAR} environment variable sets it too; default: the "
            f"project's secrets/firmware/{signing.PRIVATE_KEY_FILE}). Never generated "
            "here: a build has to be signed with the key its device's bootloader "
            "already carries."
        ),
    )
    finish_options(sign_parser)
    sign_parser.set_defaults(func=_cmd_sign)

    flash_parser = device_sub.add_parser("flash", help="flash the last built firmware (stub)")
    flash_parser.add_argument("device", help="device folder name or path")
    flash_parser.add_argument(
        "--flash-mode",
        choices=("recovery", "ota"),
        default="recovery",
        help=(
            "recovery: our MCUboot serial recovery over USB CDC, no vendor tools "
            "(planned); ota: deliberately undefined for now"
        ),
    )
    finish_options(flash_parser)
    flash_parser.set_defaults(func=_cmd_flash)

    setup_parser = device_sub.add_parser(
        "first-time-setup",
        help="one-time board provisioning: our MCUboot via vendor tooling (stub)",
    )
    setup_parser.add_argument("device", help="device folder name or path")
    finish_options(setup_parser)
    setup_parser.set_defaults(func=_cmd_first_time_setup)

    pairing_parser = device_sub.add_parser(
        "matter-pairing",
        help="show this device's Matter pairing codes, or draw new commissioning credentials",
    )
    pairing_parser.add_argument("device", help="device folder name or path")
    pairing_parser.add_argument(
        "--new",
        action="store_true",
        help=(
            "draw fresh commissioning credentials: the values go to the device's own "
            "secrets/devices/<name>.yaml, the configuration gets !secret references"
        ),
    )
    pairing_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "with --new: replace credentials that are already there (every controller "
            "that knows the device has to commission it again)"
        ),
    )
    finish_options(pairing_parser)
    pairing_parser.set_defaults(func=_cmd_matter_pairing, validate_input=_validate_matter_pairing)

    list_parser = device_sub.add_parser("list", help="list the project's devices with their state")
    finish_options(list_parser, output=True)
    list_parser.set_defaults(func=_cmd_device_list)

    boards_parser = device_sub.add_parser(
        "boards", help="list the boards MCUHome can build for, and the planned ones"
    )
    finish_options(boards_parser, output=True)
    boards_parser.set_defaults(func=_cmd_device_boards)

    # ---- environment-scoped, top-level -----------------------------------

    schema_parser = subparsers.add_parser(
        "schema", help="print the configuration JSON Schema, or the registry, as JSON"
    )
    schema_parser.add_argument(
        "what",
        nargs="?",
        default="config",
        choices=sorted(SCHEMA_EXPORTS),
        help=(
            "config: a JSON Schema for main.yaml, for editor validation and "
            "autocomplete. registry: the boards, drivers, clusters and device "
            "types MCUHome knows, as data. The document goes to stdout — "
            "redirect it into a file"
        ),
    )
    finish_options(schema_parser)
    schema_parser.set_defaults(func=_cmd_schema)

    public_key_parser = subparsers.add_parser(
        "public-key",
        help=(
            "print the public half of the firmware signing key "
            "(redirect to write a file: mcuhome public-key > signing.pub)"
        ),
    )
    public_key_parser.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "which key to take the public half of (default: the project's "
            f"secrets/firmware/{signing.PRIVATE_KEY_FILE})"
        ),
    )
    finish_options(public_key_parser)
    public_key_parser.set_defaults(func=_cmd_public_key)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="environment diagnosis: project, configuration, builders, container, permissions",
    )
    finish_options(doctor_parser, output=True)
    doctor_parser.set_defaults(func=_cmd_doctor)

    clean_parser = subparsers.add_parser("clean", help="remove build output of a device (stub)")
    clean_parser.add_argument("device", nargs="?", help="device folder name or path")
    clean_parser.add_argument("--all", action="store_true", help="clean every device")
    finish_options(clean_parser)
    clean_parser.set_defaults(func=_cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    tokens = sys.argv[1:] if argv is None else argv
    # Help wins wherever it stands (PO 2026-08-15): scanned before the
    # parse, because argparse would otherwise read `-h` as a flag's
    # missing value (`--board -h`) and refuse instead of helping.
    # Everything after a `--` separator is literal and never help.
    limit = tokens.index("--") if "--" in tokens else len(tokens)
    if any(token in ("-h", "--help") for token in tokens[:limit]):
        named = [token for token in tokens[:limit] if token not in ("-h", "--help")]
        _help_parser(parser, named).print_help()
        return phases.EXIT_OK
    args = parser.parse_args(tokens)
    output = output_module.resolve(
        mode=getattr(args, "output_mode", output_module.HUMAN),
        color=getattr(args, "color", "auto"),
        interactive=getattr(args, "interactive", None),
    )
    if getattr(args, "func", None) is None:
        parser.print_help()
        return phases.EXIT_OK
    # The interact → validate → execute contract (cli ADR 0004 §3): no
    # command asks questions yet, so the interact phase is empty
    # everywhere; a command with argument-shape rules declares them as
    # its validate phase (set_defaults(validate_input=...)) and any gap
    # is exit 2 with the action never started.
    validate_input = getattr(args, "validate_input", None)
    try:
        return phases.run(
            output=output,
            validate=None if validate_input is None else (lambda: validate_input(args, output)),
            execute=lambda: int(args.func(args, output)),
        )
    except MCUHomeError as error:
        # Both streams end up in the same terminal, and a command that
        # printed progress before failing must not have its error appear
        # above the output it refers to. Only stdout is buffered when it is
        # a pipe, so flushing it here is what keeps the order right.
        sys.stdout.flush()
        # One renderer for every mode: stderr for a human, the failure
        # document (plus the stream's error verbs) for a machine. The
        # project root, when the command got as far as resolving one, is
        # what makes the file paths project-relative rather than this
        # machine's; the working directory is the CLI's to supply — it is
        # what makes "main.yaml, line 5" shorter than an absolute path.
        output.errors([error], root=getattr(args, "json_root", None), cwd=Path.cwd())
        page = _troubleshooting_page(error)
        if page is not None and not output.machine:
            # Only for a person: the machine document is the contract, and
            # a link is not part of it.
            output.log(_("More about this: {url}").format(url=_docs_url(page)))
        return phases.EXIT_FAILURE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
