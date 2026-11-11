# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Install-time logging policy and per-run --logs override."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from privtmp import private_tempdir  # noqa: E402
from pve_prep.runlog import (  # noqa: E402
    DEFAULT_POLICY,
    read_policy,
    should_write_logs,
)


class LoggingPolicyTests(unittest.TestCase):
    def test_missing_or_junk_is_auto(self) -> None:
        with private_tempdir() as tmp:
            path = Path(tmp) / "logging"
            self.assertEqual(read_policy(path), DEFAULT_POLICY)
            path.write_text("maybe\n", encoding="utf-8")
            self.assertEqual(read_policy(path), DEFAULT_POLICY)

    def test_reads_on_off_auto(self) -> None:
        with private_tempdir() as tmp:
            path = Path(tmp) / "logging"
            for value in ("auto", "on", "off", "ON"):
                path.write_text(value + "\n", encoding="utf-8")
                self.assertEqual(read_policy(path), value.casefold())

    def test_symlink_is_auto(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp)
            target = root / "real"
            target.write_text("on\n", encoding="utf-8")
            link = root / "logging"
            link.symlink_to(target)
            self.assertEqual(read_policy(link), DEFAULT_POLICY)

    def test_interactive_follows_policy_and_cli(self) -> None:
        self.assertFalse(should_write_logs(policy="auto", cli=None, unattended=False))
        self.assertTrue(should_write_logs(policy="on", cli=None, unattended=False))
        self.assertFalse(should_write_logs(policy="off", cli=None, unattended=False))
        self.assertTrue(should_write_logs(policy="auto", cli="on", unattended=False))
        self.assertTrue(should_write_logs(policy="off", cli="on", unattended=False))
        self.assertFalse(should_write_logs(policy="on", cli="off", unattended=False))
        self.assertFalse(should_write_logs(policy="auto", cli="off", unattended=False))

    def test_unattended_always_writes(self) -> None:
        for policy in ("auto", "on", "off"):
            for cli in (None, "on", "off"):
                self.assertTrue(
                    should_write_logs(policy=policy, cli=cli, unattended=True)
                )


if __name__ == "__main__":
    unittest.main()
