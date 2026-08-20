# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Message externalization, gettext-style from day one (cli ADR 0004 §6).

The shipped language is English; translations come when the project's
community does. Until then :data:`_TRANSLATIONS` is a
``NullTranslations`` and :func:`_` is the identity — but every message
that goes through it is already a message a translator can reach, which
is the whole point of wiring this before anyone asks for it.

What never comes through here: machine output. JSON keys, stream verbs,
error codes and configuration keys are structural vocabulary, not prose,
and translating them would break every consumer (ADR 0004 §2/§6).

The catalog domain is ``mcuhome`` — the command's name — and compiled
catalogs will live under ``locale/<lang>/LC_MESSAGES/mcuhome.mo`` next
to this file. Language selection is gettext's own (``LANGUAGE``,
``LC_ALL``, ``LC_MESSAGES``, ``LANG``): a command line is the one
program whose process environment *is* the user's answer, so reading it
here is correct where it would be a boundary violation in the workbench
(ADR 0020's stated-environment rule binds the library, not its shell).
"""

from __future__ import annotations

import gettext
from pathlib import Path

__all__ = ["DOMAIN", "LOCALE_DIR", "_"]

#: The gettext domain — the name of the catalog files.
DOMAIN = "mcuhome"

#: Where compiled catalogs live, once there are any.
LOCALE_DIR = Path(__file__).resolve().parent / "locale"

_TRANSLATIONS = gettext.translation(DOMAIN, localedir=LOCALE_DIR, fallback=True)


def _(message: str) -> str:
    """*message*, translated when a catalog for the user's language exists."""
    return _TRANSLATIONS.gettext(message)
