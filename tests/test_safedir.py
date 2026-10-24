# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Unit tests for pve_prep.safedir and the directories that use it."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from privtmp import private_tempdir
from pve_prep import safedir
from pve_prep.download import DownloadError, _prepare_cache, clear_cache
from pve_prep.safedir import UnsafeDirError, prepare_private_dir, verify_private_dir
from pve_prep.vm import VmError, prepare_backup_dir

_ATTACKER = 4242


def _foreign(*owned: Path):
    """os.lstat that reports the given paths as owned by another user."""
    real_lstat = os.lstat
    marked = {str(path) for path in owned}

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if str(path) not in marked:
            return info
        fields = list(info)
        fields[4] = _ATTACKER  # st_uid
        return os.stat_result(fields)

    return mock.patch.object(safedir.os, "lstat", side_effect=lstat)


class PrivateDirTests(unittest.TestCase):
    def test_missing_levels_are_created_root_only(self) -> None:
        with private_tempdir() as tmp:
            target = Path(tmp) / "pve-template-prep" / "cache"
            prepare_private_dir(target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)

    def test_parent_owned_by_another_user_is_refused(self) -> None:
        # The reported attack: a local user creates the parent first.
        with private_tempdir() as tmp:
            parent = Path(tmp) / "pve-template-prep"
            parent.mkdir()
            with _foreign(parent), self.assertRaises(UnsafeDirError) as caught:
                prepare_private_dir(parent / "cache")
            self.assertIn("owned by uid 4242", str(caught.exception))
            self.assertFalse((parent / "cache").exists())

    def test_symlinked_parent_into_another_users_dir_is_refused(self) -> None:
        with private_tempdir() as tmp:
            theirs = Path(tmp) / "theirs"
            theirs.mkdir()
            link = Path(tmp) / "pve-template-prep"
            link.symlink_to(theirs)
            # The link is ours; where it lands is not. The resolved chain catches it.
            with _foreign(theirs.resolve()), self.assertRaises(UnsafeDirError):
                prepare_private_dir(link / "cache")
            self.assertFalse((theirs / "cache").exists())

    def test_world_writable_parent_needs_the_sticky_bit(self) -> None:
        with private_tempdir() as tmp:
            shared = Path(tmp) / "shared"
            shared.mkdir()
            os.chmod(shared, 0o777)
            with self.assertRaises(UnsafeDirError):
                prepare_private_dir(shared / "cache")
            os.chmod(shared, 0o1777)
            prepare_private_dir(shared / "cache")

    def test_final_symlink_is_refused(self) -> None:
        with private_tempdir() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "cache"
            link.symlink_to(real)
            with self.assertRaises(UnsafeDirError):
                prepare_private_dir(link)

    def test_relative_path_is_refused(self) -> None:
        with self.assertRaises(UnsafeDirError):
            verify_private_dir("var/tmp")


class CallerTests(unittest.TestCase):
    def test_cache_refuses_a_foreign_parent(self) -> None:
        with private_tempdir() as tmp:
            parent = Path(tmp) / "pve-template-prep"
            parent.mkdir()
            with _foreign(parent), self.assertRaises(DownloadError) as caught:
                _prepare_cache(parent / "cache")
            self.assertIn("unsafe cache dir", str(caught.exception))

    def test_clear_cache_refuses_a_foreign_parent(self) -> None:
        with private_tempdir() as tmp:
            cache = Path(tmp) / "pve-template-prep" / "cache"
            cache.mkdir(parents=True)
            (cache / "image.qcow2").write_bytes(b"x")
            with _foreign(cache.parent), self.assertRaises(DownloadError):
                clear_cache(cache)
            self.assertTrue((cache / "image.qcow2").exists())

    def test_backup_dir_refuses_a_foreign_parent(self) -> None:
        with private_tempdir() as tmp:
            parent = Path(tmp) / "backups"
            parent.mkdir()
            with _foreign(parent), self.assertRaises(VmError):
                prepare_backup_dir(str(parent / "pve-template-prep"))


if __name__ == "__main__":
    unittest.main()
