# The mcuhome command line

This document describes the `mcuhome` command — every command, every
flag, every document it prints and every exit code it answers with.

The command line is a **client of the workbench**: it parses arguments,
resolves configuration, calls `mcuhome.workbench.api`, and renders what
comes back. It holds no knowledge of its own about projects, devices,
builds or signing, and it is the complete client of it — everything a
person does with MCUHome is reachable from here, and what is not is
listed with its reason in
[What has no command, and why](#what-has-no-command-and-why).

A program that drives the command line reads this document and the
[Nested documents](#nested-documents) section in it; a program that
embeds the workbench reads this document as the worked example of the
API, because every command below names the api functions it calls. The
functions themselves and their signatures are the workbench's own
reference, `mcuhome-workbench/docs/api.md`; that reference defines every
document below, and [Nested documents](#nested-documents) lists the keys
of each one, so a program driving the command line needs no second file
open.

```console
$ mcuhome project init thermostats
$ cd thermostats
$ mcuhome signing create-key
$ mcuhome device new kitchen --board esp32c6_devkitc
$ mcuhome device build kitchen
```

The key is drawn once per project and before the first build, because a
build signs what it produced and MCUHome never draws key material on the
way past.

## Contents
1. [Conventions](#conventions)
2. [Global flags](#global-flags)
3. [Option flags](#option-flags)
4. [project](#project)
5. [config](#config)
6. [device](#device)
7. [secret](#secret)
8. [signing](#signing)
9. [context](#context)
10. [environment](#environment)
11. [host](#host)
12. [version](#version)
13. [Nested documents](#nested-documents)
14. [Stream events](#stream-events)
15. [Exit codes](#exit-codes)
16. [Environment variables](#environment-variables)
17. [Files](#files)
18. [In a pipeline](#in-a-pipeline)
19. [Retired spellings](#retired-spellings)
20. [What has no command, and why](#what-has-no-command-and-why)
21. [Index of commands](#index-of-commands)

## Conventions
These hold for every command; they are stated once here rather than
repeated per command.

**A command is an area and an act.** `mcuhome <area> <act>` — the area
is the singular noun of the thing acted on (`project`, `device`,
`config`, `secret`, `signing`, `context`, `environment`, `host`), the
act is the verb. Where the act is one api call, the verb is the api's
own verb for it, so the command and the function it runs carry one word:
`build`, `validate`, `rename`, `delete`, `clean`, `provision`, `verify`,
`set`, `unset`, `reveal`. Three verbs are the command line's own,
because rendering is not an api act: `list` (many things), `print` (one
document as the workbench answers it) and `info` (a summary of one
thing, composed for a person).

**No subcommand is a bare noun.** A command does something, and its name
says what — so a command that answers a table is `list-<table>` and one
that answers a document is `print-<document>`: `device list-boards`,
`device list-supported`, `device print-schema`,
`device print-matter-pairing`, `secret list-scopes`. Where the area alone already says what is listed
or printed, the verb stands on its own (`device list`, `secret list`,
`config print`, `context print`); the object is added exactly where the
area holds more than one thing to list or to print. A verb that needs an
object hyphenates it the same way (`sign-firmware`, `create-key`,
`generate-application`, `install-bootloader`). The object is the word a
person uses for the thing, which is the library's word wherever that one
is unambiguous on a command line and the domain's word where it is not:
`device create-matter-pairing` for a call named `create_pairing`,
because "pairing" alone says nothing at a terminal while Matter
commissioning credentials are what a person came for.

There is no third level, and `version` is the one top-level command
without an area: it answers a question about the installation rather
than acting on anything. Two acts are named the way a command line names
creation rather than the way the api does — `project init` and
`device new` — and they are the only two.

**No aliases.** One thing has one spelling. A spelling this command line
used to have is refused by name, with its successor in the refusal —
never accepted quietly ([Retired spellings](#retired-spellings)).

**Positionals and flags.** A command takes at most one positional, and
only for the subject of the act — the device, the project directory, the
context directory, the option key (`config set` takes the key and its
value, which is the one two-positional command: a value belongs to the
key it is written on). Everything else is a flag. The subject is
optional only where the command line can resolve it: a device command
inside a project with one device still names the device, because a
command that guesses which device is being deleted is a command nobody
can read twice.

**Flags.** A flag that carries a configuration **option** is `--` plus
the option's key with every `.` and `_` as `-`: `build.sdk_sources` →
`--build-sdk-sources`. A flag that carries an api **request field or
parameter** is `--` plus the field name with `_` as `-`, prefixed with
the subject the field belongs to where the bare name would be ambiguous
(`--build-server`, `--build-server-token`). Everything else is a flag of
the command itself. A list-valued flag is repeatable and each use
appends. A flag carrying an option or a request field always has both
boolean spellings (`--x` / `--no-x`); a flag of the command's own has
both only where the default is "on" (`--sign` / `--no-sign`), because
otherwise "not used" is the only other statement there is. No option and
no request field has a short form; the presentation flags of
[Global flags](#global-flags) do.

**Where a value comes from.** Every configuration value the command line
uses is resolved by the workbench through one ladder, later wins:

```
default → program → system file → user file → project file → environment → arguments
```

`program` is the layer an embedding program states its own defaults in,
and the command line states none: it has no defaults of its own to
impose, so no value it resolves ever carries that origin — every value
it passes is one a person typed. It
passes each flag it was actually given as one argument carrying the
option key **and the spelling used**, so a refusal names the flag rather
than a key nobody wrote. A flag that was not used is absent, not empty:
"the flag was not used" and "the flag was used to clear the value" are
different statements.

**Output modes.** `-o human` (the default) renders for a person on
stdout; `-o json` prints exactly one document on stdout when the run is
over; `-o json-stream` prints NDJSON — one message per line as the run
progresses — ending with the same document `-o json` would have printed
([Stream events](#stream-events)). Every command takes all three.
Whatever the mode, stdout carries the document or
the human rendering and nothing else: logs, build output and rendered
refusals go to stderr, so redirecting stdout into a file leaves both
halves intact. The machine modes are never interactive, whatever
`--interactive` says.

`-o` is read before any other argument is refused, so **every** run in a
machine mode prints a document — an unknown flag and a missing argument
included. In `json-stream` every run ends with exactly one `result`
line, whether or not a `start` line came first.

**Documents.** The workbench owns every document a client prints: a
command renders whole documents a result answered and never assembles
one out of fields it read off an object. A command's document starts
with `ok`, its verdict. Where the command's whole answer is one result
whose document already starts with `ok`, that document *is* the
command's document; where the result's document has none, its keys
follow `ok`; where a command collected more than one result, each sits
under a key naming it. A document always carries every key it declares —
an absent value is `null`, `[]` or `{}`, never a missing key. Keys are
lowercase with underscores.

Three rules follow from that and are stated once here. Where an api call
answers a **bare value** — a string, a path, a boolean — the command
names it under one key and adds nothing but what it was asked about:
`mcuhome secret reveal` answers `{ok, scope, key, value}` and nothing
around it. Where a flag selects a **different act**, the command answers
a different document: `--dry-run` answers what would happen, and the
document a command answers with it is listed beside the plain one; a
command that has two documents says so and lists both, and within one of
them every key is always there. And where a command answers **part of a data document**,
it names the keys it takes and takes them whole, rewriting nothing:
`mcuhome device list-boards` answers the two board lists of the hardware and
Matter table under the names that table gives them, which is a
projection and not an assembly — the entries are the ones the workbench
wrote. One command does this, and the whole document is one command
away (`mcuhome device list-supported`).

Every nested value in the documents below — a project, a build record, a
setting, an artifact, a finding — is a document of its own, with keys
spelled out in [Nested documents](#nested-documents).

**Two commands answer data.** The device-file JSON Schema and the
hardware and Matter table are documents MCUHome publishes for a client
to consume, not results of an act. With `-o json` they are printed
alone, exactly as they are, because a schema with an `ok` key is not a
schema; with `-o json-stream` they arrive as the `document` of the one
`result` message, so a consumer keeps one NDJSON parser for every
command. In `human` the schema is printed as it is — a schema has no
human rendering — and the catalog is summarised in counts, because
the part of it a person reads is the board table `mcuhome device list-boards`
prints.

**Saying no has two shapes, and they are told apart by `ok` and the
keys.** A **refusal** is a condition that stopped the command: the api
raised, or the command line refused the invocation. It replaces the
command's document with the refusal document,
`{"ok": false, "errors": [...]}`, which carries none of the command's
own keys; each entry is `message`, `file`, `line`, `column`, `key`,
`hint`, `kind`. A **negative answer** is the command's own document with
`ok: false`: a build that ran and failed, a device whose configuration
is invalid, a host check with a failing finding, a context whose bytes
no longer match. Its findings are in the `diagnostics` list of the
result that carries them, never in `errors`. Six commands can answer
negatively — `device validate`, `device info`, `device build`,
`device sign-firmware`, `host check` and `context verify`; every other
command either does what it was asked or refuses. (`sign-firmware`'s
`ok` says that every file the plan named is there afterwards; a signing
program that says no is a refusal with its own words, not a false `ok`.)

A command that **lists** is neither: its `ok` is the verdict of the
listing and never of the rows in it. `device list`, `project info` and
`secret list-scopes` answer `ok: true` with a row that carries `ok: false`,
because one device with a typo is a device with a typo and not a failed
command.

The two lists keep two names on purpose: `errors` is the refusal
document, whose entries are error documents and carry no `severity`,
while `diagnostics` is what a result answers and holds errors and
warnings together, each with its `severity`. A client reads one or the
other, never both of one command.

In `human` a refusal is rendered on stderr; in the machine modes stdout
carries the refusal document, and in `json-stream` each entry also
arrives as an `error` message as it happens. A command never formats an
exception itself.

**The `kind` of a refusal** is the name of the condition, and almost
every value comes from the workbench, where it is the exception's class
name and is listed in its reference. The command line adds three of its
own, for conditions no api call can raise because the call never
happens:

| `kind` | Condition |
|---|---|
| `UsageError` | the invocation was wrong: an unknown command or flag, a missing argument, flags that contradict each other, a value of the wrong shape |
| `RetiredSpelling` | a command or flag this command line used to have; the message names the successor |
| `CapabilityUnavailable` | the act exists as a command and MCUHome cannot perform it yet; the message names the work it waits on |

The set is append-only and complete: a `kind` that is not one of these
three is a workbench exception.

**Interactive mode.** A command asks its questions up front, before it
starts, and only when the run is interactive (a terminal, no
`--no-interactive`, no machine mode). Three acts ask: `project upgrade`
(it rewrites a project), `device delete` (it removes work), and
`device create-matter-pairing` when credentials already exist (a commissioned
device stops being reachable). Non-interactive, all three do what they
were told — the command was typed, and a script is a person who already
decided — and `device delete --force` says the same thing inside an
interactive run.

**What the command line decides.** It decides what to show and what to
ask. It decides nothing about MCUHome: which builder a build runs at,
whether a host can build, what a context pins, whether a file is a key —
all of that is an api call, and where this document says a command
"decides" something, the decision is which function to call.

## Global flags
Every command takes these.

| Flag | Value | Carries |
|---|---|---|
| `-h`, `--help` | — | the command's own help |
| `-v`, `--verbose` | — | more detail in the human rendering; changes no document and no exit code |
| `-o`, `--output` | `human` (default), `json`, `json-stream` | the output mode |
| `--color` | `auto` (default), `always`, `never` | colors in human rendering; `auto` is a terminal and no `NO_COLOR` |
| `--interactive` / `--no-interactive` | — | whether questions may be asked; the machine modes are never interactive |
| `--project-dir` | path | option `project.dir` (`MCUHOME_PROJECT_DIR`) — where the project is, instead of the upward search from the working directory |

`-v` adds detail to a human rendering and to nothing else: `device
validate` prints the resolved model with it, `device build` the commands
signing ran, `host check` every finding's detail rather than the failing
ones alone. Where a command has nothing more to say, `-v` changes
nothing.

`mcuhome --version` prints what `mcuhome version` answers, as text.
`mcuhome` with no command, and `mcuhome <area>` with no act, print help
and exit 0.

## Option flags
Every declared option whose argument channel is open has exactly one
flag, and the flag is the key. Whether a command offers a flag is that
command's decision; the spelling never is. Two groups are referred to by
name below.

**The build option flags** — offered by `device build`,
`context create`, `environment provision` and `host check`, the four
commands that resolve this machine's `build` section:

| Flag | Value | Option | Environment variable |
|---|---|---|---|
| `--build-target` | `local`, `remote` | `build.target` | `MCUHOME_BUILD_TARGET` |
| `--build-mode` | `container`, `subprocess` | `build.mode` | `MCUHOME_BUILD_MODE` |
| `--build-container-program` | string | `build.container_program` | `MCUHOME_BUILD_CONTAINER_PROGRAM` |
| `--build-container-repositories` | string, repeatable | `build.container_repositories` | `MCUHOME_BUILD_CONTAINER_REPOSITORIES` |
| `--build-cpus` | number > 0 | `build.cpus` | `MCUHOME_BUILD_CPUS` |
| `--build-memory` | bytes, or a `k`/`m`/`g` suffix | `build.memory` | `MCUHOME_BUILD_MEMORY` |
| `--build-env-store` | path | `build.env_store` | `MCUHOME_BUILD_ENV_STORE` |
| `--build-dev-workspace` | path | `build.dev_workspace` | `MCUHOME_BUILD_DEV_WORKSPACE` |
| `--build-python` | string | `build.python` | `MCUHOME_BUILD_PYTHON` |
| `--build-sdk-sources` | path, repeatable | `build.sdk_sources` | `MCUHOME_BUILD_SDK_SOURCES` |
| `--build-workspace-sources` | path, repeatable | `build.workspace_sources` | `MCUHOME_BUILD_WORKSPACE_SOURCES` |
| `--build-tools-sources` | path, repeatable | `build.tools_sources` | `MCUHOME_BUILD_TOOLS_SOURCES` |
| `--build-sdk-max-bytes` | integer ≥ 1 | `build.sdk_max_bytes` | `MCUHOME_BUILD_SDK_MAX_BYTES` |
| `--build-workspace-max-bytes` | integer ≥ 1 | `build.workspace_max_bytes` | `MCUHOME_BUILD_WORKSPACE_MAX_BYTES` |
| `--build-tools-max-bytes` | integer ≥ 1 | `build.tools_max_bytes` | `MCUHOME_BUILD_TOOLS_MAX_BYTES` |
| `--build-cache-root` | path | `build.cache_root` | `MCUHOME_BUILD_CACHE_ROOT` |
| `--build-cache-local` | path | `build.cache_local` | `MCUHOME_BUILD_CACHE_LOCAL` |
| `--build-cache-shared` | path | `build.cache_shared` | `MCUHOME_BUILD_CACHE_SHARED` |
| `--build-cache-session` | path | `build.cache_session` | `MCUHOME_BUILD_CACHE_SESSION` |
| `--build-cache-project` | path | `build.cache_project` | `MCUHOME_BUILD_CACHE_PROJECT` |

The three package-source keys are one rule and never fall back to one
another: each names the directories searched for packages of **its own
kind**. A machine that keeps every package in one directory names that
directory in all three flags, which is the statement it is actually
making.

**The signing option flags.** `--signing-key` is offered by
`device build`, `device sign-firmware`, `signing print-public-key`,
`signing create-key` and `context create` — the commands that resolve
which key this invocation means. `--signing-imgtool` is offered by
`device build`, `device sign-firmware` and `host check` — the commands
that run, or report on, the signing program.

| Flag | Value | Option | Environment variable |
|---|---|---|---|
| `--signing-key` | path | `signing.key` | `MCUHOME_SIGNING_KEY` |
| `--signing-imgtool` | string | `signing.imgtool` | `MCUHOME_SIGNING_IMGTOOL` |

**Three declared options have no flag**, and each for its own reason.
`build.builder` is configuration — which builder a plain build uses —
while `--builder` selects one for this invocation; the two are
different statements, so the option is not settable from the command
line. It does have an environment channel: `MCUHOME_BUILD_BUILDER` names
the builder a plain build uses, which is how a CI job states it without
a configuration file. The map options `builder.<name>.*` and
`registry.<base-domain>.*` are stated in a file; they are written with
`config set` ([config](#config)) and have neither a flag nor an
environment variable.

`MCUHOME_BUILD_BUILDER` is therefore the one environment variable of an
option this section does not list beside a flag; every other option's
variable stands in the two tables above.

## project
A project is a directory with a `.mcuhome-project-root` marker, the
`devices/` and `secrets/` folders and a `mcuhome.yaml`. Every other
command finds it by the ladder `--project-dir`, `MCUHOME_PROJECT_DIR`,
the upward search from the working directory.

### `mcuhome project init [<directory>]`
Creates a project: the marker, `mcuhome.yaml`, `devices/`, `secrets/`
(owner-only), the bundled trust anchor and a `.gitignore` that keeps
`secrets/` and `build/` out of version control. Without *directory* the
working directory becomes the project.

| Flag | Value | Carries |
|---|---|---|
| `--force` / `--no-force` | — | `create_project(force=…)`, default off — write into a directory that is not empty |

Calls `is_project_root`, `read_project` and `create_project`. A
directory that is already a project is **answered, not refused**:
nothing is written, `created` is empty and the exit code is 0, so a
script may run `init` before every session.

Any **other** non-empty directory is refused, listing what is in it: a
project is expected to be made in an empty directory so that `init`
cannot damage work that is already there. `--force` is what says
otherwise, and it says it for both cases — the project is created in the
directory as it stands, existing files may be overwritten, and in a
directory that is already a project the durable parts are written again
(the marker keeps the project's id; `mcuhome.yaml`, the folders, the
trust anchor and `.gitignore` are restored where they are missing).
Document: `{ok, project, created}` — `NewProject.to_dict()` behind the
verdict, `created` holding every path the call wrote, in the order it
wrote them.

### `mcuhome project info [<directory>]`
Where the project is, which id and layout version it carries, which
devices it holds, and whether it needs upgrading.

No flags of its own. Calls `resolve_project` with `require_version`
off — this is the command a person runs *because* something refused
them, so a project whose layout is too old is described rather than
refused — or `read_project` for a stated *directory*, then
`is_upgrading`, `plan_upgrade` and `find_devices`.
Document: `{ok, project, upgrading, devices, plan}` — `project` is
`Project.to_dict()`, `upgrading` what `is_upgrading` answered (an
upgrade of this project is running, or was interrupted), `devices` the
device records `device list` answers, and `plan` the migrations still
missing, each as its own document. There is
no separate "needs upgrading" key: a plan that is not empty is that
statement, and one fact in two places is one fact too many. `ok` is the
verdict of the *listing* and not of what it lists — a project holding a
device with a broken configuration is still described, with `ok` false
on that device's row.

### `mcuhome project upgrade [<directory>] [--confirm <id>] [--dry-run]`
Migrates a project to the layout this MCUHome speaks. The session
renames the project marker for the whole run, so nothing else starts
work on a project being rewritten; builds already running are waited
for, and what the migrations will do is shown before the first file
moves.

| Flag | Value | Carries |
|---|---|---|
| `--confirm` | the project's short id | command — the approval a non-interactive run gives in advance |
| `--dry-run` | — | command — show the plan and change nothing |

Interactive runs are asked for the confirmation instead; a
non-interactive run without `--confirm` is refused, and the refusal
names the id to pass. Resolves the project with `require_version` off,
like `project info` — the projects this command exists for are exactly
the ones every other command refuses. Calls `plan_upgrade`,
`find_running_builds`, `open_upgrade_session` and
`UpgradeSession.apply`, the last with `on_step` — one
`migration_started` and, once that migration is through, one
`migration_done`, both naming the migration and the two versions, both
reaching the stream as `progress` — and `should_stop`, which a stop
between migrations answers.

**Two ways it can end badly, and they leave different projects.** A
migration that refuses before it has changed anything leaves the project
untouched and the marker back where it was: fix what the refusal names
and run the upgrade again. A migration that fails after it started
moving leaves the project possibly half-migrated and the marker
renamed — every command then says an upgrade was interrupted, and the
way out is the backup. The refusal says which of the two happened, and
both carry the migration's name.

Document, after a run: `{ok, project, dry_run, from_version,
to_version, applied, stopped, remaining}` — `UpgradeResult.to_dict()`
behind the verdict and the project, with `dry_run` false. With
`--dry-run`: `{ok, project, dry_run, plan}`. A project already current
answers the same keys as a run: `{ok: true, project, dry_run: false,
from_version, to_version, applied: [], stopped: false, remaining: []}`,
with the two versions equal — the answer "nothing to do" has the shape
of the answer "this is what I did".

## config
`config` reads and writes the same option registry every other command
resolves. `print` and `get` answer the effective value with the layer it
came from; `set` and `unset` edit exactly one file, through a round-trip
editor, so comments, order and `!file` references survive.

The name a `config` command takes is an option key — `build.mode`,
`signing.key` — or one entry of a map option:
`builder.<name>.target`, `builder.<name>.server`,
`builder.<name>.container_image`, `registry.<base-domain>.untrusted`,
`registry.<base-domain>.anchor`, `registry.<base-domain>.mirrors.<source>`.
Map entries are how builders and package registries are configured from
the command line; there is no `builder` and no `registry` command group,
because a builder is configuration and a command group would be a second
place to state it.

### `mcuhome config print`
Every declared option, its effective value, the layer it came from and
the file, variable or flag that supplied it.

No flags of its own. Calls `resolve_settings`.
Document: `{ok, config}` — `config` is `Settings.to_dict()`, one entry
per declared option except the bootstrap `project.dir`, each
`{value, origin, source}`. A structured value (a builder, a registry)
renders through its own document.

### `mcuhome config get <name>`
One option's effective value.

No flags of its own. Calls `option` (to refuse a key nobody declared
with the same words a file would be refused with), `resolve_settings` and
`Settings.setting`, which answers one option key or one entry key of a
map option with the layer that entry came from.
Document: `{ok, name, value, origin, source}`.

### `mcuhome config set <name> <value> [--scope <scope>]`
Writes one option into one scope's configuration file, parsing *value*
through the option's own declaration — so a value the option cannot take
is refused here rather than at the next build.

| Flag | Value | Carries |
|---|---|---|
| `--scope` | `project` (default), `user`, `system` | `resolve_config_file(scope=…)` |

Outside a project the default scope has no file: the command refuses,
naming `--scope user` and `--scope system` as the two that do not need
one.
A list value is written as one string separated by the kind's separator:
paths by the platform's path separator, names by a comma.
Calls `resolve_config_file` and `set_config_value`.
Document: `{ok, name, value, scope, file}` — `value` is what the
declaration parsed, not the text.

### `mcuhome config unset <name> [--scope <scope>]`
Removes one option from one scope's file, leaving the rest of it alone.

Takes `--scope` like `set`, and refuses outside a project for the same
reason. Calls `resolve_config_file` and `unset_config_value`.
Document: `{ok, name, removed, scope, file}` — `removed` says whether
anything was there.

## device
A device of a project is a folder under `devices/`, and the folder is
the device's name: `devices/kitchen/main.yaml` states `name: kitchen`,
its build output is `build/kitchen/`, its secrets are
`secrets/device/kitchen.yaml`, its patches are
`devices/kitchen/patches/`. Every command below takes that name as its
positional, or a path — a device folder, or a device file, including one
that lies outside any project.

### `mcuhome device new <device> --board <board>`
Writes a new device folder with a starter configuration.

| Flag | Value | Carries |
|---|---|---|
| `--board` | board name, **required** | `create_device(board=…)` |
| `--friendly-name` | string | `create_device(friendly_name=…)` — what a controller shows; the device's own name is the positional |
| `--dry-run` | — | command — print the file that would be written and write nothing |

`mcuhome device list-boards` lists the boards a `--board` may name. With
`--dry-run` the command calls `render_device_file` and prints the text;
otherwise `create_device`, which refuses rather than overwriting.
Document: `{ok, dry_run, project, entry, name, board}`; with
`--dry-run`: `{ok, dry_run, name, board, text}` — nothing was written,
so there is no project entry to name.

### `mcuhome device list`
The project's devices with their state: board, whether the
configuration is valid and how many problems it has, whether a build is
there, whether it is signed, whether something is working in its build
directory right now.

No flags of its own. Calls `resolve_project` and `find_devices`.
Document: `{ok, project, devices}`, each device a record
`{ok, name, file, board, problems, built, signed, busy}`. The top-level
`ok` is the verdict of the **listing**: it is false only when the
project could not be listed, never because a device in it has a problem
— that is the row's own `ok`, and a client shows a list with a bad row
rather than a failed command.

A device is a folder under `devices/`. A bare device file that is not in
a folder of its own is buildable by path but is not a device of the
project, so it is not listed.

### `mcuhome device info <device>`
One device in full: what its configuration resolves to, and what its
build directory holds.

No flags of its own. Calls `resolve_device`, `validate_device`,
`read_build`, `is_busy` and, where a build report is there,
`read_build_report` with `memory_footprint`. This is one of the commands
that can answer negatively: a device whose configuration is invalid is
`ok: false` with its findings in `validation.diagnostics`, not a
refusal.
Document: `{ok, device, validation, build, footprint}` —
`validation` is `ValidationResult.to_dict()` (its `model` carries the
resolved device, commissioning credentials included, exactly as the
canonical model does), `build` is `BuildRecord.to_dict()` or `null` for
a directory that holds no build, and `footprint` the memory regions the
build report measured, `[]` where there is no report. `ok` is the
validation's.

### `mcuhome device validate <device>`
Checks a device configuration and prints what it resolves to: one pass,
every problem, nothing written.

| Flag | Value | Carries |
|---|---|---|
| `--show-sensitive` | — | command — print the commissioning credentials in the human rendering instead of masking them |

`--show-sensitive` is a rendering decision and changes no document: the
machine document is the whole canonical model either way, because a
caller that asked for the model gets the model. Calls `resolve_device`
and `validate_device`, the latter with `on_warning`, so a warning
reaches the stream while the run happens and the result carries it
afterwards.
Document: `ValidationResult.to_dict()` — `{ok, file, diagnostics,
model}`, one `diagnostics` list holding errors and warnings together,
each with its `severity`.

### `mcuhome device build [<device>]`
Builds firmware, signs it with the project's key and delivers the result
into the device's build directory. This is the command the rest of the
tool exists for.

**Where it runs.** Two questions, and only the first belongs to whoever
types the command: *where* the build runs (`local`, `remote`) and *how*
the machine that runs it executes the work (`container`, `subprocess`).
The target is decided by a ladder, most explicit first:
`--build-server` (which is `remote` by statement), then `--builder`,
then `--build-target`, then the configured `build.builder`, then
`build.target`. The mode describes **this** machine and is `--build-mode`
or `build.mode`; a remote build has no mode of its own to state, because
that machine's operator configured theirs.

**What comes back** is an unsigned image plus a build report, whichever
target ran — the private key never enters a build, at any target — and
one host-side step signs it afterwards, unless `--no-sign`.

**Patches** are the device's own and need no flag: what lies under
`devices/<device>/patches/<layer>/NNNN-*.patch` is carried into the
context and hashed into its identity, so a device builds something else
with a patch than without one. A patch set is part of a device, not of
an invocation, and there is deliberately no flag that switches one on
for one run.

| Flag | Value | Carries |
|---|---|---|
| `<device>` | device name or path | `resolve_device(spec)`; mutually exclusive with `--model`, and one of the two is required |
| `--model` | path | `read_model(path)` — a canonical model JSON instead of a device configuration |
| `--out-dir` | path | `BuildRequest.out_dir` — default `<project>/build/<device>/`, and `<cwd>/build/<device>/` for a `--model` build |
| `--builder` | name | `BuildRequest.builder` — the configured builder this build runs at, resolved by name |
| `--build-server` | address | `SelectedBuilder.server` — a build server named for this invocation, without configuring a builder |
| `--build-server-token` | token, or `-` to read it from stdin | `SelectedBuilder.token` — the credential for that server |
| `--container-image` | repository, `:tag`, `@sha256:…` or a repository with either | `BuildRequest.container_image` — the build environment this build asks for; refused for a build that starts no container (`--build-mode subprocess`), because a pin that is silently ignored is worse than a refusal |
| `--wait-for-turn` / `--no-wait-for-turn` | — | `BuildRequest.wait_for_turn`, default on — wait when a build server has no room |
| `--max-wait-seconds` | seconds | `BuildRequest.max_wait_seconds` — the bound of that wait; `0` removes it |
| `--public-key` | path | `BuildRequest.signing_pub` — the public half to compile into the bootloader when the private key is elsewhere; only with `--no-sign`. It is named after the file it takes rather than after the field, the one request-field flag that does not derive letter for letter: the field holds PEM text and the flag holds a path to it |
| `--sign` / `--no-sign` | — | command, default on — sign the delivered image here |

Plus [the build option flags](#option-flags) and `--signing-key` /
`--signing-imgtool`.

**The token has two channels and no third.** This flag, which takes `-`
so the value is read from standard input rather than standing in the
process list of every other user on the machine, and the builder's
credentials file `secrets/builder/<name>.yaml` (`mcuhome secret set
--kind builder --name <builder> --key token --value -`). There is no
environment variable and no configuration key for a token, and no
document this command line prints carries one.

**Without a signing key** — no `--signing-key`, nothing in `signing.key`,
no key in the project, and no `--no-sign --public-key` pair either — the
build refuses before it starts, and the refusal names
`mcuhome signing create-key`. The refusal is the
workbench's: resolving the key is what raises it, and its hint is
written in the same command-line words the rest of the workbench's hints
use. The command line does not compose a refusal of its own here, and
none of its three own conditions covers a missing key. With `--no-sign` and
no `--public-key` the public half still comes from the private key, so
"build here, sign elsewhere" is `--no-sign --public-key <file>`: that is
the one combination in which no private key is read at all.

Calls, in order: `resolve_device` or `read_model`; `load_model`;
`resolve_settings`; `resolve_build_options`; `resolve_builder` and
`resolve_build_target` / `resolve_build_mode`; `resolve_signing_key` and
`public_key_pem` (or the `--public-key` file's text, checked with
`is_p256_public_key`); `build_steps`, so the step line a person watches
is the steps this target will really report; then `build_firmware`,
which is the surface's one `async` function and is awaited — `on_line`,
`on_step`, `on_wait` and `should_stop` are fields of the `BuildRequest`
it is given, not parameters of the call; then `read_build_report` with
`memory_footprint` for the footprint, and `sign_firmware` with the
device's model, which writes the signed images and, for a device that
takes them, the Matter OTA image. `resolve_shutdown_seconds` is what a
stop reports as its bound.

**Stopping.** `Ctrl-C` sets the stop predicate rather than killing the
process: the build walks its ladder down, releases the build directory
and answers `ok: false, stopped: true`. The bound is stated when the
stop is requested — in `human` as a line on stderr, in `json-stream` as
one `stopping` message carrying the seconds — so a client can show that
the stop was heard rather than a run that has apparently hung. A second
`Ctrl-C` is the person overruling that, and leaves whatever it leaves.
This is one of two commands that stop cleanly; the other is
`project upgrade`, between migrations. Everywhere else `Ctrl-C` ends the
process, and what a half-written act leaves is what the api says it
leaves.

**The log.** Every line the build produced is written to
`<build dir>/build.log`, in every mode. In an interactive human run the
last lines are shown through a fixed frame that repaints in place, under
a step line that says where each step runs; anywhere else (a pipe, CI, a
machine mode) the lines pass through to stderr.

Document: `{ok, build, signing, footprint}` — `build` is
`BuildResult.to_dict()` (`{ok, stopped, target, device, context_id,
out_dir, report, container_image, artifacts, diagnostics}`), `signing`
is `SigningResult.to_dict()` or `null` for `--no-sign`, and `footprint`
the memory regions the build report measured, each `{image, region,
used, total}`. `ok` is true when the build produced its artifacts and,
where it signed, the signing answered `ok`.

`out_dir` is the build directory itself: the artifacts, the report and
the signed images are at the top of it, under plain names, because what
a user takes away from a build directory is what they must be able to
see without a filter. What the build kept for itself is hidden inside
it.

### `mcuhome device generate-application <device> --out-dir <directory>`
Writes the standalone Zephyr application the device's model describes,
and stops there. A build does not take this path — a build environment
generates from the model its context carries — so this is for whoever
wants the tree for its own sake: to read it, to diff it, to compile it
by hand. It is an act of its own rather than a flag of `device build`,
because it is not a build.

| Flag | Value | Carries |
|---|---|---|
| `--out-dir` | path, **required** | `generate_application(out_dir=…)` |

Calls `resolve_device`, `load_model` and `generate_application`.
Document: `{ok, device, out_dir, files}` — `GenerationResult.to_dict()`
behind the verdict, `files` holding every file written, in the order it
was written.

### `mcuhome device sign-firmware <device>`
Signs the application image of a finished build with the key the
device's bootloader carries, and wraps it for over-the-air delivery
where the device takes updates that way. `device build` runs this
itself; the separate command is for a build that was delivered
elsewhere, a key that only exists on this machine, or a signature that
has to be re-applied.

| Flag | Value | Carries |
|---|---|---|
| `--out-dir` | path | the build directory to sign in; default the device's own |
| `--dry-run` | — | command — print every command signing would run, and run none |

Plus `--signing-key` and `--signing-imgtool`.

Holds the build directory under the `sign` operation for the whole run,
so a build of the same device refuses in words rather than racing it.
Signing replaces what a previous run signed: the plan says which files
it will remove before it writes, and `--dry-run` is where a person sees
that before it happens.
Calls `resolve_device`, `open_build_lock`, `load_model` and
`plan_signing` (`--dry-run`) or `sign_firmware`.
Document: `SigningResult.to_dict()` — `{ok, out_dir, report_path, key,
signed, ota}`; with `--dry-run`: `{ok, dry_run, out_dir, report_path,
key, commands, outputs, removes}`, the last two naming the files signing
would write and the files it would remove first.

### `mcuhome device clean [<device>]`
Removes what a build produced — the record, the report, the artifacts,
the signed images, the OTA image, the work roots — and leaves everything
else in the directory alone, including a work root somebody named
themselves. The directory itself stays.

| Flag | Value | Carries |
|---|---|---|
| `--all` | — | command — every device of the project instead of one; mutually exclusive with the positional, and one of the two is required |

Calls `resolve_project`, `find_devices` (for `--all`), `resolve_device`
and `clean_build`, which holds each directory under the `clean`
operation and refuses in words while something is running in it.
Document: `{ok, cleaned}` — one whole `CleanResult` document per
directory, `{device, out_dir, removed}`, in the order the devices were
cleaned. One device or twenty, the shape is the same.

### `mcuhome device rename <device> --to <name>`
Renames a device: its folder with everything in it, the name inside its
file, its secrets file. Its build output is **removed** rather than
moved — build output names the device inside its own report, so a moved
build directory would describe a device that no longer exists.

| Flag | Value | Carries |
|---|---|---|
| `--to` | device name, **required** | `rename_device(to=…)` |

Calls `resolve_project` and `rename_device`, which holds both build
directories for the duration.
Document: `{ok, device, to, changed}` — `RenameResult.to_dict()` behind
the verdict, `changed` holding every path that moved or was removed, in
the order it happened.

### `mcuhome device delete <device>`
Removes a device: its folder, its build directory, and its secrets file
unless `--keep-secrets`.

| Flag | Value | Carries |
|---|---|---|
| `--keep-secrets` / `--no-keep-secrets` | — | `delete_device(keep_secrets=…)`, default off |
| `--force` | — | command, default off — do not ask, even in an interactive run |

Commissioning credentials a controller already knows cannot be drawn
again, which is what `--keep-secrets` is for. An interactive run is
asked first; a non-interactive one deletes.
Calls `resolve_project` and `delete_device`.
Document: `{ok, device, kept_secrets, removed}` —
`DeleteResult.to_dict()` behind the verdict.

### `mcuhome device print-matter-pairing <device>`
A device's Matter commissioning credentials — the manual code and the QR
payload a person types into a controller. It only reads: the credentials
a device has are drawn once, and drawing is a command of its own.

No flags of its own. Calls `resolve_device` and `read_pairing`.
Document: `{ok, device, pairing}` — the credentials document
(`discriminator`, `passcode`, `salt`, `iterations`, `test_credentials`,
`manual_code`, `qr_payload`), or `null` for a device that has none.

### `mcuhome device create-matter-pairing <device>`
Draws a device's commissioning credentials. It writes into exactly two files: the device's `main.yaml`, which gets the
`!secret` references, and `secrets/device/<device>.yaml`, which gets the
values.

| Flag | Value | Carries |
|---|---|---|
| `--force` / `--no-force` | — | `create_pairing(force=…)`, default off — replace credentials the device already has |

A device that already has credentials is refused unless `--force`, and
an interactive run is asked before the replacement, because a
commissioned device stops being reachable with the codes somebody wrote
down.
Calls `resolve_device` and `create_pairing`.
Document: `{ok, device, entry, secrets_file, pairing, replaced}` — the
`pairing` is the same document `print-matter-pairing` answers, so a client
shows the codes the same way whether it drew them or read them.

### `mcuhome device print-schema`
The JSON Schema of a device file, as data: what an editor validates
`main.yaml` against and what a form-driven client builds its fields
from.

No flags of its own. Calls `device_schema`.
This is a data command: `-o json` prints the schema alone, `human`
prints the same text (a schema has no human rendering), and
`-o json-stream` carries it as the `document` of the one `result`
message.

### `mcuhome device list-boards`
What MCUHome can build for: the supported boards with their transports,
and the planned ones with the reason they are not there yet.

No flags of its own. Calls `device_registry` and prints the two board
lists of that document whole.
Document: `{ok, boards, planned_boards}` — the two lists of the
supported-hardware document, taken whole and under the names that
document gives them.

### `mcuhome device list-supported`
Everything MCUHome knows about hardware and Matter, as data: the boards,
the drivers, the clusters, the device types, what is planned of each,
and the attribute sizes — the document a picker in a client populates
itself from.

No flags of its own. Calls `device_registry`.
This is a data command, like `device print-schema`: `-o json` prints the
document alone, `-o json-stream` carries it as the `document` of the one
`result` message, and `human` renders the counts with a line saying
where the data is. Its top-level keys are `registry_version`,
`builder_version`, `model_version`, `boards`, `planned_boards`,
`drivers`, `planned_drivers`, `clusters`, `planned_clusters`,
`device_types`, `planned_device_types` and `attribute_sizes`.

### `mcuhome device flash <device>` and `mcuhome device install-bootloader <device>`
Both refuse, naming the platform work they wait on: flashing a built
image over the serial recovery path, and the one-time board preparation
that puts our bootloader on a device with the vendor's own tooling.
They exist rather than being missing so that the answer to "how do I
flash this" is the command itself saying what is not there yet. The
second is named after what it will do rather
than after when it is done, because no subcommand of this command line
is a bare noun.

`flash` takes `--flash-mode {recovery,ota}`; both take no other flags.
They call nothing and answer the refusal document with exit 1, its
`kind` being `CapabilityUnavailable` — so a client greys a button with
the reason instead of telling a person that something failed.

## secret
A project keeps its secrets in `secrets/`, one file per kind and name:
the shared `main`, one `device` file per device, one `builder` file per
build server credential, and `signing`, which is the reference to the
firmware key. These commands are the only supported way to look at
them. **No document the workbench answers here carries a value**:
`list` masks with a constant that is not a redaction of the value — not
its length, not its first character, not whether two entries are the
same — and one command answers one value, `reveal`, which is a command
of its own so that the ask cannot be made by accident.

Every command takes the scope the same way, and every document names it
the same way — under one `scope` key, which is the scope document
`{kind, name, file, exists}`, never as two loose fields:

| Flag | Value | Carries |
|---|---|---|
| `--kind` | `main` (default), `device`, `builder`, `signing` | the `kind` parameter |
| `--name` | one plain word | the `name` parameter — a device or a builder; refused for `main` and `signing`, required for the other two |

An exposed secrets file is refused rather than read, with the `chmod`
that fixes it: a call whose whole subject is those values does not hand
them out of a file that is already handing them to everybody else.

### `mcuhome secret list-scopes`
Every scope this project could have and whether its file exists — which
devices and which builders have one.

Calls `find_secret_scopes`.
Document: `{ok, scopes}`, each `{kind, name, file, exists}`.

### `mcuhome secret list [--kind <kind>] [--name <name>]`
The keys of one scope, masked, and for the shared file which devices
refer to each key. A scope whose file does not exist answers no keys and
`exists: false` rather than refusing, so a client can open a scope it
just listed.

Calls `read_secrets`.
Document: `{ok, scope, keys}`, each key `{key, masked, used_by}`.

### `mcuhome secret reveal --key <key> [--kind <kind>] [--name <name>]`
One secret's value — the one command that answers one.

| Flag | Value | Carries |
|---|---|---|
| `--key` | the key, **required** | `reveal_secret(key=…)` |

Human rendering prints the value alone on stdout, with no decoration, so
it can be read into a variable. The `signing` scope is refused: key
material is neither printed nor typed in.
Calls `reveal_secret`, which answers the value itself.
Document: `{ok, scope, key, value}` — the one document of this command
line that carries a secret, because asking for exactly this value is
what the command is.

### `mcuhome secret set --key <key> --value <value> [--kind <kind>] [--name <name>]`
Writes one key, creating the file owner-only if it is the first.
Comments, order, blank lines, quoting and every other entry survive the
edit, and the whole file is written or none of it.

| Flag | Value | Carries |
|---|---|---|
| `--key` | the key, **required** | `set_secret(key=…)` |
| `--value` | the value, **required**; `-` reads it from stdin | `set_secret(value=…)` |

`--value -` is how a secret is set without standing in the shell's
history or in the process list of every other user on the machine. The
`signing` scope is refused, naming the two ways a key is stated instead:
`--signing-key` for one you already have, `mcuhome signing create-key`
for one you do not.
Calls `set_secret`.
Document: `{ok, scope, key, changed}` — `SecretChange.to_dict()` behind
the verdict; the scope names the file.

### `mcuhome secret unset --key <key> [--kind <kind>] [--name <name>]`
Removes one key and answers whether it was there. Removing the last
entry leaves an empty file — the file is the user's, and the command was
asked to remove one secret.

Calls `unset_secret`.
Document: `{ok, scope, key, changed}` — `changed` says whether the key
was there.

### `mcuhome secret delete --kind <kind> --name <name>`
Removes a whole `device` or `builder` secrets file. `main` and `signing`
are refused: they are the project's own and are emptied key by key,
never removed under a user's feet.

Calls `delete_secret_file`.
Document: `{ok, scope, key, changed}` — `key` is empty (a file, not an
entry) and `changed` says whether a file was there.

## signing
The firmware signing key of a project: one P-256 key pair under
`secrets/signing/`, referenced by `secrets/signing/key.yaml`. A device
only accepts images signed with the key its bootloader carries, which is
why nothing here ever generates one by accident.

### `mcuhome signing print-public-key`
The public half of the signing key, as PEM. This is what goes into a
build somebody else runs and into a bootloader somebody else compiles.

Takes `--signing-key`. Calls `resolve_signing_key` and `public_key_pem`;
it never writes, and a project with no key is refused naming
`mcuhome signing create-key`.
Human rendering prints the PEM alone on stdout, so it can be redirected
into a file. Document: `{ok, path, in_secrets, created, public_key}` —
the private half is in no document this command line prints.

### `mcuhome signing create-key`
Draws the project's signing key, once. Asked again it answers the key
that is there, with `created: false`: generating over existing key
material is the one thing it never does.

Takes `--signing-key`, which is where the key is written when it is not
the project's own `secrets/signing/key.pem`.
Calls `create_signing_key`.
Document: `{ok, path, in_secrets, created, public_key}`.

## context
A build context is what a build is attributed to: the resolved package
pins, the canonical model, the public signing key, the device's patches.
A build creates one itself; these commands are for the caller who wants
one without a build — to hand it to a build server, to check what a
build would be attributed to, or to verify that a context directory
still holds what it declares.

### `mcuhome context create <device> --out-dir <directory>`
Resolves every pin and writes a locked context at *out_dir*, which has
to be new or empty.

| Flag | Value | Carries |
|---|---|---|
| `--out-dir` | path, **required** | `create_context(out_dir=…)` |
| `--public-key` | path | `create_context(signing_pub=…)` — the public half to write into the context when the private key is elsewhere |

Plus [the build option flags](#option-flags) and `--signing-key`.

Calls `resolve_device`, `load_model`, `resolve_settings`,
`resolve_build_options`, `resolve_signing_key` and `public_key_pem` (or
the `--public-key` file's text), `create_context` with `on_line`, then
`lock_context` — locking is the act of whoever builds the context, and
the identity it computes is what a build server checks its copy against.
`create_context` requires a scratch area (`work_root`); the command
states a hidden directory beside *out_dir* and removes it when it is
done, which is the one path it picks rather than is given. It reports no
stages: what it has to say are the log lines `on_line` carries, on
stderr.
Document: `{ok, out_dir, context}` — `context` is what
`mcuhome context print` answers for the directory just written.

### `mcuhome context verify <directory>`
Recomputes the identity of a context directory and lists every file
whose bytes no longer match what the manifest declares.

No flags of its own. Calls `verify_context`, which answers rather than
raising — a context whose bytes no longer match is `ok: false` with the
mismatches listed, not a refusal.
Document: `ContextVerification.to_dict()` — `{ok, root, context_id,
actual_id, mismatches}`, each mismatch `{path, declared_sha256,
actual_sha256}`.

### `mcuhome context print <directory>`
What the context holds: its identity once it is locked, the SDK it pins
and that package's hash, the build environment it names, the board, how
many files it carries and which patches. In human rendering the
generator chain is printed the way a manifest states it.

No flags of its own. Calls `read_context_facts`, which reads the
manifest with `read_context_manifest` where the context is locked, and
`read_generator_chain` with `format_generator_chain` for the chain.
Document: `{ok, context}`. Every key of `context` is optional to a
consumer and the set is append-only: this is display material, and `id`
is there only once the context is locked.

## environment
A build environment is the compiler stack a build runs in: a container
image, or two packages unpacked into a per-user store. The area word is
short for exactly that and has nothing to do with the process
environment or the `MCUHOME_*` variables, which are
[Environment variables](#environment-variables).

### `mcuhome environment provision <package>`
Puts a build-environment package into this machine's store, ahead of the
build that needs it — which is what a machine that has to build offline,
or a machine being set up, needs.

*package* is a package file (`…/mcuhome-build-workspace-0.2.0.tar.zst`)
or a package name with an optional constraint and hash
(`mcuhome-build-tools`, `mcuhome-sdk:~=0.2.0`,
`mcuhome-build-workspace:0.2.0@sha256:…`). The three package names are
the only ones accepted, the operator directories of that package's kind
are searched first, the registry second, and the hash is checked on
every path.

Takes [the build option flags](#option-flags) — `--build-env-store`
says where the store is, the three `*-sources` flags where packages are
looked for, the three `*-max-bytes` flags what a package may unpack to.

Calls `resolve_settings`, `resolve_build_options` and
`provision_environment` with `on_line` and the project, so a package no
operator directory offers is fetched from the configured registry and
checked against the trust anchor the project carries. It reports no
stages and **cannot be stopped cleanly**: the call takes no stop
predicate, because it is bounded by the package's own size rather than
by a caller's patience, and `Ctrl-C` ends the process — an interrupted
run leaves nothing a build can find, because the store marker is written
last.
Document: `{ok, kind, name, version, sha256, path}` — the store entry,
provisioned and frozen; a package already in the store is answered
rather than unpacked a second time.

## host
### `mcuhome host check`
What a build on this machine would need, reported rather than raised:
one finding per thing examined, each with what was found and the fix
where there is one. Which checks run follows the configured mode — the
container runtime and the image search only for `container`, the
environment store and the interpreter only for `subprocess` — because
the two need disjoint things of a host, and reporting on what this
machine will never run is noise. The signing program, the compiler
cache, the project, the resolved configuration, the configured builders
and the permissions of the project's secrets are examined either way —
every one of them a finding of the same shape, so the command renders a
list and decides nothing about what to probe.

Takes [the build option flags](#option-flags) and `--signing-imgtool`,
so a person can ask what a build *would* need under a mode they have not
configured yet.

Calls `resolve_settings`, `resolve_build_options`, `check_build_host`
and `read_cache_usage`. It reports no stages, and it raises nothing: a
host that cannot build is the answer — `ok: false` with the failing
findings in the list, not a refusal. It does talk to this machine — it runs the container runtime's
version command, asks the configured container repositories what they
publish, asks the interpreter its version and walks the cache — so it
costs what those cost.
Document: `{ok, host, cache}` — `host` is `HostCheckResult.to_dict()`
(`{ok, findings}`, each `{ok, check, detail, hint}`), `cache` one
`{tier, path, size, files}` entry per configured cache tier. `ok` is the
host check's: any failing finding makes it false, and the command
exits 1.

## version
### `mcuhome version`
Which MCUHome packages are installed and at which version — the command
line, the workbench, the device model and the code generator. This is
the first thing to state in a bug report, and the reason `--version`
exists as a flag as well: the flag prints the same answer as text.

No flags of its own. Calls `stack_versions`, which answers the packages
the workbench knows of.
Document: `{ok, command_line, stack}` — `stack` is that answer, one
entry per package name, the version or an empty string for a package
that is not installed; `command_line` is this package's own version, the
one fact the workbench cannot know and the only one this command line
states beside a document rather than inside it.

## Nested documents
Every document a command prints is composed of these, and every one of
them is produced by the workbench. The keys are listed here so a client
that drives the command line has them in one place; the workbench's
reference is where they are defined, and it is the normative one.

| Document | Keys |
|---|---|
| project | `root`, `id`, `discovered`, `version` |
| new project | `project`, `created` |
| new device | `project`, `entry`, `name`, `board` |
| device record | `ok`, `name`, `file`, `board`, `problems`, `built`, `signed`, `busy` |
| validation | `ok`, `file`, `diagnostics`, `model` |
| finding (diagnostic) | `severity`, `message`, `file`, `line`, `column`, `key`, `hint`, `kind` |
| error entry | the same without `severity` |
| build | `ok`, `stopped`, `target`, `device`, `context_id`, `out_dir`, `report`, `container_image`, `artifacts`, `diagnostics` |
| artifact | `root`, `path`, `role`, `sha256` |
| build record | `out_dir`, `device`, `context_id`, `artifacts`, `report`, `signed`, `container_image`, `busy` |
| memory region | `image`, `region`, `used`, `total` |
| signing | `ok`, `out_dir`, `report_path`, `key`, `signed`, `ota` |
| signed artifact | `format` (`bin` or `hex`), `path` |
| signing plan | `out_dir`, `report_path`, `key`, `commands` (each `format`, `argv`, `output`), `outputs`, `removes` |
| signing key | `path`, `in_secrets`, `created`, `public_key` |
| generation | `device`, `out_dir`, `files` |
| clean | `device`, `out_dir`, `removed` |
| rename | `device`, `to`, `changed` |
| delete | `device`, `kept_secrets`, `removed` |
| pairing | `discriminator`, `passcode`, `salt`, `iterations`, `test_credentials`, `manual_code`, `qr_payload` |
| new pairing | `entry`, `secrets_file`, `pairing`, `replaced` |
| setting | `value`, `origin`, `source` — `origin` one of `default`, `program`, `system`, `user`, `project`, `environment`, `arguments` |
| builder | `name`, `target`, `origin`, `source`, `server`, `container_image` |
| selected builder | `target`, `builder`, `server`, `container_image` — never a token |
| registry | `base_domain`, `origin`, `source`, `untrusted`, `mirrors`, `anchor` |
| secret scope | `kind`, `name`, `file`, `exists` |
| secret key | `key`, `masked`, `used_by` |
| secret file | `scope`, `keys` |
| secret change | `scope`, `key`, `changed` |
| migration | `from_version`, `to_version`, `name`, `description`, `details` |
| upgrade | `from_version`, `to_version`, `applied`, `stopped`, `remaining` |
| running build | `directory`, `device`, `operation`, `process`, `started`, `name` |
| host check | `ok`, `findings` |
| host finding | `ok`, `check` (one of `runtime`, `image`, `store`, `python`, `workspace`, `imgtool`, `cache`, `project`, `configuration`, `builder`, `secrets`), `detail`, `hint` |
| cache tier | `tier` (`local`, `session`, `project`, `shared`), `path`, `size`, `files` |
| store entry | `kind`, `name`, `version`, `sha256`, `path` |
| context verification | `ok`, `root`, `context_id`, `actual_id`, `mismatches` |
| file mismatch | `path`, `declared_sha256`, `actual_sha256` |
| context facts | `id` (once locked), `sdk`, `sdk_sha256`, `build_environment`, `board`, `files`, `patches` — display material, every key optional to a consumer, the set append-only |

Two of the values above are documents of another format and are carried
through unchanged. The **model** of a validation is the canonical device
model — the device-model package's own format, the same document a build
context carries as `model/device-model.json`, versioned by the model
format version the workbench reports; `mcuhome device print-schema` answers
the schema of the *device file* a person writes, which is a different
document. The **build report** is the build environment's format, and
the memory regions above are what this command line reads out of it.

## Stream events
`-o json-stream` prints NDJSON: one JSON message per line, flushed as it
happens. Seven verbs, and the vocabulary is append-only — a consumer
ignores a verb it does not know:

| Verb | Message | When |
|---|---|---|
| `start` | `{"verb": "start", "task": "<command>", …}` | the command begins, once |
| `progress` | `{"verb": "progress", "stage": "<key>", …}` | a stage started, or has learned something |
| `diagnostic` | `{"verb": "diagnostic", "diagnostic": {severity, message, file, line, column, key, hint, kind}}` | a non-fatal finding, as it is found |
| `wait` | `{"verb": "wait", "retry_after": …, "waited": …, "attempt": …}` | a build server has no room yet |
| `stopping` | `{"verb": "stopping", "seconds": …}` | a stop was requested, with the bound it may take |
| `error` | `{"verb": "error", "error": {message, file, line, column, key, hint, kind}}` | one refusal, as it happens |
| `result` | `{"verb": "result", "document": {…}}` | the last line, always: the document `-o json` would have printed |

**`start`.** `task` is the command as typed without its flags —
`"device build"`, `"config print"`, and `"version"` for the one command
that has no area. Two commands carry one more key, and no other key is
promised: `device build` carries `steps`, the ordered stage keys this
build will really report (what `build_steps` answered, so a client can
lay out "step k of n" before anything runs), and `project upgrade`
carries `plan`, the migration documents it is about to apply.

**`progress`.** `stage` is the api's own step vocabulary and nothing
invented, and the two commands that report stages report them
differently — the vocabulary says which pattern applies.

A **build** reports `context`, `environment` and `compile`, and each of
them arrives **once or twice**: once when the stage starts, and a second
time with facts when it has something worth stating. A remote build's
`environment` has nothing to state and sends no second message, so a
consumer marks a stage started when it first sees it, never infers
completion from a repeat, and takes the start of stage *k+1*, or the
`result` line, as the end of stage *k*. The facts are extra keys of the
message; the first message carries none.

An **upgrade** reports a pair per migration: `migration_started` and,
when that migration is through, `migration_done` — both carrying the
same three facts, `name`, `from_version` and `to_version`. Here the
second message *is* the completion, and `name` is what matches the pair
against the migrations the `start` message listed in `plan`. A run that
was stopped, or a migration that failed, leaves a `migration_started`
without its `migration_done`, and the `result` document says which
migrations were applied and which remain.

No count and no percentage is reported by either run, because a step
knows that it started and, later, what it found — and nothing in
between.

**Which commands report stages at all.** Two: `device build` and
`project upgrade`. Every other command emits `start`, whatever
diagnostics it finds, and `result` — including the three that can take
minutes (`context create`, `environment provision`, `host check`), which
say what they are doing through their log lines on stderr. A client
shows a determinate progress bar for the first two and a spinner for
the rest.

**`diagnostic`** carries a whole finding — the refusal document's keys
plus `severity` — and is how a warning reaches a client: an exposed
secrets file, an unverified registry, a retired environment variable
that is still set. `error` is refusals only. In `human` a warning is one
line on stderr; in `-o json` it is in the `diagnostics` list of the
result that carries one and, where no result does, on stderr as text.

**`wait`** is a verb rather than a stage because the build has not
started and may never start. **`stopping`** is sent once when a stop is
requested, so a client can show that the stop was heard rather than a
run that hangs.

The build log is not in the stream. It goes to stderr line by line and
to `build.log` in full — a consumer that wants it reads either, and a
consumer that wants the structure is not made to filter thousands of
compiler lines out of it.

## Exit codes
Three, and deliberately no more.

| Code | Means |
|---|---|
| `0` | the command did what it was asked |
| `1` | the command ran and the answer is no: a refusal, a failed build, a stopped build, a host check with a failing finding, an invalid device |
| `2` | the invocation was wrong: an unknown command or flag, a missing argument, a flag that contradicts another, a value of the wrong shape — nothing ran |

A command runs in three phases, in this order: **interact** (only in an
interactive run, and only the three acts that ask), **validate** (every
input present and well-shaped, wherever it came from), **execute**. A
problem found in validate is exit 2 and execute is never entered — the
boundary is not "before anything is written" but sharper: the act does
not start. A value a **flag** carries is parsed in validate, so
`--build-memory 3x` is exit 2; the same value in a configuration file or
an environment variable is refused while the command runs and is exit 1,
because by then the invocation was not what was wrong.

Every exit code corresponds to the document's `ok`: `0` is `ok: true`,
`1` and `2` are `ok: false` — as a refusal document with the conditions
in `errors`, or, for the six commands that can answer negatively, as
the command's own document with its findings in `diagnostics`. The two
data documents (`device print-schema`, `device list-supported`) carry no verdict of
their own: there the exit code is the whole statement.

## Environment variables
**Options.** Every option whose environment channel is open has one
variable, `MCUHOME_<AREA>_<NAME>`, listed with its flag in
[Option flags](#option-flags). The command line does not read them: it
hands the process environment to the workbench, which resolves them in
the configuration layer, one layer below the flags. `MCUHOME_BUILD_BUILDER`
is one of them and has no flag beside it (see [Option flags](#option-flags)).

Four variables MCUHome used to read draw a warning naming their
successor, rather than a refusal — a stale variable in a shell profile
would otherwise block every command, including the one that fixes it:
`MCUHOME_CCACHE_DIR` → `MCUHOME_BUILD_CACHE_ROOT`,
`MCUHOME_DEFAULT_BUILDER` → `MCUHOME_BUILD_BUILDER`, `MCUHOME_DOCKER` →
`MCUHOME_BUILD_CONTAINER_PROGRAM`, `MCUHOME_IMGTOOL` →
`MCUHOME_SIGNING_IMGTOOL`. The warning travels like every other
diagnostic.

**A build server token has no variable.** The two channels are
`--build-server-token` (which takes `-` to read the value from standard
input) and `secrets/builder/<name>.yaml`, on purpose: a token that could
be exported would be in every child process of the shell that exported
it. `--build-server` is a flag of the build server's own subject and not
of the `build` area — there is no option `build.server` — so the two
cannot be confused for a configuration key.

**`NO_COLOR`** is the one variable the command line itself consumes: a
non-empty value turns colors off under `--color auto`.

**What MCUHome sets for a build environment it starts** —
`MCUHOME_BUILDER_BASE_DIR`, `MCUHOME_BUILDER_TOOLS`,
`MCUHOME_BUILDER_WORKSPACE` — are never read as configuration and are
not set by this command line; the prefix is what tells the two families
apart.

**Host facts** (`PATH`, `HOME`, `XDG_CONFIG_HOME`, `XDG_CONFIG_DIRS`,
`XDG_CACHE_HOME`, and the Windows equivalents) are read out of the same
environment mapping, never out of the process by anything below this
command line.

## Files
Every file MCUHome reads and writes — the three configuration files, the
project marker, device files, the secrets tree, build directories, the
build-environment store, the compiler cache — belongs to the workbench
and is documented there. The command line adds two of its own:

| What | Where | Written by |
|---|---|---|
| build log | `<build dir>/build.log`, in every output mode | `device build` |
| a public key it was given | read only, at the path `--public-key` names | `device build`, `context create` |

Everything else on disk is written through an api call, at the path that
call documents.

## In a pipeline
A script drives the command line the way any other program does: it
states every input, asks for `-o json`, and reads the exit code.

```console
$ mcuhome project init project
$ cd project
$ mcuhome device build devices/thermostat.yaml \
    --out-dir build/thermostat \
    --signing-key "$KEY" \
    --build-target local --build-mode subprocess \
    --build-sdk-sources "$SOURCES/sdk" \
    --build-workspace-sources "$SOURCES/workspace" \
    --build-tools-sources "$SOURCES/tools" \
    -o json > build.json
$ test "$(jq -r .ok build.json)" = true
```

The positional is a device of the project by name, or a path to a device
file — which is what a job that generates the file it builds passes. The
three package kinds are named one by one: a directory holding SDK
packages is not where workspace packages are looked for, and a machine
that keeps all three in one directory says so three times.

The same build in a container states the image it wants **beside** the
three source directories, not instead of them: a build resolves all
three package pins into its context whichever mode runs it, and what the
image replaces is the unpacking of the two environment packages, not the
pins that name them.

```console
$ mcuhome device build devices/thermostat.yaml \
    --out-dir build/thermostat --signing-key "$KEY" \
    --build-target local --build-mode container \
    --container-image "$IMAGE" \
    --build-sdk-sources "$SOURCES/sdk" \
    --build-workspace-sources "$SOURCES/workspace" \
    --build-tools-sources "$SOURCES/tools" -o json
```

A build that has to run somewhere else states the server on the command
line and hands the credential through standard input, so it is in no
process list:

```console
$ printf %s "$TOKEN" | mcuhome device build kitchen --build-target remote \
    --build-server builds.example.org --build-server-token - -o json
```

A job that wants to show progress reads the stream instead. Every line
is one message, the last one is always `result`, and the exit code says
the same thing its `ok` does:

```console
$ mcuhome device build kitchen -o json-stream | while read -r line; do
>   case "$(jq -r .verb <<<"$line")" in
>     start)    jq -r '.steps | join(" → ")' <<<"$line" ;;
>     progress) jq -r .stage <<<"$line" ;;
>     result)   jq -r '.document.ok' <<<"$line" ;;
>   esac
> done
```

## Retired spellings
A spelling this command line used to have is refused by name, with its
successor in the message and exit code 2. It is never accepted as an
alias: two spellings for one thing is what makes a surface unlearnable,
and a refusal that names the successor teaches it once.

| Retired | Now |
|---|---|
| `mcuhome clean` | `mcuhome device clean` |
| `mcuhome doctor` | `mcuhome host check` |
| `mcuhome schema config` | `mcuhome device print-schema` |
| `mcuhome schema registry` | `mcuhome device list-supported` |
| `mcuhome public-key` | `mcuhome signing print-public-key` |
| `mcuhome device boards` | `mcuhome device list-boards` |
| `mcuhome device first-time-setup` | `mcuhome device install-bootloader` |
| `mcuhome device matter-pairing` | `mcuhome device print-matter-pairing` |
| `mcuhome device matter-pairing --new` | `mcuhome device create-matter-pairing` |
| a build directory or a build-report file as the positional of `mcuhome device sign-firmware` | the device, plus `--out-dir` for a directory that is not its own |
| `--build-dir` | `--out-dir` |
| `--sdk-sources` | `--build-sdk-sources`, and the two kinds beside it |
| `--build-token` | `--build-server-token` |
| `--no-wait` | `--no-wait-for-turn` |
| `--max-wait` | `--max-wait-seconds` |
| `--name` (on `device new`) | `--friendly-name` |
| `--generate-only` | `mcuhome device generate-application` |
| `--project`, `--user`, `--system` | `--scope project`, `--scope user`, `--scope system` |
| `--confirm-upgrade` | `--confirm` |
| `--json` | `-o json` |
| `--server`, `--token` | `--build-server`, `--build-server-token` |
| `--method` | `--build-target` and `--build-mode` |
| `mcuhome validate`, `build`, `sign`, `new`, `init` (top level) | the same act under its area |
| `mcuhome init-pairing`, `mcuhome device init-pairing` | `mcuhome device create-matter-pairing` |

## What has no command, and why
The workbench's surface is larger than this command tree, and the
difference is not an oversight. Everything below is reachable by a
program that embeds the workbench; none of it is an act a person
performs at a terminal.

**The bootstrap.** `find_project_root`, `is_project_root`,
`is_upgrading`, `resolve_project` — every command runs them before it
does anything, and `project info` prints what they answer.

**Parsers and seams.** `read_yaml_file`, `to_json`, `error_dicts`,
`expand_user_path`, `parse_memory`, `parse_container_image`,
`parse_container_reference`, `resolve_host_limits`, `sha256_file`,
`context_id`, `format_generator_chain`, `current_user`,
`resolve_shutdown_seconds` — how a flag becomes a value, a value a
document, and a refusal a rendered line. `resolve_shutdown_seconds` is
what `device build` states when it says how long a stop may take;
`random_pairing` and `generate_key_pem` are where randomness enters and
exist so a test can pin it.

**Guards that are already run.** `require_secret_file` runs before every
secrets call and inside the host check; `is_p256_private_key` and
`is_p256_public_key` are how `--public-key` and `--signing-key` are
checked; `require_container_runtime` and `require_container_image` are
what `host check` reports and what a build raises.

**The backend role.** `open_builder_session`, `create_launcher`,
`BuilderSession`, `Step`, `StepResult`, `Liveness`, `CacheTier` — the
seam a build server drives, one step of the build-environment
specification at a time. A command line asks for firmware; what a build
server does with a session is its own program.

**Container images.** `resolve_container_program`,
`resolve_container_image`, `ensure_container_image` — an image is chosen
by the package set a *device's* context pins and never by its name, so
there is nothing to pull before a device is named, and the build of that
device pulls what it needs. What a person wants to know beforehand —
whether the runtime is there and whether a matching image can be
found — is what `host check` answers.

**Package acquisition.** `open_package_registry`, `fetch_sdk_package`,
`resolve_package` — a build and `environment provision` acquire what
they need, from the operator directories first and the registry second.
Downloading a package for its own sake is not an act this command line
offers; which registry is asked and what it is trusted with is
configuration (`config set registry.<base-domain>.…`).

**Cache paths.** `resolve_cache_root`, `resolve_cache_tiers` — `host
check` reports where the tiers are and, with `read_cache_usage`, what
they hold; the paths themselves are the `--build-cache-*` flags.

**Reading a report.** `read_build_report` and `memory_footprint` are
what `device build` and `device info` render the memory footprint from;
the report itself is a file in the build directory, and printing a file
is not a command.

**OTA.** `write_ota_image`, `ota_file_name`, `ota_parameters` — signing
wraps a signed image for a device that takes updates that way, so there
is no separate act; the three stay on the api for a client that does it
in a different order.

**A form-driven client's shapes.** `DeviceOutline`, `BusChoice`,
`PeripheralChoice`, `EndpointChoice`, `ClusterChoice` — what a client
with a form hands `create_device`. A command line has no form:
`device new` writes the commented example and a person edits it.

**Vocabulary.** The constants and the exception names are what every
document above is spelled with; they are not acts.

## Index of commands
Every command, and the api functions it calls. Nothing else in this
package is a supported surface: programs embed `mcuhome.workbench.api`,
which this command line is the worked example of.

| Command | Calls |
|---|---|
| `project init [<dir>]` | `is_project_root`, `read_project`, `create_project` |
| `project info [<dir>]` | `resolve_project`, `read_project`, `is_upgrading`, `plan_upgrade`, `find_devices` |
| `project upgrade [<dir>]` | `plan_upgrade`, `find_running_builds`, `open_upgrade_session`, `UpgradeSession.apply` |
| `config print` | `resolve_settings` |
| `config get <name>` | `option`, `resolve_settings`, `Settings.setting` |
| `config set <name> <value>` | `resolve_config_file`, `set_config_value` |
| `config unset <name>` | `resolve_config_file`, `unset_config_value` |
| `device new <device>` | `resolve_project`, `render_device_file`, `create_device` |
| `device list` | `resolve_project`, `find_devices` |
| `device info <device>` | `resolve_device`, `validate_device`, `read_build`, `is_busy`, `read_build_report`, `memory_footprint` |
| `device validate <device>` | `resolve_device`, `validate_device` |
| `device build [<device>]` | `resolve_device`, `read_model`, `load_model`, `resolve_settings`, `resolve_build_options`, `resolve_builder`, `resolve_build_target`, `resolve_build_mode`, `resolve_signing_key`, `public_key_pem`, `is_p256_public_key`, `build_steps`, `build_firmware`, `sign_firmware`, `read_build_report`, `memory_footprint`, `resolve_shutdown_seconds` |
| `device generate-application <device>` | `resolve_device`, `load_model`, `generate_application` |
| `device sign-firmware <device>` | `resolve_device`, `load_model`, `open_build_lock`, `plan_signing`, `sign_firmware` |
| `device clean [<device>]` | `resolve_project`, `resolve_device`, `find_devices`, `clean_build` |
| `device rename <device>` | `resolve_project`, `rename_device` |
| `device delete <device>` | `resolve_project`, `delete_device` |
| `device print-matter-pairing <device>` | `resolve_device`, `read_pairing` |
| `device create-matter-pairing <device>` | `resolve_device`, `create_pairing` |
| `device print-schema` | `device_schema` |
| `device list-boards` | `device_registry` |
| `device list-supported` | `device_registry` |
| `device flash <device>` | — (refuses) |
| `device install-bootloader <device>` | — (refuses) |
| `secret list-scopes` | `resolve_project`, `find_secret_scopes` |
| `secret list` | `resolve_project`, `read_secrets` |
| `secret reveal` | `resolve_project`, `reveal_secret` |
| `secret set` | `resolve_project`, `set_secret` |
| `secret unset` | `resolve_project`, `unset_secret` |
| `secret delete` | `resolve_project`, `delete_secret_file` |
| `signing print-public-key` | `resolve_settings`, `resolve_signing_key`, `public_key_pem` |
| `signing create-key` | `resolve_settings`, `create_signing_key` |
| `context create <device>` | `resolve_device`, `load_model`, `resolve_settings`, `resolve_build_options`, `resolve_signing_key`, `public_key_pem`, `create_context`, `lock_context`, `read_context_facts` |
| `context verify <dir>` | `verify_context` |
| `context print <dir>` | `read_context_facts`, `read_generator_chain`, `format_generator_chain` |
| `environment provision <package>` | `resolve_settings`, `resolve_build_options`, `provision_environment` |
| `host check` | `resolve_settings`, `resolve_build_options`, `check_build_host`, `read_cache_usage` |
| `version` | `stack_versions` |

Every command also calls `resolve_settings` where it offers an option
flag, and `error_dicts` where it refuses.
