# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``python -m mcuhome.cli`` — same thing as the ``mcuhome`` command."""

from __future__ import annotations

from mcuhome.cli.main import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
