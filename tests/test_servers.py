# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The configured build servers: ``build-servers.toml`` and its tokens (E63).

The third rung of E53's ladder, on its own. What the ladder *as a whole*
does — flag, then variable, then this — is asserted in
``test_cli.py``; here the file itself is the subject: what it may say,
what it may not, and what a user is told when it says something else.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcuhome.model.errors import ConfigError

from mcuhome_cli import servers

TOKEN = "s3cret-bearer-token"


def _configure(
    tmp_path: Path,
    *,
    toml: str | None = None,
    tokens: dict[str, str] | None = None,
    mode: int = 0o600,
) -> dict[str, str]:
    """A configuration directory, and the environment that names it."""
    config_home = tmp_path / "xdg"
    directory = config_home / "mcuhome"
    directory.mkdir(parents=True, exist_ok=True)
    if toml is not None:
        (directory / servers.CONFIG_FILE).write_text(toml, encoding="utf-8")
    for label, token in (tokens or {}).items():
        token_dir = directory / servers.TOKENS_DIR
        token_dir.mkdir(exist_ok=True)
        path = token_dir / label
        path.write_text(token, encoding="utf-8")
        path.chmod(mode)
    return {"XDG_CONFIG_HOME": str(config_home)}


TWO_SERVERS = """\
default = "home"

[server.home]
url = "wss://build.lan:8443/ws"

[server.laptop]
url = "ws://127.0.0.1:8080/ws"
"""


# --------------------------------------------------------------------------
# A label is not a URL, and that is the whole discrimination
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "wss://build.lan:8443/ws",
        "ws://127.0.0.1:8080/ws",
        "https://build.example/ws",
        "unix+ws://socket/ws",
    ],
)
def test_an_address_is_recognised_by_its_scheme(value) -> None:
    assert servers.looks_like_url(value)


@pytest.mark.parametrize(
    "value",
    ["home", "build-lan", "laptop2", "wss", "ws:/typo", "//host/ws", "a.b.c"],
)
def test_everything_without_a_scheme_is_a_label(value) -> None:
    """Including the near-misses: they are looked up, never dialled.

    ``ws:/typo`` and ``//host/ws`` are not addresses a socket can be
    opened on, so treating them as labels turns a typo into "not a build
    server MCUHome is configured with" plus the list of ones that are —
    which is the message that leads to the fix.
    """
    assert not servers.looks_like_url(value)


def test_a_url_is_used_as_given_and_never_opens_the_file(tmp_path) -> None:
    """Rung 1 and 2 behave exactly as before E63: address in, address out."""
    env = _configure(tmp_path, toml="this is not TOML at all {{{")
    resolution = servers.resolve("wss://direct.example/ws", token="from-the-flag", env=env)
    assert resolution == servers.Resolution(
        url="wss://direct.example/ws", token="from-the-flag"
    )


def test_a_url_without_a_token_stays_without_one(tmp_path) -> None:
    """A server named by address uses --token, the variable, or nothing."""
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": TOKEN})
    assert servers.resolve("wss://direct.example/ws", token=None, env=env).token is None


# --------------------------------------------------------------------------
# A label brings its token with it
# --------------------------------------------------------------------------


def test_a_label_resolves_to_its_url_and_its_token(tmp_path) -> None:
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"laptop": TOKEN})
    resolution = servers.resolve("laptop", token=None, env=env)
    assert resolution.url == "ws://127.0.0.1:8080/ws"
    assert resolution.token == TOKEN
    assert resolution.label == "laptop"
    assert resolution.warnings == ()


def test_the_default_is_what_no_name_resolves_to(tmp_path) -> None:
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": TOKEN})
    resolution = servers.resolve(None, token=None, env=env)
    assert (resolution.url, resolution.token, resolution.label) == (
        "wss://build.lan:8443/ws",
        TOKEN,
        "home",
    )


def test_a_token_from_above_wins_and_the_file_is_not_even_opened(tmp_path) -> None:
    """The ladder holds inside the rung: --token beats tokens/<label>.

    Asserted through a label whose token file does not exist at all — if
    the rung below were consulted, this would refuse.
    """
    env = _configure(tmp_path, toml=TWO_SERVERS)
    resolution = servers.resolve("home", token="from-the-flag", env=env)
    assert (resolution.url, resolution.token) == ("wss://build.lan:8443/ws", "from-the-flag")


def test_a_trailing_newline_is_not_part_of_the_token(tmp_path) -> None:
    """Every editor writes one; a bearer token has none."""
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": f"  {TOKEN}\n"})
    assert servers.resolve("home", token=None, env=env).token == TOKEN


