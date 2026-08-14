<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0002 — The thin shell and its packaging

- Status: draft
- Date: 2026-08-14

Extracted 2026-08-14 from firmware ADR 0017 (§1, §3, Consequences),
ADR 0020 §2 and ADR 0007, which now reference this draft instead of
recording the CLI side themselves.

## Context

The command line began as part of the one builder package in the
firmware repository and was split into its own repository in the
2026-08-08 re-layout (firmware ADR 0017). What kind of program it is,
what it may depend on, and what its distribution is called are
decisions about *this* program — they belong here.

## Decision

1. **The CLI is a thin shell.** It parses arguments and renders
   results; every stage of the actual pipeline (validate, generate,
   compile, sign) is a call into the published packages. Programs
   never use the shell: they embed `mcuhome.workbench.api`, the
   supported programmatic surface. Logic that is not argument parsing
   or rendering does not belong in this repository.

2. **The distribution bears the plain name `mcuhome`**, so that
   `pip install mcuhome` yields the command a user expects, and the
   console script keeps the same name (`mcuhome = mcuhome_cli.cli:main`;
   `python -m mcuhome_cli` is the identical entry). The firmware
   repository renounced the plain name for exactly this purpose
   (firmware ADR 0020 §2 records the renouncing half). The import
   package is `mcuhome_cli`; the services keep `mcuhome-dashboard`
   and `mcuhome-build-server`.

3. **Exactly one dependency: `mcuhome-compiler`**, which pulls
   `mcuhome-workbench` and `mcuhome-model` with it. The CLI is the
   local-dev case — the one consumer entitled to all three (firmware
   ADR 0020 §3). Corollaries, both standing rules since before the
   split: using the CLI must never require the dashboard or any
   dashboard version, and the command itself adds nothing
   toolchain-shaped to the host — `mcuhome validate` and
   `mcuhome build --generate-only` need Python and nothing else; what
   a compiling build needs is the platform's decision (firmware
   ADR 0007), not this program's.

4. **Thin-consumer versioning.** The shell has its own version and
   release cadence; it declares a supported version range of the
   published packages and follows their releases. Deliberate
   consequence: `mcuhome --version` reports the **builder's** version
   (`mcuhome.model.__version__`), not the shell's — that number, not
   the shell's, determines what a build produces.

## Consequences

- `pip install mcuhome` is the whole user-facing install story once
  the packages are published; until then the editable-checkout install
  in the README is the documented path.
- The ruff settings mirror the firmware repository and the pinned
  version is bumped together with it; CI checks out the private
  `mcuhome` dependency with a read-only deploy key.
- A feature request that needs new logic is by construction a request
  against the workbench (or deeper), never against this repository —
  the boundary rule of ADR 0001.

## Open points for the CLI design round

- Publication (PyPI) is tied to going public; nothing is published
  today.
- Whether the shell's own version should ever surface (e.g. in
  `--version` output alongside the builder's) is undecided.
