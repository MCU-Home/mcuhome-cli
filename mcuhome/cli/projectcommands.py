# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome project`` — creating, describing and upgrading a project.

A project is a directory with a ``.mcuhome-project-root`` marker, the
``devices/`` and ``secrets/`` folders and a ``mcuhome.yaml``. ``init``
makes one, ``info`` says what is in it and whether it needs upgrading,
and ``upgrade`` migrates it to the layout this MCUHome speaks.

**The order of an upgrade** is what makes it safe, and it is the order
of the three phases: the plan is shown and the confirmation asked
first — a non-interactive run brings it as ``--confirm <id>``, which
names the project so a script that ended up in the wrong directory
refuses instead of migrating something else — then the session renames
the project marker for the whole run, waits out the builds that started
before it, and applies one migration at a time.

``Ctrl-C`` sets a stop predicate rather than cutting a migration in
half: the one that is running finishes, its version is written, and the
run ends there with the migrations it did not reach in ``remaining``.
"""

from __future__ import annotations

import signal
import time
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import Any

from mcuhome.workbench import api

from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, GREEN, YELLOW, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_FAILURE, EXIT_OK

__all__ = [
    "confirm_upgrade",
    "project_info",
    "project_init",
    "project_upgrade",
    "validate_upgrade",
]

#: How often the wait for a running build looks again.
_WAIT_SECONDS = 1.0


def project_init(invocation: Invocation) -> int:
    """``mcuhome project init [<directory>]``: create a project."""
    root = _stated_directory(invocation) or invocation.cwd
    force = bool(invocation.flag("force", False))
    invocation.start()
    output = invocation.output
    if api.is_project_root(root) and not force:
        # Answered, not refused: a script may run init before every
        # session, and a project that is already there is the outcome it
        # asked for.
        project = api.read_project(root)
        output.human(
            _("{path} is already a project (id {id}); nothing was written.").format(
                path=output.path(project.root), id=project.id
            )
        )
        output.result({"ok": True, "project": project.to_dict(), "created": []})
        return EXIT_OK
    created = api.create_project(root, force=force)
    output.human(_("Created a project in {path}:").format(path=output.path(created.project.root)))
    for path in created.created:
        output.human(f"  {output.path(path)}")
    output.human()
    output.human(_("Next: mcuhome signing create-key, then mcuhome device new <name>."))
    output.result({"ok": True, **created.to_dict()})
    return EXIT_OK


def project_info(invocation: Invocation) -> int:
    """``mcuhome project info [<directory>]``: where the project is and what it holds."""
    project = _project(invocation)
    invocation.start()
    upgrading = api.is_upgrading(project.root)
    plan = api.plan_upgrade(_version(project))
    devices = api.find_devices(project)
    output = invocation.output
    output.human(
        _("Project {path} (id {id}, layout version {version})").format(
            path=output.path(project.root), id=project.id, version=_version(project)
        )
    )
    if upgrading:
        output.human(_("An upgrade of this project is running, or was interrupted."))
    if plan:
        output.human()
        output.human(_("Missing migrations:"))
        for migration in plan:
            output.human(f"  {migration.to_version}. {migration.description}")
    output.human()
    output.human(_print_devices(devices, output=output))
    output.result(
        {
            "ok": True,
            "project": project.to_dict(),
            "upgrading": upgrading,
            "devices": [device.to_dict() for device in devices],
            "plan": [migration.to_dict() for migration in plan],
        }
    )
    return EXIT_OK


def validate_upgrade(invocation: Invocation) -> list[api.MCUHomeError]:
    """The confirmation rule of ``project upgrade``, before anything moves.

    Without a terminal to confirm at, ``--confirm`` is required and has
    to name *this* project. Problems resolving the project itself are
    left to the run — they are refusals, not wrong invocations.
    """
    if invocation.flag("dry_run", False):
        return []
    try:
        project = _project(invocation)
    except api.MCUHomeError:
        return []
    if not api.plan_upgrade(_version(project)):
        return []
    confirm = invocation.flag("confirm")
    file = project.file
    if confirm is None:
        if invocation.output.interactive:
            return []
        return [
            _refuse(
                _("An upgrade has to be confirmed, and there is no terminal to confirm at."),
                hint=_(
                    "back the project up first, then name the project you mean:\n    {command}"
                ).format(command=_confirm_command(project)),
            )
        ]
    if file is None or not file.matches(str(confirm)):
        return [
            _refuse(
                _('"{given}" does not name the project in {path}.').format(
                    given=confirm, path=project.root
                ),
                hint=_("pass this project's id — the short form is enough:\n    {command}").format(
                    command=_confirm_command(project)
                ),
            )
        ]
    return []


def confirm_upgrade(invocation: Invocation) -> None:
    """The question ``project upgrade`` asks, up front and only once.

    The answer is kept on the arguments, because the interact phase
    decides and the execute phase acts: a run nobody confirmed changes
    nothing.
    """
    if invocation.flag("dry_run", False) or invocation.flag("confirm") is not None:
        return
    try:
        project = _project(invocation)
    except api.MCUHomeError:
        return
    plan = api.plan_upgrade(_version(project))
    if not plan:
        return
    output = invocation.output
    _print_plan(project, plan, output=output)
    output.human()
    output.human(output.style(_("This cannot be undone."), YELLOW, BOLD))
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
        answer = ""
    invocation.args.confirmed = answer.strip().lower() == "yes"


def project_upgrade(invocation: Invocation) -> int:
    """``mcuhome project upgrade [<directory>]``: migrate a project."""
    project = _project(invocation)
    plan = api.plan_upgrade(_version(project))
    output = invocation.output
    invocation.start(plan=[migration.to_dict() for migration in plan])

    if not plan:
        # "Nothing to do" has the shape of "this is what I did": a
        # client reads one document either way, and the two versions
        # being equal is the whole statement.
        version = _version(project)
        output.human(
            _("The project in {path} is up to date (layout version {version}).").format(
                path=output.path(project.root), version=version
            )
        )
        nothing = api.UpgradeResult(from_version=version, to_version=version, applied=())
        output.result(
            {"ok": True, "project": project.to_dict(), "dry_run": False, **nothing.to_dict()}
        )
        return EXIT_OK

    if invocation.flag("dry_run", False):
        _print_plan(project, plan, output=output)
        _print_details(plan, output=output)
        output.human()
        output.human(output.muted(_("Nothing was changed (--dry-run).")))
        output.result(
            {
                "ok": True,
                "project": project.to_dict(),
                "dry_run": True,
                "plan": [migration.to_dict() for migration in plan],
            }
        )
        return EXIT_OK

    if not _confirmed(invocation):
        # Only an interactive run reaches this: a machine mode is never
        # interactive, and without a terminal the validate phase already
        # required `--confirm`. The answer is the document of a run that
        # applied nothing — nothing was changed, and everything remains.
        version = _version(project)
        output.human(_("Cancelled. Nothing was changed."))
        cancelled = api.UpgradeResult(
            from_version=version, to_version=version, applied=(), stopped=True
        )
        output.result(
            {"ok": False, "project": project.to_dict(), "dry_run": False, **cancelled.to_dict()}
        )
        return EXIT_FAILURE

    with api.open_upgrade_session(project.root) as session:
        _print_plan(project, session.plan, output=output)
        output.human()
        _wait_for_builds(session, output=output)
        result = _apply(session, output=output)

    upgraded = api.read_project(project.root, require_version=False)
    document = {
        "ok": not result.stopped,
        "project": upgraded.to_dict(),
        "dry_run": False,
        **result.to_dict(),
    }
    if result.stopped:
        output.human()
        output.human(
            _("Stopped at layout version {version}. Run the upgrade again to continue.").format(
                version=result.to_version
            )
        )
        output.result(document)
        return EXIT_FAILURE
    output.human()
    output.human(
        f"{output.style('✓', GREEN, BOLD)} "
        + _("Project upgraded: layout version {old} → {new}.").format(
            old=result.from_version, new=result.to_version
        )
    )
    _print_details(result.applied, output=output)
    output.result(document)
    return EXIT_OK


# -- the parts of an upgrade ------------------------------------------


def _confirmed(invocation: Invocation) -> bool:
    """Whether this run may rewrite the project.

    Either the interact phase was answered ``yes``, or ``--confirm``
    named the project — the validate phase is what checked that it named
    *this* one.
    """
    if invocation.flag("confirm") is not None:
        return True
    return bool(invocation.flag("confirmed", False))


def _wait_for_builds(session: api.UpgradeSession, *, output: Output) -> None:
    """Wait until nothing is working in this project's build directories.

    No lock is taken: the project marker is already renamed, so no build
    can *start* — this only waits out the ones that began before the
    upgrade did. The wait is a log line rather than a stage: it is not
    part of the migration vocabulary, and a stage nobody published is a
    word a client cannot read.
    """
    announced: set[str] = set()
    while True:
        busy = session.running_builds()
        if not busy:
            if announced:
                output.log(_("Done waiting — nothing is working in this project any more."))
            return
        for entry in busy:
            if entry.name in announced:
                continue
            announced.add(entry.name)
            output.log(
                _("Waiting for {device}: {what} (process {process}, started {started}).").format(
                    device=entry.name,
                    what=entry.operation or _("a run"),
                    process=entry.process or "?",
                    started=entry.started or "?",
                )
            )
        time.sleep(_WAIT_SECONDS)


def _apply(session: api.UpgradeSession, *, output: Output) -> api.UpgradeResult:
    """Run the migrations, reporting each as a started/done pair."""

    def report(key: str, **facts: Any) -> None:
        output.progress(key, **facts)
        if key == "migration_started":
            output.human(_("  {name} …").format(name=facts.get("name", "")))
        else:
            output.human(
                f"  {output.style('✓', GREEN)} "
                + _("layout version {version}").format(version=facts.get("to_version", ""))
            )

    with _StopAfterCurrentMigration(output) as stopper:
        return session.apply(on_step=report, should_stop=stopper.requested)


class _StopAfterCurrentMigration:
    """``Ctrl-C`` and ``SIGTERM`` stop the upgrade — between migrations.

    Cutting a migration in half is what leaves a project broken, so the
    signal only sets a flag and the run ends at the next clean boundary.
    A person who really wants out now is told how (three presses within
    three seconds) and what it costs; ``SIGKILL`` cannot be caught at
    all, and that case is what the renamed project marker makes visible
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

    def _announce(self) -> None:
        """Say once that the stop was heard, and what its bound is.

        The bound of this stop is the migration that is running, which is
        not a number anybody has — so the stream says ``seconds: null``
        rather than inventing one.
        """
        if not self._stop:
            self._output.stopping(seconds=None)

    def _handle(self, number: int, _frame: FrameType | None) -> None:
        self._announce()
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


