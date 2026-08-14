# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the CLI behavior tests.

The example configuration and the fixture tree are copies of the ones in
the mcuhome-sdk repository (``docs/design/examples/`` and
``tests_py/data/tree/``): the CLI tests exercise the command surface over
a real configuration without reaching into the SDK repo's checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcuhome.compiler import container
from mcuhome.compiler import localbackend as lb
from mcuhome.workbench.project import init_project

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = TESTS_DIR / "data"
EXAMPLES_DIR = DATA_DIR / "examples"
FIXTURE_TREE = DATA_DIR / "tree"

# git does not record the 700/600 modes the secrets hygiene of ADR 0022
# §5 expects, so a fresh checkout's fixture secrets would draw the
# permission warning in every test that reads them. Setting the modes at
# collection is invisible to git (only the executable bit is tracked).
FIXTURE_TREE.joinpath("secrets").chmod(0o700)
FIXTURE_TREE.joinpath("secrets", "main.yaml").chmod(0o600)


def make_project(root: Path) -> Path:
    """A real project directory at *root* — marker, layout, permissions.

    What the tests reach for where they used to scribble a bare tree:
    since ADR 0022 every directory a ``--project-dir`` names (and every
    root the upward search may find) has to carry the marker, so a test
    that wants one asks ``mcuhome init``'s own implementation.
    """
    return init_project(root, force=root.is_dir()).project.root


#: A configuration that passes every check — the same baseline the
#: builder's own suite uses.
VALID_CONFIG = """\
device:
  name: bench-node
  board: nrf7002dk/nrf5340/cpuapp

network:
  thread:
    device_role: ftd
  matter:
    enabled: true
    use_test_pairing: true

hardware:
  buses:
    i2c0:
      controller: arduino_i2c
  peripherals:
    baro:
      driver: bosch,bmp180
      bus: i2c0

node:
  endpoints:
    - id: 1
      device_type: temperature_sensor
      clusters:
        temperature_measurement:
          source: baro.temperature
          sampling: 10s
"""


@pytest.fixture(autouse=True)
def _no_real_user_environment(monkeypatch, tmp_path):
    """No test may touch the developer's own MCUHome configuration.

    ``$XDG_CONFIG_HOME/mcuhome/`` on the machine running this suite holds
    this user's real user configuration layer (``configuration.yaml``,
    ADR 0022) and their real build servers (``build-servers.toml`` and
    tokens, E63) — both of which a test resolving settings or the remote
    ladder would otherwise read. Point the variable at the test's own
    tmp_path instead, and drop every ``MCUHOME_*`` variable a developer's
    shell may carry: the project override would send every test into that
    developer's project, the rest would silently win a layer.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    for variable in (
        "MCUHOME_SIGNING_KEY",
        "MCUHOME_PROJECT_DIR",
        "MCUHOME_SDK_SOURCES",
        "MCUHOME_JOBS",
        "MCUHOME_BUILD_METHOD",
        "MCUHOME_BUILD_SERVER",
        "MCUHOME_BUILD_TOKEN",
        "NO_COLOR",
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(autouse=True)
def _no_droppings_in_the_fixtures():
    """The committed fixtures stay byte-identical through every test.

    Since ADR 0015 §8 the signing key is generated *inside the project*
    (``secrets/firmware/mcuboot.yaml``), and a bare YAML file's stand-in
    project is its own directory — so a build test that forgets to state
    a key or a project would generate one into the repository checkout.
    That must fail the test, not linger as an untracked secret.
    """
    yield
    assert not (EXAMPLES_DIR / "secrets").exists(), "a test wrote secrets into the examples dir"
    assert not (FIXTURE_TREE / "secrets" / "firmware").exists(), (
        "a test generated a signing key inside the fixture tree"
    )


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    """Nothing in this suite is allowed to reach a container runtime.

    A safety net, not a convenience: ``mcuhome build`` defaults to the
    container (through the build-container ABI since E54), so a test that
    forgets to stub it would otherwise quietly start a real Matter build on
    the machine running pytest. Both container seams are closed — the
    ``local-dev`` preflight helper and the local backend's one impure
    docker call — so neither path can escape. Tests that want a working
    build replace these with their own stub, which wins because their
    monkeypatch is applied later.
    """

    def refuse(command, env):
        raise AssertionError(f"a test tried to run {command[0]!r}: stage 5 must be stubbed")

    def refuse_docker(argv, on_line=None):
        raise AssertionError(f"a test tried to run docker {argv!r}: the seam must be stubbed")

    monkeypatch.setattr(container, "_run_quiet", refuse)
    monkeypatch.setattr(lb, "_run_command", refuse_docker)
