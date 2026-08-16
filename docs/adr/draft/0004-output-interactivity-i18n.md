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

## Pinned during implementation (C1, 2026-08-14)

- Module homes: the contract lives in three modules of `mcuhome_cli` —
  `output.py` (modes, stream discipline, colors, the resolved
  interactivity), `phases.py` (the three-phase runner and the exit-code
  vocabulary), `i18n.py` (the gettext scaffolding, domain `mcuhome`,
  catalogs under `mcuhome_cli/locale/`).
- Stream message shape: every NDJSON message is
  `{"verb": ..., ...payload}`; `result` carries the document under a
  `document` key, `error` one serialized error under `error`. The
  `result` document is the same JSON **value** `-o json` prints — the
  serialization differs (`-o json` indents for the human peeking at a
  pipe, the stream is one compact line per message, flushed as it
  happens).
- `-o` is wired on `validate` and `build` — the two commands that had
  `--json`. The remaining commands take it as the vocabulary step
  rebuilds them (ADR 0003); until then `schema`/`public-key` keep their
  old `-o PATH` meaning ("write to a file"), which is exactly why the
  step must rename one of the two spellings.
- Phase adoption in C1: every command runs through the runner;
  `build` declares the first real validate phase (the
  `--no-sign`/`--public-key` pairing and the public-key file checks —
  read-only, instant, exit 2). Deeper per-command validate phases land
  with the vocabulary step, when each command is rebuilt anyway.
- i18n staging: the *skeleton* is wired from day one (the new modules'
  messages go through `_()`); the as-grown command texts are deliberately
  not wrapped — they are replaced wholesale by the vocabulary step, and
  wrapping strings that are about to die is motion, not progress.
- Colors in C1 are infrastructure plus first uses (error first line,
  warning prefix, the NEW-key note); the uniform rendering layer of §1
  is the vocabulary step's companion work.

## Pinned during implementation (C2, 2026-08-14)

- The `-o PATH` collision is resolved (PO 2026-08-14): the old
  file-writing spelling on `schema`/`public-key` retired without a
  replacement flag — stdout is the document channel (§2), and writing
  a file is a shell redirect. `-o` selects the output format on every
  command that has machine output (`device validate`, `device build`,
  `device list`, `config *`, `doctor`) and means nothing else anywhere.
- §1's one table style is `mcuhome_cli.output.format_table` — plain
  cells, aligned columns, two spaces apart; a caller that wants color
  styles the finished line (alignment counts characters). `device
  list` and `config print` render through it; `doctor` styles its
  status column the same way the error renderer styles its first line
  (green/yellow/red, bold).
- New machine documents, all `{"ok": ...}`-shaped like the existing
  two: `config print` (`config`: per-option value/origin/source),
  `config get`/`set`/`unset` (the one option, the file), `device list`
  (`devices`: name/board/ok/problems/built/signed), `doctor`
  (`checks`: check/status/detail; `ok` is "no check failed"). Stream
  `start` tasks: `config-print`, `config-get`, `config-set`,
  `config-unset`, `list`, `doctor`.
- i18n: the C2-rebuilt command texts go through `_()`; rendering-heavy
  data lines (paths, tables, key-value dumps) stay unwrapped — they
  carry no prose to translate.
- Interactivity detection: stdin **and** stdout must be TTYs; no command
  asks questions yet, so the interact phase is empty everywhere — the
  contract and its forcing rules are in place and tested.

## The live build view (PO 2026-08-15)

A build is minutes of someone else's output; the two questions a person
has — how far along, and where is this running — got no answer while
the compile log scrolled the terminal away. Decided and built
(`mcuhome_cli/buildview.py`):

- **Step line.** Every build states its steps up front and repaints
  them in place: green done, yellow running, red failed, plain pending.
  Each label carries the execution place — `compile (container
  zephyr-4.4.0-r9)`, `compile (remote attic)`, `compile (local west)`,
  `sign (local)` — because making *where* visible is half the point.