# -- shared renderings and lookups ------------------------------------


def _stated_directory(invocation: Invocation) -> Path | None:
    """The positional *directory*, expanded, or ``None`` where none was given."""
    stated = invocation.flag("directory")
    if stated is None:
        return None
    return api.expand_user_path(str(stated), env=invocation.env)


def _project(invocation: Invocation) -> api.Project:
    """The project ``info`` and ``upgrade`` work on, version check off.

    These are the two commands a person runs *because* something refused
    them, so a project whose layout is too old is described rather than
    refused.
    """
    stated = _stated_directory(invocation)
    if stated is not None:
        return api.read_project(stated, require_version=False)
    return invocation.project(require_version=False)


def _version(project: api.Project) -> int:
    return api.PROJECT_VERSION if project.file is None else project.file.version


def _confirm_command(project: api.Project) -> str:
    token = "" if project.file is None else project.file.token
    return f"mcuhome project upgrade {project.root} --confirm {token}"


def _refuse(message: str, *, hint: str) -> api.ConfigError:
    """A refusal about the project, in the shape every other one has."""
    return api.ConfigError(message, hint=hint)


def _print_plan(project: api.Project, plan: Sequence[api.Migration], *, output: Output) -> None:
    """What the upgrade will do, before the first file moves."""
    file = project.file
    named = f"  {output.muted(_('(id {short})').format(short=file.short_id))}" if file else ""
    output.human(
        _("Upgrading the project in {path}").format(path=output.path(project.root)) + named
    )
    output.human(
        _("from layout version {old} to {new}:").format(
            old=_version(project), new=plan[-1].to_version
        )
    )
    output.human()
    for number, migration in enumerate(plan, start=1):
        output.human(f"  {number}. {migration.description}")


def _print_details(applied: Sequence[api.Migration], *, output: Output) -> None:
    """The long form: what changed, and what to watch out for from now on."""
    for migration in applied:
        output.human()
        output.human(output.heading(migration.description))
        for line in migration.details.splitlines():
            output.human(f"  {line}" if line else "")


def _print_devices(devices: Sequence[api.DeviceRecord], *, output: Output) -> str:
    """The project's devices, or the line that says it has none."""
    if not devices:
        return _("No devices yet — mcuhome device new <name> writes one.")
    rows: list[list[str | Cell]] = [[_("device"), _("board"), _("problems")]]
    for device in devices:
        rows.append(
            [
                device.name,
                device.board,
                Cell(str(device.problems), () if device.ok else (YELLOW,)),
            ]
        )
    return format_table(rows, header=True, output=output)
