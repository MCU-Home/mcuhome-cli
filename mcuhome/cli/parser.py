# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The command tree: every area, every act, every flag.

``mcuhome <area> <act>`` — the area is the singular noun of the thing
acted on, the act is the verb, and there is no third level. ``version``
is the one top-level command without an area, because it answers a
question about the installation rather than acting on anything.

The tree here **is** ``docs/cli.md``: the reference names every command
with its positionals and its flags, and ``tests/python/test_reference.py``
reads that document and asserts this parser matches it. A command that
is added here without being written there fails the suite, and so does
one written there and missing here.

Three kinds of flag meet on a command, and only the first is this
module's to name:

* the command's own (``--dry-run``, ``--scope``, ``--all``), named by
  the command-line scheme;
* one carrying an api **request field**, spelled by the field with ``_``
  as ``-`` and prefixed by its subject where the bare name would be
  ambiguous (``--build-server``, ``--build-server-token``);
* one carrying a configuration **option**, whose spelling is the
  workbench's own derivation and never this module's decision
  (:mod:`mcuhome.cli.optionflags`).

``-o/--output`` is read before any other argument is refused
(:func:`read_presentation`), so **every** run in a machine mode prints a
document — an unknown flag and a missing argument included — and
``-h``/``--help`` wins wherever it stands, because a person asking for
help has already said what they need.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mcuhome.workbench import api

from mcuhome.cli import (
    buildcommand,
    configcommands,
    devicecommands,
    devicedata,
    optionflags,
    projectcommands,
    secretcommands,
)
from mcuhome.cli import output as output_module
from mcuhome.cli.errors import UsageError
from mcuhome.cli.i18n import _
from mcuhome.cli.unavailable import refuses
from mcuhome.cli.versioncommand import version

__all__ = ["Presentation", "build_parser", "read_presentation"]

#: What a command that is not implemented in this version says it waits
#: on. The three platform acts name their own work instead.
_NOT_YET = _(
    "this version of MCUHome does not implement it yet; mcuhome version says which one is installed"
)


@dataclass(frozen=True)
class Presentation:
    """The three knobs that decide how a run is rendered.

    Read straight off the tokens, before the parse, so that a refusal of
    the parse itself is rendered the way the invocation asked for.
    """

    mode: str = output_module.HUMAN
    color: str = "auto"
    interactive: bool | None = None


def read_presentation(tokens: Sequence[str]) -> Presentation:
    """The output mode, the color rule and interactivity, before the parse.

    Only what stands before a ``--`` separator counts; after it a token
    is a value. A mode nobody offers is refused here, because the
    refusal has to be rendered somehow and this is what decides how.
    """
    examined = list(tokens[: tokens.index("--")]) if "--" in tokens else list(tokens)
    presentation = Presentation()
    index = 0
    while index < len(examined):
        token = examined[index]
        value: str | None = None
        if token.startswith("--output="):
            token, _sep, value = token.partition("=")
        elif token.startswith("-o") and token != "-o":
            token, value = "-o", token[2:]
        if token in ("-o", "--output"):
            if value is None:
                index += 1
                value = examined[index] if index < len(examined) else ""
            if value not in output_module.MODES:
                raise UsageError(
                    _("-o takes one of {modes}, not {value!r}.").format(
                        modes=", ".join(output_module.MODES), value=value
                    ),
                    hint=_(
                        "human renders for a person, json answers one document, "
                        "json-stream answers NDJSON as the run happens"
                    ),
                )
            presentation = Presentation(value, presentation.color, presentation.interactive)
        elif token == "--color":
            index += 1
            presentation = Presentation(
                presentation.mode,
                examined[index] if index < len(examined) else "",
                presentation.interactive,
            )
        elif token.startswith("--color="):
            presentation = Presentation(
                presentation.mode, token.partition("=")[2], presentation.interactive
            )
        elif token == "--interactive":
            presentation = Presentation(presentation.mode, presentation.color, True)
        elif token == "--no-interactive":
            presentation = Presentation(presentation.mode, presentation.color, False)
        index += 1
    if presentation.color not in ("auto", "always", "never"):
        raise UsageError(
            _("--color takes auto, always or never, not {value!r}.").format(
                value=presentation.color
            ),
            hint=_("auto means a terminal and no NO_COLOR"),
        )
    return presentation


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Usage lines that identify, not enumerate.

    The usage line carries what an invocation *must* say — positionals
    and required flags — and folds everything optional into
    ``[options]``; the grouped option list below has the detail.
    """

    def _format_usage(self, usage, actions, groups, prefix):  # noqa: ANN001, ANN202
        if usage is None:
            shown = [action for action in actions if not action.option_strings or action.required]
            if len(shown) < len(actions):
                text = super()._format_usage(None, shown, groups, prefix)
                return text.rstrip("\n") + " [options]\n\n"
        return super()._format_usage(usage, actions, groups, prefix)


class _Parser(argparse.ArgumentParser):
    """argparse, with this command line's help and refusal contract.

    A usage problem is a refusal like every other one: it is raised as
    :class:`~mcuhome.cli.errors.UsageError` rather than printed and
    exited on, so that a machine mode answers it as a document and the
    exit code is the contract's 2.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", _HelpFormatter)
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> Any:
        raise UsageError(
            message,
            hint=_("run {prog} --help for what this command takes.").format(prog=self.prog),
        )


