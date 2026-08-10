# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The ``mcuhome`` command line (mcuhome's builder-pipeline.md §8).

This is the thin command shell of the MCUHome repo family: it parses
arguments, calls into the ``mcuhome`` builder library, and renders the
result for a human (or, with ``--json``, for a machine). All actual
building, validation and signing logic lives in the library — this
package adds no behavior of its own beyond the command surface.

::

    mcuhome new          <device>      # scaffold a device folder
    mcuhome validate     <device>      # stages 1-3, prints a summary
    mcuhome build        <device>      # stages 1-5
    mcuhome sign         <build-dir>   # apply the signature afterwards
    mcuhome init-pairing <device>      # draw commissioning credentials
    mcuhome public-key                 # the public half of the signing key
    mcuhome schema       [what]        # the schema and the registry, as JSON
    mcuhome clean        <device|--all>

``clean`` exists so the surface is stable and refuses cleanly rather than
being missing; everything else is implemented.

``build`` selects one of the three build methods (ADR 0020 decision 6),
never a code path: ``local`` compiles in a build container on this
machine and is the default (E54), ``local-dev`` compiles on the host
toolchain, and ``remote`` compiles on a build server. There is exactly
one spelling for each — ``--method <name>`` and nothing else (E62). The
choice is a ladder: ``--method``, then :data:`METHOD_VAR`, then the
default. Whichever ran,
what comes back is an **unsigned** image plus a build report, and one
host-side step signs it (:func:`_sign_after_build`, E56) — so the private
key is absent from every build on every method, not merely from the ones
that happen to run elsewhere.

``validate`` and ``build`` take ``--json``, which replaces the whole
human rendering with one machine-readable document on stdout — the
resolved model or the build manifest on success, the errors of
:meth:`~mcuhome.errors.ConfigError.to_dict` on failure. Exit codes are the
same either way, and the build log still goes to stderr, so redirecting
stdout into a file leaves both halves intact.

``validate --json`` carries the **whole** canonical model, commissioning
credentials included, exactly as ``device-model.json`` does: it is the
output of stages 1-3 and a caller that asked for the model gets the
model. ``build --json`` carries the build manifest, which has none — a
manifest describes artifacts. Neither prints the human commissioning
block, which exists for a person holding a device they just built.

**This module is not an API.** Programs embed :mod:`mcuhome.api`, which
is the supported surface; everything here is a command line, free to
change its internals between releases. The one stable machine-facing
promise this package makes is the command surface itself — a build
server feature-probes ``mcuhome build --help`` for ``--model``,
``--no-sign``/``--public-key`` and ``--json``.

