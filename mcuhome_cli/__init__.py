# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The ``mcuhome`` command line — a thin shell over the ``mcuhome`` library.

Argument parsing and human rendering live here; every stage of the
actual pipeline (validate, generate, compile, sign) is a call into the
``mcuhome`` package. Programs never import this package: the supported
programmatic surface is :mod:`mcuhome.api`.
"""

#: This package's own version. ``mcuhome --version`` deliberately reports
#: the *builder's* version instead — that is the number that determines
#: what a build produces; the shell adds nothing to it.
__version__ = "0.1.0.dev0"
