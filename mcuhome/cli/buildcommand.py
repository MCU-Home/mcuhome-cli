# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome device build`` — the command the rest of the tool exists for.

A build produces an **unsigned** image plus a build report, whichever
target ran it, and one host-side step signs it afterwards: the private
key never enters a build, at any target, and there is one place where it
is read at all.

**Where it runs** is a ladder, most explicit first — ``--build-server``
(which is remote by statement), ``--builder``, ``--build-target``, the
configured ``build.builder``, ``build.target``. The last two rungs are
the workbench's own (``resolve_builder``), so the command line walks
only the three a person typed. **How** this machine executes a local
build is ``build.mode``, and a remote build has no mode of its own to
state.

**What this module decides is which function to call.** The target, the
image, the key, the steps a target reports and whether a pin may be
honoured are all api answers; nothing here re-derives one.

``Ctrl-C`` sets the stop predicate rather than killing the process: the
build walks its ladder down, releases the build directory and answers
``ok: false, stopped: true``. The bound that may take is stated once, so
a client can show that the stop was heard rather than a run that has
apparently hung; a second ``Ctrl-C`` is the person overruling that.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import Any

from mcuhome.workbench import api

from mcuhome.cli import buildview, stdinvalue
from mcuhome.cli.errors import UsageError
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, GREEN, RED, YELLOW, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_FAILURE, EXIT_OK

__all__ = ["build", "validate_build"]

#: Regions the linker reports that are not memory on the device at all.
#: Zephyr collects the interrupt-table metadata in an ``IDT_LIST`` region
#: at a made-up address and the final link discards it, so it holds
#: nothing on any board and never could. Printing "0 of 32 KiB" invites a
#: reader to compare it with FLASH, where the number means something.
#: Filtered by name and never by value: a genuinely empty region is a
#: fact about the build and stays in the table.
LINKER_ONLY_REGIONS = frozenset({"IDT_LIST"})

#: Where a fill level stops being unremarkable. Below the first, no
#: color: an image that fits is not news.
_TIGHT_PERCENT = 75.0
_CRITICAL_PERCENT = 90.0

#: What each build step is called on the step line, and where the one
#: that runs somewhere else gets its place appended.
_STEP_LABELS = {"context": _("context"), "environment": _("build environment"), "compile": None}


def validate_build(invocation: Invocation) -> list[api.MCUHomeError]:
    """Everything about the invocation that is wrong before anything runs.

    All of it is read-only and instant, which is what the validate phase
    is for: a person learns in a second rather than ten minutes into a
    Matter compile. The rules are the ones the reference states at the
    flags — the two input forms, the detached pair, and the credential
    that names no server.
    """
    problems: list[api.MCUHomeError] = []
    device = invocation.flag("device")
    model = invocation.flag("model")
    if device is None and model is None:
        problems.append(
            UsageError(
                _("mcuhome device build needs a device, or --model with a canonical model."),
                hint=_("name the device:\n    mcuhome device build <device>"),
            )
        )
    elif device is not None and model is not None:
        problems.append(
            UsageError(
                _("--model builds a canonical model instead of a device, so it takes no device."),
                hint=_("drop the device name, or drop --model"),
            )
        )
    if (
        invocation.flag("build_server_token") is not None
        and invocation.flag("build_server") is None
    ):
        problems.append(
            UsageError(
                _("--build-server-token is the credential for --build-server, and none is named."),
                hint=_(
                    "name the server this credential belongs to, or configure a builder and "
                    "put the token in its credentials file:\n"
                    "    mcuhome secret set --kind builder --name <builder> --key token --value -"
                ),
            )
        )
    problems.extend(_wait_problems(invocation))
    problems.extend(_key_problems(invocation))
    return problems


def build(invocation: Invocation) -> int:
    """``mcuhome device build [<device>]``: build firmware and sign it."""
    model, out_dir, project = _input(invocation)
    settings = invocation.settings(project=project)
    options = api.resolve_build_options(settings)
    selection = _selected_builder(invocation, settings, project)
    signing_pub, key = _signing_material(invocation, settings, project)
    steps = api.build_steps(target=selection.target, options=options)
    invocation.start(steps=list(steps))
    # The whole command holds the build directory, not the compile alone:
    # delivering the artifacts and signing the image both write files a
    # second run would be overwriting underneath.
    with api.open_build_lock(out_dir, device=model.device.name, operation="build"):
        return _build_holding_the_directory(
            invocation,
            model,
            out_dir,
            project=project,
            settings=settings,
            options=options,
            selection=selection,
            steps=steps,
            signing_pub=signing_pub,
            key=key,
        )


