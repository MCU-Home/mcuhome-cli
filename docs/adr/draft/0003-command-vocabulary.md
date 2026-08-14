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

## Open points

- `device init-pairing` naming (keep, or fold under a future
  credentials noun) — revisit when secrets tooling grows.
- `clean` semantics (what exactly is removed, per builder type).
- Mode-specific `device build` flags beyond
  `--build-mode`/`--build-server`/`--build-token` are pinned during
  implementation.