def build_parser() -> argparse.ArgumentParser:
    """The whole tree, as the reference states it."""
    parser = _Parser(
        prog="mcuhome",
        description=_("Build Zephyr firmware from an MCUHome YAML device description."),
        epilog=_(
            "the workflow:\n"
            "  mcuhome project init            create a project directory\n"
            "  mcuhome signing create-key      draw the project's firmware key\n"
            "  mcuhome device new NAME         describe a device\n"
            "  mcuhome device build NAME       compile its firmware\n"
        ),
    )
    parser.add_argument("-h", "--help", action="help", help=_("show this help message and exit"))
    parser.add_argument(
        "--version",
        action="store_true",
        help=_("print what mcuhome version answers, as text"),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help=_("more detail in the human rendering")
    )
    areas = parser.add_subparsers(dest="area")

    _project(_area(areas, "project", _("create, describe and upgrade a project")))
    _config(_area(areas, "config", _("read and write the option registry")))
    _device(_area(areas, "device", _("the devices of a project")))
    _secret(_area(areas, "secret", _("the secrets of a project")))
    _signing(_area(areas, "signing", _("the firmware signing key of a project")))
    _context(_area(areas, "context", _("what a build is attributed to")))
    _environment(_area(areas, "environment", _("the compiler stack a build runs in")))
    _host(_area(areas, "host", _("what a build on this machine would need")))

    versions = _Area(areas, "").command(
        "version", _("which MCUHome packages are installed"), version
    )
    _finish(versions)
    return parser


# -- the areas ---------------------------------------------------------


def _project(sub: _Area) -> None:
    init = sub.command("init", _("create a project"), projectcommands.project_init)
    _positional(init, "directory", optional=True, help=_("where the project goes"))
    _switch(
        init,
        "--force",
        help=_("write into a directory that is not empty"),
        negated=_("refuse a directory that is not empty (the default)"),
    )
    _finish(init)

    info = sub.command(
        "info", _("where the project is and what it holds"), projectcommands.project_info
    )
    _positional(info, "directory", optional=True, help=_("the project to describe"))
    _finish(info)

    upgrade = sub.command(
        "upgrade", _("migrate a project to this layout"), projectcommands.project_upgrade
    )
    _positional(upgrade, "directory", optional=True, help=_("the project to migrate"))
    _value(upgrade, "--confirm", metavar="ID", help=_("the project's short id, as the approval"))
    _switch(upgrade, "--dry-run", help=_("show the plan and change nothing"))
    upgrade.set_defaults(
        interact=projectcommands.confirm_upgrade, validate=projectcommands.validate_upgrade
    )
    _finish(upgrade)