def test_a_token_file_with_more_than_a_token_is_refused(tmp_path) -> None:
    """Whitespace inside is a file with something else in it."""
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": "token = s3cret\n"})
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert "more than a token" in refusal.value.message


def test_an_empty_token_file_is_refused(tmp_path) -> None:
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": "\n"})
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert "empty" in refusal.value.message
    assert str(servers.token_path("home", env)) in (refusal.value.hint or "")


def test_a_label_without_a_token_file_is_refused_by_path(tmp_path) -> None:
    """A build server has no configuration in which it needs no token.

    So an unauthenticated session is not a thing that can work — it is a
    401 ten seconds later, in a place where the fix is much harder to
    name. The refusal names the file to write and the two rungs above it.
    """
    env = _configure(tmp_path, toml=TWO_SERVERS)
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert refusal.value.location.file == servers.token_path("home", env)
    assert "MCUHOME_BUILD_TOKEN" in (refusal.value.hint or "")


# --------------------------------------------------------------------------
# Permissions: the signing key's stance, said out loud
# --------------------------------------------------------------------------


def test_an_owner_only_token_file_says_nothing(tmp_path) -> None:
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": TOKEN}, mode=0o600)
    assert servers.resolve("home", token=None, env=env).warnings == ()


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o666])
def test_a_readable_by_others_token_file_warns_loudly_and_still_builds(tmp_path, mode) -> None:
    """Warned, not refused — and the warning names the exact chmod.

    Refusing would make the command line stricter about a bearer token
    than :mod:`mcuhome.workbench.signing` is about the private signing
    key, which is the graver secret and is created owner-only and never
    checked again. Same stance, one voice louder.
    """
    env = _configure(tmp_path, toml=TWO_SERVERS, tokens={"home": TOKEN}, mode=mode)
    resolution = servers.resolve("home", token=None, env=env)
    assert resolution.token == TOKEN
    assert len(resolution.warnings) == 1
    warning = resolution.warnings[0]
    assert warning.startswith("Warning:")
    assert f"chmod 600 {servers.token_path('home', env)}" in warning


# --------------------------------------------------------------------------
# What the file may not say
# --------------------------------------------------------------------------


def test_a_malformed_file_is_refused_at_the_file(tmp_path) -> None:
    env = _configure(tmp_path, toml='default = "home"\n[server.home\nurl = "wss://x/ws"\n')
    with pytest.raises(ConfigError) as refusal:
        servers.resolve(None, token=None, env=env)
    assert "not valid TOML" in refusal.value.message
    assert refusal.value.location.file == servers.config_path(env)


def test_a_default_naming_a_server_that_is_not_there_is_refused(tmp_path) -> None:
    env = _configure(
        tmp_path, toml='default = "office"\n\n[server.home]\nurl = "wss://build.lan/ws"\n'
    )
    with pytest.raises(ConfigError) as refusal:
        servers.resolve(None, token=None, env=env)
    assert 'default = "office"' in refusal.value.message
    assert "home" in (refusal.value.hint or "")


def test_an_unknown_label_lists_the_configured_ones(tmp_path) -> None:
    env = _configure(tmp_path, toml=TWO_SERVERS)
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("offcie", token=None, env=env)
    assert "offcie" in refusal.value.message
    assert "home, laptop" in (refusal.value.hint or "")


def test_servers_without_a_default_are_refused_only_when_nothing_chose(tmp_path) -> None:
    """A file of servers and no ``default`` is legitimate — until it is not.

    Naming one on every build is a way to work; forgetting to is the
    moment that becomes a problem, and the moment the labels are worth
    listing.
    """
    toml = '[server.home]\nurl = "wss://build.lan/ws"\n'
    env = _configure(tmp_path, toml=toml, tokens={"home": TOKEN})
    assert servers.resolve("home", token=None, env=env).url == "wss://build.lan/ws"
    with pytest.raises(ConfigError) as refusal:
        servers.resolve(None, token=None, env=env)
    assert "names no default" in refusal.value.message
    assert 'default = "home"' in (refusal.value.hint or "")


def test_a_server_without_a_url_is_refused(tmp_path) -> None:
    env = _configure(tmp_path, toml='[server.home]\ntoken = "no"\n')
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert "has no url" in refusal.value.message


def test_an_address_without_a_scheme_is_refused(tmp_path) -> None:
    """The other half of the discrimination rule, enforced where it is written.

    A label never has a scheme, so a URL always must — otherwise
    ``--server build.lan`` would be ambiguous in the one place the
    ambiguity cannot be resolved.
    """
    env = _configure(tmp_path, toml='[server.home]\nurl = "build.lan:8443"\n')
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert "without a scheme" in refusal.value.message
    assert "wss://build.lan:8443/ws" in (refusal.value.hint or "")


