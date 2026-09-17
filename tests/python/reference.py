# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``docs/cli.md``, read as data.

The reference is the contract: every command with its positionals and
its flags, the option-flag tables, the global flags, the retired
spellings and the index. This module turns that document into values a
test can compare the parser against, and it deliberately understands
only the shapes the reference actually uses — a new shape has to be
taught here, which is the point: nobody adds a command to the tree
without writing it down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The reference this package's command line is held to.
REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "cli.md"

#: What a section's prose means by "the build option flags".
BUILD_GROUP = "@build"


@dataclass(frozen=True)
class DocumentedCommand:
    """One command as the reference states it."""

    words: tuple[str, ...]
    #: ``(name, required)`` in the order the heading names them.
    positionals: tuple[tuple[str, bool], ...]
    flags: frozenset[str]
    required_flags: frozenset[str]

    @property
    def spelling(self) -> str:
        return " ".join(self.words)


def lines() -> list[str]:
    return REFERENCE.read_text(encoding="utf-8").splitlines()


def sections(level: str) -> list[tuple[str, list[str]]]:
    """Every ``level`` heading with the lines under it, up to the next one."""
    found: list[tuple[str, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for line in lines():
        marker = line.split(" ")[0]
        if line.startswith(level + " "):
            if heading is not None:
                found.append((heading, body))
            heading, body = line[len(level) + 1 :], []
        elif heading is not None and set(marker) == {"#"} and len(marker) <= len(level):
            found.append((heading, body))
            heading, body = None, []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        found.append((heading, body))
    return found


def tables(body: list[str]) -> list[list[list[str]]]:
    """Every markdown table in *body*, as rows of stripped cells."""
    found: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in body:
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            current.append(cells)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def quoted(cell: str) -> list[str]:
    """Everything in backticks, in order."""
    return re.findall(r"`([^`]+)`", cell)


def flags_in(cell: str) -> list[str]:
    """Every long flag named in *cell*, without the value set it takes."""
    return [token.split()[0] for token in quoted(cell) if token.startswith("--")]


def _spelling(text: str) -> DocumentedCommand:
    """One ``mcuhome …`` spelling from a heading."""
    text = text.removeprefix("mcuhome ").strip()
    optional = re.findall(r"\[([^\]]*)\]", text)
    words: list[str] = []
    positionals: list[tuple[str, bool]] = []
    flags: set[str] = set()
    required_flags: set[str] = set()
    tokens = re.sub(r"\[[^\]]*\]", " ", text).split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("<"):
            positionals.append((token.strip("<>"), True))
        elif token.startswith("--"):
            flags.add(token)
            required_flags.add(token)
            if index + 1 < len(tokens) and tokens[index + 1].startswith("<"):
                index += 1
        else:
            words.append(token)
        index += 1
    for part in optional:
        for position, token in enumerate(part.split()):
            if token.startswith("--"):
                flags.add(token)
            elif token.startswith("<") and position == 0:
                positionals.append((token.strip("<>"), False))
    return DocumentedCommand(
        tuple(words), tuple(positionals), frozenset(flags), frozenset(required_flags)
    )


def _group_flags(body: list[str], *, act: str, shared: bool) -> set[str]:
    """The flags a section's tables and its "Plus"/"Takes" sentences name.

    *shared* says the section covers more than one command, and a
    sentence that opens with a command's own name then belongs to that
    command alone (``` `flash` takes --flash-mode ```).
    """
    flags: set[str] = set()
    for table in tables(body):
        if table[0][0].lower() != "flag":
            continue
        for row in table[1:]:
            flags.update(flags_in(row[0]))
    prose = " ".join(line for line in body if not line.startswith("|"))
    for sentence in re.split(r"(?<=\.)\s+", prose):
        if not re.match(r"^\s*(Plus|Takes)\b", sentence) and " takes " not in sentence:
            continue
        subject = re.match(r"^\s*`([a-z-]+)` takes", sentence)
        if subject is not None and subject.group(1) != act:
            continue
        if subject is None and shared and " takes " in sentence:
            continue
        if "the build option flags" in sentence:
            flags.add(BUILD_GROUP)
        flags.update(flags_in(sentence))
    return flags


def documented_commands() -> dict[tuple[str, ...], DocumentedCommand]:
    """Every ``### `mcuhome …``` heading of the reference, as a command."""
    commands: dict[tuple[str, ...], DocumentedCommand] = {}
    for heading, body in sections("###"):
        spellings = [token for token in quoted(heading) if token.startswith("mcuhome")]
        for spelling in spellings:
            command = _spelling(spelling)
            group = _group_flags(body, act=command.words[-1], shared=len(spellings) > 1)
            commands[command.words] = DocumentedCommand(
                words=command.words,
                positionals=command.positionals,
                flags=command.flags | frozenset(group),
                required_flags=command.required_flags,
            )
    return commands


def _table_after(marker: str) -> list[list[str]]:
    """The first table that follows the line containing *marker*."""
    body = lines()
    start = next(index for index, line in enumerate(body) if marker in line)
    found = tables(body[start:])
    return found[0]


def option_flag_rows() -> list[tuple[str, str, str]]:
    """``(flag, option key, environment variable)`` of the two option tables."""
    rows: list[tuple[str, str, str]] = []
    for marker in ("**The build option flags**", "**The signing option flags.**"):
        for row in _table_after(marker)[1:]:
            rows.append((quoted(row[0])[0], quoted(row[2])[0], quoted(row[3])[0]))
    return rows


def global_flags() -> set[str]:
    """Every flag of the *Global flags* table."""
    flags: set[str] = set()
    for row in _table_after("| Flag | Value | Carries |")[1:]:
        flags.update(token.split()[0] for token in quoted(row[0]) if token.startswith("-"))
    return flags


def retired_spellings() -> tuple[dict[str, tuple[str, str]], int]:
    """Every retired spelling with its successors and where it was retired.

    A row whose *Retired* cell is a list of spellings is data; one that
    describes a spelling in words (a positional that is not taken any
    more) is prose and cannot be checked mechanically, so it is counted
    and the count is what a new prose row has to change. A row that
    qualifies its spelling — ``--name`` (on ``device new``) — carries the
    command it was retired on beside the successor.
    """
    spellings: dict[str, tuple[str, str]] = {}
    prose = 0
    for row in _table_after("| Retired | Now |")[1:]:
        retired, successor = row[0], row[1]
        qualifier = re.search(r"\(on `([^`]+)`\)", retired)
        retired = re.sub(r"\(on `[^`]+`\)", "", retired)
        rest = re.sub(r"`[^`]+`", "", retired).replace(",", "").replace("(top level)", "").strip()
        if rest:
            prose += 1
            continue
        for spelling in quoted(retired):
            spellings[spelling] = (successor, qualifier.group(1) if qualifier else "")
    return spellings, prose


def index_commands() -> set[tuple[str, ...]]:
    """The command words of the *Index of commands* table."""
    return set(index_calls())


def index_calls() -> dict[tuple[str, ...], list[str]]:
    """The *Calls* cell of the index, by command words.

    Every backticked token of the cell, in order — the caller decides
    which of them are api names.
    """
    found: dict[tuple[str, ...], list[str]] = {}
    for row in _table_after("| Command | Calls |")[1:]:
        spelling = quoted(row[0])[0]
        words = [token for token in spelling.split() if not token.startswith(("<", "[", "-"))]
        found[tuple(words)] = quoted(row[1])
    return found


def prose_calls() -> dict[tuple[str, ...], list[str]]:
    """Every backticked token of a command section's prose, by command.

    The tables are left out: a flag table's *Carries* column names the
    api field a flag fills, which is not a call the command makes.
    """
    found: dict[tuple[str, ...], list[str]] = {}
    for heading, body in sections("###"):
        prose = " ".join(line for line in body if not line.startswith("|"))
        for spelling in quoted(heading):
            if not spelling.startswith("mcuhome"):
                continue
            found[_spelling(spelling).words] = quoted(prose)
    return found
