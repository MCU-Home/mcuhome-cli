# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The ``mcuhome`` command line — a thin shell over the workbench.

Argument parsing and rendering live here; every act a command performs
is a call into :mod:`mcuhome.workbench.api`, which is also the one
module this package imports of it. Programs never import this package:
what a program embeds is that api, and ``docs/cli.md`` is the worked
example of it.
"""

#: This package's own version — the first line of ``mcuhome --version``
#: and the ``command_line`` key of what ``mcuhome version`` answers. It
#: is the one fact the workbench cannot know, because it cannot know
#: what embeds it.
__version__ = "0.1.0.dev0"
