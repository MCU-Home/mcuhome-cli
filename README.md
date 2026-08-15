<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# mcuhome

The `mcuhome` command line — a thin shell over the
[MCUHome library family](https://github.com/mcu-home/mcuhome). The
distribution carries the plain name (`pip install mcuhome`) because it
is what puts the `mcuhome` command on PATH; the library ships as
`mcuhome-model`, `mcuhome-workbench` and `mcuhome-compiler`.

It parses arguments and renders results; every stage of the actual
pipeline (validate, generate, compile, sign) is a call into the
library. Programs never use this shell: they embed
`mcuhome.workbench.api`, the supported programmatic surface.

```
mcuhome init               [dir]         # create a project directory
mcuhome config             [print|get|set|unset]  # read/write configuration values
mcuhome device new         <device>      # scaffold a device folder
mcuhome device validate    <device>      # stages 1-3, prints a summary
mcuhome device build       <device>      # stages 1-5, a flashable image
mcuhome device sign-firmware <device|path>   # apply the signature afterwards
mcuhome device matter-pairing <device>  # show this device's commissioning codes
mcuhome device matter-pairing --new <device>  # draw fresh commissioning credentials
mcuhome device list                     # list project devices with status
mcuhome public-key                      # the public half of the signing key
mcuhome schema             [config|registry]  # the schema and the registry, as JSON
mcuhome doctor                          # environment diagnosis
mcuhome device flash       <device>      # flash the last built/signed firmware (stub)
mcuhome device first-time-setup <device> # one-time board provisioning (stub)
mcuhome clean              <device|--all> # remove build output (stub)
```

Commands run inside an MCUHome **project directory** (ADR 0022): a
directory `mcuhome init` created, found like a git checkout by
searching upward, or named explicitly (`--project-dir`,
`MCUHOME_PROJECT_DIR`). Devices live in `devices/<name>/main.yaml`,
secrets under `secrets/` (mode 700, kept out of git by the generated
`.gitignore`), and the firmware signing key is per-project —
`secrets/firmware/mcuboot.yaml`, generated on first need
(`--signing-key`/`MCUHOME_SIGNING_KEY` override it with a plain PEM
file). Options like `--sdk-sources` and `--jobs` resolve through the
five configuration layers of ADR 0022 (defaults, system, user,
project, environment, command line), each value knowing where it came
from — `mcuhome config print` shows the resolved tree.

`-o json` emits one machine-readable document on stdout after the run;
`-o json-stream` emits NDJSON as the run progresses (verbs `start`,
`progress`, `error`, `result` — the vocabulary is append-only, so
consumers ignore verbs they do not know). Exit codes are the same in
every mode — 0 success, 1 the operation failed, 2 usage error — the
build log goes to stderr, and both machine forms are non-interactive.
`--color auto|always|never` follows the `NO_COLOR` convention.
`mcuhome --version` reports the whole stack, one line per part.

## Build servers and builders

Where a build runs is a **builder** (ADR 0023): configuration about a
build method, declared once in any configuration layer and selected
per invocation — the configured `default_builder`, an explicit
`--builder NAME`, or the fully manual `--build-mode` rung that
bypasses the list for a one-off build:

```sh
mcuhome device build <device>                   # the configured default builder
mcuhome device build <device> --builder attic   # a named builder
mcuhome device build <device> --build-mode remote \
  --build-server wss://host:8443/session --build-token <token>
```

```yaml
# mcuhome.yaml (or the user/system configuration.yaml)
builders:
  - name: attic
    type: remote            # local | local-dev | remote
    server: wss://build.lan:8443/session
default_builder: attic
```

A remote builder's bearer token lives next to the other secrets, in
`secrets/build-server/<name>.yaml` (`token: …`, owner-only) — the
committed configuration names servers and never carries a credential.

## Development install

Nothing is on PyPI yet, so the `mcuhome-workbench` dependency and the
SDK distributions behind its `local` extra come from sibling checkouts
(ADR 0024 split: [mcu-home/mcuhome](https://github.com/mcu-home/mcuhome)
carries the workbench,
[mcu-home/mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) the
model and the compiler). From this repository's root, with both cloned
next to it — one invocation, so pip resolves the sibling pins against
the copies being installed:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../mcuhome-sdk/packaging/model \
            -e ../mcuhome-sdk/packaging/compiler \
            -e '../mcuhome[remote,local]'
pip install -e '.[dev]'
```

Working on this repository alone — no sibling checkouts — works too:
pip pulls the library family straight from git (it clones internally,
you keep exactly one checkout):

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install \
  "mcuhome-model @ git+https://github.com/mcu-home/mcuhome-sdk#subdirectory=packaging/model" \
  "mcuhome-compiler @ git+https://github.com/mcu-home/mcuhome-sdk#subdirectory=packaging/compiler" \
  "mcuhome-workbench[remote,local] @ git+https://github.com/mcu-home/mcuhome" \
  -e '.[dev]'
```

Then:

```sh
mcuhome --help
pytest                            # the CLI behavior tests, ~1 s
ruff check . && ruff format --check .
```

The tests never compile firmware and never touch docker or a real
signing key — stage 5 is stubbed, the same way the builder's own suite
does it.

## License

Apache-2.0, like every MCUHome repository.
