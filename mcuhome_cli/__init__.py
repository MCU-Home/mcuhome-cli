# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The ``mcuhome`` command line — a thin shell over the ``mcuhome`` library.

Argument parsing and human rendering live here; every stage of the
actual pipeline (validate, generate, compile, sign) is a call into the
``mcuhome`` package. Programs never import this package: the supported
programmatic surface is :mod:`mcuhome.api`.
"""

#: This package's own version — the first line of ``mcuhome --version``,
#: which reports the whole stack, one line per part (cli ADR 0002 §5):
#: the CLI, the workbench, the compiler (when installed), the model.
__version__ = "0.1.0.dev0"