@pytest.mark.parametrize("label", ["", "../../etc/shadow", "a/b", "."])
def test_a_label_that_is_not_a_name_is_refused(tmp_path, label) -> None:
    """A label names a file in ``tokens/``, so it has to be a name.

    Nothing here is attacker-controlled — it is the user's own file — but
    a label with a path separator would silently read a token from
    somewhere else entirely, and "somewhere else entirely" is not a thing
    to be quiet about.
    """
    env = _configure(tmp_path, toml=f'[server."{label}"]\nurl = "wss://x/ws"\n')
    with pytest.raises(ConfigError) as refusal:
        servers.resolve(None, token=None, env=env)
    assert "usable name" in refusal.value.message


def test_a_label_that_is_a_url_is_refused(tmp_path) -> None:
    env = _configure(tmp_path, toml='[server."wss://build.lan/ws"]\nurl = "wss://x/ws"\n')
    with pytest.raises(ConfigError) as refusal:
        servers.resolve(None, token=None, env=env)
    assert "URL rather than a name" in refusal.value.message


@pytest.mark.parametrize(
    "toml",
    [
        "default = 7\n",
        'server = "home"\n',
        "[server.home]\nurl = 8443\n",
    ],
)
def test_a_value_of_the_wrong_type_is_refused_with_the_shape(tmp_path, toml) -> None:
    env = _configure(tmp_path, toml=toml)
    with pytest.raises(ConfigError) as refusal:
        servers.resolve(None, token=None, env=env)
    assert "[server.home]" in (refusal.value.hint or "")


# --------------------------------------------------------------------------
# Unknown keys: room for later, not a trap
# --------------------------------------------------------------------------


def test_unknown_keys_are_warned_about_and_the_build_goes_on(tmp_path) -> None:
    """The file has two authors, and only one of them is this release.

    A dashboard writing an option a released command line predates must
    not take that user's build down over a key that changes nothing — and
    a human's typo must not pass in silence. So: warned, by name, and
    resolved anyway.
    """
    toml = (
        'default = "home"\ntimeout = 30\n\n'
        '[server.home]\nurl = "wss://build.lan/ws"\nverify = false\n'
    )
    env = _configure(tmp_path, toml=toml, tokens={"home": TOKEN})
    resolution = servers.resolve(None, token=None, env=env)
    assert resolution.url == "wss://build.lan/ws"
    assert len(resolution.warnings) == 2
    assert any('"timeout"' in warning for warning in resolution.warnings)
    assert any('"verify"' in warning for warning in resolution.warnings)


# --------------------------------------------------------------------------
# Where the files are, and when there are none
# --------------------------------------------------------------------------


def test_the_files_live_under_the_configuration_directory(tmp_path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    assert servers.config_path(env) == tmp_path / "cfg" / "mcuhome" / "build-servers.toml"
    assert servers.token_path("home", env) == tmp_path / "cfg" / "mcuhome" / "tokens" / "home"
    home_env = {"HOME": str(tmp_path / "someone")}
    assert servers.config_path(home_env).is_relative_to(tmp_path / "someone" / ".config")


def test_no_file_leaves_the_rung_unanswered(tmp_path) -> None:
    """Not an error: there is no build server, which the caller refuses."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "nothing-here")}
    assert servers.resolve(None, token=None, env=env) == servers.Resolution()


def test_an_environment_without_a_home_has_no_rung_to_climb(tmp_path) -> None:
    """A service started without a login session has no ``HOME``.

    A rung that cannot be reached is unanswered, not broken — so a
    ``remote`` build there refuses with the ladder rather than with a
    lecture about ``HOME``.
    """
    assert servers.resolve(None, token="from-the-environment", env={}) == servers.Resolution(
        token="from-the-environment"
    )


def test_naming_a_label_there_is_an_error_about_home(tmp_path) -> None:
    """Asking for the file by name is different from falling back to it."""
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env={})
    assert "HOME" in refusal.value.message


def test_a_label_without_a_file_says_how_to_write_one(tmp_path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "nothing-here")}
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert servers.CONFIG_FILE in refusal.value.message
    assert "[server.home]" in (refusal.value.hint or "")
    assert refusal.value.location.file == servers.config_path(env)


def test_a_directory_where_the_token_should_be_is_refused(tmp_path) -> None:
    env = _configure(tmp_path, toml=TWO_SERVERS)
    servers.token_path("home", env).mkdir(parents=True)
    with pytest.raises(ConfigError) as refusal:
        servers.resolve("home", token=None, env=env)
    assert "no token file" in refusal.value.message