``validate`` writes nothing at all. ``build`` writes only into its build
directory, which is deliberately outside the configuration tree
(builder-pipeline.md §2): ``<tree root>/build/<device>/`` unless
``--build-dir`` says otherwise. Inside it, the generated application is
``app/`` and the compiler's working tree is ``build/`` — everything a
human is meant to read on one side, machine spoil on the other.
``init-pairing`` is the one command that writes into the *configuration*
tree, and it writes into exactly one file: the device's own
(:mod:`mcuhome.provision`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from mcuhome.compiler import container, workspace
from mcuhome.compiler.generate import write_tree
from mcuhome.model import __version__, export, ota, pairing, registry
from mcuhome.model import manifest as manifest_module
from mcuhome.model.errors import BuildError, ConfigError, MCUHomeError
from mcuhome.model.model import DeviceModel, PairingModel
from mcuhome.workbench import (
    api,
    configschema,
    imgtool,
    otafile,
    provision,
    scaffold,
    signing,
)
from mcuhome.workbench import tree as tree_module
from mcuhome.workbench.tree import ConfigTree, resolve_device

__all__ = [
    "BUILD_DIR",
    "format_build_summary",
    "format_commissioning",
    "format_summary",
    "load_device_model",
    "main",
]

#: Directory the per-device build trees are created in, at the tree root.
#: A sibling of ``devices/``, never inside it — build output must not turn
#: up in the user's config diffs (builder-pipeline.md §2).
BUILD_DIR = "build"

#: ``PATH``-style environment variable naming SDK source directories for
#: the default (container) build path. ``--sdk-source`` beats it.
SDK_SOURCE_VAR = "MCUHOME_SDK_SOURCE"

#: Which of the three build methods runs, when no flag says. Second rung
#: of the ladder below; the name is this implementation's and has not
#: been decided by the product owner — see :func:`_resolve_method`.
METHOD_VAR = "MCUHOME_BUILD_METHOD"

#: The build server the ``remote`` method talks to, and the bearer token
#: for it (E53). ``--server``/``--token`` beat them.
SERVER_VAR = "MCUHOME_BUILD_SERVER"
TOKEN_VAR = "MCUHOME_BUILD_TOKEN"


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


def load_device_model(entry: Path, *, tree: ConfigTree) -> DeviceModel:
    """Run stages 1-3 on one device configuration.

    Kept as a name because the CLI is written in terms of it; the
    implementation is :func:`mcuhome.api.load_model`, which is the
    supported one.
    """
    return api.load_model(entry, tree=tree)


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


def format_commissioning(credentials: PairingModel) -> str:
    """The two strings a human needs to add the device to a controller.

    Printed, never written: the builder keeps no record of a device's
    codes beyond the configuration file the user owns and the firmware it
    compiles. Anyone holding either of those holds the passcode, which is
    what makes them worth saying out loud here.
    """
    tuple_ = pairing.Pairing(
        discriminator=credentials.discriminator,
        passcode=credentials.passcode,
        salt=credentials.salt,
        iterations=credentials.iterations,
    )
    lines = [
        "Commissioning",
        f"  manual code    {tuple_.manual_code}",
        f"  QR code        {tuple_.qr_payload}",
        f"  discriminator  {credentials.discriminator} (0x{credentials.discriminator:03X})",
    ]
    if credentials.test_credentials:
        lines.append(
            "  NOTE: these are the credentials published with the Matter SDK. Anyone "
            "who\n        knows them can commission this device — bench use only."
        )
    return "\n".join(lines)


def format_summary(model: DeviceModel) -> str:
    """The human-readable picture of a resolved device."""
    lines: list[str] = []
    device = model.device
    lines.append(f"Device     {device.name} ({device.friendly_name})")
    lines.append(f"Board      {device.board}")
    lines.append(f"Power      {device.power_source}")

    network = model.network
    if network.transport == "thread" and network.thread is not None:
        role = {"ftd": "router", "mtd": "end device"}.get(
            network.thread.device_role, network.thread.device_role
        )
        lines.append(f"Transport  Thread, {role}")
    elif network.transport:
        lines.append(f"Transport  {network.transport}")
    else:
        lines.append("Transport  none (standalone device)")
    lines.append(f"Matter     {'enabled' if network.matter_enabled else 'disabled'}")
    lines.append(f"Zephyr     {model.toolchain.zephyr_line}")
    blobs = ", ".join(f"{name}: {value}" for name, value in model.toolchain.blobs.items())
    lines.append(
        f"Blobs      {blobs or 'none integrated yet'} (blob_usage: {model.toolchain.blob_usage})"
    )

    if model.hardware.buses or model.hardware.peripherals:
        lines.append("")
        lines.append("Hardware")
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
        lines.append("Endpoints")
        for endpoint in model.endpoints:
            types = ", ".join(
                f"{item.name} ({item.id:#06x} rev {item.revision})"
                for item in endpoint.device_types
            )
            alias = f" [{endpoint.alias}]" if endpoint.alias else ""
            lines.append(f"  endpoint {endpoint.id}{alias}: {types}")
            for cluster in endpoint.clusters:
                lines.append(
                    f"    {cluster.name} ({cluster.id:#06x} rev "
                    f"{cluster.cluster_revision}, {len(cluster.attrs)} attributes)"
                )

    if model.channels:
        lines.append("")
        lines.append("Channels")
        for channel in model.channels:
            unit, raw_per_unit = _cluster_unit(channel.cluster_id)
            if channel.report_delta:
                natural = channel.report_delta / raw_per_unit
                delta = f"report on {natural:g} {unit} change"
            else:
                delta = "report every sample"
            lines.append(
                f"  {channel.source.channel} -> endpoint {channel.endpoint_id} "
                f"{channel.cluster_id:#06x}/{channel.attr_id:#06x}, every "
                f"{_format_duration(channel.sample_period_ms)}, {delta}"
            )

    if model.network.pairing is not None:
        lines.append("")
        lines.append(format_commissioning(model.network.pairing))

    if model.build.snippets or model.build.kconfig:
        lines.append("")
        lines.append("Build")
        if model.build.snippets:
            lines.append(f"  snippets: {', '.join(model.build.snippets)}")
        lines.append(f"  {len(model.build.kconfig)} Kconfig settings")

    return "\n".join(lines)


def format_build_summary(
    name: str,
    *,
    images: list[workspace.ImageArtifacts],
    memory: dict[str, list[workspace.MemoryRegion]],
    merged: Path | None = None,
) -> str:
    """What came out of stage 5: which images, where, and what they cost.

    Two images, not one, since ADR 0015: a bootloader and an application
    signed for it. Both are reported, because "the firmware" is now both
    of them and a user installing only the second one has a brick.
    """
    lines = [f"Built {name}."]
    for image in images:
        lines.append("")
        lines.append(image.describe())
        for path in image.files:
            lines.append(f"  {path}")
        for region in memory.get(image.name, []):
            lines.append(f"  memory: {region.describe()}")
    if merged is not None:
        lines.append("")
        lines.append("Combined (every image at its own offset, for a full-chip flash)")
        lines.append(f"  {merged}")
    return "\n".join(lines)


def format_flash_layout(board: str) -> str:
    """The partition table the images were built against (ADR 0015)."""
    definition = registry.BOARDS.get(board)
    if definition is None or definition.update_scheme is None:
        return ""
    scheme = definition.update_scheme
    lines = [
        f"Flash layout (class {scheme.board_class}, MCUboot {scheme.mcuboot_mode}, "
        f"staging: {scheme.staging})"
    ]
    lines += [f"  {entry.describe()}" for entry in scheme.partitions]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _print_json(data: object) -> None:
    """The one place ``--json`` writes, so every document is shaped alike."""
    print(json.dumps(data, indent=2))


def _cmd_validate(args: argparse.Namespace) -> int:
    tree, entry = resolve_device(args.device, cwd=Path.cwd(), config_root=args.config_root)
    args.json_root = tree.root
    result = api.validate_device(entry, tree=tree)
    if getattr(args, "json", False):
        _print_json(result.to_dict())
        return 0 if result.ok else 1
    if not result.ok:
        result.raise_errors()
    assert result.model is not None  # noqa: S101 - ok means there is one
    print(format_summary(result.model))
    if args.verbose:
        print()
        print(result.model.to_json(), end="")
    print()
    print(f"{entry} is valid.")
    return 0


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


def _resolve_build_key(args: argparse.Namespace) -> tuple[Path, signing.SigningKey | None]:
    """Which key file the build gets, and whether it is the private one.

    Two shapes of the same argument (ADR 0015 decision 8). Normally it is
    the user's own private key, generated on first need; with
    ``--no-sign`` it is the **public** half, which is all the bootloader
    needs and all a machine that must not be able to sign may have.
    Either way it is resolved *before* the build: a missing or unusable
    key is a refusal a user should get in a second, not ten minutes into
    a Matter compile.
    """
    if not args.no_sign:
        key = signing.signing_key(args.signing_key, env=_process_env())
        return key.path, key
    if args.public_key is None:
        raise BuildError(
            "--no-sign needs the public half of your signing key (--public-key).",
            hint=(
                "MCUboot verifies against a public key compiled into the "
                "bootloader, so a build that does not sign still has to be told "
                "which key the signature will come from. Write yours out and pass "
                "it:\n"
                f"    mcuhome public-key -o {signing.PUBLIC_KEY_FILE}\n"
                f"    mcuhome build <device> --no-sign --public-key {signing.PUBLIC_KEY_FILE}\n"
                "The private half stays where it is; mcuhome sign applies the "
                "signature afterwards."
            ),
        )
    path = Path(args.public_key).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BuildError(
            f"MCUHome cannot read the public key {path}: {error.strerror}.",
            hint=f"write one with: mcuhome public-key -o {path}",
        ) from error
    except UnicodeDecodeError as error:
        raise BuildError(
            f"{path} is not a PEM public key.",
            hint=f"write one with: mcuhome public-key -o {path}",
        ) from error
    if signing.looks_like_p256_key(text):
        raise BuildError(
            f"{path} is a private key, and --public-key wants the public half.",
            hint=(
                "the whole point of --no-sign is that the private key never "
                "reaches the machine that builds (ADR 0015 decision 8). Write the "
                "public half out and pass that:\n"
                f"    mcuhome public-key --signing-key {path} -o {signing.PUBLIC_KEY_FILE}"
            ),
        )
    if not signing.looks_like_p256_public_key(text):
        raise BuildError(
            f"{path} is not an ECDSA P-256 public key in PEM form.",
            hint=(
                "MCUHome signs with ECDSA P-256 (ADR 0015 decision 8). Write the "
                "public half of your key with: mcuhome public-key -o <file>"
            ),
        )
    return path, None


def _build_input(args: argparse.Namespace) -> tuple[DeviceModel, Path]:
    """The model to build and where to build it, from either input form.

    Two ways in, one result. The normal one runs stages 1-3 on a device
    configuration; ``--model`` takes a canonical model that some other
    machine already resolved and starts at stage 4 (builder-pipeline.md
    §6, dashboard ADR 0007 decision 4). The second path deliberately never
    touches a configuration tree — a build server has no business holding
    one, and no business holding the secrets file next to it.
    """
    if args.model is not None:
        model = api.read_model(Path(args.model))
        # No tree to hang the default on, so the build directory is
        # relative to where the command was run. A caller that cares —
        # every build server does — passes --build-dir.
        root = Path.cwd()
    else:
        tree, entry = resolve_device(args.device, cwd=Path.cwd(), config_root=args.config_root)
        args.json_root = tree.root
        model = load_device_model(entry, tree=tree)
        root = tree.root
    return model, args.build_dir or root / BUILD_DIR / model.device.name


def _resolve_method(args: argparse.Namespace, env: dict[str, str]) -> str:
    """Which build method runs: flag, then variable, then the default (E53/E54).

    The ladder E53 fixed for the remote server address, applied to the
    method itself: an explicit flag beats the environment, and the
    environment beats the default, which is the local build container
    (E54). E53's third rung — a file under ``$XDG_CONFIG_HOME/mcuhome/`` —
    is deliberately **not** implemented here: the decision names the
    directory and no file format, and inventing one would fix a
    configuration surface that has not been decided.

    ``--method <name>`` is the only spelling of a method (E62). The host
    method once had a flag of its own, ``--native``, from before the
    methods had names; it is gone rather than carried, because nothing
    outside this repository family has ever depended on it and a project
    that is not public yet owes no backward compatibility.

    The flag and variable *names* below are this implementation's and are
    not in the decision log, which settles the ladder's shape and the
    ``--server``/``--token`` rung only. They are one constant each,
    exactly so that a decision can rename them.
    """
    from_flag = getattr(args, "method", None)
    if from_flag:
        return api.resolve_method(from_flag)
    return api.resolve_method(env.get(METHOD_VAR))


def _remote_server(args: argparse.Namespace, env: dict[str, str]) -> tuple[str | None, str | None]:
    """Build server and token: flag, then variable, then nothing (E53).

    The same two rungs as the method above, and the same missing third —
    ``$XDG_CONFIG_HOME/mcuhome/`` is where E53 puts the last one, and what
    a file there is called and contains is undecided. A ``remote`` build
    with neither rung answered is refused by
    :class:`~mcuhome.workbench.api.RemoteNotConfigured`, which
    names both knobs, rather than defaulted to a server nobody chose.
    """
    server = getattr(args, "server", None) or env.get(SERVER_VAR) or None
    token = getattr(args, "token", None) or env.get(TOKEN_VAR) or None
    return server, token


def _run_method(request: api.BuildRequest, *, method: str) -> api.BuildOutcome:
    """Run one build method and wait for it.

    The one place the command line crosses the async boundary (E53's
    lead-engineer note): the three build methods are awaitable because
    ``remote`` drives a socket and the other two block for minutes, and a
    command line owns its event loop, so it wraps the whole build in a
    single :func:`asyncio.run` and its user sees no difference.
    """
    return asyncio.run(api.run_build(request, method=method))


def _cmd_build(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    method = _resolve_method(args, _process_env())
    model, out_dir = _build_input(args)

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
        written = write_tree(model, out_dir=out_dir, config_name=model.device.source)
        generated = [str(path.relative_to(out_dir)) for path in written]
        if not as_json:
            print(f"Generated {len(written)} files for {model.device.name} in {out_dir}:")
            for name in generated:
                print(f"  {name}")
        if args.generate_only:
            if as_json:
                _print_json(
                    {
                        "ok": True,
                        "device": model.device.name,
                        "build_dir": str(out_dir),
                        "generated": generated,
                        "manifest": None,
                    }
                )
                return 0
            _print_commissioning(model)
            return 0
        return _build_local_dev(args, model, out_dir, generated=generated, as_json=as_json)

    return _build_delivered(args, model, out_dir, method=method, as_json=as_json)


def _build_local_dev(
    args: argparse.Namespace,
    model: DeviceModel,
    out_dir: Path,
    *,
    generated: list[str],
    as_json: bool,
) -> int:
    """``--method local-dev``: compile on the host, then host-sign (E56).

    The local-dev escape hatch (ADR 0007) for a contributor who already has
    a west workspace. Since E56 it is not a special case: like the two
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
    key_path, key = _resolve_build_key(args)
    # E56: no build signs inline, so the west build gets the PUBLIC key for
    # the bootloader and nothing more — the private key stays for the host
    # signing step below, exactly as the container path keeps it off the
    # container.
    bootloader_key = _bootloader_public_key(key_path, key, out_dir)
    resolved_jobs = workspace.resolve_jobs(env=env, cli_jobs=args.jobs)
    bootloader_snippets = () if scheme is None else scheme.bootloader_snippets

    def announce(plan: workspace.BuildPlan) -> None:
        """Say what is about to run — after every pre-flight refusal, before it."""
        if not as_json:
            print()
            print(f"Building {model.device.name} for {model.device.board} in {plan.topdir}")
            print(f"  jobs {resolved_jobs.value} ({resolved_jobs.source})")
            print(_signing_key_note(key) if key is not None else _detached_key_note(key_path))
            print(f"  {' '.join(plan.command)}")
            print()
        # The build log is written by a subprocess to the same terminal;
        # flush so the header above it is not still sitting in this
        # process's buffer.
        sys.stdout.flush()

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
            jobs=resolved_jobs.value,
            bootloader_key=bootloader_key,
            snippets=snippets,
            bootloader_snippets=bootloader_snippets,
            # The library states neither of these for itself: `mcuhome` on
            # a command line *is* the local-dev case (E18), and a command
            # line is the one caller entitled to say "where I am installed"
            # and "where I was run".
            module_dir=workspace.installed_module_dir(),
            started_in=Path.cwd(),
            on_plan=announce,
            # In --json mode the compiler's own output would break the
            # document, so it goes to stderr — where a log belongs anyway,
            # and where a caller redirecting stdout into a file still sees
            # progress.
            stream=sys.stderr if as_json else None,
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
        ota_image = _sign_after_build(
            model, out_dir, key=args.signing_key, env=env, report=outcome.report
        ).ota
        # Re-read so the artifact list now includes the freshly signed image.
        images = workspace.build_images(plan.build_dir, app_image=plan.app_dir.name)

    if as_json:
        _print_json(
            {
                "ok": True,
                "device": model.device.name,
                "build_dir": str(out_dir),
                "generated": generated,
                "manifest_path": str(manifest_path),
                "manifest": manifest_module.read_manifest(Path(manifest_path)),
            }
        )
        return 0

    print()
    print(
        format_build_summary(
            model.device.name,
            images=images,
            memory=workspace.parse_image_memory_report(
                log, images=[image.name for image in images]
            ),
            merged=merged,
        )
    )
    print(f"  {manifest_path}")
    if ota_image is not None:
        print()
        print(_ota_note(ota_image))
    layout = format_flash_layout(model.device.board)
    if layout:
        print()
        print(layout)
    if args.no_sign:
        print()
        print(_detached_next_step(out_dir))
    _print_commissioning(model)
    return 0


def _bootloader_public_key(key_path: Path, key: signing.SigningKey | None, out_dir: Path) -> Path:
    """The PUBLIC key file west compiles into the bootloader (E56).

    Every build is unsigned now, so west never receives the private key —
    it gets the public half, which is all MCUboot needs and is useless for
    signing. With ``--no-sign`` the user already wrote that file out
    (``--public-key``); otherwise it is derived from the private key into
    the build directory, where the host signing step then uses the private
    half.
    """
    if key is None:
        return key_path
    public = out_dir / ".mcuhome-signing.pub"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(
        signing.public_key_pem(key_path.read_text(encoding="utf-8")), encoding="utf-8"
    )
    return public


def _build_delivered(
    args: argparse.Namespace,
    model: DeviceModel,
    out_dir: Path,
    *,
    method: str,
    as_json: bool,
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
    key_path, key = _resolve_build_key(args)
    # Only the public half ever reaches the build environment. Derived from
    # the private key on the signing path, taken verbatim from --public-key
    # on the detached one — never the private half, on either.
    signing_pub = _public_pem_for_context(key_path, key)
    resolved_jobs = workspace.resolve_jobs(env=env, cli_jobs=args.jobs)
    remote = method == api.REMOTE
    reference = "" if remote else container.image_reference(env, override=args.image)
    server, token = _remote_server(args, env)

    if not as_json:
        print()
        where = "on a build server" if remote else "in the build container"
        print(f"Building {model.device.name} for {model.device.board} {where}")
        if remote:
            # Only when there is one: a run that is about to be refused for
            # the lack of an address should not print "server None" first.
            if server:
                print(f"  server {server}")
        else:
            print(f"  image {reference}")
        print(f"  jobs {resolved_jobs.value} ({resolved_jobs.source})")
        print(_signing_key_note(key) if key is not None else _detached_key_note(key_path))
        print()
    sys.stdout.flush()

    def sink(line: str) -> None:
        # The build log belongs on stderr in --json mode (where stdout is
        # the document) and on the terminal otherwise.
        print(line, file=sys.stderr if as_json else sys.stdout)

    # A hidden scratch area under the build directory: the context and the
    # session tree live here and are rebuilt each run; the durable
    # artifacts are copied up into out_dir below.
    outcome = _run_method(
        api.BuildRequest(
            model=model,
            out_dir=out_dir,
            env=env,
            jobs=resolved_jobs.value,
            signing_pub=signing_pub,
            sdk_sources=_sdk_sources(args, env),
            image=None if remote else reference,
            server=server,
            token=token,
            on_line=sink,
        ),
        method=method,
    )
    if not outcome.successful:
        raise _delivered_build_failed(outcome)

    copied = _collect_delivered_artifacts(outcome, out_dir)
    report = imgtool.read_build_report(out_dir / outcome.report)

    # Whatever an earlier build of this directory signed is (re)produced only
    # on the signing branch below; drop any stale signed image and .ota first
    # so a --no-sign run cannot leave a flashable lookalike beside the fresh
    # unsigned firmware.
    _drop_signed_lookalikes(out_dir)

    signed: list[Path] = []
    ota_image = None
    if not args.no_sign:
        result = _sign_after_build(
            model, out_dir, key=args.signing_key, env=env, report=outcome.report
        )
        signed, ota_image = result.signed, result.ota

    if as_json:
        _print_json(
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
        return 0

    print()
    print(_format_local_summary(model.device.name, copied, signed, report))
    if ota_image is not None:
        print()
        print(_ota_note(ota_image))
    layout = format_flash_layout(model.device.board)
    if layout:
        print()
        print(layout)
    if args.no_sign:
        print()
        print(_detached_next_step(out_dir))
    _print_commissioning(model)
    return 0


def _sdk_sources(args: argparse.Namespace, env: dict[str, str]) -> tuple[Path, ...]:
    """SDK source directories: ``--sdk-source`` (repeatable) then the variable.

    The local build fetches the MCUHome SDK as a hash-pinned package from
    operator-configured directories only (ADR 0018, contract v1's first
    tier). ``--sdk-source`` names one and repeats; ``MCUHOME_SDK_SOURCE``
    is a ``PATH``-style list of them. Order is preserved and duplicates
    dropped. Irrelevant to ``local-dev``, which needs no SDK package.
    """
    sources = [Path(entry).expanduser() for entry in (args.sdk_source or [])]
    from_env = env.get(SDK_SOURCE_VAR)
    if from_env:
        sources += [Path(entry).expanduser() for entry in from_env.split(os.pathsep) if entry]
    return tuple(dict.fromkeys(sources))


def _public_pem_for_context(key_path: Path, key: signing.SigningKey | None) -> str:
    """The **public** key PEM that goes into the context — never the private half.

    On the signing path *key* is the resolved private key, and the public
    half is derived from it; with ``--no-sign`` *key* is None and *key_path*
    already points at the public key the user wrote out. Either way what
    leaves this function is a public key, which is all a build container may
    ever hold (ADR 0015 decision 8).
    """
    if key is not None:
        return signing.public_key_pem(key_path.read_text(encoding="utf-8"))
    return key_path.read_text(encoding="utf-8")


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
            f"the build ran {where}, through the build-container ABI (ADR 0018). "
            "The build log above carries what west and the compiler said; "
            "--method local-dev compiles on the host instead."
        ),
    )


def _describe_report_region(region: dict) -> str:
    """One §7.2.1 ``memory`` entry, rendered like the linker's own table.

    The same shape ``format_build_summary`` prints for a ``local-dev`` build, from
    the report the container measured rather than a host build log this
    path never produced.
    """
    used = float(region.get("used", 0)) / 1024
    total = float(region.get("total", 0)) / 1024
    percent = float(region.get("percent", 0.0))
    return f"{region.get('region')} {used:.1f} KiB of {total:.1f} KiB ({percent:.1f}%)"


def _format_local_summary(
    name: str,
    copied: list[tuple[str, str, Path]],
    signed: list[Path],
    report: dict,
) -> str:
    """What the container delivered, what the host signed, and the footprint."""
    lines = [f"Built {name}."]
    lines.append("")
    lines.append("Artifacts")
    for role, _, destination in copied:
        lines.append(f"  {destination}  ({role})")
    for path in signed:
        lines.append(f"  {path}  (signed)")
    memory = report.get("memory")
    if isinstance(memory, list) and memory:
        lines.append("")
        lines.append("Memory")
        for region in memory:
            if isinstance(region, dict):
                lines.append(f"  {region.get('image')}: {_describe_report_region(region)}")
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


def _ota_note(image: ota.OtaImage) -> str:
    return (
        f"Matter OTA image (version {image.version}, "
        f"SoftwareVersion {image.software_version}):\n"
        f"    {image.path}\n"
        "Put it where your controller's OTA provider looks for images; the device "
        "downloads it\nover Thread once a controller announces the provider or the "
        "next periodic query runs."
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
    model: DeviceModel, out_dir: Path, *, key: Path | None, env: dict[str, str], report: str
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
        plan, data, ota_image = _apply_manifest_signature(out_dir, key=key, env=env)
        return _Signed(signed=list(plan.outputs), ota=ota_image, manifest=data)
    plan = imgtool.sign_report(
        out_dir,
        key=key,
        env=env,
        topdir=workspace.find_topdir(workspace.installed_module_dir(), Path.cwd()),
    )
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
        f"    mcuhome sign {out_dir}"
    )


def _update_scheme_of(model: DeviceModel) -> registry.UpdateSchemeDef | None:
    board = registry.BOARDS.get(model.device.board)
    return None if board is None else board.update_scheme


def _signing_key_note(key: signing.SigningKey) -> str:
    """Where the signing key is, and — loudly — when it is brand new.

    A new key is not a detail: MCUboot verifies against the public half
    compiled into the bootloader already on the device, so firmware
    signed with a key that was just generated is firmware an already
    bootstrapped device will refuse.
    """
    if not key.created:
        return f"  signing key {key.path}"
    return (
        f"  signing key {key.path}\n"
        "               NEW — MCUHome had none and generated one just now. Keep "
        "it: every\n"
        "               device bootstrapped with it only accepts firmware signed "
        "with it,\n"
        "               and replacing it means bootstrapping those devices again."
    )


def _detached_key_note(path: Path) -> str:
    """Where the *public* key came from, and what it does not let happen."""
    return (
        f"  public key  {path}\n"
        "              --no-sign: the bootloader gets this, the application is\n"
        "              left unsigned, and no private key is anywhere near this build."
    )


def _print_commissioning(model: DeviceModel) -> None:
    """The pairing codes, last, where a freshly built device needs them."""
    if model.network.pairing is None:
        return
    print()
    print(format_commissioning(model.network.pairing))


def _cmd_init_pairing(args: argparse.Namespace) -> int:
    tree, entry = resolve_device(args.device, cwd=Path.cwd(), config_root=args.config_root)
    result = provision.init_pairing(
        entry,
        secrets_file=tree.secrets_file,
        use_secrets=args.secrets,
        force=args.force,
    )
    verb = "Replaced the commissioning credentials in" if result.replaced else "Wrote"
    print(f"{verb} {result.entry}.")
    if result.secrets_file is not None:
        print(f"The values themselves are in {result.secrets_file}.")
    print()
    print(format_commissioning(_pairing_model(result.pairing)))
    print()
    print(
        "Keep the configuration safe: it is the only copy. Anyone who has it — or the "
        "firmware\nbuilt from it — can commission this device."
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


def _cmd_new(args: argparse.Namespace) -> int:
    created = scaffold.new_device(
        args.device, board=args.board, cwd=Path.cwd(), config_root=args.config_root
    )
    if created.created_tree:
        print(f"Started a configuration tree in {created.tree.root}.")
    print(f"Wrote {created.entry}.")
    print()
    print("Next:")
    print(f"  mcuhome init-pairing {created.name}    draw this device's commissioning codes")
    print(f"  mcuhome validate {created.name}        see what it resolves to")
    print(f"  mcuhome build {created.name}           compile it")
    print()
    print(
        "The configuration has no hardware in it yet — the file carries a complete, "
        "commented\nexample to uncomment and adjust."
    )
    return 0


def _apply_manifest_signature(
    target: Path, *, key: Path | None, env: dict[str, str]
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
    plan = imgtool.sign_build(
        target,
        key=key,
        env=env,
        # Where MCUboot's own imgtool would be, if this machine happens to
        # be a west workspace. It usually is not: signing runs where the
        # key is (ADR 0015 decision 8), and imgtool.sign_build no longer
        # goes looking by itself.
        topdir=workspace.find_topdir(workspace.installed_module_dir(), Path.cwd()),
    )
    data = manifest_module.record_signature(
        manifest_module.read_manifest(plan.manifest_path), out_dir=plan.out_dir, files=plan.outputs
    )
    ota_image = _sign_ota(data, out_dir=plan.out_dir, outputs=plan.outputs)
    if ota_image is not None:
        manifest_module.record_ota(data, ota_image, out_dir=plan.out_dir)
    manifest_module.dump_manifest(data, out_dir=plan.out_dir)
    return plan, data, ota_image


def _cmd_sign(args: argparse.Namespace) -> int:
    target = Path(args.target)
    # One verb, two report shapes, chosen by which file the directory
    # holds. A local-dev build dir carries build-manifest.json (the manifest
    # path below); a container build dir carries the leaner §7.2.1
    # build-report.json (the local backend's delivery), which has no
    # manifest to fold a signature back into and no OTA parameters to wrap.
    if _target_is_build_report(target):
        return _cmd_sign_report(args, target)
    plan, data, ota_image = _apply_manifest_signature(
        target, key=args.signing_key, env=_process_env()
    )
    print(f"Signed the application image of {plan.out_dir} with {plan.key}:")
    for path in plan.outputs:
        print(f"  {path}")
    if ota_image is not None:
        print()
        print(_ota_note(ota_image))
    print()
    print(
        f"imgtool sign --version {plan.parameters.version} "
        f"--header-size {plan.parameters.header_size} "
        f"--slot-size {plan.parameters.slot_size} --align {plan.parameters.align}\n"
        "  — the parameters the build manifest states, which are the ones the build "
        "would have\n    used itself."
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


def _cmd_sign_report(args: argparse.Namespace, target: Path) -> int:
    """Sign the firmware a container build delivered, from its §7.2.1 report.

    The detached-signing tail of the default build path: the report carries
    the imgtool parameters and the unsigned ``firmware.*`` sit beside it, so
    a machine that has only the delivery and the private key produces the
    flashable images. There is no manifest to record the signature in and
    no OTA block to wrap — those belong to ``mcuhome build``, which does
    both in one step, or to the machine that holds the device model.
    """
    plan = imgtool.sign_report(
        target,
        key=args.signing_key,
        env=_process_env(),
        topdir=workspace.find_topdir(workspace.installed_module_dir(), Path.cwd()),
    )
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


def _cmd_public_key(args: argparse.Namespace) -> int:
    key = signing.signing_key(args.signing_key, env=_process_env(), create=False)
    pem = signing.public_key_pem(key.path.read_text(encoding="utf-8"))
    if args.output is None:
        print(pem, end="")
        return 0
    output = Path(args.output).expanduser()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(pem, encoding="utf-8")
    except OSError as error:
        raise ConfigError(
            f"MCUHome cannot write {output}: {error.strerror}.",
            hint="pick a writable location",
        ) from error
    print(f"Wrote the public half of {key.path} to {output}.")
    print("It is not a secret: it is what a build server needs and all it may have.")
    return 0


#: What ``mcuhome schema`` can emit, and what produces it.
SCHEMA_EXPORTS = {
    "config": configschema.config_json_schema,
    "registry": export.registry_data,
}


def _cmd_schema(args: argparse.Namespace) -> int:
    text = export.to_json(SCHEMA_EXPORTS[args.what]())
    if args.output is None:
        print(text, end="")
        return 0
    output = Path(args.output).expanduser()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    except OSError as error:
        raise ConfigError(
            f"MCUHome cannot write {output}: {error.strerror}.",
            hint="pick a writable location",
        ) from error
    print(f"Wrote the {args.what} document to {output}.")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    del args
    print("mcuhome clean is not implemented yet.", file=sys.stderr)
    print(
        f"Build output is self-contained: delete the {BUILD_DIR}/ directory, or the "
        "one --build-dir pointed at, and nothing else is affected.",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcuhome",
        description="Build Zephyr firmware from an MCUHome YAML device configuration.",
    )
    parser.add_argument("--version", action="version", version=f"mcuhome {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print the resolved device model",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--config-root",
            type=Path,
            default=None,
            metavar="PATH",
            help="configuration tree root (default: found by searching upwards)",
        )
        # Also accepted after the subcommand, where people reach for it.
        # SUPPRESS so that leaving it out here does not overwrite the
        # value given before the subcommand.
        subparser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="also print the resolved device model",
        )

    def add_json_option(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--json",
            action="store_true",
            help=(
                "print one machine-readable document on stdout instead of the "
                "human summary; errors come out the same way and exit codes do "
                "not change (validate emits the resolved model, build the build "
                "manifest; the build log goes to stderr)"
            ),
        )

    new_parser = subparsers.add_parser(
        "new", help="create a new device folder with a starter configuration"
    )
    new_parser.add_argument("device", help="device name; it becomes the folder and the hostname")
    new_parser.add_argument(
        "--board",
        required=True,
        metavar="TARGET",
        help=(
            "Zephyr board target this device runs on, verbatim "
            f"(supported today: {', '.join(sorted(registry.BOARDS))})"
        ),
    )
    add_common_options(new_parser)
    new_parser.set_defaults(func=_cmd_new)

    validate_parser = subparsers.add_parser(
        "validate", help="check a device configuration and print what it resolves to"
    )
    validate_parser.add_argument(
        "device", help="device folder name, or the path of a device folder or YAML file"
    )
    add_json_option(validate_parser)
    add_common_options(validate_parser)
    validate_parser.set_defaults(func=_cmd_validate)

    build_parser_ = subparsers.add_parser("build", help="build firmware for a device")
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
            f"where to generate the application (default: <tree root>/{BUILD_DIR}/<device>, "
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
    # ADR 0020 decision 6 / E18: three build methods behind one interface.
    # The name selects a method, not a code path.
    build_parser_.add_argument(
        "--method",
        metavar="NAME",
        default=None,
        help=(
            "which build method compiles this device: "
            + ", ".join(api.METHODS)
            + f" (default: {api.DEFAULT_METHOD}, the build container on this "
            f"machine; {METHOD_VAR} sets it too)"
        ),
    )
    build_parser_.add_argument(
        "--server",
        metavar="URL",
        default=None,
        help=(f"build server the {api.REMOTE} method talks to ({SERVER_VAR} sets it too)"),
    )
    build_parser_.add_argument(
        "--token",
        metavar="TOKEN",
        default=None,
        help=f"bearer token for that build server ({TOKEN_VAR} sets it too)",
    )
    build_parser_.add_argument(
        "--image",
        metavar="REF",
        default=None,
        help=(
            f"builder image to compile in (default: {container.IMAGE}; the "
            f"{container.IMAGE_VAR} environment variable sets it too)"
        ),
    )
    build_parser_.add_argument(
        "--sdk-source",
        action="append",
        metavar="DIR",
        help=(
            "directory holding the hash-pinned MCUHome SDK package the build "
            "container fetches (repeatable; searched in order). The default "
            f"container build needs one; {SDK_SOURCE_VAR} is a PATH-style list of "
            f"them, and --method {api.LOCAL_DEV} needs none"
        ),
    )
    build_parser_.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "ECDSA P-256 private key to sign the firmware with (default: "
            f"{signing.default_key_path(_process_env())}, generated on first use; the "
            f"{signing.KEY_VAR} environment variable sets it too)"
        ),
    )
    build_parser_.add_argument(
        "--no-sign",
        action="store_true",
        help=(
            "build the application UNSIGNED and record the signing parameters in "
            "the build manifest, so that the private key never has to be on the "
            "machine that compiles (ADR 0015 decision 8); needs --public-key, and "
            "mcuhome sign applies the signature afterwards"
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
            f"available RAM; {workspace.JOBS_VAR} overrides the auto-detection, "
            "--jobs overrides both)"
        ),
    )
    add_json_option(build_parser_)
    add_common_options(build_parser_)
    build_parser_.set_defaults(func=_cmd_build)

    sign_parser = subparsers.add_parser(
        "sign", help="sign the application image of a finished build"
    )
    sign_parser.add_argument(
        "target",
        help=f"build directory, or the {manifest_module.MANIFEST_FILE} inside one",
    )
    sign_parser.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "ECDSA P-256 private key to sign with (default: "
            f"{signing.default_key_path(_process_env())}; the {signing.KEY_VAR} environment "
            "variable sets it too). Never generated here: a build has to be signed "
            "with the key its device's bootloader already carries."
        ),
    )
    add_common_options(sign_parser)
    sign_parser.set_defaults(func=_cmd_sign)

    public_key_parser = subparsers.add_parser(
        "public-key", help="print the public half of the firmware signing key"
    )
    public_key_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="write it to a file instead of stdout",
    )
    public_key_parser.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "which key to take the public half of "
            f"(default: {signing.default_key_path(_process_env())})"
        ),
    )
    add_common_options(public_key_parser)
    public_key_parser.set_defaults(func=_cmd_public_key)

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
            "types MCUHome knows, as data"
        ),
    )
    schema_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="write it to a file instead of stdout",
    )
    add_common_options(schema_parser)
    schema_parser.set_defaults(func=_cmd_schema)

    init_parser = subparsers.add_parser(
        "init-pairing",
        help="draw this device's commissioning credentials and write them into its configuration",
    )
    init_parser.add_argument("device", help="device folder name or path")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "replace credentials that are already there (every controller that knows "
            "the device has to commission it again)"
        ),
    )
    init_parser.add_argument(
        "--secrets",
        action="store_true",
        help=(
            f"put the values in the tree's {tree_module.SECRETS_FILE} and reference them "
            "with !secret, for a configuration that lives in version control"
        ),
    )
    add_common_options(init_parser)
    init_parser.set_defaults(func=_cmd_init_pairing)

    clean_parser = subparsers.add_parser("clean", help="remove build output of a device")
    clean_parser.add_argument("device", nargs="?", help="device folder name or path")
    clean_parser.add_argument("--all", action="store_true", help="clean every device")
    add_common_options(clean_parser)
    clean_parser.set_defaults(func=_cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except MCUHomeError as error:
        # Both streams end up in the same terminal, and a command that
        # printed progress before failing must not have its error appear
        # above the output it refers to. Only stdout is buffered when it is
        # a pipe, so flushing it here is what keeps the order right.
        sys.stdout.flush()
        if getattr(args, "json", False):
            # The same document shape as a successful --json run, so a
            # caller parses one thing and reads `ok`. The tree root, when
            # the command got as far as resolving one, is what makes the
            # file paths tree-relative rather than this machine's.
            _print_json(
                {"ok": False, "errors": error.to_dicts(root=getattr(args, "json_root", None))}
            )
            return 1
        # The working directory is the CLI's to supply: it is what makes
        # "main.yaml, line 5" shorter than an absolute path, and it is the
        # one place in the system that legitimately knows it.
        print(error.render(Path.cwd()), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
