<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0003 — Command vocabulary

- Status: draft
- Date: 2026-08-14

Redefined from scratch in the product owner's CLI design round of
2026-08-14. The previous revision of this draft recorded the surface
that had grown during platform development; that surface is source
material and migration mapping here, not the design.

## Context

The as-grown command set was flat, its spellings were never decided as
a set, and whole areas (project setup, configuration, flashing) had no
commands at all. The design round defined the vocabulary as a product:
noun-grouped, explicit, extensible.

## Decision

### 1. Shape: one noun namespace, explicit names

Device-scoped operations live under the `device` noun. Names are
deliberately explicit (`sign-firmware`, not `sign` — a future
`sign-ota-update` may join). Project-scoped and environment-scoped
commands stay top-level.

### 2. The command set

| Command | What it does | Status |
|---|---|---|
| `init` | Creates a project in the current (or `--project-dir`) directory: `mcuhome.yaml`, `devices/`, `secrets/` (mode 700) and a `.gitignore` containing `secrets/`. Warns and refuses on a non-empty directory; `--force` proceeds anyway | new |
| `config` | Reads/writes configuration values; scopes `--system`, `--user`, `--project` (default project). `config print` resolves the full inheritance tree and shows every effective value with its origin layer | new |
| `device new <device> --name NAME --board TARGET` | Scaffolds a device folder with a starter `main.yaml`. Draws **no** credentials (that is `init-pairing`'s job, so builds stay byte-identical). The human-readable `--name` is destined for the device's Matter identity (BasicInformation) — emitting it is the generator's job, a platform work item | exists (moves under `device`, gains `--name`) |
| `device validate <device>` | Stages 1-3, diagnostics rendered per ADR 0004. Deliberately named `validate` (not `verify`) — `verify` is a session-protocol action and stays one | exists |
| `device build <device>` | Builds through the selected builder: default builder, `--builder NAME`, or fully manual `--build-mode` + mode-specific flags — `--build-server`, `--build-token`, the workspace path (the rung is firmware ADR 0023's; the spellings are pinned here). Every build delivers an unsigned image plus one host-side signing step unless `--no-sign` defers it | exists (flags change) |
| `device sign-firmware <device>` | Applies the detached signature to the device's last build with the local key (the `--signing-key` override continues; `--no-sign --public-key` stays a `build` flag pair) | exists (was `sign`) |
| `device flash <device>` | Flashes the last built/signed firmware. `--flash-mode recovery` = our own MCUboot serial recovery over USB CDC — needs **no** vendor tools, the bootloader presents itself as a plain serial port and accepts DFU. `--flash-mode ota` = deliberately undefined for now | stub |
| `device first-time-setup <device>` | One-time board provisioning: builds and flashes our MCUboot bootloader using vendor-specific tooling — the one deliberate exception to "nothing toolchain-shaped on the host" (ADR 0002). Which tools per vendor, and how they are obtained, is analyzed later | stub |
| `device init-pairing <device>` | Draws commissioning credentials once, into the project's secrets (`--force`, `--secrets` continue). Pairing codes are printed, never stored | exists |
| `device list` | Lists the project's devices with their state | new |
| `device boards` | Lists the boards MCUHome builds for, and the planned ones, from the registry — under `device` because boards only mean anything to a device (PO 2026-08-15) | new |
| `schema [config\|registry]` | Emits the `main.yaml` JSON Schema or the registry as JSON | exists |
| `public-key` | Prints/writes the public half of the signing key; never generates one | exists |
| `doctor` | Environment diagnosis: docker reachable, image present, project valid, permissions sane — the "why does nothing work" command | new |
| `clean <device\|--all>` | Removes build output | stub |
| `--version` | The whole stack, one line per part (ADR 0002) | changes |

### 3. General flags

`--project-dir` / `MCUHOME_PROJECT_DIR` (the project-directory
override, firmware ADR 0022 — replaces `--config-root`), `-o/--output`,
`--color`, `--interactive`/`--no-interactive` (all ADR 0004),
`-v/--verbose`. Every general option follows the configuration model
of firmware ADR 0022: declared once, with the channels it may be set
through.

### 4. Retirements

Replaced without compatibility aliases (pre-1.0, nothing external
depends on the spellings — the E62 rule):

| Old | Replaced by |
|---|---|
| flat `validate`/`build`/`sign` … | the `device` namespace |
| `--json` | `-o json` / `-o json-stream` (ADR 0004) |
| `--method` / `MCUHOME_BUILD_METHOD` | `--build-mode` + the builder configuration (firmware ADR 0023) |
| `--server`, `--token`, `MCUHOME_BUILD_SERVER/_TOKEN`, `build-servers.toml`, `tokens/<label>` | named builders + `secrets/build-server/<name>.yaml` (firmware ADR 0023) |
| `--config-root`, tree auto-discovery | `--project-dir` + `mcuhome.yaml` upward search (firmware ADR 0022) |

## Consequences

- The dashboard is untouched — it embeds `mcuhome.workbench.api`; this
  vocabulary exists for humans and scripts.
- `first-time-setup` and `flash --flash-mode recovery` give the full
  no-vendor-tools loop after a single provisioning step; both wait on
  platform work (phase 3: CDC-ACM recovery in our bootloader) and ship
  as honest stubs until then.
- Old spellings die in the same release that introduces the new ones.

## Pinned during implementation (C1, 2026-08-14)

- `init` shipped ahead of the vocabulary step, with this table's
  semantics (plus: it also creates the ADR 0022 marker and `devices/`,
  is a no-op on an existing project, and takes an optional positional
  directory). It could not wait: the project model removed the implicit
  tree creation the old `new` did, so without `init` a fresh machine
  had no way to a first project. Everything else in §2 lands as its own
  step.

## Pinned during implementation (C2, 2026-08-14)

The whole of §2 and §4 landed as one step. What the table left open:

- **The `-o` collision** (PO decision 2026-08-14): `schema` and
  `public-key` lost their `-o PATH` spelling without replacement —
  their document goes to stdout, which per ADR 0004 *is* the file API
  (`mcuhome public-key > signing.pub`). `-o` means the output format
  everywhere it exists, and nothing else, anywhere.
- **Mode-specific `device build` flags**: `--build-server` and
  `--build-token` (remote), `--workspace` (local-dev; optional — the
  discovery from the install location and the working directory stays
  the default), `--container-image` (local; spelled out because a bare
  "image" means firmware in this tool — PO 2026-08-15, renamed from
  `--image` without alias; the builder-list key stays `image`, scoped
  by its `type: local` block, and so does the compiler's
  `MCUHOME_BUILDER_IMAGE`). The rungs do not mix: a mode flag
  without `--build-mode` — or beside the wrong mode, or `--builder`
  together with `--build-mode`, or `--build-mode remote` without
  `--build-server` — is an exit-2 refusal in the validate phase, and
  the build never starts. `--build-mode` itself is an argparse choice
  of the three method names, so a typo is exit 2 too.
- `device sign-firmware` takes a device name (signs that device's last
  build, `<project>/build/<device>/`) *or* a path — a build directory
  or one of the two report files — for the detached workflow, which
  owns no project. A path that is a build wins over a device sharing
  its spelling; an existing path that is neither is refused with what a
  sign target must hold.
- `config` verbs: `print` (the resolved tree, per-option origin; the
  per-builder defining layer included), `get NAME`, `set NAME VALUE`,
  `unset NAME`. The scope flags sit on `set`/`unset` only, default
  `--project`; writes go through the workbench
  (`set_config_value`/`unset_config_value`, ADR 0022) — round-trip
  edited, so comments and `!file` references survive. List-valued
  options split `os.pathsep`-style on `set`, like their environment
  variable; `builders` is refused toward the file's own `builders:`
  list.
- `device list` columns: device, board, status (`ok` / *n problems*),
  build (`-` / `unsigned` / `signed`). The board falls back to the raw
  YAML when the device does not validate — one drawn credential away
  from valid still names its board. Build state reads both report
  shapes (E55).
- `doctor` checks, in order: stack (versions), project, configuration
  (resolves), builders (list + default resolvable; credential warnings
  surface here), container (docker, daemon, image — the compiler's own
  preflight), secrets (permission walk). Any `fail` exits 1; warnings
  alone exit 0.
- The stubs (`device flash`, `device first-time-setup`, `clean`)
  refuse as typed errors naming the plan — exit 1, uniform rendering,
  never a silent no-op.
- `device new --name NAME` writes the starter's `friendly_name`
  (quoted); the default stays the title-cased device name. Emitting it
  into the Matter identity remains the generator's work item.
- §4 executed in the same step: `mcuhome_cli.servers`,
  `build-servers.toml`, `tokens/<label>`, `MCUHOME_BUILD_*` and the
  `--method`/`--server`/`--token` spellings are gone; user-facing
  texts in all repos (hints, docstrings, scaffold) speak this ADR's
  vocabulary.

## Pinned in the usability round (PO feedback, 2026-08-15)

The product owner's first hands-on pass over the C2 surface; each item
is a decision, not a style preference.

- **The help contract.** A usage line *identifies* rather than
  enumerates: positionals plus required flags plus `[options]`, and a
  usage error appends "Run … --help for the full option list" instead
  of re-listing everything. In `--help` itself, the command's own
  options come first and the shared flags (`-h`, `-o`,
  `--project-dir`, `-v`, `--color`, `--interactive`) sit apart as a
  *general options* group below. And `-h`/`--help` wins wherever it
  stands — scanned before the parse, so `device new --board -h` helps
  instead of complaining about `--board`'s missing value; only a `--`
  separator ends the scan. Help returns exit 0 as a plain return, not
  an argparse `SystemExit`.
- **Refusal order.** The project is resolved first at runtime, before
  any command-specific validation — outside a project the user hears
  *that*, once, not after every corrected argument. Parser syntax
  errors (exit 2) stay ahead of it, the convention of every CLI: a
  syntactically broken invocation has no command yet.
- **`mcuhome init` rendering**: created files first, then directories
  with a trailing slash, painted in ls's blue when color is on; the
  next-steps point at `device new --help` and `mcuhome --help` (whose
  epilog carries the workflow) rather than at an argument the reader
  cannot fill in yet.
- **Stable links** (`t.mcuhome.org`, the project's *target* host —
  workspace decision): pages a shipped binary links to, one path per
  page, versioned by this CLI's `major.minor`
  (`…/docs/getting-started/0.1`). Static content today, redirects to
  docs.mcuhome.org once that exists; the repository behind it is
  github.com/mcu-home/t.mcuhome.org.
- **Board discoverability**: `device boards` (table row above) answers
  from `registry.BOARDS`/`PLANNED_BOARDS`, and `--board`'s help and the
  unknown-board refusal point at it. Deliberately not a link to
  Zephyr's board list: MCUHome accepts only boards it has brought up.

## Open points

- `device init-pairing` naming (keep, or fold under a future
  credentials noun) — revisit when secrets tooling grows.
- `clean` semantics (what exactly is removed, per builder type).