def _build_holding_the_directory(
    invocation: Invocation,
    model: api.DeviceModel,
    out_dir: Path,
    *,
    project: api.Project | None,
    settings: api.Settings,
    options: api.BuildOptions,
    selection: api.SelectedBuilder,
    steps: Sequence[str],
    signing_pub: str,
    key: api.SigningKey | None,
) -> int:
    output = invocation.output
    view = buildview.make_view(
        _step_line(steps, selection=selection, options=options),
        output=output,
        log_path=out_dir / buildview.LOG_FILE,
    )
    _print_header(
        model,
        selection=selection,
        options=options,
        key=key,
        public_key=_public_key_path(invocation),
        signing=invocation.flag("sign") is not False,
        output=output,
    )

    def on_step(stage: str, **facts: Any) -> None:
        # One seam, three consumers: the machine modes get the stage with
        # whatever facts came with it, a live human run gets the
        # repainted step line, and a step that established something
        # worth stating leaves a line saying what.
        output.progress(stage, **facts)
        view.step(stage)
        note = _step_note(stage, facts, output=output)
        if note is not None:
            view.note(note)

    def on_wait(wait: api.SeatWait) -> None:
        # Its own verb and not a stage: the build has not started and may
        # never start, so a step bar that moved here would be showing
        # progress that does not exist.
        output.wait(retry_after=wait.retry_after, waited=wait.waited, attempt=wait.attempt)
        view.waiting(_waiting_note(wait, output=output))

    with _StopTheBuild(output) as stopper:
        try:
            result = asyncio.run(
                api.build_firmware(
                    api.BuildRequest(
                        model=model,
                        out_dir=out_dir,
                        env=invocation.env,
                        options=options,
                        builder=selection,
                        mode=invocation.flag("build_mode"),
                        container_image=invocation.flag("container_image"),
                        # The project's trust anchors live under its root.
                        # A stand-in root, invented for a device file with
                        # no project above it, is not one MCUHome ever
                        # wrote anchors into.
                        project_root=(
                            project.root if project is not None and project.discovered else None
                        ),
                        registries=settings.value("registry"),
                        signing_pub=signing_pub,
                        wait_for_turn=invocation.flag("wait_for_turn") is not False,
                        max_wait_seconds=_max_wait_seconds(invocation),
                        on_line=view.line,
                        on_step=on_step,
                        on_wait=on_wait,
                        should_stop=stopper.requested,
                    ),
                    target=selection.target,
                )
            )
        except BaseException:
            # Collapse the frame before whatever renders the refusal, and
            # put the log's tail back: the frame's scrollback went with
            # it, and the tail is what a person diagnoses from.
            view.close(success=False)
            _print_log_tail(view, out_dir / buildview.LOG_FILE, output=output)
            raise
    view.close(success=result.ok)

    if not result.ok:
        _print_log_tail(view, out_dir / buildview.LOG_FILE, output=output)
        _print_failure(result, output=output)
        output.result({"ok": False, "build": result.to_dict(), "signing": None, "footprint": []})
        return EXIT_FAILURE

    footprint = _footprint(out_dir / result.report)
    signing = None
    if invocation.flag("sign") is not False:
        signing = _sign(invocation, model, out_dir, settings=settings, project=project)
    ok = signing is None or signing.ok
    _print_summary(
        model,
        result,
        signing,
        footprint,
        out_dir=out_dir,
        output=output,
        signed=signing is not None,
    )
    output.result(
        {
            "ok": ok,
            "build": result.to_dict(),
            "signing": None if signing is None else signing.to_dict(),
            "footprint": [region.to_dict() for region in footprint],
        }
    )
    return EXIT_OK if ok else EXIT_FAILURE


# -- what this invocation is about --------------------------------------


def _input(invocation: Invocation) -> tuple[api.DeviceModel, Path, api.Project | None]:
    """The model, the build directory and the project, from either form.

    Two ways in, one answer. The normal one resolves a device of a
    project; ``--model`` takes a canonical model another machine already
    resolved and touches no project at all — with no project there is no
    project configuration layer and no signing key beside it, and both
    are statements this build makes on purpose.
    """
    output = invocation.output
    stated = invocation.flag("model")
    if stated is not None:
        model = api.read_model(_path(invocation, str(stated)))
        return model, _out_dir(invocation, invocation.cwd, model.device.name), None
    project, entry = api.resolve_device(
        str(invocation.flag("device")),
        env=invocation.env,
        cwd=invocation.cwd,
        project_dir=invocation.project_dir,
    )
    model = api.load_model(
        entry, project=project, on_warning=lambda finding: output.finding(finding.to_dict())
    )
    return model, _out_dir(invocation, project.root, model.device.name), project


