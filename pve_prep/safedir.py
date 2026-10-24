# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Private directories that no other local user can redirect. Stdlib only.

A directory is private when every directory on its path, before and after
symlinks are resolved, belongs to root or to this process's user, and no
other user can rename entries in it. A world-writable directory counts only
with the sticky bit set, as /tmp and /var/tmp have: there, nobody else can
rename or replace an entry this user owns.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafeDirError(Exception):
    """A directory on the path could be swapped by another user."""


def _trusted_owner(uid: int) -> bool:
    return uid in {0, os.geteuid()}


def _check_entry(path: Path) -> None:
    """One path entry, not followed. A symlink counts as its owner and its parent."""
    info = os.lstat(path)
    if not _trusted_owner(info.st_uid):
        raise UnsafeDirError(f"{path} is owned by uid {info.st_uid}; remove it or chown it to root")
    if stat.S_ISLNK(info.st_mode):
        return
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeDirError(f"{path} is not a directory")
    others_write = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if others_write and not info.st_mode & stat.S_ISVTX:
        raise UnsafeDirError(f"{path} is writable by other users and not sticky")


def _prefixes(path: Path) -> list[Path]:
    parts = path.parts
    return [Path(*parts[: index + 1]) for index in range(len(parts))]


def verify_private_dir(path: str | Path) -> Path:
    """Raise UnsafeDirError unless path is an existing private directory.

    Each prefix of the path as given is checked unfollowed, then each prefix
    of where it resolves. A symlink planted by another user, or a directory
    another user could rename, fails either way.
    """
    given = Path(path)
    if not given.is_absolute():
        raise UnsafeDirError(f"{given} is not an absolute path")
    for prefix in _prefixes(given):
        _check_entry(prefix)
        for real in _prefixes(Path(os.path.realpath(prefix))):
            _check_entry(real)
    final = Path(os.path.realpath(given))
    if not final.is_dir():
        raise UnsafeDirError(f"{given} is not a directory")
    return final


def prepare_private_dir(path: str | Path) -> Path:
    """Create path as needed, verify it, and set it to mode 0700.

    Missing directories are created 0700 one level at a time. Each existing
    level is checked before the next is created inside it, so mkdir never
    follows a symlink another user planted.
    """
    given = Path(path)
    if not given.is_absolute():
        raise UnsafeDirError(f"{given} is not an absolute path")
    for prefix in _prefixes(given):
        if not os.path.lexists(prefix):
            verify_private_dir(prefix.parent)
            try:
                os.mkdir(prefix, 0o700)
            except FileExistsError:
                pass
    if Path(given).is_symlink():
        raise UnsafeDirError(f"{given} is a symlink")
    final = verify_private_dir(given)
    os.chmod(final, 0o700)
    return final