def _config(sub: _Area) -> None:
    printing = sub.command(
        "print", _("every option and where it came from"), configcommands.config_print
    )
    _finish(printing)

    getting = sub.command("get", _("one option's effective value"), configcommands.config_get)
    _positional(getting, "name", help=_("an option key, or one entry key of a map option"))
    _finish(getting)

    setting = sub.command("set", _("write one option into one file"), configcommands.config_set)
    _positional(setting, "name", help=_("an option key, or one entry key of a map option"))
    _positional(setting, "value", help=_("the value, parsed through the option's declaration"))
    _scope(setting)
    _finish(setting)

    unsetting = sub.command(
        "unset", _("remove one option from one file"), configcommands.config_unset
    )
    _positional(unsetting, "name", help=_("an option key, or one entry key of a map option"))
    _scope(unsetting)
    _finish(unsetting)


def _device(sub: _Area) -> None:
    new = sub.command("new", _("write a new device"), devicecommands.device_new)
    _positional(new, "device", help=_("the device's name"))
    _value(new, "--board", metavar="BOARD", required=True, help=_("the board it is built for"))
    _value(new, "--friendly-name", metavar="NAME", help=_("what a controller shows"))
    _switch(new, "--dry-run", help=_("print the file that would be written"))
    _finish(new)

    listing = sub.command(
        "list", _("the project's devices with their state"), devicecommands.device_list
    )
    _finish(listing)

    info = sub.command("info", _("one device in full"), devicecommands.device_info)
    _positional(info, "device", help=_("device name or path"))
    _finish(info)

    validate = sub.command(
        "validate", _("check a device configuration"), devicecommands.device_validate
    )
    _positional(validate, "device", help=_("device name or path"))
    _switch(validate, "--show-sensitive", help=_("print the commissioning credentials"))
    _finish(validate)

    build = sub.command("build", _("build firmware and sign it"), buildcommand.build)
    _positional(build, "device", optional=True, help=_("device name or path"))
    _value(build, "--model", metavar="PATH", help=_("a canonical model instead of a device"))
    _value(build, "--out-dir", metavar="PATH", help=_("the build directory"))
    _value(build, "--builder", metavar="NAME", help=_("the configured builder this build runs at"))
    _value(build, "--build-server", metavar="ADDRESS", help=_("a build server for this invocation"))
    _value(
        build,
        "--build-server-token",
        metavar="TOKEN",
        help=_("the credential for that server; - reads it from stdin"),
    )
    _value(
        build,
        "--container-image",
        metavar="IMAGE",
        help=_("the build environment this build asks for"),
    )
    _switch(
        build,
        "--wait-for-turn",
        help=_("wait when a build server has no room"),
        negated=_("refuse rather than wait when a build server has no room"),
    )
    _value(
        build,
        "--max-wait-seconds",
        metavar="SECONDS",
        help=_("the bound of that wait; 0 removes it"),
    )
    _value(build, "--public-key", metavar="PATH", help=_("the public half to compile in"))
    _switch(
        build,
        "--sign",
        help=_("sign the delivered image here"),
        negated=_("leave the delivered image unsigned"),
    )
    _build_options(build)
    build.set_defaults(validate=buildcommand.validate_build)
    _finish(build)

    generate = sub.command(
        "generate-application",
        _("write the standalone Zephyr application"),
        devicecommands.device_generate_application,
    )
    _positional(generate, "device", help=_("device name or path"))
    _value(generate, "--out-dir", metavar="PATH", required=True, help=_("where the tree goes"))
    _finish(generate)

    sign = sub.command(
        "sign-firmware",
        _("sign the image of a finished build"),
        devicecommands.device_sign_firmware,
    )
    _positional(sign, "device", help=_("device name or path"))
    _value(sign, "--out-dir", metavar="PATH", help=_("the build directory to sign in"))
    _switch(sign, "--dry-run", help=_("print every command signing would run"))
    optionflags.add_option_flags(sign, optionflags.signing_option_flags(), group=_("option flags"))
    sign.set_defaults(validate=devicecommands.validate_sign_firmware)
    _finish(sign)

    clean = sub.command("clean", _("remove what a build produced"), devicecommands.device_clean)
    _positional(clean, "device", optional=True, help=_("device name or path"))
    _switch(clean, "--all", help=_("every device of the project"))
    clean.set_defaults(validate=devicecommands.validate_clean)
    _finish(clean)

    rename = sub.command("rename", _("rename a device"), devicecommands.device_rename)
    _positional(rename, "device", help=_("the device's name"))
    _value(rename, "--to", metavar="NAME", required=True, help=_("the new name"))
    _finish(rename)

    delete = sub.command("delete", _("remove a device"), devicecommands.device_delete)
    _positional(delete, "device", help=_("the device's name"))
    _switch(
        delete,
        "--keep-secrets",
        help=_("keep the device's secrets file"),
        negated=_("remove the device's secrets file as well (the default)"),
    )
    _switch(delete, "--force", help=_("do not ask, even in an interactive run"))
    delete.set_defaults(interact=devicecommands.ask_before_deleting)
    _finish(delete)

    print_pairing = sub.command(
        "print-matter-pairing",
        _("a device's commissioning credentials"),
        devicecommands.device_print_matter_pairing,
    )
    _positional(print_pairing, "device", help=_("device name or path"))
    _finish(print_pairing)

    create_pairing = sub.command(
        "create-matter-pairing",
        _("draw commissioning credentials"),
        devicecommands.device_create_matter_pairing,
    )
    _positional(create_pairing, "device", help=_("device name or path"))
    _switch(
        create_pairing,
        "--force",
        help=_("replace credentials it already has"),
        negated=_("refuse credentials that are already there (the default)"),
    )
    create_pairing.set_defaults(interact=devicecommands.ask_before_replacing_pairing)
    _finish(create_pairing)

    schema = sub.command(
        "print-schema",
        _("the JSON Schema of a device file"),
        devicedata.device_print_schema,
    )
    _finish(schema)

    boards = sub.command(
        "list-boards",
        _("the boards MCUHome can build for"),
        devicedata.device_list_boards,
    )
    _finish(boards)

    supported = sub.command(
        "list-supported",
        _("everything MCUHome knows about hardware and Matter"),
        devicedata.device_list_supported,
    )
    _finish(supported)

    flash = sub.command(
        "flash",
        _("put a built image on the board"),
        refuses(
            "device flash",
            waits_on=_("flashing a built image over the serial recovery path is not there yet"),
        ),
    )
    _positional(flash, "device", help=_("device name or path"))
    _value(
        flash,
        "--flash-mode",
        metavar="MODE",
        choices=("recovery", "ota"),
        help=_("how the image reaches the board"),
    )
    _finish(flash)

    bootloader = sub.command(
        "install-bootloader",
        _("prepare a board for MCUHome"),
        refuses(
            "device install-bootloader",
            waits_on=_(
                "the one-time board preparation with the vendor's own tooling is not there yet"
            ),
        ),
    )
    _positional(bootloader, "device", help=_("device name or path"))
    _finish(bootloader)


