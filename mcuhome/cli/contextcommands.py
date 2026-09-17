# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome context`` — what a build is attributed to.

A build context is the resolved package pins, the canonical model, the
public signing key and the device's patches, in one directory whose
identity is computed from its bytes. A build creates one itself; these
three commands are for the caller who wants one without a build — to
hand it to a build server, to see what a build would be attributed to,
or to check that a directory still holds what it declares.

**Creating and locking are two acts and this command does both.**
``create_context`` writes the base context, and locking — hashing the
files and computing the identity — is the act of whoever builds it, so a
context created here is locked here: the identity a build server checks
its copy against is the one this run wrote down.

The scratch area ``create_context`` needs is the one path this command
line picks rather than is given: a hidden directory beside *out_dir*,
removed when the run is over, wherever the run ends.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from mcuhome.workbench import api

from mcuhome.cli.errors import UsageError
from mcuhome.cli.i18n import _
from mcuhome.cli.invocation import Invocation
from mcuhome.cli.output import BOLD, DIM, GREEN, RED, Cell, Output, format_table
from mcuhome.cli.phases import EXIT_FAILURE, EXIT_OK

__all__ = ["context_create", "context_print", "context_verify", "validate_create"]

#: The facts a context states, in the order a person reads them, with
#: the label each one is printed under. A key the workbench does not
#: answer is left out of the rendering, and one this version does not
#: know is not rendered at all: the set is append-only display material,
#: and a renderer shows what it recognizes.
_FACT_LABELS = (
    ("id", _("id")),
    ("board", _("board")),
    ("sdk", _("sdk")),
    ("sdk_sha256", _("sdk hash")),
    ("build_environment", _("build environment")),
    ("build_workspace", _("build workspace")),
    ("build_tools", _("build tools")),
)


def validate_create(invocation: Invocation) -> list[api.MCUHomeError]:
    """What ``--public-key`` names has to be a public key, and readable.

    The file is read here, in the validate phase, and the text is kept
    for the run: a file that is missing, a directory, or bytes that are
    not text is a wrong invocation — exit 2, with the resolution of the
    pins never started — rather than an exception out of the middle of a
    command that has already written something. A **private** key is
    refused by name: the point of the flag is that the private half stays
    where it is, and a context never carries one.
    """
    stated = invocation.flag("public_key")
    if stated is None:
        return []
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
                    "a context carries the public half alone, so that whoever builds it never "
                    "has the key. Write that half out and pass it:\n"
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
    invocation.args.public_key_text = text
    return []


def context_create(invocation: Invocation) -> int:
    """``mcuhome context create <device> --out-dir <directory>``.

    Resolves every pin, writes the context and locks it. What it has to
    say while it runs are the log lines the resolution reports, on
    stderr; it states no stages, because the pins are resolved in one
    step whose parts are not a vocabulary anybody promised.
    """
    output = invocation.output
    project, entry = api.resolve_device(
        str(invocation.flag("device")),
        env=invocation.env,
        cwd=invocation.cwd,
        project_dir=invocation.project_dir,
    )
    settings = invocation.settings(project=project)
    options = api.resolve_build_options(settings)
    model = api.load_model(
        entry, project=project, on_warning=lambda finding: output.finding(finding.to_dict())
    )
    out_dir = _path(invocation, str(invocation.flag("out_dir")))
    signing_pub = _public_key(invocation, settings, project)
    invocation.start()
    # Beside the context rather than in a temporary directory of the
    # system's: the SDK package is unpacked here to be read out of bytes
    # that were verified, and that belongs on the same filesystem the
    # context is being written on. The name is MCUHome's own, hidden and
    # prefixed like every other file it writes into a user's directory,
    # so one left behind by an interrupted run is a leftover and goes
    # with this one.
    work_root = out_dir.parent / f".mcuhome-{out_dir.name}-work"
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        api.create_context(
            model,
            out_dir=out_dir,
            work_root=work_root,
            options=options,
            signing_pub=signing_pub,
            # The project's trust anchors lie under its root. A stand-in
            # root, invented for a device file with no project above it,
            # is not one MCUHome ever wrote anchors into.
            project_root=project.root if project.discovered else None,
            registries=settings.value("registry"),
            on_line=output.log,
        )
        # Locking is the act of whoever builds the context: it hashes
        # what is in the directory and writes the identity down.
        api.lock_context(out_dir)
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    facts = api.read_context_facts(out_dir)
    output.human(
        f"{output.style('✓', GREEN, BOLD)} "
        + _("Wrote the context to {path}.").format(path=output.path(out_dir))
    )
    output.human()
    _print_facts(facts, chain="", output=output)
    output.result({"ok": True, "out_dir": str(out_dir), "context": facts})
    return EXIT_OK


