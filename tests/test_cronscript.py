# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Unit tests for pve_prep.cronscript. No /etc writes."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    from privtmp import private_tempdir
except ImportError:
    # `python3 -m unittest tests.test_cronscript` does not put tests/ on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from privtmp import private_tempdir

from pve_prep.cronscript import (
    CronScriptError,
    cron_script,
    install_cron_script,
    remove_cron_script,
)

_PROGRAM = "/usr/local/sbin/pve-template-prep"
_WEEKLY = "#!/bin/sh\nexec /usr/local/sbin/pve-template-prep --unattended weekly\n"
_MONTHLY = "#!/bin/sh\nexec /usr/local/sbin/pve-template-prep --unattended monthly\n"
_NAME = "pve-template-prep"


def _path(root: str, schedule: str) -> Path:
    return Path(root) / f"cron.{schedule}" / _NAME


class CronScriptTextTests(unittest.TestCase):
    def test_weekly_and_monthly_are_an_exec(self) -> None:
        self.assertEqual(cron_script(_PROGRAM, "weekly"), _WEEKLY)
        self.assertEqual(cron_script(_PROGRAM, "monthly"), _MONTHLY)

    def test_shell_metacharacters_are_quoted(self) -> None:
        program = "/usr/local/sbin/pve-template-prep$(id)"
        script = cron_script(program, "weekly")
        self.assertEqual(
            script,
            "#!/bin/sh\nexec '/usr/local/sbin/pve-template-prep$(id)' --unattended weekly\n",
        )

    def test_space_or_newline_in_program_is_rejected(self) -> None:
        with self.assertRaises(CronScriptError):
            cron_script("/usr/local/sbin/pve template-prep", "weekly")
        with self.assertRaises(CronScriptError):
            cron_script("/usr/local/sbin/pve\ntemplate-prep", "monthly")

    def test_nul_in_program_is_rejected(self) -> None:
        with self.assertRaises(CronScriptError):
            cron_script("/usr/local/sbin/pve\0template-prep", "weekly")

    def test_relative_program_is_rejected(self) -> None:
        with self.assertRaises(CronScriptError):
            cron_script("pve-template-prep", "weekly")
        with self.assertRaises(CronScriptError):
            cron_script("usr/local/sbin/pve-template-prep", "monthly")

    def test_other_schedules_are_rejected(self) -> None:
        with self.assertRaises(CronScriptError):
            cron_script(_PROGRAM, "daily")
        with self.assertRaises(CronScriptError):
            cron_script(_PROGRAM, "Weekly")


