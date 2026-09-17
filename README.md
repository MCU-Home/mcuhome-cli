# mcuhome-cli

`mcuhome-cli` is the command line of MCUHome — the `mcuhome` program. It is
the **complete client of the workbench**: everything a person does with
MCUHome is reachable from here, and none of it happens here. The command
line parses arguments, renders output and reads the process environment;
every build, validation and signing step it invokes lives in
`mcuhome.workbench.api`, which it is also the worked example of.

## What this repository holds

- The `mcuhome` console script and its parser: 38 commands — the eight areas
  `project`, `config`, `device`, `secret`, `signing`, `context`,
  `environment` and `host`, and `version` on its own at the top.
- Three output modes behind one `-o/--output` flag — `human`, `json` and
  `json-stream` — on every command, so one command serves a person and a
  program.
- A live build view: a step line naming where each step runs, a repainted
  window on the build log, and the full log in a file.
- The three phases every run walks (interact, validate, execute), the three
  exit codes and the four refusal kinds the command line owns.
- Message externalization (gettext) for every string a person reads, with
  machine output deliberately outside it.

## Using it

The distribution installs one console script, `mcuhome`, which works on a
project directory — create one, draw the project's signing key, scaffold a
device and build it:

```sh
mcuhome project init thermostats
cd thermostats
mcuhome signing create-key
mcuhome device new kitchen --board nrf7002dk/nrf5340/cpuapp
mcuhome device build kitchen
```

The key is drawn once per project and before the first build, because a
build signs what it produced and MCUHome never draws key material on the
way past.

Every command answers `--help`, and `-o json` / `-o json-stream` hand a
driving process the same information as a document instead of a rendering.

## The reference