def _out_dir(invocation: Invocation, base: Path, device: str) -> Path:
    """``--out-dir``, or the device's own build directory under *base*."""
    stated = invocation.flag("out_dir")
    if stated is not None:
        return _path(invocation, str(stated))
    return (base / api.BUILD_DIR / device).resolve()


def _path(invocation: Invocation, text: str) -> Path:
    """One path a flag carried: ``~`` expanded, absolute, resolved."""
    path = api.expand_user_path(text, env=invocation.env)
    if not path.is_absolute():
        path = invocation.cwd / path
    return path.resolve()


def _selected_builder(
    invocation: Invocation, settings: api.Settings, project: api.Project | None
) -> api.SelectedBuilder:
    """Where this build runs — the three rungs a person types.

    ``--build-server`` names a server without configuring a builder and
    is remote by statement; ``--builder`` names one that is configured;
    ``--build-target`` names the target outright, which is what has to
    beat a configured ``build.builder``. Below those, ``resolve_builder``
    walks the two configured rungs itself.

    ``--container-image`` is not part of the selection: it pins the image
    for this one invocation, means the same thing at both targets, and
    reaches the build as the request field it is.
    """
    output = invocation.output
    server = invocation.flag("build_server")
    if server is not None:
        return api.SelectedBuilder(
            target=api.TARGET_REMOTE,
            server=str(server),
            token=stdinvalue.resolve_value(
                invocation.flag("build_server_token"), flag="--build-server-token"
            ),
        )
    named = invocation.flag("builder")
    if named is None and invocation.flag("build_target") is not None:
        return api.SelectedBuilder(target=api.resolve_build_target(invocation.flag("build_target")))
    return api.resolve_builder(
        settings,
        name=None if named is None else str(named),
        project=project,
        env=invocation.env,
        on_warning=lambda finding: output.finding(finding.to_dict()),
    )


def _public_key_path(invocation: Invocation) -> Path | None:
    """The file ``--public-key`` names, or ``None`` where it was not used."""
    stated = invocation.flag("public_key")
    return None if stated is None else _path(invocation, str(stated))


def _signing_material(
    invocation: Invocation, settings: api.Settings, project: api.Project | None
) -> tuple[str, api.SigningKey | None]:
    """The **public** PEM the build environment gets, and where it came from.

    Only the public half ever reaches a build. Normally it is derived
    from the private key this invocation resolved, in memory and never by
    re-reading a path; with ``--no-sign --public-key`` it is the file's
    own text, and that is the one combination in which no private key is
    read at all.
    """
    public_key = _public_key_path(invocation)
    if public_key is not None:
        return public_key.read_text(encoding="utf-8"), None
    key = api.resolve_signing_key(
        settings.value("signing.key"), env=invocation.env, project=project
    )
    return api.public_key_pem(key.pem), key


def _sign(
    invocation: Invocation,
    model: api.DeviceModel,
    out_dir: Path,
    *,
    settings: api.Settings,
    project: api.Project | None,
) -> api.SigningResult:
    """The one host-side signing step, where the private key already is.

    Under ``-v`` the plan is asked first and its commands are shown: the
    plan raises everything the run would raise and writes nothing, so
    seeing the commands costs a person nothing but the resolution.
    """
    output = invocation.output
    key = settings.value("signing.key")
    imgtool = settings.value("signing.imgtool")
    if invocation.flag("verbose", False) and not output.machine:
        plan = api.plan_signing(
            out_dir, env=invocation.env, key=key, project=project, imgtool=imgtool, model=model
        )
        output.human()
        output.human(output.heading(_("Signing")))
        for command in plan.commands:
            output.human(f"  {output.muted(' '.join(command.argv))}")
    return api.sign_firmware(
        out_dir, env=invocation.env, key=key, project=project, imgtool=imgtool, model=model
    )


def _footprint(report_path: Path) -> tuple[api.MemoryRegion, ...]:
    """What the build report measured, or nothing where it measured none."""
    return api.memory_footprint(api.read_build_report(report_path))


def _max_wait_seconds(invocation: Invocation) -> float:
    stated = invocation.flag("max_wait_seconds")
    return api.DEFAULT_MAX_WAIT_SECONDS if stated is None else float(stated)


