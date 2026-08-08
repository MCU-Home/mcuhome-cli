<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# mcuhome-cli

The `mcuhome` command line — a thin shell over the
[MCUHome builder library](https://github.com/mcu-home/mcuhome).

It parses arguments and renders results; every stage of the actual
pipeline (validate, generate, compile, sign) is a call into the
`mcuhome` package. Programs never use this shell: they embed
`mcuhome.api`, the supported programmatic surface.

```
mcuhome new          <device>      # scaffold a device folder
mcuhome validate     <device>      # stages 1-3, prints a summary
mcuhome build        <device>      # stages 1-5, a flashable image
mcuhome sign         <build-dir>   # apply the signature afterwards
mcuhome init-pairing <device>      # draw commissioning credentials
mcuhome public-key                 # the public half of the signing key
mcuhome schema       [what]        # the schema and the registry, as JSON
mcuhome clean        <device|--all>
```

`mcuhome validate --json` and `mcuhome build --json` replace the human
rendering with one machine-readable document on stdout; exit codes do
not change and the build log goes to stderr. `mcuhome --version`
reports the *builder's* version — that is the number that determines
what a build produces.

## Development install

Nothing is on PyPI yet (the repositories are private), so the `mcuhome`
dependency comes from a sibling checkout. From this repository's root,
with `mcuhome` cloned next to it:

```sh
pip install -e ../mcuhome
pip install -e '.[dev]'
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
