<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0004 — Configuration ladders: method, server, SDK sources

- Status: draft
- Date: 2026-08-14

Extracted 2026-08-14. These decisions (the E53/E54 ladder, E62's
single spelling, E63's server file) had **no durable record anywhere**
— only code, the cli README, and dead-end citations ("firmware E53/
E63") in dashboard ADR 0013 pointing at firmware documents that never
carried those numbers. This draft is now their home.

## Context

Where a build runs is deployment configuration, not a property of the
device (firmware ADR 0020's build methods). The CLI needs a
predictable way to let a user say it once — per invocation, per
shell, or per machine — without the workbench having to know how the
answer was produced.

## Decision

### 1. The method ladder (E53/E54)

`--method` > `MCUHOME_BUILD_METHOD` > default. The rung order is the
usual explicit-beats-ambient rule; there is deliberately **no file
rung for the method** — a config file remembers *addresses* (§2), not
*which method this invocation means*. The method vocabulary
(`local`/`local-dev`/`remote`), the default (`local`), name validation
and the typed refusals (`UnknownMethod`, `MethodUnavailable`) are the
workbench's (firmware ADR 0020); the CLI owns the spellings and the
ladder.

**One spelling per concept (E62):** `--method local-dev` is the only
way to say host-toolchain; the older `--native` alias was removed, not
deprecated — the project is private, no external invocation can
depend on it, and two spellings for one thing is how the
builder/build-server confusion started.

### 2. The server ladder and the server file (E53 rung 3 = E63)

`--server` > `MCUHOME_BUILD_SERVER` > the server file. `--server`
takes a **URL or a label**; labels resolve through
`$XDG_CONFIG_HOME/mcuhome/build-servers.toml` with one
`[server.<label>]` table per server, and one bearer token per label in
`tokens/<label>` (trailing whitespace stripped). A label without a
token file is a typed refusal; group/world-readable token files draw a
warning. Warnings go to stderr always — in `--json` mode stdout
belongs to the document. `--token`/`MCUHOME_BUILD_TOKEN` override the
token rung.

Parsing and resolution of all of this is the CLI's
(`mcuhome_cli/servers.py`); the workbench's `BuildRequest` takes only
the **resolved** server and token and explicitly assigns the ladder to
the caller. The XDG directory resolution itself is the platform's
(`mcuhome.model.userpaths`).

### 3. SDK sources

`--sdk-source DIR` (repeatable) > `MCUHOME_SDK_SOURCE` (PATH-style,
split on `:`). The CLI owns these spellings and the splitting; pin
resolution, the one-resolver-for-both-methods rule and the sha256
byte-identity guard are the workbench's (E65, firmware ADR 0018/0019).

### 4. Ownership of the remaining spellings

| Spelling | CLI owns | Platform owns |
|---|---|---|
| `--image` | flag | `MCUHOME_BUILDER_IMAGE`, default pin, resolution order (compiler) |
| `--jobs N` | flag + type check | `MCUHOME_JOBS`, auto-detection formula (compiler) |
| `--config-root` | flag | tree discovery/resolution (workbench) |
| `--build-dir` | flag + the `build/`-sibling default | the layout rationale (builder-pipeline.md §2) |
| `--model` | flag + the XOR-with-device group | the wire-format decision (firmware ADR 0018, dashboard ADR 0007) |

### 5. The hint-text seam

The workbench's refusal messages quote CLI spellings by name —
`RemoteNotConfigured` names all three server rungs, the pin-resolver
hints name `MCUHOME_SDK_SOURCE`. That is deliberate UX (the refusal
tells a human what to type), but it means **renaming any spelling in
§1–§3 touches workbench hint text too**; the rename is still a CLI
decision, the workbench just follows.

## Consequences

- A user can pin a method per machine only via their shell
  environment, by design; addresses and tokens are the only thing the
  config file remembers.
- Everything the dashboard needs arrives as resolved values through
  `run_build` — none of these ladders exist for it (its
  `--build-method` is deployment configuration of the service, per
  dashboard ADR 0013).

## Open points for the CLI design round

- The environment-variable **names** (`MCUHOME_BUILD_METHOD`,
  `MCUHOME_BUILD_SERVER`, `MCUHOME_BUILD_TOKEN`, `MCUHOME_SDK_SOURCE`)
  are marked in code as never formally decided — confirm or rename
  (renames touch the workbench hint texts, §5).
- Whether `build-servers.toml` should gain per-server defaults beyond
  address+token (e.g. a preferred image or method) is open — today it
  deliberately remembers addresses only.
- Dashboard ADR 0013's citations "firmware E53/E63/E56" point at
  numbers no firmware document carries; when the dashboard block
  happens, they should be re-aimed at this draft and 0003 (recorded in
  ROADMAP, dashboard cleanup todo).
