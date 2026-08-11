# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The third rung of the remote-build ladder: configured build servers (E63).

E53 fixed the ladder — ``--server``/``--token``, then
``MCUHOME_BUILD_SERVER``/``MCUHOME_BUILD_TOKEN``, then a file under
``$XDG_CONFIG_HOME/mcuhome/`` — and left the file undecided. E63 decided
it, and this module is the whole of it::

    $XDG_CONFIG_HOME/mcuhome/build-servers.toml
    $XDG_CONFIG_HOME/mcuhome/tokens/<label>

The file names servers; the token files hold secrets. That split is the
point of the shape, not an accident of it: a configuration file is
something a user pastes into a bug report, copies to a second machine or
puts in a dotfiles repository, and a bearer token in it would travel with
every one of those. Two files, and only one of them has to be kept::

    default = "home"

    [server.home]
    url = "wss://build.lan:8443/session"

    [server.laptop]
    url = "ws://127.0.0.1:8080/session"

``--server``/``MCUHOME_BUILD_SERVER`` take **a label or a URL**, and the
two are told apart by the one thing that cannot be mistaken: a URL has a
scheme (:func:`looks_like_url`), a table key never does — which is why an
``url =`` without a scheme is refused here rather than stored. A label
brings its token with it; a URL uses whatever the two rungs above
supplied, exactly as before, and never touches this file at all.

**Nothing here writes.** The file is the user's, or a dashboard's; the
command line reads it with :mod:`tomllib` and says precisely what is
wrong when it cannot. An unknown key is a warning rather than a refusal —
the file has two authors, and a dashboard that writes an option a
released command line predates must not take that user's build down over
a key that changes nothing. A typo still gets said out loud.

**Token file permissions** follow the signing key's stance
(:mod:`mcuhome.workbench.signing`): owner-only is what MCUHome writes and
what it expects, and a file that is group- or world-readable is reported
loudly rather than refused — refusing would make the command line
stricter about a bearer token than it is about the private signing key,
which is the graver secret. The warning names the exact ``chmod``.

