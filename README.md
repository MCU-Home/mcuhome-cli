# mcuhome-cli

`mcuhome-cli` is the command line of MCUHome — the `mcuhome` program. It is
the thin shell over the workbench library: it parses arguments, renders
output and reads the process environment, while every build, validation and
signing step it invokes lives in the library.

## What this repository holds

- The `mcuhome` console script and its parser: projects, devices, builds,
  signing, configuration and diagnostics.
- Three output modes behind one `-o/--output` flag — human, JSON and NDJSON —
  so one command serves a person and a program.
- A live build view: a step line naming where each step runs, a repainted
  window on the build log, and the full log in a file.
- The phase contract that turns a library refusal into an exit code, a hint
  and a documentation link.
- Message externalization (gettext) for every string a person reads, with
  machine output deliberately outside it.

## Using it

The distribution installs one console script, `mcuhome`, which works on a
project directory — create one, scaffold a device in it, and build that
device:

```sh
mcuhome project init my-project
cd my-project
mcuhome device new my-device --board nrf7002dk/nrf5340/cpuapp
mcuhome device build my-device
```

Every command answers `--help`, and `-o json` / `-o json-stream` hand a
driving process the same information as a document instead of a rendering.

### Where a build runs, and how

`mcuhome device build` places a build on two axes. The **target** is where it
runs, the **mode** is how this machine executes a local one; each flag sets the
configuration option of the same name for that one invocation:

```sh
mcuhome device build my-device --build-target local  --build-mode container
mcuhome device build my-device --build-target local  --build-mode subprocess
mcuhome device build my-device --build-target remote --build-server buildbox:8080
```

| flag | what it states |
|---|---|
| `--build-target local\|remote` | build on this machine, or on a build server (`build.target`) |
| `--build-mode container\|subprocess` | how a local build is executed (`build.mode`); a remote build has no mode of its own to state |
| `--build-server ADDRESS`, `--build-token TOKEN` | the remote target's server and its bearer token; a configured builder carries its own |
| `--container-image PIN` | pin the image for this one build: a repository, `:tag`, `@sha256:…`, or a repository with either. Overrides the device's `sources.container_image` |
| `--sdk-sources DIR` | a directory holding the hash-pinned SDK package (repeatable, searched in order). Optional at both targets — without one the package registry answers |
| `--builder NAME` | build through a configured builder instead of stating target and flags |

Nothing tells a build how many jobs to run: a build is given a CPU and a memory
budget (`build.cpus`, `build.memory`), the build environment sizes its own
parallelism from it, and a container build is held to it from outside.

`mcuhome config print` lists every option with the layer it came from, which is
where the two keys are read back:

```console
$ mcuhome config print
option                        value                               origin
build.target                  local                               default
build.mode                    container                           default
build.container_repositories  ghcr.io/mcu-home/build-environment  default
…
$ mcuhome config set build.mode subprocess --user
```

`mcuhome doctor` answers the same question for the machine rather than for one
build: its `builders` line names the configured builders, or says what a plain
`mcuhome device build` would do — "none configured — a plain build runs on this
machine, in a build container" — and the container check is skipped where the
mode is `subprocess`.

### Building against your own west workspace

Working on the SDK itself is a build like any other, with the environment
pointed at a west workspace you maintain:

```sh
mcuhome config set build.mode subprocess --user
mcuhome config set build.dev_workspace ~/work/mcuhome-workspace --user
mcuhome device build my-device
```

The workspace — the directory holding `.west/`, the `mcuhome-sdk` checkout,
`zephyr/`, `modules/` and `bootloader/` — is then the whole environment, and
the tools are the ones on your `PATH`. Nothing is fetched, unpacked or
verified, and nothing is written into the workspace. It builds on this machine
only: a development build has no image to run in and no pinned packages to name,
so `--build-mode container`, `--build-target remote` and a device that states
any `sources.*` entry are each refused with the reason. The command line says
which environment a build used — the image and its digest for a container
build, the package versions for an unpacked one, and the workspace path for a
development build.

## How it fits into MCUHome

This package declares one dependency,
[mcuhome-workbench](https://github.com/mcu-home/mcuhome-workbench), which
resolves the device model and runs the build and the signature. Where a
build runs is its **target** (`--build-target`, or the `build.target`
option): a `local` build compiles in a build environment on this machine,
built from [mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) together
with the C runtime it compiles against; a `remote` build hands the context
to a server from
[mcuhome-buildserver](https://github.com/mcu-home/mcuhome-buildserver). How
this machine executes a local build is its **mode** (`--build-mode`, or
`build.mode`): in a build container, or as a child process against the
build environment MCUHome unpacked here. `--container-image` pins the image
for one build, in place of the device's own `sources.container_image`.
[mcuhome-ui](https://github.com/mcu-home/mcuhome-ui) offers the same
operations in a browser, over the same workbench API.

## Development — how to work on this repository

This repository has its own virtual environment in `.venv/`; nothing is
installed into the system Python or into another repository's environment.
`bin/` holds the user-facing entry points, `scripts/` the development
tooling: `scripts/test` and `scripts/lint` dispatch the checks — `all` runs
every one, `list` names them, `<name>` runs one — and each check is its own
wrapper in `scripts/test.d/` or `scripts/lint.d/`. The wrappers select
`.venv` themselves (never activate one by hand) and are exactly what CI
runs, one job per check.

Needs Python ≥3.13 and sibling checkouts of `mcuhome-sdk` (`packaging/model`,
`packaging/compiler`) and `mcuhome-workbench` — this package's one
dependency — with its `remote` and `generate` extras.

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
host applies the signature, so a build server is never handed a key.
Commissioning passcodes are masked in output that merely passes by, and
only `mcuhome device matter-pairing` or an explicit `--show-sensitive`
prints them. Report a vulnerability through the organization's security
policy, [SECURITY.md](https://github.com/mcu-home/.github/blob/main/SECURITY.md).

## Documentation

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
