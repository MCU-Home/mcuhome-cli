# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""A flag value that may be read from standard input.

A flag whose value is a secret takes ``-`` for "read it from standard
input", so the secret need not stand in a shell's history or in the
process list of every other user on the machine:

.. code-block:: console

    $ printf %s "$TOKEN" | mcuhome device build kitchen \\
          --build-server builds.example.org --build-server-token -

One reader for every such flag, because "what does ``-`` mean here" must
have one answer: the value is read whole, the line ending a shell added
is dropped, and an empty read is refused rather than passed on as an
empty secret. Standard input is read **once** per run — a command that
offers two of these flags may take ``-`` on one of them.
"""

from __future__ import annotations

import sys
from typing import TextIO

from mcuhome.cli.errors import UsageError
from mcuhome.cli.i18n import _

__all__ = ["FROM_STDIN", "resolve_value"]

#: What a flag writes to mean "the value is on standard input".
FROM_STDIN = "-"


def resolve_value(raw: str | None, *, flag: str, stream: TextIO | None = None) -> str | None:
    """*raw*, or what standard input carried where *raw* is ``-``.

    ``None`` stays ``None``: the flag was not used, which is a different
    statement from a value that is empty. *stream* is the test seam for
    standard input. Raises :class:`~mcuhome.cli.errors.UsageError` where
    there is nothing to read — a run that would sit waiting for a value
    nobody is going to type is a run that looks like it hung.
    """
    if raw != FROM_STDIN:
        return raw
    source = sys.stdin if stream is None else stream
    if source is None or source.isatty():
        raise UsageError(
            _("{flag} - reads the value from standard input, and nothing is piped in.").format(
                flag=flag
            ),
            hint=_('pipe it:\n    printf %s "$VALUE" | mcuhome … {flag} -').format(flag=flag),
        )
    value = source.read().rstrip("\r\n")
    if not value:
        raise UsageError(
            _("{flag} - read an empty value from standard input.").format(flag=flag),
            hint=_("the value has to arrive on standard input, without a trailing newline"),
        )
    return value