A trailing newline is not part of a token: the file is edited by hand,
every editor writes one, and a bearer token contains no whitespace
anyway. Surrounding whitespace is stripped; whitespace *inside* the token
is a refusal, because that is a file with something else in it and a
header with a newline in it is not a thing to send.
"""

from __future__ import annotations

import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from mcuhome.model.errors import ConfigError, Location
from mcuhome.model.userpaths import config_dir

__all__ = [
    "CONFIG_FILE",
    "DEFAULT_KEY",
    "Resolution",
    "SERVER_TABLE",
    "TOKENS_DIR",
    "URL_KEY",
    "config_path",
    "looks_like_url",
    "resolve",
    "token_path",
]

#: The file, under the MCUHome configuration directory.
CONFIG_FILE = "build-servers.toml"

#: Directory holding one token file per label, beside it.
TOKENS_DIR = "tokens"

#: Table holding one sub-table per build server, and the top-level key
#: naming the one used when nothing else says.
SERVER_TABLE = "server"
DEFAULT_KEY = "default"

#: The one key a server table must carry today. Everything else in it is
#: room for later, and warned about rather than refused.
URL_KEY = "url"

#: A URL scheme per RFC 3986 §3.1, followed by an authority. This is the
#: whole label/URL discrimination: a value that matches is a URL, and a
#: value that does not is a label.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")

#: Label characters that would make a token file name mean something
#: other than a file in :data:`TOKENS_DIR`.
_ILLEGAL_LABEL_PARTS = ("/", "\\", "\x00")


def looks_like_url(value: str) -> bool:
    """Whether *value* is a URL rather than a label (E63).

    Deliberately one rule, stated once: a URL carries a scheme and ``://``
    and a label carries neither, so ``wss://build.lan/session`` is an
    address and ``build-lan`` is a name. Anything in between — ``ws:/x``,
    ``//host`` — is a label, and looked up as one, which fails by naming
    the labels that do exist rather than by dialling something odd.
    """
    return bool(_SCHEME.match(value))


@dataclass(frozen=True)
class Resolution:
    """What the ladder resolved to, and anything it wants said out loud.

    *url* is ``None`` when no rung answered — there is no default build
    server, so that is not an error here; it is what makes
    :class:`~mcuhome.workbench.api.RemoteNotConfigured` the refusal a
    user sees, with all three rungs named in one place.
    """

    url: str | None = None
    token: str | None = None
    label: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ServerFile:
    """The parsed file: which servers it names, and which one by default."""

    path: Path
    default: str | None
    urls: dict[str, str]
    warnings: tuple[str, ...]


# --------------------------------------------------------------------------
# Where the files are
# --------------------------------------------------------------------------


def config_path(env: dict[str, str]) -> Path:
    """``$XDG_CONFIG_HOME/mcuhome/build-servers.toml``, from a stated *env*."""
    return config_dir(env) / CONFIG_FILE


def token_path(label: str, env: dict[str, str]) -> Path:
    """``$XDG_CONFIG_HOME/mcuhome/tokens/<label>``, from a stated *env*."""
    return config_dir(env) / TOKENS_DIR / label


# --------------------------------------------------------------------------
# The ladder's third rung
# --------------------------------------------------------------------------


def resolve(named: str | None, *, token: str | None, env: dict[str, str]) -> Resolution:
    """The build server to use, from *named* and the configured file.

    *named* is whatever the two rungs above produced — a flag, then the
    environment variable — and *token* likewise. Four cases, in the order
    they are decided:

    1. *named* is a URL: it is the answer, *token* travels with it, and
       the file is never opened.
    2. *named* is a label: the file must exist and must know it, and the
       label's token file is read unless *token* already answered.
    3. *named* is absent and the file names a ``default``: that label,
       resolved exactly as in 2.
    4. *named* is absent and there is no file (or it configures no server
       at all): nothing is resolved, and the caller refuses with the
       ladder in hand.
    """
    if named:
        if looks_like_url(named):
            return Resolution(url=named, token=token)
        return _from_label(_read_required(named, env), named, token=token, env=env)

    configured = _read_optional(env)
    if configured is None or not configured.urls:
        return Resolution(token=token, warnings=() if configured is None else configured.warnings)
    if configured.default is None:
        raise _refuse_no_default(configured)
    return _from_label(configured, configured.default, token=token, env=env, by_default=True)


def _from_label(
    configured: _ServerFile,
    label: str,
    *,
    token: str | None,
    env: dict[str, str],
    by_default: bool = False,
) -> Resolution:
    url = configured.urls.get(label)
    if url is None:
        raise _refuse_unknown_label(configured, label, by_default=by_default)
    resolved, warnings = _token_for(label, given=token, env=env)
    return Resolution(
        url=url,
        token=resolved,
        label=label,
        warnings=configured.warnings + warnings,
    )


# --------------------------------------------------------------------------
# Reading the file
# --------------------------------------------------------------------------


def _read_optional(env: dict[str, str]) -> _ServerFile | None:
    """The configured file, or ``None`` when this environment has none.

    "None" covers two things that are the same thing to a caller who did
    not name a server: there is no such file, and there is no home
    directory to look for one in — a service started without a login
    session has no ``HOME``, and a ladder rung that cannot be reached is
    an unanswered rung, not a failure. Naming a label *is* asking for the
    file, which is why :func:`_read_required` lets that refusal through.
    """
    try:
        path = config_path(env)
    except ConfigError:
        return None
    if not path.exists():
        return None
    return _read(path)


def _read_required(named: str, env: dict[str, str]) -> _ServerFile:
    path = config_path(env)
    if not path.exists():
        raise ConfigError(
            f'"{named}" is not a URL, so MCUHome looked for a build server by that '
            f"name — and there is no {CONFIG_FILE} to look in.",
            location=Location(file=path),
            hint=(
                f"either give the address itself (--server wss://host/session), or "
                f"write that file:\n"
                f'    default = "{named}"\n\n'
                f"    [{SERVER_TABLE}.{named}]\n"
                f'    {URL_KEY} = "wss://host:8443/session"\n'
                f"A name has a scheme nowhere in it; an address always has one."
            ),
        )
    return _read(path)


def _read(path: Path) -> _ServerFile:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except OSError as error:
        raise ConfigError(
            f"MCUHome cannot read the build-server file: {error.strerror or 'unreadable'}.",
            location=Location(file=path),
            hint=(
                "this file names the build servers the remote method can use "
                "(E63). Make it readable, or move it aside and name the server "
                "with --server."
            ),
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(
            f"The build-server file is not valid TOML: {error}.",
            location=Location(file=path),
            hint=(
                "one table per build server, and the label used when nothing "
                "else says:\n"
                '    default = "home"\n\n'
                f"    [{SERVER_TABLE}.home]\n"
                f'    {URL_KEY} = "wss://build.lan:8443/session"'
            ),
        ) from error

    warnings: list[str] = []
    for key in document:
        if key not in (DEFAULT_KEY, SERVER_TABLE):
            warnings.append(
                _warning(
                    f'{path} carries an unknown top-level key "{key}", which MCUHome ignores.',
                    f'the keys here are "{DEFAULT_KEY}" and the [{SERVER_TABLE}.<label>] tables',
                )
            )

    default = document.get(DEFAULT_KEY)
    if default is not None and not isinstance(default, str):
        raise _refuse_type(path, DEFAULT_KEY, "the label of one of the servers below", default)

    servers = document.get(SERVER_TABLE, {})
    if not isinstance(servers, dict):
        raise _refuse_type(path, SERVER_TABLE, "a table per build server", servers)

    urls: dict[str, str] = {}
    for label, table in servers.items():
        _check_label(path, label)
        if not isinstance(table, dict):
            raise _refuse_type(path, f"{SERVER_TABLE}.{label}", "a table", table)
        urls[label] = _url_of(path, label, table)
        warnings.extend(
            _warning(
                f'{path} carries an unknown key "{key}" under [{SERVER_TABLE}.{label}], '
                f"which MCUHome ignores.",
                f'the only key a build server takes today is "{URL_KEY}"',
            )
            for key in table
            if key != URL_KEY
        )

    return _ServerFile(path=path, default=default, urls=urls, warnings=tuple(warnings))


def _check_label(path: Path, label: str) -> None:
    """A label is a name, and a name is also a token file's name."""
    if looks_like_url(label):
        # Before the general check below, which a URL also fails: this is
        # the mistake the reader made, and the one worth naming.
        raise ConfigError(
            f'"{label}" is a URL rather than a name for a build server.',
            location=Location(file=path, key=f"{SERVER_TABLE}.{label}"),
            hint=(
                f"the label is the short name you type after --server; the address "
                f'goes inside the table: [{SERVER_TABLE}.home] with {URL_KEY} = "{label}"'
            ),
        )
    if not label or label in (".", "..") or any(part in label for part in _ILLEGAL_LABEL_PARTS):
        raise ConfigError(
            f'"{label}" is not a usable name for a build server.',
            location=Location(file=path, key=f"{SERVER_TABLE}.{label}"),
            hint=(
                f"a label names the server on the command line and names its token "
                f"file in {TOKENS_DIR}/, so it cannot be empty and cannot contain a "
                f"path separator. Pick a plain name: [{SERVER_TABLE}.home]"
            ),
        )