def _secret(sub: _Area) -> None:
    scopes = sub.command(
        "list-scopes",
        _("every scope this project could have"),
        secretcommands.secret_list_scopes,
    )
    _finish(scopes)

    listing = sub.command("list", _("the keys of one scope, masked"), secretcommands.secret_list)
    _scope_of_secret(listing)
    _finish(listing)

    reveal = sub.command("reveal", _("one secret's value"), secretcommands.secret_reveal)
    _value(reveal, "--key", metavar="KEY", required=True, help=_("the key to answer"))
    _scope_of_secret(reveal)
    _finish(reveal)

    setting = sub.command("set", _("write one key"), secretcommands.secret_set)
    _value(setting, "--key", metavar="KEY", required=True, help=_("the key to write"))
    _value(
        setting,
        "--value",
        metavar="VALUE",
        required=True,
        help=_("the value; - reads it from stdin"),
    )
    _scope_of_secret(setting)
    setting.set_defaults(validate=secretcommands.validate_set)
    _finish(setting)

    unsetting = sub.command("unset", _("remove one key"), secretcommands.secret_unset)
    _value(unsetting, "--key", metavar="KEY", required=True, help=_("the key to remove"))
    _scope_of_secret(unsetting)
    _finish(unsetting)

    deleting = sub.command("delete", _("remove a whole secrets file"), secretcommands.secret_delete)
    _scope_of_secret(deleting, required=True)
    _finish(deleting)


