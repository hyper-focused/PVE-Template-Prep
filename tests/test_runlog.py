# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""The unattended detail log. One root-only file per VMID."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from privtmp import private_tempdir  # noqa: E402
from pve_prep.runlog import RunLogError, write_run_log  # noqa: E402


class RunLogTests(unittest.TestCase):
    def test_replaces_one_private_file(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp) / "log"
            first = write_run_log(910, "first", directory=root)
            second = write_run_log(910, "second", directory=root)
            self.assertEqual(first, second)
            self.assertEqual(second.read_text(encoding="utf-8"), "second\n")
            self.assertEqual(second.stat().st_mode & 0o777, 0o600)
            self.assertFalse(second.is_symlink())

    def test_refuses_a_symlink_and_a_bad_vmid(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp) / "log"
            root.mkdir(mode=0o700)
            dest = root / "910.log"
            dest.symlink_to(root / "elsewhere")
            with self.assertRaises(RunLogError):
                write_run_log(910, "nope", directory=root)
            self.assertTrue(dest.is_symlink())
        with self.assertRaises(RunLogError):
            write_run_log(True, "nope", directory="/tmp")  # type: ignore[arg-type]
        with self.assertRaises(RunLogError):
            write_run_log(1, "nope", directory="/tmp")

    def test_refuses_a_relative_directory(self) -> None:
        with self.assertRaises(RunLogError):
            write_run_log(910, "nope", directory="logs")
        self.assertFalse(Path("logs").exists())


if __name__ == "__main__":
    unittest.main()