# -- the validate phase's own rules -------------------------------------


def _wait_problems(invocation: Invocation) -> list[api.MCUHomeError]:
    """``--max-wait-seconds`` is a number of seconds, and ``0`` is no bound."""
    stated = invocation.flag("max_wait_seconds")
    if stated is None:
        return []
    try:
        seconds = float(stated)
    except ValueError:
        seconds = -1.0
    if seconds < 0:
        return [
            UsageError(
                _("--max-wait-seconds takes a number of seconds, not {value!r}.").format(
                    value=str(stated)
                ),
                hint=_("0 removes the bound; leave the flag out for the default"),
            )
        ]
    return []


def _key_problems(invocation: Invocation) -> list[api.MCUHomeError]:
    """The detached pair: ``--public-key`` belongs to ``--no-sign`` alone.

    What the flag names has to *be* a public key, and the check happens
    here rather than after the compile — a build that is going to be
    refused should be refused before it runs.
    """
    stated = invocation.flag("public_key")
    if stated is None:
        return []
    if invocation.flag("sign") is not False:
        return [
            UsageError(
                _("--public-key is the key a build that does not sign compiles in."),
                hint=_(
                    "a build that signs derives the public half from the signing key itself. "
                    "Add --no-sign, or drop --public-key"
                ),
            )
        ]
    path = _path(invocation, str(stated))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [
            UsageError(
                _("{path} is not a readable PEM file.").format(path=path),
                hint=_(
                    "write the public half out:\n    mcuhome signing print-public-key > {path}"
                ).format(path=path),
            )
        ]
    if api.is_p256_private_key(text):
        return [
            UsageError(
                _("{path} is a private key, and --public-key wants the public half.").format(
                    path=path
                ),
                hint=_(
                    "the point of --no-sign is that the private key never reaches the machine "
                    "that builds. Write the public half out and pass that:\n"
                    "    mcuhome signing print-public-key > <file>"
                ),
            )
        ]
    if not api.is_p256_public_key(text):
        return [
            UsageError(
                _("{path} is not an ECDSA P-256 public key in PEM form.").format(path=path),
                hint=_(
                    "MCUHome signs with ECDSA P-256. Write the public half of your key:\n"
                    "    mcuhome signing print-public-key > <file>"
                ),
            )
        ]
    return []


# -- stopping -----------------------------------------------------------


class _StopTheBuild:
    """``Ctrl-C`` stops the build through its predicate, not by killing it.

    A build that is cut off leaves its build directory held and its work
    half-written; the predicate lets it walk its own ladder down, release
    the directory and answer that it was stopped. The bound that may take
    is stated once when the stop is requested, so a client can show that
    the stop was heard rather than a run that hangs. A second ``Ctrl-C``
    is the person overruling that, and leaves whatever it leaves.
    """

    def __init__(self, output: Output) -> None:
        self._output = output
        self._stop = False
        self._previous: dict[int, Any] = {}

    def requested(self) -> bool:
        return self._stop

    def __enter__(self) -> _StopTheBuild:
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

    def _handle(self, number: int, _frame: FrameType | None) -> None:
        if not self._stop:
            seconds = api.resolve_shutdown_seconds(cancel_grace_seconds=0)
            self._stop = True
            self._output.stopping(seconds=seconds)
            self._output.log(
                _(
                    "Stopping the build — it releases the build directory on the way out, "
                    "which can take up to {seconds:.0f} seconds.\n"
                    "Press Ctrl+C again to leave now and leave whatever it leaves."
                ).format(seconds=seconds)
            )
            return
        if number != signal.SIGINT:
            return
        signal.signal(signal.SIGINT, self._previous.get(signal.SIGINT, signal.SIG_DFL))
        raise KeyboardInterrupt


# -- what a person watching a build reads -------------------------------


def _step_line(
    steps: Sequence[str], *, selection: api.SelectedBuilder, options: api.BuildOptions
) -> list[buildview.BuildStep]:
    """The steps this target really reports, each labeled with its place."""
    where = _where(selection, options)
    return [
        buildview.BuildStep(
            key, _STEP_LABELS.get(key) or _("compile ({where})").format(where=where)
        )
        for key in steps
    ]