- **The window.** The build log shows through a fixed 15-line frame
  (`WINDOW_LINES`), newest at the bottom, repainted in place
  (docker-build style, plain ANSI, throttled, thread-safe). On finish
  the frame collapses to the step line + log pointer; the summary
  prints below. On failure it collapses, the log **tail** is printed
  back (the frame's scrollback is gone with it), then the refusal — the
  things worth keeping are what remains.
- **`build.log` always.** The full log goes to `build.log` in the build
  directory in every mode and method; the line under the step line says
  so. The file is contract, the frame is rendering.
- **Degradation.** The live view runs only on an interactive human run
  (ADR §3 detection). Non-TTY, `--no-interactive` and the machine modes
  keep the linear behavior: log lines pass through (stdout human,
  stderr machine), steps stay silent in linear human runs and travel as
  `progress` verbs in the stream.
- **The honest-progress seam.** `BuildRequest.on_step` (workbench):
  the build methods state `context` and `compile` themselves; the CLI
  adds `artifacts` and `sign`. Stage names are append-only stream
  vocabulary like the verbs; `-o json-stream` gained `context` and
  `artifacts` (additive).

## The window belongs to the step producing output (PO 2026-08-16)

The first cut painted the frame from the first step on, so `validate`
and `context` — which write no log lines at all — sat under an empty
box that read as output gone missing. Corrected:

- The frame **opens with the first log line** of a step and **closes
  when that step ends**; before and between, only the step line (and,
  once a log exists, the pointer to it) is painted. A build spends its
  context, artifact and signing steps without a frame, which is honest:
  those steps produce no log.
- Steps that establish something **say so in one line that stays**
  (`view.note`, written above the repainted block, never rewound over,
  never in `build.log`): `validate` names board, transport, Matter and
  the endpoint/channel counts; `context` names the SDK version, the
  Zephyr line, the patches (by name, or "no patches"), the file count
  and — once locked — the context ID.
- The facts behind those lines come through the same seam as the steps:
  `BuildRequest.on_step` takes optional keyword **facts**, and a step
  may report itself twice — once on entry, once with what it found out.
  The machine modes carry the facts as `progress` data (append-only,
  consumers ignore what they do not know); the human rendering is the
  CLI's, out of `mcuhome.workbench.contextdir.context_facts`, which
  reads them back off the written context rather than trusting a
  caller's memory of it.

## Tables and color (PO 2026-08-16)

The build summary was a wall of same-colored key-value lines. One table
style (`output.format_table`, now with per-column alignment, an
optional header row and `Cell` for a colored cell), and:

- **Memory** is one row per image, one column per region, rounded to
  whole KiB and whole percent — the exact bytes stay in `-o json` and
  the build report. `IDT_LIST` is dropped: Zephyr collects interrupt
  metadata in a bogus region at a made-up address that the final link
  discards, so it is not memory on the device. The filter is **by
  name** — a region that genuinely exists and is empty stays in the
  table (PO: no blanket dropping of zero values).
- **Flash layout** is a table too (partition, device, range, size),
  with class/mode/staging as a muted subtitle.
- **Color palette**, deliberately small and non-informational (a
  `--color never` run says everything): headings bold, labels and
  supporting prose dim, paths blue, success green, warnings yellow,
  refusals red. Fill levels are plain below 75 %, yellow from 75 %, red
  from 90 % — emphasis on a number that is printed either way.

## Confirming something that cannot be undone (PO 2026-08-16)

`project upgrade` is the first command that can damage a project, and
it sets the shape for every later one (a future `clean --all`, a
destructive `flash`). The rules, pinned:

- **A typed word, not a keypress.** The prompt takes `yes` (trimmed,
  case-insensitive) and treats everything else — including `y` and an
  empty line — as "no". A confirmation that a stray Enter can pass is
  not one.
- **Non-interactive confirmation names the object, not the intent.**
  The flag is `--confirm-upgrade <project id>`, not `--force` or
  `--yes`: a general switch, once written into a script, confirms
  whatever that script later points at, while an id refuses to name
  the wrong project. The short form (six hex characters) is what a
  person types; the full id is what a script pastes.
- **The confirmation is a validate-phase rule.** Missing without a
  terminal, or naming a different project, is exit 2 with the exact
  command to run — and the run has not touched anything at that point.
- **It comes last.** Everything the command can do without consent —
  resolving, taking the object, waiting for other work to finish —
  happens *before* the question, so that "yes" is followed by the
  action and not by a wait.
- **Cancelling is exit 1, not 0.** Nothing happened, but a script must
  not read "done".
- **Interrupting is a contract of its own.** During the destructive
  part, Ctrl+C stops at the next safe boundary rather than immediately;
  the first press says so, and says how to insist (three presses within
  three seconds) and what insisting costs. What cannot be caught —
  SIGKILL — is left legible in the object's own state instead.

## Waiting for a turn on a build server (PO 2026-08-16)

A busy build server no longer just refuses: it holds a turn for the
client it turned away and says when to come back (project ADR 0027). So
a remote build can now spend a long time before it starts, and the view
had no way to say so.

- **A third kind of line: one that replaces itself.** `view.waiting()`,
  beside `line()` (into the log and the frame) and `note()` (stays, and
  scrolls up). A note is worth keeping because it says what a step found
  out; a wait says the same thing over and over until it stops being
  true, and hours of it would bury the run in identical lines. On the
  live view it is painted under the step line and replaced in place; on
  the linear view it is printed each time, because nothing repaints
  there and silence through a wait of hours is the one thing this must
  not do; under a machine mode it prints nothing, because the numbers
  already travel as `progress`.
- **It is not a step**, and gets its own seam (`BuildRequest.on_wait`)
  rather than a key in the step vocabulary. Nothing is being built yet
  and may never be, and a step bar claiming otherwise would show
  progress that does not exist. The machine modes get
  `progress(stage="waiting", retry_after_seconds, waited_seconds,
  attempt)` — an honest stage in the same sense as every other one: it
  says where the run is, not how far.
- **The line states the wait and never a position.** That is not a
  rendering choice: the server does not send one. Order is its business
  and a later version may admit a paying client ahead of a free one, so
  a client that printed a place would be printing something nobody
  promised.
- **Two flags, and they are not mode flags.** `--no-wait` fails at once;
  `--max-wait TIME` bounds the wait (`30m`, `6h`, `0` for no bound;
  default six hours). They stay out of `_MODE_FLAGS` because a remote
  build is a remote build whether the mode was named outright or came
  from a configured builder — and a local build never waits at all.
- **Output is proof the wait is over.** The live view drops the line the
  moment a log line arrives, whoever forgot to take it away.

## Open points

- The exact JSON document schemas per command (the old build/validate
  envelopes are the starting point) are pinned during implementation.
- `question`/`answer` stream verbs: deferred; design only when a
  consumer actually needs them.
- The live view covers `device build`; extending it to `flash`/
  `first-time-setup` happens when those stop being stubs.
