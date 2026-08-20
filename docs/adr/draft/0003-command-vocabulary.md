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

Device-scoped operations live under the `device` noun, project-scoped
ones under `project` (PO 2026-08-16, with the project-version round:
`init` moved there and `info`/`upgrade` joined it — three commands about
the project *itself* are a noun, not three top-level verbs).
Environment-scoped commands stay top-level. Names are deliberately
explicit (`sign-firmware`, not `sign` — a future `sign-ota-update` may
join).

### 2. The command set

| Command | What it does | Status |
|---|---|---|
| `project init [dir]` | Creates a project in the named (or current, or `--project-dir`) directory, creating missing directories on the way: `mcuhome.yaml`, `devices/`, `secrets/` (mode 700), a `.gitignore` containing `secrets/`, and the project file with its version and id. Warns and refuses on a non-empty directory; `--force` proceeds anyway | new (was top-level `init`) |
| `project info [dir]` | What this project is: path, id and short id, project version, devices. Answers for an **outdated** project too — it is the command a person runs after another one refused | new (2026-08-16) |
| `project upgrade [dir]` | Migrates a project to the layout this MCUHome speaks (firmware ADR 0022 §1.1). `--dry-run` prints the plan and the long explanations and changes nothing; `--confirm-upgrade ID` confirms without a prompt | new (2026-08-16) |
| `config` | Reads/writes configuration values; scopes `--system`, `--user`, `--project` (default project). `config print` resolves the full inheritance tree and shows every effective value with its origin layer | new |
| `device new <device> --name NAME --board TARGET` | Scaffolds a device folder with a starter `main.yaml`. Draws **no** credentials (that is `matter-pairing --new`'s job, so builds stay byte-identical). The human-readable `--name` is destined for the device's Matter identity (BasicInformation) — emitting it is the generator's job, a platform work item | exists (moves under `device`, gains `--name`) |
| `device validate <device>` | Stages 1-3, diagnostics rendered per ADR 0004. Deliberately named `validate` (not `verify`) — `verify` is a session-protocol action and stays one | exists |
| `device build <device>` | Builds through the selected builder: default builder, `--builder NAME`, or fully manual `--build-mode` + mode-specific flags — `--build-server`, `--build-token`, the workspace path (the rung is firmware ADR 0023's; the spellings are pinned here). Every build delivers an unsigned image plus one host-side signing step unless `--no-sign` defers it | exists (flags change) |
| `device sign-firmware <device>` | Applies the detached signature to the device's last build with the local key (the `--signing-key` override continues; `--no-sign --public-key` stays a `build` flag pair) | exists (was `sign`) |
| `device flash <device>` | Flashes the last built/signed firmware. `--flash-mode recovery` = our own MCUboot serial recovery over USB CDC — needs **no** vendor tools, the bootloader presents itself as a plain serial port and accepts DFU. `--flash-mode ota` = deliberately undefined for now | stub |
| `device first-time-setup <device>` | One-time board provisioning: builds and flashes our MCUboot bootloader using vendor-specific tooling — the one deliberate exception to "nothing toolchain-shaped on the host" (ADR 0002). Which tools per vendor, and how they are obtained, is analyzed later | stub |
| `device matter-pairing <device>` | Shows the device's Matter pairing codes — the one *explicit* ask, so it prints them in the clear. `--new` draws commissioning credentials once: `!secret` references into `main.yaml`, values into the device's own `secrets/devices/<name>.yaml` (`--force` with `--new` replaces). Refuses when Matter is off — it never switches a protocol on behind the author (PO 2026-08-15; renamed from `init-pairing`, `--secrets` retired: the secrets path is the only path) | exists (renamed 2026-08-15) |
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
| top-level `init` | `project init` — no alias, and no "moved" hint either (PO 2026-08-16: the CLI has no users yet, so the break is free) |

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
- §4 executed in the same step: `mcuhome.cli.servers`,
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
- **`mcuhome project init` rendering**: created files first, then directories
  with a trailing slash, painted in ls's blue when color is on; the
  next-steps point at `device new --help` and `mcuhome --help` (whose
  epilog carries the workflow) rather than at an argument the reader
  cannot fill in yet.
- **Stable links** (`t.mcuhome.org`, the project's *target* host —
  workspace decision): pages a shipped binary links to, under the
  scheme `/<source-repo>/<target-area>/<target-detail>/<version>` —
  the linking tool is part of a link's identity (the dashboard's
  getting-started is not the CLI's), the version is the linking tool's
  `major.minor` (`…/cli/docs/getting-started/0.1`). Static content
  today, redirects to docs.mcuhome.org once that exists; the
  repository behind it is github.com/mcu-home/site-t.mcuhome.org.
- **Board discoverability**: `device boards` (table row above) answers
  from `registry.BOARDS`/`PLANNED_BOARDS`, and `--board`'s help and the
  unknown-board refusal point at it. Deliberately not a link to
  Zephyr's board list: MCUHome accepts only boards it has brought up.
- **`device matter-pairing`** resolves the old `init-pairing` open
  point (PO 2026-08-15): Matter in the name — a WiFi-only device of a
  future protocol has no business with this command, and the old name
  said nothing. Show by default, `--new` to draw, `--force` only
  beside `--new` (alone it is an exit-2 refusal). Retired without
  aliases: the `init-pairing` spelling and its `--secrets` flag.
- **Sensitive output** (PO 2026-08-15): *passing-by* human output —
  `device validate`'s summary, the pairing reminder at the end of a
  build — masks the pairing codes (manual code, QR payload); the
  discriminator stays, the device broadcasts it anyway. The explicit
  asks show them: `device matter-pairing`, or `validate
  --show-sensitive`. The machine documents (`-o json`) stay complete
  on purpose — `validate -o json` is the wire form `device build
  --model` consumes, and masked values there would build wrong
  firmware. Coupling masking generally to `!secret` provenance is a
  named later step: it needs provenance through the resolved model,
  which the loader deliberately erases today.

## One build directory, one operation at a time (PO 2026-08-16)

Two builds of one device ran at once and the second one's fresh work
tree deleted the first one's generated headers mid-compile; the first
died on `autoconf.h: No such file or directory`, which explains nothing.
The guard is a lock on the **build directory**
(`mcuhome.workbench.build_lock`, `.mcuhome-build.lock`, an OS advisory
lock the kernel releases when the holder ends), and the rule is a
command-vocabulary rule rather than a build one:

- **Every command that reads or writes a build directory takes it**,
  naming what it does: `build`, `sign`, `flash`, `clean`. `device build`
  holds it for the whole command — generate, compile, collect *and* the
  host-side signing — not just for the compile; `device sign-firmware`
  holds it for its run.
- **`device flash`, `device first-time-setup` and `clean` take it the
  day they stop being stubs**, with `operation="flash"` / `"clean"`.
  This is the point of the rule: a build that rewrites
  `firmware.signed.hex` while it is being flashed puts half of one image
  and half of another on the device, and neither command could notice.
- The refusal names the running operation, its process and its start
  time; only a refused *build* is offered the `--build-dir` escape,
  because only a build can go somewhere else.
- Read-only commands (`validate`, `list`, `boards`, `config`, `doctor`,
  `matter-pairing`) do not take it: they touch the configuration, not
  the build output.

## The project noun and its upgrade (PO 2026-08-16)

The project-version round of firmware ADR 0022 §1.1 lands here as three
commands and one new interaction. What is pinned:

- **The noun.** `project init` / `project info` / `project upgrade`;
  top-level `init` is gone without an alias and without a hint.
- **The path argument.** All three take an optional directory. Given
  one, it names *that* directory and the upward search is off — the
  rule `--project-dir` follows everywhere. Without one, `init` means
  here and the other two search upward, because a person standing in
  `devices/porch` means their project. `init` creates missing
  directories on the way; `upgrade` and `info` require an existing
  project.
- **The confirmation.** An upgrade cannot be undone, so it asks for a
  typed `yes` — not a keypress, not a default-yes. Without a terminal
  the flag is `--confirm-upgrade ID`, which takes the **project's own
  id** (full or the six-character short form) rather than a
  `--force`-shaped switch: a `--force` in a script is set once and then
  applies to whatever directory the script ends up in, while an id
  refuses to name the wrong project. `--no-interactive` without it is
  an exit-2 usage refusal that prints the exact command to run.
- **The order** (the load-bearing part): take the project — which
  renames its file, so nothing new can start — *then* wait for builds
  that were already running, *then* ask. Asking first and waiting after
  would leave a person watching a wait they may cancel at the exact
  moment it ends, with the upgrade starting into the cancellation.
- **Ctrl+C.** During the wait it cancels immediately and puts the
  project back. During the migrations it does **not**: the current
  migration finishes, the version reached is written, and the run stops
  cleanly, because interrupting a migration half-way is what breaks a
  project. Three presses within three seconds abort anyway, after a
  warning saying what that costs; the window is a window, so a stray
  press an hour later only prints the hint again. SIGTERM behaves like
  one press. SIGKILL cannot be caught, and the renamed project file is
  what makes that case legible afterwards.
- **`--dry-run`** prints the plan and every migration's long
  explanation and touches nothing — including the rename.
- **Output.** All three commands take `-o json`: the dashboard will use
  the API directly, but a user's own scripts drive the CLI.
- **The docs link.** The end of an upgrade, and the refusal after an
  interrupted one, point at `t.mcuhome.org/cli/docs/project-upgrade/…`
  — the one place that can say "restore your backup" at length.

## Open points

- `clean` semantics (what exactly is removed, per builder type).