class InstallTests(unittest.TestCase):
    def test_install_creates_mode_755_and_exact_text(self) -> None:
        with private_tempdir() as tmp:
            previous = os.umask(0o077)
            try:
                with mock.patch("pve_prep.cronscript.os.chown") as chown:
                    path = install_cron_script(_PROGRAM, "weekly", directory=tmp)
            finally:
                os.umask(previous)
            self.assertEqual(path, _path(tmp, "weekly"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(path.read_bytes(), _WEEKLY.encode("utf-8"))
            chown.assert_not_called()

    def test_weekly_and_monthly_do_not_share_a_file(self) -> None:
        with private_tempdir() as tmp:
            weekly = install_cron_script(_PROGRAM, "weekly", directory=tmp)
            monthly = install_cron_script(_PROGRAM, "monthly", directory=tmp)
            self.assertEqual(weekly.read_bytes(), _WEEKLY.encode("utf-8"))
            self.assertEqual(monthly.read_bytes(), _MONTHLY.encode("utf-8"))
            self.assertNotEqual(weekly, monthly)

    def test_second_install_keeps_mtime_and_text(self) -> None:
        with private_tempdir() as tmp:
            path = install_cron_script(_PROGRAM, "weekly", directory=tmp)
            pinned = 1_700_000_000_000_000_000
            os.utime(path, ns=(pinned, pinned))
            before = path.stat()
            again = install_cron_script(_PROGRAM, "weekly", directory=tmp)
            after = path.stat()
            self.assertEqual(again, path)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_ino, before.st_ino)
            self.assertEqual(path.read_bytes(), _WEEKLY.encode("utf-8"))

    def test_different_file_is_left_unchanged(self) -> None:
        with private_tempdir() as tmp:
            path = _path(tmp, "weekly")
            path.parent.mkdir()
            original = b"not the cron script\n"
            path.write_bytes(original)
            before = path.stat()
            with self.assertRaises(CronScriptError) as caught:
                install_cron_script(_PROGRAM, "weekly", directory=tmp)
            after = path.stat()
            self.assertIn("left unchanged", str(caught.exception))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(after.st_ino, before.st_ino)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_symlink_destination_is_refused(self) -> None:
        with private_tempdir() as tmp:
            target = Path(tmp) / "elsewhere"
            original = b"keep me\n"
            target.write_bytes(original)
            cron_dir = Path(tmp) / "cron.monthly"
            cron_dir.mkdir()
            link = cron_dir / _NAME
            link.symlink_to(target)
            with self.assertRaises(CronScriptError):
                install_cron_script(_PROGRAM, "monthly", directory=tmp)
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), str(target))

            target.write_bytes(_MONTHLY.encode("utf-8"))
            with self.assertRaises(CronScriptError):
                install_cron_script(_PROGRAM, "monthly", directory=tmp)
            self.assertEqual(target.read_bytes(), _MONTHLY.encode("utf-8"))
            self.assertTrue(link.is_symlink())

    def test_symlink_directory_is_refused(self) -> None:
        with private_tempdir() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "cron.weekly"
            link.symlink_to(real)
            with self.assertRaises(CronScriptError):
                install_cron_script(_PROGRAM, "weekly", directory=tmp)
            self.assertEqual(list(real.iterdir()), [])
            self.assertTrue(link.is_symlink())

    def test_default_directory_follows_the_schedule(self) -> None:
        # Never open the real /etc cron directories.
        missing = FileNotFoundError(2, "No such file or directory")
        with mock.patch("pve_prep.cronscript.os.open", side_effect=missing) as opened:
            with self.assertRaises(CronScriptError) as weekly:
                install_cron_script(_PROGRAM, "weekly")
            with self.assertRaises(CronScriptError) as monthly:
                install_cron_script(_PROGRAM, "monthly")
        self.assertIn("/etc/cron.weekly", str(weekly.exception))
        self.assertIn("/etc/cron.monthly", str(monthly.exception))
        self.assertEqual(
            [call.args[0] for call in opened.call_args_list],
            [Path("/etc/cron.weekly"), Path("/etc/cron.monthly")],
        )

    def test_missing_directory_names_the_path(self) -> None:
        with private_tempdir() as tmp:
            missing = Path(tmp) / "no-such-cron"
            with self.assertRaises(CronScriptError) as caught:
                install_cron_script(_PROGRAM, "weekly", directory=missing)
            self.assertIn(str(missing), str(caught.exception))
            self.assertFalse(missing.exists())

    def test_root_chowns_the_new_script(self) -> None:
        with private_tempdir() as tmp:
            with mock.patch("pve_prep.cronscript.os.geteuid", return_value=0), mock.patch(
                "pve_prep.cronscript.os.chown"
            ) as chown:
                path = install_cron_script(_PROGRAM, "monthly", directory=tmp)
            self.assertEqual(path.read_bytes(), _MONTHLY.encode("utf-8"))
            chown.assert_called_once()
            self.assertEqual(chown.call_args.args[1:], (0, 0))


class RemoveTests(unittest.TestCase):
    def test_remove_ours_and_leave_absent(self) -> None:
        with private_tempdir() as tmp:
            path = install_cron_script(_PROGRAM, "weekly", directory=tmp)
            self.assertTrue(remove_cron_script(_PROGRAM, "weekly", directory=tmp))
            self.assertFalse(path.exists())
            self.assertFalse(remove_cron_script(_PROGRAM, "weekly", directory=tmp))

    def test_remove_leaves_a_different_file(self) -> None:
        with private_tempdir() as tmp:
            path = _path(tmp, "weekly")
            path.parent.mkdir()
            path.write_text("other\n", encoding="utf-8")
            with self.assertRaises(CronScriptError) as caught:
                remove_cron_script(_PROGRAM, "weekly", directory=tmp)
            self.assertIn("left unchanged", str(caught.exception))
            self.assertEqual(path.read_text(encoding="utf-8"), "other\n")

    def test_remove_refuses_a_symlink(self) -> None:
        with private_tempdir() as tmp:
            target = Path(tmp) / "elsewhere"
            target.write_text(_WEEKLY, encoding="utf-8")
            cron_dir = Path(tmp) / "cron.weekly"
            cron_dir.mkdir()
            link = cron_dir / _NAME
            link.symlink_to(target)
            with self.assertRaises(CronScriptError):
                remove_cron_script(_PROGRAM, "weekly", directory=tmp)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), _WEEKLY)


if __name__ == "__main__":
    unittest.main()