def _where(selection: api.SelectedBuilder, options: api.BuildOptions) -> str:
    """Where the compile runs, in the words the header uses too."""
    if selection.target == api.TARGET_REMOTE:
        named = selection.server or (None if selection.builder is None else selection.builder.name)
        return _("remote {name}").format(name=named) if named else _("remote")
    if options.mode == api.MODE_SUBPROCESS:
        return _("this machine")
    return _("build container")


def _print_header(
    model: api.DeviceModel,
    *,
    selection: api.SelectedBuilder,
    options: api.BuildOptions,
    key: api.SigningKey | None,
    public_key: Path | None,
    signing: bool,
    output: Output,
) -> None:
    """What is being built, where, and which key it is built against.

    The key line says which half was read, because that is the whole
    difference between the two ways to run this: a build that signs here
    reads the private key, and ``--no-sign --public-key`` is the one
    combination in which no private key is touched at all.
    """
    if output.machine:
        return
    output.human()
    output.human(
        f"{output.heading(_('Building'))} {output.style(model.device.name, BOLD)} "
        f"{_('for')} {model.device.board} {output.muted(_where(selection, options))}"
    )
    if selection.builder is not None:
        output.human(
            f"  {output.muted(_('builder'))} {selection.builder.name} "
            f"{output.muted(f'({selection.builder.target})')}"
        )
    if key is None:
        output.human(f"  {output.muted(_('public key '))} {output.path(public_key)}")
        output.human(output.muted(_("  no private key is anywhere near this build (--no-sign).")))
    else:
        output.human(f"  {output.muted(_('signing key'))} {output.path(key.path)}")
        if not signing:
            output.human(output.muted(_("  --no-sign: only its public half reaches the build.")))
    output.human()


def _note(label: str, parts: Sequence[str], *, output: Output) -> str:
    """One step's line: which step, then what it established.

    The label column is nine wide **plus** a separator rather than ten
    flat, so a label longer than the column does not run straight into
    its first fact with nothing between them.
    """
    return "  " + output.muted(f"{label:<9} ") + output.muted(" · ").join(parts)


def _count(number: int, thing: str) -> str:
    return f"{number} {thing}" if number == 1 else f"{number} {thing}s"


def _step_note(stage: str, facts: dict[str, Any], *, output: Output) -> str | None:
    """The line a step's facts make, or ``None`` where they make none.

    Facts are append-only display material: what is rendered is what this
    version recognizes, and one it does not know is carried by the
    machine modes and ignored here rather than guessed at. Every value is
    read defensively — a line that describes a build must never be the
    thing that ends it.
    """
    if not facts:
        return None
    if stage == "environment":
        return _environment_note(facts, output=output)
    if stage != "context":
        return None
    parts = [_("SDK {version}").format(version=facts.get("sdk", "?"))]
    patches = facts.get("patches") or []
    parts.append(
        _("patches: {names}").format(names=", ".join(patches)) if patches else _("no patches")
    )
    if facts.get("files"):
        parts.append(_count(int(facts["files"]), "file"))
    identity = str(facts.get("id") or "")
    if identity:
        # The whole identity is in the manifest and in the document;
        # twelve hex digits are what a person compares two builds with.
        parts.append(_("id {short}").format(short=identity.partition(":")[2][:12]))
    return _note(_("context"), parts, output=output)


def _environment_note(facts: dict[str, Any], *, output: Output) -> str | None:
    """Which build environment this compile ran in, and how it was found.

    The one line that makes a moving tag honest: the same configuration
    can resolve to two images on two days, so what it resolved to *this
    time* is stated rather than left in a file.
    """
    reference = str(facts.get("build_environment") or "")
    if not reference:
        return None
    name, _sep, digest = reference.partition("@")
    parts = [name] if name else []
    if digest:
        parts.append(_("digest {short}").format(short=digest.partition(":")[2][:12]))
    zephyr = str(facts.get("zephyr") or "")
    if zephyr:
        parts.append(_("Zephyr {version}").format(version=zephyr))
    found_under = str(facts.get("found_under") or "")
    if found_under and found_under != name.rpartition(":")[2]:
        parts.append(_("found under {tag}").format(tag=found_under))
    if facts.get("fetched"):
        parts.append(_("fetched"))
    return _note(_("environment"), parts, output=output)


def _waiting_note(wait: api.SeatWait, *, output: Output) -> str:
    """The line a person reads while a build server holds its turn.

    The wait and nothing else, because the wait is all the server says:
    the order of its queue is its own business, and a client that
    reported a position would be reporting something nobody promised.
    """
    parts = [
        _("the build server is busy — trying again in {delay}").format(
            delay=_duration(wait.retry_after or 0.0)
        )
    ]
    if wait.waited > 0:
        parts.append(_("waiting {waited} so far").format(waited=_duration(wait.waited)))
    return _note(_("queue"), parts, output=output)


