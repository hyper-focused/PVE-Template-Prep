# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Temp directories that pve_prep.safedir accepts as private.

A dev box or sandbox can hand out a world-writable, non-sticky TMPDIR.
safedir rightly refuses anything under it, so tests fall back to /tmp.
"""

from __future__ import annotations

import tempfile
import unittest
from tempfile import TemporaryDirectory

from pve_prep.safedir import UnsafeDirError, verify_private_dir


def private_root() -> str:
    """The first temp root with no directory another user could swap."""
    for candidate in (tempfile.gettempdir(), "/tmp"):
        try:
            verify_private_dir(candidate)
        except (UnsafeDirError, OSError):
            continue
        return candidate
    raise unittest.SkipTest("no private temp root on this host")


def private_tempdir() -> TemporaryDirectory:
    return TemporaryDirectory(dir=private_root())
