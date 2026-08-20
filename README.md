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

## How it fits into MCUHome

This package declares one dependency,
[mcuhome-workbench](https://github.com/mcu-home/mcuhome-workbench), which
resolves the device model and runs the build and the signature. A `local`
build compiles in a build environment on this machine, built from
[mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) together with the C
runtime it compiles against; a `remote` build hands the context to a server
from
[mcuhome-buildserver](https://github.com/mcu-home/mcuhome-buildserver).
[mcuhome-ui](https://github.com/mcu-home/mcuhome-ui) offers the same
operations in a browser, over the same workbench API.

## Working on this repository

The repository needs Python 3.13 and its own virtual environment. Install
the workbench, plus the model and compiler packages, from their checkouts,
then this package with its `dev` extra, and run the suite in `tests/python`:

```sh
pip install -e '.[dev]'
pytest
```

`ruff check` and `ruff format --check` lint the source. GitHub Actions runs
the same gates — `lint-ruff` and `test-pytest`, alongside `license-reuse`,
`spell-codespell`, `hygiene-prehooks` and `commits-conventional` — on pushes
to `main` and on every pull request.

## Security

The private signing key stays on the machine the command runs on: a build
yields an unsigned image whichever method ran, and a separate step on this
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
- [Decision records](docs/adr) — the command vocabulary and the output
  contract
- [MCUHome on GitHub](https://github.com/mcu-home) — the other repositories
  of the project

## Contributing and support

Problems and questions go to
[Issues](https://github.com/mcu-home/mcuhome-cli/issues). The contributing
rules live with the organization, in
[CONTRIBUTING.md](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
