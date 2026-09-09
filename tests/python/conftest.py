# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the CLI behavior tests.

The example configuration and the fixture tree are copies of the ones in
the mcuhome-sdk repository (``docs/design/examples/`` and
``tests/python/data/tree/``): the CLI tests exercise the command surface over
a real configuration without reaching into the SDK repo's checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcuhome.workbench import configuration, containerbuild, ociregistry, subprocessbuild
from mcuhome.workbench.project import init_project

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = TESTS_DIR / "data"
EXAMPLES_DIR = DATA_DIR / "examples"
FIXTURE_TREE = DATA_DIR / "tree"

# git does not record the 700/600 modes the secrets hygiene rules
# expect, so a fresh checkout's fixture secrets would draw the
# permission warning in every test that reads them. Setting the modes at
# collection is invisible to git (only the executable bit is tracked).
FIXTURE_TREE.joinpath("secrets").chmod(0o700)
FIXTURE_TREE.joinpath("secrets", "main.yaml").chmod(0o600)


def make_project(root: Path) -> Path:
    """A real project directory at *root* — marker, layout, permissions.

    What the tests reach for where they used to scribble a bare tree:
    every directory a ``--project-dir`` names (and every
    root the upward search may find) has to carry the marker, so a test
    that wants one asks ``mcuhome project init``'s own implementation.
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
    this user's real user configuration layer (``configuration.yaml``)
    and their real build servers (``build-servers.toml`` and
    tokens) — both of which a test resolving settings or the remote
    ladder would otherwise read. Point the variable at the test's own
    tmp_path instead, and drop every ``MCUHOME_*`` variable a developer's
    shell may carry: the project override would send every test into that
    developer's project, the rest would silently win a layer.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    for variable in (
        "MCUHOME_SIGNING_KEY",
        "MCUHOME_PROJECT_DIR",
        "MCUHOME_BUILD_SDK_SOURCES",
        "MCUHOME_JOBS",
        "MCUHOME_DEFAULT_BUILDER",
        "NO_COLOR",
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(autouse=True)
def _no_real_system_layer(monkeypatch, tmp_path_factory):
    """No test may read the machine's own ``/etc/mcuhome``.

    The command line resolves the same five layers everything else does,
    and the system layer is the one that is not derived from a stated
    environment by convention — a machine's is where the machine says it
    is. A developer or a CI image carrying
    ``/etc/mcuhome/configuration.yaml`` would otherwise feed it into
    every test that runs a build, and the tests about what this
    invocation resolved would answer differently there than here.
    ``XDG_CONFIG_DIRS`` is what states that directory, so the process
    gets one pointing at an empty directory of this suite's own, and an
    environment stated without the variable is answered with the same
    empty one.
    """
    empty = tmp_path_factory.mktemp("system-config")
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(empty))
    real = configuration.system_config_dir

    def stated_or_empty(env):
        return real(env) if env.get("XDG_CONFIG_DIRS") else empty / "mcuhome"

    monkeypatch.setattr(configuration, "system_config_dir", stated_or_empty)


@pytest.fixture(autouse=True)
def _no_droppings_in_the_fixtures():
    """The committed fixtures stay byte-identical through every test.

    The signing key is generated *inside the project*
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
    container (through the build environment specification's container
    profile), so a test that forgets to stub it would otherwise quietly
    start a real Matter build on the machine running pytest. Every seam
    that could start a real process is closed — the container runtime's
    two impure operations (:class:`~mcuhome.workbench.containerbuild.Runtime`
    driving ``docker``) and the subprocess profile's one child-process
    launch — so neither path can escape. Tests that want a working build
    replace these with their own stub, which wins because their
    monkeypatch is applied later.
    """

    def refuse_run(self, argv, on_line=None):
        raise AssertionError(f"a test tried to run {argv[0]!r}: the seam must be stubbed")

    def refuse_spawn(self, argv, on_line=None):
        raise AssertionError(f"a test tried to run {argv[0]!r}: the seam must be stubbed")

    def refuse_process(argv, *, env=None, cwd=None, on_line=None):
        raise AssertionError(f"a test tried to run {argv[0]!r}: the seam must be stubbed")

    monkeypatch.setattr(containerbuild.Runtime, "run", refuse_run)
    monkeypatch.setattr(containerbuild.Runtime, "spawn", refuse_spawn)
    monkeypatch.setattr(subprocessbuild, "spawn_process", refuse_process)


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """Nothing in this suite is allowed to reach a container registry.

    Choosing a build environment goes over HTTPS now, so a test that
    forgot to pin one would otherwise depend on ghcr.io being up and on
    what is published there today. A build that must resolve one pins a
    digest and stubs ``docker image inspect``, which is the path an
    air-gapped user takes too.
    """

    def refuse(self, url, headers, timeout):
        del headers, timeout
        raise AssertionError(
            f"a test tried to reach {url}: pin the environment with a digest instead"
        )

    monkeypatch.setattr(ociregistry.Registry, "_urlopen", refuse)