**[`docs/cli.md`](docs/cli.md) is the contract**: every command, every flag,
every document a command prints and every exit code it answers with. A
program that drives `mcuhome` reads that document; this README is the door
to it. The workbench's own surface — the functions each command calls — is
[`mcuhome-workbench/docs/api.md`](https://github.com/mcu-home/mcuhome-workbench/blob/main/docs/api.md).

## Where a build runs, and how

`mcuhome device build` places a build on two axes. The **target** is where
it runs — `local` on this machine, `remote` on a build server — and the
**mode** is how this machine executes a local one, in a build container or
as a child process against an unpacked build environment:

```sh
mcuhome device build kitchen --build-target local  --build-mode container
mcuhome device build kitchen --build-target local  --build-mode subprocess
mcuhome device build kitchen --build-target remote --build-server buildbox:8080
```

Each of those flags carries the configuration option of the same name
(`build.target`, `build.mode`) for that one invocation; `--builder` picks a
builder configured under `builder.<name>` instead of stating the axes, and
`--container-image` pins the image for one build in place of the device's
own `sources.container_image`. The whole table is in
[the reference](docs/cli.md).

Nothing tells a build how many jobs to run: a build is given a CPU and a
memory budget (`build.cpus`, `build.memory`), the build environment sizes
its own parallelism from it, and a container build is held to it from
outside.

`mcuhome config print` lists every option with the layer it came from,
which is where those keys are read back:

```console
$ mcuhome config print
option                        value                               origin   source
…
build.target                  local                               default
build.mode                    container                           default
build.container_repositories  ghcr.io/mcu-home/build-environment  default
…
$ mcuhome config set build.mode subprocess --scope user
```

`mcuhome host check` answers the same question for the machine rather than
for one build: one finding per thing it examined, with the fix where there
is one. What it probes follows the configured mode — the container runtime
and the image search for `container`; the interpreter for `subprocess`,
beside either the west workspace a development build compiles in or the
build-environment store that holds the packages — while the signing
program, the compiler cache, the project, the resolved configuration, the
configured builders and the permissions of the project's secrets are
examined either way.

### Building against your own west workspace

Working on the SDK itself is a build like any other, with the environment
pointed at a west workspace you maintain:

```sh
mcuhome config set build.mode subprocess --scope user
mcuhome config set build.dev_workspace ~/work/mcuhome-workspace --scope user
mcuhome device build kitchen
```

The workspace — the directory holding `.west/`, the `mcuhome-sdk` checkout,
`zephyr/`, `modules/` and `bootloader/` — is then the whole environment, and
the tools are the ones on your `PATH`. Nothing is fetched, unpacked or
verified, and nothing is written into the workspace. It builds on this machine
only: a development build has no image to run in and no pinned packages to name,
so `--build-mode container`, `--build-target remote` and a device that states a
`sources.*` entry other than its default are each refused with the reason. The
command line says which environment a build used — the image and its digest for
a container build, the package versions for an unpacked one, and the workspace
path for a development build.

## How it fits into MCUHome

This package declares one dependency,
[mcuhome-workbench](https://github.com/mcu-home/mcuhome-workbench), and
imports exactly one module of it: `mcuhome.workbench.api`, the workbench's
supported surface. The workbench resolves the device model and runs the
build and the signature. A `local` build compiles in a build environment on
this machine, built from
[mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) together with the C
runtime it compiles against; a `remote` build hands the context to a server
from
[mcuhome-buildserver](https://github.com/mcu-home/mcuhome-buildserver).
[mcuhome-ui](https://github.com/mcu-home/mcuhome-ui) offers the same
operations in a browser, over the same workbench API — which is why the
command line is written as the worked example of that API rather than as a
program with knowledge of its own.

## Development — how to work on this repository

This repository has its own virtual environment in `.venv/`; nothing is
installed into the system Python or into another repository's environment.
It ships one user-facing entry point and it is a console script, so
`scripts/` holds the development tooling and nothing else: `scripts/test`
and `scripts/lint` dispatch the checks — `all` runs every one, `list` names
them, `<name>` runs one — and each check is its own wrapper in
`scripts/test.d/` or `scripts/lint.d/`. The wrappers select `.venv`
themselves (never activate one by hand) and are exactly what CI runs, one
job per check.

Needs Python ≥3.13 and sibling checkouts of `mcuhome-workbench` — this
package's one dependency, installed with its `remote` and `generate`
extras — of `mcuhome-sdk`, which holds the device model (`packaging/model`)
and the code generator (`packaging/compiler`), and of `mcuhome-packagetool`,
which the workbench imports to verify what a package registry serves. None
of them is published yet, so all four are installed from the checkouts
beside this one, in one invocation, so pip resolves their pins against each
other rather than against an index:

```sh
python3 -m venv .venv && .venv/bin/pip install \
  ../mcuhome-sdk/packaging/model -e ../mcuhome-packagetool \
  '../mcuhome-workbench[remote,generate]' \
  ../mcuhome-sdk/packaging/compiler -e . --group dev
```

```sh
scripts/test all
scripts/lint all
```

The rules that hold across every MCUHome repository — coding standards,
commits, licensing — are in the organization's
[contributing guide](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## Security

The private signing key stays on the machine the command runs on: a build
yields an unsigned image wherever it ran, and a separate step on this
host applies the signature, so a build server is never handed a key. A
build server's token has two channels and no third — `--build-server-token
-`, which reads it from standard input rather than leaving it in the
process list, and `secrets/builder/<name>.yaml` — and no document this
command line prints carries one.

Commissioning passcodes are masked in the human rendering that merely
passes them by; `mcuhome device print-matter-pairing` is the command that
prints them, `mcuhome device validate --show-sensitive` is what unmasks
them in a rendering, and `mcuhome secret reveal --key <key>` answers one
secret value to a caller that asked for exactly that one. Report a
vulnerability through the organization's security policy,
[SECURITY.md](https://github.com/mcu-home/.github/blob/main/SECURITY.md).

## Documentation

- [`docs/cli.md`](docs/cli.md) — the reference: every command, flag,
  document and exit code
- [Getting started](https://t.mcuhome.org/cli/docs/getting-started/0.1/) — a
  first project, device and build
- [Supported boards](https://t.mcuhome.org/cli/docs/device-supported-boards/0.1/)
  — the targets a device can name
- [MCUHome on GitHub](https://github.com/mcu-home) — the other repositories
  of the project

## Contributing and support

Problems and questions go to
[Issues](https://github.com/mcu-home/mcuhome-cli/issues). The contributing
rules live with the organization, in
[CONTRIBUTING.md](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
