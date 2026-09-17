# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome signing`` — the firmware signing key of a project.

One P-256 key pair under ``secrets/signing/``, referenced by
``secrets/signing/key.yaml``. A device only accepts images signed with
the key its bootloader carries, which is why the two acts are apart:
``print-public-key`` never writes, and ``create-key`` is the one place a
key comes into existence — asked twice it answers the key that is there
rather than drawing a second one over it.

**The private half is in no document either command prints.** What they
answer is where the key is, whether it is the project's own, whether
this run drew it, and the public half — which is what goes into a build
somebody else runs and into a bootloader somebody else compiles.

``--signing-key`` is the same flag on both, and means what the option
``signing.key`` means: the key file this invocation is about, instead of
the project's own.
"""

from __future__ import annotations

from mcuhome.workbench import api

from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, GREEN, Output
from mcuhome.cli.phases import EXIT_OK

__all__ = ["signing_create_key", "signing_print_public_key"]


def signing_print_public_key(invocation: Invocation) -> int:
    """``mcuhome signing print-public-key``: the public half, as PEM.

    Reading is not the moment to draw a key: a project that has none is
    the workbench's refusal, and it names the command that draws one.
    """
    output = invocation.output
    project = invocation.find_project()
    settings = invocation.settings(project=project)
    invocation.start()
    key = api.resolve_signing_key(
        settings.value("signing.key"), env=invocation.env, project=project
    )
    # The PEM and nothing around it, so `> key.pub` is a key file. The
    # text already ends in a newline, which `human` adds itself.
    output.human(api.public_key_pem(key.pem).rstrip("\n"))
    output.result({"ok": True, **key.to_dict()})
    return EXIT_OK


def signing_create_key(invocation: Invocation) -> int:
    """``mcuhome signing create-key``: draw the project's signing key, once."""
    output = invocation.output
    project = invocation.find_project()
    settings = invocation.settings(project=project)
    invocation.start()
    key = api.create_signing_key(
        env=invocation.env, project=project, path=settings.value("signing.key")
    )
    _print_key(key, output=output)
    output.result({"ok": True, **key.to_dict()})
    return EXIT_OK


def _print_key(key: api.SigningKey, *, output: Output) -> None:
    """Where the key is, and what drawing one means for a device.

    The public half is not printed here: it is one command away, and a
    person who just drew a key is told which one rather than handed a
    block of PEM they did not ask for.
    """
    if not key.created:
        output.human(
            _("This project already has a signing key: {path}.").format(path=output.path(key.path))
        )
        output.human(output.muted(_("Nothing was drawn.")))
        return
    output.human(
        f"{output.style('✓', GREEN, BOLD)} "
        + _("Drew the signing key: {path}.").format(path=output.path(key.path))
    )
    output.human()
    output.human(
        output.muted(
            _(
                "Every image this project builds is signed with it, and a device only\n"
                "accepts images signed with the key its bootloader carries — keep the\n"
                "file, and keep it out of version control.\n"
                "    mcuhome signing print-public-key    the half a bootloader is compiled with"
            )
        )
    )
