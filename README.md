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

## Build servers

`mcuhome build --method remote` compiles on a build server. Which one is
a ladder — the flag beats the environment, and the environment beats the
configuration file:

```sh
mcuhome build <device> --method remote --server wss://host:8443/session --token <token>
MCUHOME_BUILD_SERVER=wss://host:8443/session MCUHOME_BUILD_TOKEN=<token> mcuhome build …
```

For more than one server, name them once in
`$XDG_CONFIG_HOME/mcuhome/build-servers.toml` (`~/.config/mcuhome/` on a
normal Linux account):

```toml
default = "home"

[server.home]
url = "wss://build.lan:8443/session"

[server.laptop]
url = "ws://127.0.0.1:8080/session"
```

`--server` and `MCUHOME_BUILD_SERVER` then take a **label** as well as an
address — `mcuhome build <device> --method remote --server laptop` — and
`default` says which one a build that names none uses. A label is told
from an address by its scheme: a URL has one, a label never does.

Tokens are **not** in that file: each server's bearer token is its own
file, `tokens/<label>` next to it, holding the token and nothing else.
That keeps the file that names servers free of secrets, so it can be
copied to another machine or pasted into a bug report. Write it
owner-only — a token is a bearer credential, and MCUHome says so loudly
when other accounts can read it:

```sh
(umask 177; printf %s '<token>' > ~/.config/mcuhome/tokens/home)
```

A label brings its token along automatically; `--token` and
`MCUHOME_BUILD_TOKEN` override it, and a trailing newline in the file is
ignored. The file is yours (or a dashboard's) — the command line only
ever reads it.

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
