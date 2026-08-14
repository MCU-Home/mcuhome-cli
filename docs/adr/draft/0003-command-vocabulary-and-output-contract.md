<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0003 — Command vocabulary and output contract

- Status: draft
- Date: 2026-08-14

Extracted 2026-08-14. The `--json` surface and `mcuhome new` were
first recorded in dashboard ADR 0011 "Block 0" (now superseded,
frozen); the signing spellings in firmware draft ADR 0015 decision 8;
the pairing output contract in firmware draft ADR 0016 decision 6; the
rest only in code and READMEs. This draft is now the home of all of
it; the sources reference it.

## Context

The command set and its output behaviour are the CLI's actual product.
They grew decision by decision across two years of platform work
without a record of their own. This draft states the as-built surface
so the detailed CLI design round has one document to review, amend, or
overturn.

## Decision

### 1. The command set

| Command | What it does | Logic lives in |
|---|---|---|
| `new <device> --board TARGET` | Scaffolds a device folder with a starter `main.yaml` (creates the configuration tree if needed). `--board` is required; choices come from the registry. Draws **no** credentials — that is `init-pairing`'s job, so builds stay byte-identical | workbench `scaffold.new_device` |
| `validate <device>` | Stages 1–3; human summary, or `--json` | workbench `api.validate_device` |
| `build <device \| --model PATH>` | Stages 1–5 through one of three build methods; `--model` starts at stage 4, reads no tree and no secrets. `--generate-only` stops after stage 4. `-S/--snippet` (repeatable) appends after the configuration's own snippets, deduplicated | workbench `api.run_build` |
| `sign <build-dir \| manifest \| report>` | Applies the detached signature to a finished build; accepts a build directory, `build-manifest.json` or `build-report.json` | workbench `imgtool.sign_*` |
| `init-pairing <device>` | Draws commissioning credentials once, into the device's YAML; `--force` replaces, `--secrets` writes to `secrets.yaml` with `!secret` references | workbench `provision.init_pairing` |
| `public-key` | Prints (or `-o` writes) the public half of the signing key; never generates one | workbench `signing.public_key_pem` |
| `schema [config\|registry]` | Emits the `main.yaml` JSON Schema or the boards/drivers/clusters/device-types registry as JSON | workbench/model export |
| `clean <device \| --all>` | Deliberate stub: exists so the surface is stable, prints "not implemented yet" | cli only |

Top level: a bare `mcuhome` prints help and exits 0; `-v/--verbose` is
accepted before and after the subcommand; `--version` reports the
builder's version (ADR 0002 decision 4).

### 2. Detached signing, as the user types it

Every build method delivers an **unsigned** image; signing is one
host-side step. The CLI composes that step itself over
`mcuhome.workbench.imgtool` — the same each-caller-composes-it pattern
dashboard ADR 0013 decision 6 records for the dashboard. Spellings:
`build --no-sign --public-key <file>` (the pair is enforced; a private
key passed as `--public-key` is refused), `sign <build-dir>`,
`public-key`, and `--signing-key <path>` to override the per-user key.
The key mechanics, key paths and `MCUHOME_SIGNING_KEY` are the
workbench's (firmware draft ADR 0015 decision 8).

### 3. Pairing codes are printed, never stored

`validate`, `build` and `init-pairing` print the manual pairing code
and the `MT:` QR payload; nothing else emits them, and no build
artifact contains them (firmware draft ADR 0016 decision 6 owns the
credential model).

### 4. The machine-readable surface (`--json`)

`--json` exists on `validate` and `build` only. stdout carries exactly
one JSON document, written by a single funnel; everything else moves
to stderr — rendered errors (with tree-relative paths against the
caller's cwd), the streamed build log, and warnings (always stderr,
even without `--json`). stdout is flushed before errors and before
subprocess logs, so ordering holds.

Document shapes:

- `validate --json`: the whole canonical device model, exactly as
  `device-model.json` — including commissioning credentials, because
  the resolved model *is* the build input (the document body is the
  workbench's `ValidationResult.to_dict()`).
- `build --json`, three envelope variants, assembled by the CLI:
  `--generate-only` → `{ok, device, build_dir, generated,
  manifest: null}`; local-dev → `{ok, device, build_dir, generated,
  manifest_path, manifest}` (manifest re-read from disk); local/remote
  → `{ok, device, build_dir, image, signed, artifacts: [{role, path}],
  signed_artifacts, report, ota}`.
- Failure, either command: `{"ok": false, "errors": [...]}` with the
  model's error dictionaries.

Ownership: the envelope keys and the stream discipline are CLI
decisions; the document bodies (model, manifest, report, error dicts)
are platform-owned.

### 5. Exit codes

`0` success; `1` for every rendered `MCUHomeError`, for
`validate --json` with `ok: false`, and for the `clean` stub; `2` for
argparse usage errors (argparse's default — see open points).

## Consequences

- The dashboard and any other program embed
  `mcuhome.workbench.api` and are unaffected by anything here — this
  surface exists for humans and shell scripts.
- Changing an envelope key or a spelling is a CLI decision with CLI
  compatibility cost only; changing what a document *contains* is a
  platform decision.

## Open points for the CLI design round

- `clean` is an unimplemented placeholder — implement, redesign, or
  drop.
- Exit code `2` for usage errors is inherited from argparse, nowhere
  decided; decide and pin it (or a different code map).
- `cli.py`'s module docstring promises "a build server feature-probes
  `mcuhome build --help`" — no build-server code does that since the
  job protocol was dismantled. Decide: keep as a forward promise or
  delete.
- `--keep-going` was once documented (builder-pipeline.md §8) but
  never implemented; it is dropped from the record here and should
  stay dropped unless re-decided.
- The human output rendering (summaries, footprint tables) has no
  recorded design at all — fair game for the design round.
