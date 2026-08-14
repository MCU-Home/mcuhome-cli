<!--
SPDX-FileCopyrightText: 2026 The MCUHome Contributors
SPDX-License-Identifier: Apache-2.0
-->

# 0004 — Output, interactivity, internationalization

- Status: draft
- Date: 2026-08-14

Defined in the product owner's CLI design round of 2026-08-14. This
draft's previous subject (the configuration ladders) moved to the
platform: the configuration model is firmware ADR 0022, builder
selection firmware ADR 0023 — shared with the dashboard, and therefore
not a CLI decision.

## Context

The CLI is the product's face for terminal users. Its output grew
command by command with no shared design: mixed rendering styles, a
single-purpose `--json` flag on two commands, no interactivity rules,
no i18n story, exit codes inherited from argparse. The design round
replaces all of that with one contract.

## Decision

### 1. Human output, uniformly rendered

The default output is for an interactive human. Rendering is
**uniform across every command**: the same table style, the same
diagnostic form, the same progress presentation everywhere — a user
who has seen one command's output has seen them all. Long-running
tasks show progress; progress is honest — stages and counters from
real events, never invented percentages. Errors are sensible: what
failed, where, and what to type next.

### 2. Machine output: `-o/--output`

A general `-o/--output` flag selects the format: `human` (default),
`json`, `json-stream`.

- **`-o json` (static):** stdout carries exactly one JSON document,
  emitted after the run completes. Failure form:
  `{"ok": false, "errors": [...]}`.
- **`-o json-stream` (live):** NDJSON — one JSON message per line, as
  the run progresses. Four verbs, and only these for now:
  `start` (a task begins), `progress` (stage/counter updates),
  `error`, `result` (the final document — byte-for-byte the same
  document `-o json` would have emitted). The verb vocabulary is
  **append-only** and consumers must ignore unknown verbs, so later
  additions (an interactive `question`/`answer` pair is explicitly
  deferred, not designed) never break existing consumers.

Stream discipline, all modes: stdout belongs to the document or the
human rendering; logs, warnings and rendered errors go to stderr.
Machine documents never contain localized text in structural positions
(keys, verbs, codes).

### 3. Interactivity: interact → validate → execute

Default: auto-detected — interactive when attached to a TTY, otherwise
non-interactive. `--interactive` / `--no-interactive` override the
detection; `-o json` and `-o json-stream` force non-interactive.

Every command runs in three phases, in this order and with this
contract (PO 2026-08-14):

1. **interact** — runs only in interactive mode, and **at application
   start**: every question the command will need answered is asked and
   answered up front, never scattered through the run.
2. **validate** — checks that every required input is present and
   valid, wherever it came from (arguments, environment,
   configuration, or the interact phase). Any gap ends the process
   here with exit code 2.
3. **execute** — the actual action. It is **never invoked** when
   validate failed: the boundary is not "before anything is written"
   but sharper — the action does not start at all.

In non-interactive mode the interact phase is skipped entirely, so a
missing required input is an immediate exit-2 refusal in validate.
The three phases are the *contract*, not a prescribed class layout —
how a command implements them is its own business.

### 4. Exit codes

Exactly three: `0` success, `1` operation failed, `2` usage or
argument error (including the non-interactive missing-input refusal).
Nothing else, deliberately.

### 5. Colors

`--color auto|always|never`, default `auto` (TTY detection). The
`NO_COLOR` environment convention is respected.

### 6. Internationalization

Messages are externalized gettext-style from day one; the shipped
language is English, translations come when the project's community
does. Machine output — JSON keys, stream verbs, error codes, config
keys — is never translated.

## Consequences

- The old `--json` flag and its per-command envelopes are retired with
  this contract (ADR 0003 §4); consumers move to `-o`.
- `-o json-stream` is what the dashboard-style live experience looks
  like in a pipe — CI systems and wrappers get live progress without
  scraping human output.
- Uniform rendering means a small internal rendering layer instead of
  per-command print statements — the one place the "thin shell" is
  allowed to have real code (ADR 0002: rendering is the shell's job).
- Honest progress depends on what the workbench emits; where the
  platform provides only a log stream, the CLI shows stages, not
  percentages.

## Open points

- The exact JSON document schemas per command (the old build/validate
  envelopes are the starting point) are pinned during implementation.
- `question`/`answer` stream verbs: deferred; design only when a
  consumer actually needs them.