def _signing(sub: _Area) -> None:
    public = sub.command(
        "print-public-key",
        _("the public half of the signing key"),
        refuses("signing print-public-key", waits_on=_NOT_YET),
    )
    optionflags.add_option_flags(public, _one_option("signing.key"), group=_("option flags"))
    _finish(public)

    create = sub.command(
        "create-key",
        _("draw the project's signing key"),
        refuses("signing create-key", waits_on=_NOT_YET),
    )
    optionflags.add_option_flags(create, _one_option("signing.key"), group=_("option flags"))
    _finish(create)


def _context(sub: _Area) -> None:
    create = sub.command(
        "create",
        _("write a locked build context"),
        refuses("context create", waits_on=_NOT_YET),
    )
    _positional(create, "device", help=_("device name or path"))
    _value(create, "--out-dir", metavar="PATH", required=True, help=_("where the context goes"))
    _value(create, "--public-key", metavar="PATH", help=_("the public half to write into it"))
    _build_options(create, signing=_one_option("signing.key"))
    _finish(create)

    verify = sub.command(
        "verify",
        _("check a context against its manifest"),
        refuses("context verify", waits_on=_NOT_YET),
    )
    _positional(verify, "directory", help=_("the context directory"))
    _finish(verify)

    printing = sub.command(
        "print", _("what a context holds"), refuses("context print", waits_on=_NOT_YET)
    )
    _positional(printing, "directory", help=_("the context directory"))
    _finish(printing)


def _environment(sub: _Area) -> None:
    provision = sub.command(
        "provision",
        _("put a build-environment package into the store"),
        refuses("environment provision", waits_on=_NOT_YET),
    )
    _positional(provision, "package", help=_("a package file, or a name with a constraint"))
    _build_options(provision, signing=())
    _finish(provision)


def _host(sub: _Area) -> None:
    check = sub.command(
        "check",
        _("what a build on this machine would need"),
        refuses("host check", waits_on=_NOT_YET),
    )
    _build_options(check, signing=_one_option("signing.imgtool"))
    _finish(check)


# -- the building blocks ----------------------------------------------


@dataclass(frozen=True)
class _Area:
    """One area of the tree, and the acts registered under it."""

    sub: Any
    name: str

    def command(self, act: str, help: str, handler: Any) -> argparse.ArgumentParser:
        """One command: its own parser, its handler, and the task it is.

        *task* is the command as typed without its flags, which is what
        the stream's `start` message names it by.
        """
        parser = self.sub.add_parser(act, help=help, description=help)
        parser.set_defaults(handler=handler, task=f"{self.name} {act}".strip())
        return parser


def _area(areas: Any, name: str, help: str) -> _Area:
    """One area, whose own help is what ``mcuhome <area>`` prints."""
    parser = areas.add_parser(name, help=help, description=help)
    parser.add_argument("-h", "--help", action="help", help=_("show this help message and exit"))
    parser.set_defaults(show_help=parser.print_help)
    return _Area(parser.add_subparsers(dest="act"), name)