def context_verify(invocation: Invocation) -> int:
    """``mcuhome context verify <directory>``: do the bytes still match?

    One of the commands that can answer negatively: a context whose
    bytes no longer match what its manifest declares is this command's
    own document with ``ok`` false and every disagreement in
    ``mismatches`` — the run happened and the answer is no.
    """
    output = invocation.output
    root = _path(invocation, str(invocation.flag("directory")))
    invocation.start()
    verification = api.verify_context(root)
    _print_verification(verification, output=output)
    output.result(verification.to_dict())
    return EXIT_OK if verification.ok else EXIT_FAILURE


def context_print(invocation: Invocation) -> int:
    """``mcuhome context print <directory>``: what the context holds.

    The generator chain is read in every mode and rendered in one:
    ``human`` prints it the way a manifest states it, and the document
    is the facts alone. Reading it in every mode is what keeps the
    command's answer the same whoever asked — a directory this version
    cannot read is refused in all three, rather than in the one that
    happens to render the chain.
    """
    output = invocation.output
    root = _path(invocation, str(invocation.flag("directory")))
    invocation.start()
    facts = api.read_context_facts(root)
    chain = api.format_generator_chain(api.read_generator_chain(root / api.BUILD_CONTEXT_FILE))
    _print_facts(facts, chain=chain, output=output)
    output.result({"ok": True, "context": facts})
    return EXIT_OK


# -- what these commands share ------------------------------------------


def _path(invocation: Invocation, text: str) -> Path:
    """One path a flag or a positional carried: ``~`` expanded, absolute."""
    path = api.expand_user_path(text, env=invocation.env)
    if not path.is_absolute():
        path = invocation.cwd / path
    return path.resolve()


def _public_key(invocation: Invocation, settings: api.Settings, project: api.Project) -> str:
    """The **public** PEM the context carries, and where it came from.

    ``--public-key`` names a file — the half to write into the context
    when the private key is somewhere else, which is what a person
    building on one machine and signing on another has. Its text was
    read and checked in the validate phase, so the act starts with a key
    rather than with a path it still has to open. Without the flag the
    key this invocation resolves is read and its public half derived, in
    memory: a context never carries a private key.
    """
    stated = invocation.flag("public_key_text")
    if stated is not None:
        return str(stated)
    key = api.resolve_signing_key(
        settings.value("signing.key"), env=invocation.env, project=project
    )
    return api.public_key_pem(key.pem)


# -- the human renderings -----------------------------------------------


def _print_facts(facts: dict[str, Any], *, chain: str, output: Output) -> None:
    """What a context holds, one fact per line.

    Display material: what this version knows how to name is named, the
    counts are counts, and the patches are listed because a patched
    context builds different firmware from an unpatched one.
    """
    if output.machine:
        return
    rows: list[list[str | Cell]] = []
    for key, label in _FACT_LABELS:
        if key in facts:
            rows.append([Cell(label, (DIM,)), str(facts[key])])
    if "files" in facts:
        rows.append([Cell(_("files"), (DIM,)), str(facts["files"])])
    patches = facts.get("patches") or []
    rows.append(
        [Cell(_("patches"), (DIM,)), ", ".join(str(patch) for patch in patches) if patches else "—"]
    )
    if chain:
        rows.append([Cell(_("generator"), (DIM,)), chain])
    output.human(format_table(rows, output=output))
    if "id" not in facts:
        output.human()
        output.human(
            output.muted(_("This context is not locked yet; its identity is computed when it is."))
        )


def _print_verification(verification: api.ContextVerification, *, output: Output) -> None:
    """Whether the bytes still match, and every disagreement where they do not."""
    if output.machine:
        return
    if verification.ok:
        output.human(
            f"{output.style('✓', GREEN, BOLD)} "
            + _("{path} holds what it declares ({id}).").format(
                path=output.path(verification.root), id=verification.declared_id
            )
        )
        return
    output.human(
        f"{output.style('✗', RED, BOLD)} "
        + _("{path} no longer holds what it declares:").format(path=output.path(verification.root))
    )
    for problem in verification.problems():
        output.human(f"  {problem}")