def _url_of(path: Path, label: str, table: dict[str, object]) -> str:
    url = table.get(URL_KEY)
    if url is None:
        raise ConfigError(
            f'The build server "{label}" has no {URL_KEY}.',
            location=Location(file=path, key=f"{SERVER_TABLE}.{label}"),
            hint=f'add the address it is reachable at: {URL_KEY} = "wss://host:8443/session"',
        )
    if not isinstance(url, str) or not url.strip():
        key = f"{SERVER_TABLE}.{label}.{URL_KEY}"
        raise _refuse_type(path, key, "the address as a string", url)
    if not looks_like_url(url):
        raise ConfigError(
            f'The build server "{label}" has an address without a scheme: "{url}".',
            location=Location(file=path, key=f"{SERVER_TABLE}.{label}.{URL_KEY}"),
            hint=(
                f'write the whole address: {URL_KEY} = "wss://{url}/session" — "wss://" '
                'for TLS, and "ws://" only on a network you own, because the token '
                "travels on it"
            ),
        )
    return url


# --------------------------------------------------------------------------
# Reading a token
# --------------------------------------------------------------------------


def _token_for(
    label: str, *, given: str | None, env: dict[str, str]
) -> tuple[str, tuple[str, ...]]:
    """The token for *label*: the rung above, or the label's own file.

    A label without a token is refused rather than sent unauthenticated:
    a MCUHome build server has no configuration in which it listens
    without a bearer token, so an anonymous session is not a thing that
    can work — it is a 401 ten seconds later, in a place where the fix is
    much harder to name.
    """
    if given:
        return given, ()

    path = token_path(label, env)
    if not path.is_file():
        raise ConfigError(
            f'The build server "{label}" has no token file.',
            location=Location(file=path),
            hint=(
                f"write the bearer token that server issued into {path}, readable by "
                f"nobody else:\n"
                f"    (umask 177; printf %s '<token>' > {path})\n"
                f"--token and MCUHOME_BUILD_TOKEN name one instead. A build server "
                f"always requires a token; there is no configuration in which it "
                f"listens without one."
            ),
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        reason = getattr(error, "strerror", None) or "it is not text"
        raise ConfigError(
            f'MCUHome cannot read the token of build server "{label}": {reason}.',
            location=Location(file=path),
            hint=(
                f"the file holds the bearer token and nothing else. Rewrite it:\n"
                f"    (umask 177; printf %s '<token>' > {path})"
            ),
        ) from error

    warnings: list[str] = []
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        warnings.append(
            _warning(
                f'the token of build server "{label}" is readable by other accounts '
                f"on this machine (mode {mode:04o}): {path}.",
                f"chmod 600 {path} — a build-server token is a bearer credential: "
                f"whoever holds it can build on that server, as you",
            )
        )

    token = text.strip()
    if not token:
        raise ConfigError(
            f'The token file of build server "{label}" is empty.',
            location=Location(file=path),
            hint=(
                f"write the bearer token that server issued into it:\n"
                f"    (umask 177; printf %s '<token>' > {path})"
            ),
        )
    if any(character.isspace() for character in token):
        raise ConfigError(
            f'The token file of build server "{label}" holds more than a token.',
            location=Location(file=path),
            hint=(
                f"a bearer token contains no spaces and no line breaks — a trailing "
                f"newline is fine and ignored, anything else is something else in "
                f"the file. Rewrite it:\n"
                f"    (umask 177; printf %s '<token>' > {path})"
            ),
        )
    return token, tuple(warnings)


# --------------------------------------------------------------------------
# Refusals and warnings
# --------------------------------------------------------------------------


def _refuse_no_default(configured: _ServerFile) -> ConfigError:
    return ConfigError(
        "The build-server file configures build servers but names no default, and no "
        "--server said which one to use.",
        location=Location(file=configured.path),
        hint=(
            f'name one at the top of the file — {DEFAULT_KEY} = "'
            f'{next(iter(configured.urls))}" — or pass --server <label> for this '
            f"build. Configured: {_labels(configured)}."
        ),
    )


def _refuse_unknown_label(configured: _ServerFile, label: str, *, by_default: bool) -> ConfigError:
    what = (
        f'The build-server file sets {DEFAULT_KEY} = "{label}"'
        if by_default
        else f'"{label}" is not a build server MCUHome is configured with'
    )
    return ConfigError(
        f"{what}, and there is no [{SERVER_TABLE}.{label}] table.",
        location=Location(file=configured.path, key=f"{SERVER_TABLE}.{label}"),
        hint=(
            f"configured build servers: {_labels(configured)}. Add the table, fix the "
            f"name, or give the address itself — --server wss://host/session."
        ),
    )


def _refuse_type(path: Path, key: str, expected: str, value: object) -> ConfigError:
    return ConfigError(
        f'"{key}" in the build-server file must be {expected}, and this is {type(value).__name__}.',
        location=Location(file=path, key=key),
        hint=(
            "one table per build server, and the label used when nothing else says:\n"
            '    default = "home"\n\n'
            f"    [{SERVER_TABLE}.home]\n"
            f'    {URL_KEY} = "wss://build.lan:8443/session"'
        ),
    )


def _labels(configured: _ServerFile) -> str:
    return ", ".join(sorted(configured.urls)) or "none"


def _warning(message: str, fix: str) -> str:
    """One warning, in the shape :meth:`ConfigError.render` uses for errors.

    Same three questions, same layout, different first word: a user reads
    both on the same terminal and should not have to learn two forms.
    """
    return f"Warning: {message}\n  Fix: {fix}"
