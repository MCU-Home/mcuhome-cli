<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0002 — The thin shell and its packaging

- Status: draft
- Date: 2026-08-14
- Will supersede on finalization: the cli dependency arrow recorded in
  firmware ADR 0017 §1 / ADR 0020 (`mcuhome-compiler` →
  `mcuhome-workbench[local,remote]`)

Originally extracted from firmware ADR 0017/0020/0007; reshaped by the
product owner's CLI design round of 2026-08-14.

## Context

The command line is the first of the two user-facing tools (the
dashboard is the second; both drive the same workbench). The guiding
usage picture for defaults and priorities: MCUHome will mostly run in
the Home Assistant world, split across two Apps — a dashboard App and
a build-server App — where building is always remote. MCUHome stays
fully standalone-capable, but the standalone user is closer to a
developer and may face more manual setup.

## Decision

1. **The CLI is a thin shell.** It parses arguments, resolves
   configuration, and renders results; every stage of the actual
   pipeline (validate, generate, compile, sign) is a call into
   `mcuhome.workbench.api`, the supported programmatic surface.
   Programs never drive the shell — they embed the workbench. Logic
   that is not argument parsing, configuration binding, or rendering
   does not belong in this repository.

2. **The distribution bears the plain name `mcuhome`**, so that
   `pip install mcuhome` yields the command a user expects; the
   console script has the same name (`mcuhome = mcuhome_cli.cli:main`;
   `python -m mcuhome_cli` is the identical entry). The platform
   repository renounced the plain name for exactly this purpose
   (firmware ADR 0020 §2). The import package is `mcuhome_cli`.

3. **The direct dependency is the workbench:**
   `mcuhome-workbench[local,remote]`. The workbench is the API the
   shell actually talks to, so it is the declared dependency — the
   compiler is deliberately **not** a direct dependency; it arrives
   through the workbench's `local` extra, which the workbench owns
   (the extra is new — a small platform-side change this draft
   requires). All three build modes work out of the box: `local` and
   `local-dev` via the `local` extra (compiler), `remote` via the
   `remote` extra (aiohttp, zstandard). Standing rules: using the CLI
   must never require the dashboard, and the command adds nothing
   toolchain-shaped to the host — with exactly one deliberate
   exception, `device first-time-setup` (ADR 0003), whose job is to
   install our bootloader using vendor tooling once.

4. **Versioning: SemVer, coupled at major.minor from v1.0.** Every
   repository releases SemVer. From v1.0 on, the CLI's major.minor is
   coupled to the platform's: when the platform releases X.Y.0, the
   CLI follows with its own X.Y.0 within days. Patch releases are
   per-repository, independent, and must not change APIs — cli X.Y.a
   works with workbench X.Y.b for any a, b. Every cross-repository
   dependency edge is therefore a PEP 440 `~=X.Y.0` constraint (same
   major.minor family, newest patch wins). Before v1.0 nothing is
   enforced: there are no published packages yet, development runs on
   editable checkouts.

5. **`mcuhome --version` reports the whole stack**, one line per
   part: the CLI's own version, the workbench version, the compiler
   version (when installed), and the model version. (This replaces
   the earlier builder-version-only behavior.)

## Consequences

- `pip install mcuhome` is the whole install story once packages are
  published; until then the editable-checkout install in the README
  is the documented path.
- A feature request that needs new logic is by construction a request
  against the workbench (or deeper), never against this repository —
  the boundary rule of ADR 0001.
- The workbench gains a `local` extra (platform-side work item); the
  cli's pyproject changes from `mcuhome-compiler` to
  `mcuhome-workbench[local,remote]`.
- The ruff settings mirror the platform repository; the pinned version
  is bumped together with it.

## Open points

- PyPI publication timing: repositories go public with the split
  block, but publishing 0.x packages to PyPI is a separate decision,
  not yet taken.
