# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome host check`` — what a build on this machine would need.

Reported rather than raised: one finding per thing examined, each with
what was found and the fix where there is one. Which checks run follows
the configured mode — the container runtime and the image search for
``container``, the environment store and the interpreter for
``subprocess`` — because the two need disjoint things of a host, and
reporting on what this machine will never run is noise.

**It raises nothing and it decides nothing.** A host that cannot build
is the answer: ``ok`` false with the failing findings in the list, exit
1, and never a refusal. Which checks there are, what each one probes and
what it says are the workbench's; this module renders a list.

The one thing it resolves differently from every other command is the
project: with the layout version **not** required, because a project
older than this MCUHome is exactly what the check reports — a command
that refused over it could never say so.
"""

from __future__ import annotations

from collections.abc import Sequence

from mcuhome.workbench import api

from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, DIM, GREEN, RED, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_FAILURE, EXIT_OK

__all__ = ["host_check"]

#: The units a cache size is printed in, smallest first.
_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def host_check(invocation: Invocation) -> int:
    """``mcuhome host check``: the findings, and what they cost to get.

    It reports no stages and talks to this machine while it runs — the
    container runtime's version, what the configured repositories
    publish, the interpreter, the cache — so it costs what those cost.
    """
    output = invocation.output
    project = invocation.find_project(require_version=False)
    settings = invocation.settings(project=project)
    options = api.resolve_build_options(settings)
    invocation.start()
    result = api.check_build_host(
        options=options,
        env=invocation.env,
        project=project,
        imgtool=settings.value("signing.imgtool"),
        settings=settings,
    )
    cache = api.read_cache_usage(options=options, env=invocation.env)
    _print_findings(result, verbose=bool(invocation.flag("verbose", False)), output=output)
    _print_cache(cache, output=output)
    output.result(
        {
            "ok": result.ok,
            "host": result.to_dict(),
            "cache": [usage.to_dict() for usage in cache],
        }
    )
    return EXIT_OK if result.ok else EXIT_FAILURE


# -- the human renderings -----------------------------------------------


def _print_findings(result: api.HostCheckResult, *, verbose: bool, output: Output) -> None:
    """One line per finding, and the fix under the ones that need one.

    A finding that is fine is one line with its name: what a person came
    for is what is *not* fine. ``-v`` prints every finding's detail
    rather than the failing ones alone.
    """
    if output.machine:
        return
    for finding in result.findings:
        mark = output.style("✓", GREEN, BOLD) if finding.ok else output.style("✗", RED, BOLD)
        detail = f"  {finding.detail}" if finding.detail and (verbose or not finding.ok) else ""
        output.human(f"{mark} {finding.check}{detail}")
        if not finding.ok and finding.hint:
            lines = finding.hint.splitlines()
            output.human(f"  {_('Fix:')} {lines[0]}")
            for line in lines[1:]:
                output.human(f"       {line}")
    if not result.ok:
        failing = sum(1 for finding in result.findings if not finding.ok)
        output.human()
        output.human(
            _("{failing} of {total} checks need attention.").format(
                failing=failing, total=len(result.findings)
            )
        )


def _print_cache(usage: Sequence[api.CacheUsage], *, output: Output) -> None:
    """What the compiler cache holds, one row per configured tier."""
    if output.machine or not usage:
        return
    output.human()
    output.human(output.heading(_("Compiler cache")))
    rows: list[list[str | Cell]] = [[_("tier"), _("size"), _("files"), _("path")]]
    for tier in usage:
        rows.append(
            [
                tier.tier,
                _size(tier.size),
                str(tier.files),
                Cell(str(tier.path), () if tier.files else (DIM,)),
            ]
        )
    output.human(format_table(rows, header=True, align="lrr", output=output))


def _size(size: int) -> str:
    """Bytes as a person reads them; the document carries the number."""
    value = float(size)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError  # pragma: no cover - the loop answers at the last unit