def _positional(
    parser: argparse.ArgumentParser, name: str, *, help: str, optional: bool = False
) -> None:
    parser.add_argument(name, nargs="?" if optional else None, default=None, help=help)


def _switch(
    parser: argparse.ArgumentParser, spelling: str, *, help: str, negated: str = ""
) -> None:
    """A flag with no value.

    *negated* is the help of the ``--no-x`` spelling, and giving it is
    what creates that spelling. Both exist for a flag carrying a request
    field, and for a flag of the command's own where the default is
    "on": "not used" and "turned off" are different statements, and
    where the default is off there is nothing for ``--no-x`` to name.
    Both are listed in the help, because a spelling a person cannot find
    is a spelling they do not have.
    """
    dest = spelling[2:].replace("-", "_")
    parser.add_argument(spelling, dest=dest, action="store_true", default=None, help=help)
    if negated:
        parser.add_argument(f"--no-{spelling[2:]}", dest=dest, action="store_false", help=negated)


def _value(
    parser: argparse.ArgumentParser,
    spelling: str,
    *,
    metavar: str,
    help: str,
    required: bool = False,
    choices: Sequence[str] | None = None,
    default: str | None = None,
) -> None:
    parser.add_argument(
        spelling,
        dest=spelling[2:].replace("-", "_"),
        metavar=metavar,
        required=required,
        choices=list(choices) if choices else None,
        default=default,
        help=help,
    )


def _scope(parser: argparse.ArgumentParser) -> None:
    """``--scope`` — which of the three configuration files is edited."""
    _value(
        parser,
        "--scope",
        metavar="SCOPE",
        choices=api.CONFIG_SCOPES,
        default="project",
        help=_("which configuration file is written: project (default), user, system"),
    )


def _scope_of_secret(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    """``--kind`` and ``--name`` — the scope every secret command takes."""
    _value(
        parser,
        "--kind",
        metavar="KIND",
        choices=api.SECRET_KINDS,
        required=required,
        default=None if required else "main",
        help=_("which secrets file: main (default), device, builder, signing"),
    )
    _value(
        parser,
        "--name",
        metavar="NAME",
        required=required,
        help=_("the device or the builder the file belongs to"),
    )


def _one_option(name: str) -> tuple[optionflags.OptionFlag, ...]:
    """One option's flag, for a command that offers exactly that one."""
    return tuple(flag for flag in optionflags.option_flags() if flag.option.name == name)


def _build_options(
    parser: argparse.ArgumentParser,
    *,
    signing: Sequence[optionflags.OptionFlag] | None = None,
) -> None:
    """The build option flags, and the signing ones a command also offers."""
    flags = list(optionflags.build_option_flags())
    flags.extend(optionflags.signing_option_flags() if signing is None else signing)
    optionflags.add_option_flags(parser, flags, group=_("option flags"))


def _finish(parser: argparse.ArgumentParser) -> None:
    """The flags every command takes, listed after its own.

    ``--project-dir`` is among them and is the one option flag every
    command offers: where the project is, instead of the upward search
    from the working directory.
    """
    general = parser.add_argument_group(_("general options"))
    general.add_argument("-h", "--help", action="help", help=_("show this help message and exit"))
    general.add_argument(
        "-o",
        "--output",
        dest="output_mode",
        choices=output_module.MODES,
        default=output_module.HUMAN,
        metavar="FORMAT",
        help=_(
            "human (the default) renders for a person, json answers one document when the "
            "run is over, json-stream answers NDJSON as it runs"
        ),
    )
    general.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help=_("more detail in the human rendering"),
    )
    general.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help=_("color in the human rendering; auto is a terminal and no NO_COLOR"),
    )
    general.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        default=None,
        help=_("ask the command's questions even without a terminal"),
    )
    general.add_argument(
        "--no-interactive",
        dest="interactive",
        action="store_false",
        help=_("never ask; a machine mode is never interactive anyway"),
    )
    optionflags.add_option_flags(general, optionflags.project_option_flags())
