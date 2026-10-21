# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Background detection. The probe runs against a real pseudo-terminal."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep import termbg  # noqa: E402

_DA1_REPLY = b"\033[?62;22c"


class _Terminal:
    """The far end of a pty. Answers the probe the way a terminal would, or not at all."""

    def __init__(self, reply: bytes | None) -> None:
        self.master, self.slave = os.openpty()
        self.device = os.ttyname(self.slave)
        self.reply = reply
        self.heard = b""
        self._thread = threading.Thread(target=self._answer, daemon=True)
        self._thread.start()

    def _answer(self) -> None:
        while b"\033[c" not in self.heard:
            try:
                chunk = os.read(self.master, 256)
            except OSError:
                return
            if not chunk:
                return
            self.heard += chunk
        if self.reply is not None:
            os.write(self.master, self.reply)

    def close(self) -> None:
        os.close(self.slave)
        os.close(self.master)
        self._thread.join(timeout=2)


class ProbeTest(unittest.TestCase):
    def _probe(self, reply: bytes | None, timeout: float):
        terminal = _Terminal(reply)
        try:
            started = time.monotonic()
            found = termbg.query_background(timeout, device=terminal.device)
            return found, time.monotonic() - started, terminal.heard
        finally:
            terminal.close()

    def test_a_white_background_reads_light(self) -> None:
        found, _elapsed, heard = self._probe(
            b"\033]11;rgb:ffff/ffff/ffff\033\\" + _DA1_REPLY, timeout=5
        )
        self.assertEqual(found, (1.0, 1.0, 1.0))
        self.assertEqual(termbg.theme_for(found), "light")
        self.assertIn(b"\033]11;?", heard)

    def test_da1_alone_means_no_support_and_no_wait(self) -> None:
        found, elapsed, _heard = self._probe(_DA1_REPLY, timeout=5)
        self.assertIsNone(found)
        self.assertLess(elapsed, 2)

    def test_silence_ends_at_the_timeout(self) -> None:
        found, elapsed, _heard = self._probe(None, timeout=0.2)
        self.assertIsNone(found)
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 2)

    def test_no_terminal_is_none(self) -> None:
        self.assertIsNone(termbg.query_background(0.1, device="/nonexistent/tty"))


class DetectTest(unittest.TestCase):
    def test_override_wins_without_a_probe(self) -> None:
        with mock.patch.object(termbg, "query_background", side_effect=AssertionError("probed")):
            self.assertEqual(termbg.detect(environ={"PVE_PREP_THEME": "Light"}), "light")
            self.assertEqual(termbg.detect(environ={"PVE_PREP_THEME": "dark"}), "dark")

    def test_probe_beats_colorfgbg(self) -> None:
        with mock.patch.object(termbg, "query_background", return_value=(0.0, 0.0, 0.0)):
            self.assertEqual(termbg.detect(environ={"COLORFGBG": "0;15"}), "dark")

    def test_colorfgbg_when_the_probe_has_no_answer(self) -> None:
        with mock.patch.object(termbg, "query_background", return_value=None):
            self.assertEqual(termbg.detect(environ={"COLORFGBG": "0;15"}), "light")
            self.assertEqual(termbg.detect(environ={"COLORFGBG": "15;default;0"}), "dark")

    def test_dark_when_nothing_answers(self) -> None:
        with mock.patch.object(termbg, "query_background", return_value=None):
            self.assertEqual(termbg.detect(environ={}), "dark")
            self.assertEqual(termbg.detect(environ={"PVE_PREP_THEME": "auto"}), "dark")
            self.assertEqual(termbg.detect(environ={"COLORFGBG": "default;default"}), "dark")
        self.assertEqual(termbg.detect(environ={}, probe=False), "dark")

    def test_colorfgbg_reads_the_last_field(self) -> None:
        self.assertEqual(termbg.from_colorfgbg("0;15"), "light")
        self.assertEqual(termbg.from_colorfgbg("0;7"), "light")
        self.assertEqual(termbg.from_colorfgbg("15;0"), "dark")
        self.assertEqual(termbg.from_colorfgbg("7;8"), "dark")
        self.assertIsNone(termbg.from_colorfgbg(""))
        self.assertIsNone(termbg.from_colorfgbg("0;99"))

    def test_reply_channels_of_any_width(self) -> None:
        self.assertEqual(termbg.parse_reply(b"\033]11;rgb:0000/0000/0000\007"), (0.0, 0.0, 0.0))
        self.assertEqual(termbg.parse_reply(b"\033]11;rgb:ff/ff/ff\033\\"), (1.0, 1.0, 1.0))
        found = termbg.parse_reply(b"junk\033]11;rgba:1e1e/1e1e/1e1e/ffff\007")
        self.assertAlmostEqual(found[0], 0x1E1E / 0xFFFF)
        self.assertIsNone(termbg.parse_reply(_DA1_REPLY))

    def test_mid_grey_is_the_line(self) -> None:
        self.assertEqual(termbg.theme_for((0.12, 0.12, 0.12)), "dark")
        self.assertEqual(termbg.theme_for((0.99, 0.97, 0.9)), "light")
        self.assertEqual(termbg.theme_for((0.0, 0.0, 1.0)), "dark")


if __name__ == "__main__":
    unittest.main()