def _duration(seconds: float) -> str:
    """A wait as a person reads it — ``45s``, ``2m 30s``, ``1h 5m``."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        minutes, rest = divmod(whole, 60)
        return f"{minutes}m" if rest == 0 else f"{minutes}m {rest}s"
    hours, rest = divmod(whole, 3600)
    minutes = rest // 60
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"


def _print_log_tail(view: object, log_path: Path, *, output: Output) -> None:
    """After a live frame collapsed, put the log's tail back on stderr.

    The linear views passed the whole log through already, so only the
    live frame owes the reader this. Its own trouble (a closed stream,
    say) must never replace what it is decorating.
    """
    try:
        if not isinstance(view, buildview.LiveView):
            return
        tail = buildview.log_tail(log_path)
        if tail:
            output.log(tail)
    except Exception:  # noqa: BLE001 - deliberately silent, see docstring
        pass


def _print_failure(result: api.BuildResult, *, output: Output) -> None:
    """What a build that ran and said no has to say, for a person.

    The findings are the workbench's own words — the delivery condition
    that failed, or what a build server refused with — and a machine mode
    reads them out of the document instead.
    """
    if output.machine:
        return
    if result.stopped:
        output.log(
            output.style(_("Stopped."), YELLOW, BOLD)
            + " "
            + _("The build was ended before it produced anything.")
        )
        return
    output.log(output.style(_("The firmware did not build."), RED, BOLD))
    for finding in result.diagnostics:
        document = finding.to_dict()
        output.log(f"  {document['message']}")
        if document.get("hint"):
            output.log(f"  {output.muted(str(document['hint']))}")


def _print_summary(
    model: api.DeviceModel,
    result: api.BuildResult,
    signing: api.SigningResult | None,
    footprint: Sequence[api.MemoryRegion],
    *,
    out_dir: Path,
    output: Output,
    signed: bool,
) -> None:
    """What the build delivered, what was signed, and what it cost."""
    if output.machine:
        return
    output.human()
    output.human(
        f"{output.style('✓', GREEN, BOLD)} " + _("Built {device}.").format(device=model.device.name)
    )
    output.human()
    output.human(output.heading(_("Artifacts")))
    for artifact in result.artifacts:
        output.human(f"  {output.path(out_dir / artifact.path)}  {output.muted(artifact.role)}")
    if signing is not None:
        for entry in signing.signed:
            output.human(f"  {output.path(entry.path)}  {output.style(_('(signed)'), GREEN)}")
        if signing.ota is not None:
            output.human(f"  {output.path(signing.ota)}  {output.muted(_('(over-the-air image)'))}")
    table = _memory_table(footprint, output=output)
    if table:
        output.human()
        output.human(table)
    if not signed:
        output.human()
        output.human(
            _(
                "This build is UNSIGNED, and MCUboot boots nothing it cannot verify.\n"
                "Sign it where your private key is:\n"
                "    mcuhome device sign-firmware {device} --out-dir {path}"
            ).format(device=model.device.name, path=out_dir)
        )


def _memory_table(entries: Sequence[api.MemoryRegion], *, output: Output) -> str:
    """The footprint table: one row per image, one column per region.

    Rounded to whole KiB and whole percent on purpose — a tenth of a KiB
    is noise in a number a person reads to answer "will the next feature
    still fit". The exact bytes stay in the document and in the build
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
    rows: list[list[str | Cell]] = [[_("Image"), *regions]]
    for image in images:
        row: list[str | Cell] = [image]
        for region in regions:
            entry = by_key.get((image, region))
            if entry is None:
                row.append("")
                continue
            percent = 100.0 * entry.used / entry.total if entry.total else 0.0
            text = (
                f"{kib(entry.used):>{used_width[region]}} / "
                f"{kib(entry.total):>{total_width[region]}} KiB  {round(percent):>3}%"
            )
            row.append(Cell(text, _fill_codes(percent)))
        rows.append(row)
    return (
        output.heading(_("Memory"))
        + "\n"
        + format_table(rows, header=True, indent="  ", output=output)
    )


def _fill_codes(percent: float) -> tuple[str, ...]:
    if percent >= _CRITICAL_PERCENT:
        return (RED, BOLD)
    if percent >= _TIGHT_PERCENT:
        return (YELLOW,)
    return ()
