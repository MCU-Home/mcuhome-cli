# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the CLI behavior tests.

The example configuration and the fixture tree are copies of the ones in
the mcuhome repository (``docs/design/examples/`` and
``tests_py/data/tree/``): the CLI tests exercise the command surface over
a real configuration without reaching into the library repo's checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcuhome.compiler import container

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = TESTS_DIR / "data"
EXAMPLES_DIR = DATA_DIR / "examples"
FIXTURE_TREE = DATA_DIR / "tree"

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
def _no_real_signing_key(monkeypatch, tmp_path):
    """No test may touch the developer's own firmware signing key.

    ``mcuhome build`` generates one on first need under
    ``$XDG_CONFIG_HOME/mcuhome/`` (mcuhome ADR 0015 decision 8), which on
    the machine running this suite is a real, long-lived private key.
    Point the variable at the test's own tmp_path instead.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.delenv("MCUHOME_SIGNING_KEY", raising=False)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    """Nothing in this suite is allowed to reach a container runtime.

    A safety net, not a convenience: ``mcuhome build`` defaults to the
    container, so a test that forgets to stub stage 5 would otherwise
    quietly start a real Matter build on the machine running pytest.
    Tests that want a working preflight replace this with their own
    runner, which wins because their monkeypatch is applied later.
    """

    def refuse(command, env):
        raise AssertionError(f"a test tried to run {command[0]!r}: stage 5 must be stubbed")

    monkeypatch.setattr(container, "_run_quiet", refuse)
