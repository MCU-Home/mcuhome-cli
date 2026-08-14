<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# Architecture Decision Records

CLI-specific design decisions, in lightweight
[MADR](https://adr.github.io/madr/) style: **Context / Decision /
Consequences**, plus a status.

**Scope (boundary rule, ADR 0001):** only decisions about the CLI
itself live here — command vocabulary, flag/environment spellings,
output and exit-code contract, packaging, the thin-shell nature.
Anything concerning `mcuhome.workbench`/`mcuhome.compiler` or other
components the CLI merely calls belongs in that component's
repository. Project-wide decisions live in
[mcu-home/mcuhome/docs/adr](https://github.com/mcu-home/mcuhome/tree/main/docs/adr).

## Lifecycle: draft first, final when real

ADRs follow the project-wide draft-first lifecycle of
[ADR 0021](https://github.com/mcu-home/mcuhome/blob/main/docs/adr/0021-draft-first-adr-lifecycle.md):
an ADR starts in [`draft/`](draft/) as a **living document** — while
the component it decides about is being built, changes land as better
text, never as amendment or erratum sections; git history is the
changelog. `draft` describes the document's maturity, not missing
approval. When the component is implemented and verified, the ADR is
finalized: rewritten from the real result and moved to this directory
with a `Finalized:` date, immutable from then on except for its status
line. Numbers come from one sequence and follow the document for life.

Statuses: `draft` (in `draft/`), `accepted`, `deferred`,
`superseded by NNNN`.

## Final ADRs

None yet — the CLI is before its detailed design round; everything is
draft.

## Draft ADRs

| ADR | Title |
|---|---|
| [0001](draft/0001-record-cli-decisions-here.md) | Record CLI decisions in this repository |
| [0002](draft/0002-the-thin-shell-and-its-packaging.md) | The thin shell and its packaging |
| [0003](draft/0003-command-vocabulary.md) | Command vocabulary |
| [0004](draft/0004-output-interactivity-i18n.md) | Output, interactivity, internationalization |
