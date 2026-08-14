<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0001 — Record CLI decisions in this repository

- Status: draft
- Date: 2026-08-14

## Context

The `mcuhome` command line had no decision records of its own. Its
decisions were scattered: the distribution name lived in firmware
ADR 0020 §2, the thin-shell topology in firmware ADR 0017, the
machine-readable surface in a **superseded** dashboard ADR (0011), the
signing and pairing spellings in firmware draft ADRs 0015/0016, and
the configuration ladders in nothing but code and README. Anyone
redesigning the CLI had no single place to read what is decided, and
the repositories that did carry the records are the wrong owners.

## Decision

CLI design decisions are recorded as ADRs in this repository,
following the project-wide draft-first lifecycle of
[ADR 0021](https://github.com/mcu-home/mcuhome/blob/main/docs/adr/0021-draft-first-adr-lifecycle.md):
drafts in `docs/adr/draft/` are living documents; a final ADR is
written from the real result once the component is done, and is then
immutable.

**Boundary rule (product owner, 2026-08-14):** ADRs here may only
concern the CLI itself — command vocabulary, flag and environment
spellings, output and exit-code contract, the shell's packaging and
its thin-shell nature. Anything that concerns `mcuhome.workbench`,
`mcuhome.compiler`, the session protocol, or any other component the
CLI merely calls belongs in that component's repository. The test:
*if the CLI were rewritten from scratch, would this decision constrain
the rewrite (CLI) or the platform underneath it (not CLI)?*

The one-time extraction of 2026-08-14 moved the existing CLI decisions
out of the firmware ADRs into drafts 0002–0004 here; the source ADRs
now reference these instead of recording them. Dashboard ADRs were
deliberately left untouched (their cleanup is a future dashboard
block).

## Consequences

- One place to read what is decided about the CLI, before the detailed
  CLI design round.
- The firmware ADRs stay the home of everything platform-shaped; the
  workbench/CLI seam of each mechanism is stated explicitly in the
  drafts here.
- Superseded dashboard ADR 0011 remains frozen history; its live CLI
  content now lives here (drafts 0003/0004).
