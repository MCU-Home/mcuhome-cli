# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the command line's tests.

The suite drives ``main([...])`` in-process against real projects in
``tmp_path`` and lets the workbench do its own work — mocking a call
that is cheap would test the mock. What it does close off is everything
that leaves this machine or costs minutes: no child process, no socket.
A test that needs one stubs exactly that seam itself.

Nothing here reaches into the workbench's internals. The command line is
a client of ``mcuhome.workbench.api``, and so is its test suite; the
guards below sit on the standard library instead, which is where a
process or a connection actually starts.
"""

from __future__ import annotations

import socket
import subprocess
import urllib.request
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from mcuhome.workbench import api

#: The variables a developer's shell may carry that no declared option
#: would clear. ``NO_COLOR`` is the one this command line consumes
#: itself; the four below are the retired spellings the configuration
#: layer still **reads in order to warn about them**, each producing a
#: finding — a line on stderr, a ``diagnostic`` message in the stream —
#: in every run of a developer who has one exported. They are not
#: options, so the loop over ``api.OPTIONS`` below does not reach them.
_OWN_ENVIRONMENT = (
    "NO_COLOR",
    "MCUHOME_CCACHE_DIR",
    "MCUHOME_DEFAULT_BUILDER",
    "MCUHOME_DOCKER",
    "MCUHOME_IMGTOOL",
)


@pytest.fixture(autouse=True)
def _no_real_user_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test reads the machine's own configuration.

    ``$XDG_CONFIG_HOME/mcuhome/`` holds this user's real user layer and
    ``XDG_CONFIG_DIRS`` names the system one; both are pointed at empty
    directories of the test's own, and every ``MCUHOME_*`` variable is
    dropped — one left standing would silently win a layer and the test
    about what an invocation resolved would answer differently here than
    on the machine next to it. A retired variable would not win a layer
    but would add a finding to every run, which is the same problem
    wearing a different hat, so :data:`_OWN_ENVIRONMENT` clears those
    too.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg-system"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    for variable in _OWN_ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)
    for option in api.OPTIONS:
        if option.env_var:
            monkeypatch.delenv(option.env_var, raising=False)


@pytest.fixture(autouse=True)
def _no_child_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in this suite starts a program.

    A safety net, not a convenience: a command that reaches a container
    runtime, an interpreter or a signing program would otherwise start a
    real one on the machine running pytest.
    """

    def refuse(argv: object, *args: object, **kwargs: object) -> None:
        raise AssertionError(f"a test tried to run {argv!r}: stub the seam it needs")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in this suite opens a connection."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A real project directory — marker, layout, permissions.

    Made the way ``mcuhome project init`` makes one, because a test that
    scribbled a tree of its own would be testing a project MCUHome never
    writes.
    """
    return api.create_project(tmp_path / "project").project.root


@pytest.fixture
def in_project(project: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A project, with the working directory inside it."""
    monkeypatch.chdir(project)
    yield project


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> Mapping[str, str]:
    """The process environment as the tests have set it up."""
    import os

    return dict(os.environ)


#: The board the device fixtures are built for. One of the boards the
#: workbench knows, named rather than picked, so a change to the table
#: does not silently change what these tests build.
BOARD = "nrf7002dk/nrf5340/cpuapp"


@pytest.fixture
def device(in_project: Path) -> Path:
    """A project with one device that validates, and a signing key.

    Everything a build needs before it starts: the device folder, the
    commissioning credentials its Matter stack requires, and the key the
    delivered image is signed with. Made through the api, because a tree
    scribbled here would be a project MCUHome never writes.
    """
    import os

    project = api.read_project(in_project)
    new = api.create_device("kitchen", project=project, board=BOARD)
    api.create_pairing(Path(new.entry), project=project)
    api.create_signing_key(env=dict(os.environ), project=project)
    return in_project
