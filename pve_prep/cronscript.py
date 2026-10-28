# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Install a cron script that execs this tool. Stdlib only.

The script does not download images, run virt-customize, or call qm.
Its body is one exec of the Python program.
"""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

_NAME = "pve-template-prep"
_SCHEDULES = frozenset({"weekly", "monthly"})


class CronScriptError(Exception):
    """The cron script was refused or could not be installed."""


def cron_script(program: str, schedule: str) -> str:
    """Return the shell script that execs program with --unattended schedule."""
    if schedule not in _SCHEDULES:
        raise CronScriptError(f"schedule must be weekly or monthly, not {schedule!r}")
    if "\0" in program or any(character.isspace() for character in program):
        raise CronScriptError(f"program contains whitespace or NUL: {program!r}")
    if not Path(program).is_absolute():
        raise CronScriptError(f"program must be an absolute path: {program!r}")
    return f"#!/bin/sh\nexec {shlex.quote(program)} --unattended {schedule}\n"


def install_cron_script(
    program: str,
    schedule: str,
    *,
    directory: str | Path | None = None,
) -> Path:
    """Install cron_script into directory, or /etc/cron.<schedule> when omitted.

    The same text is not rewritten. Different text raises CronScriptError and
    is left unchanged. A symlink directory or destination is not followed.
    The cron directory is not created.
    """
    text = cron_script(program, schedule)
    cron_dir = Path(f"/etc/cron.{schedule}") if directory is None else Path(directory)
    # lstat, then open the directory itself. A symlink must not receive the script.
    if cron_dir.is_symlink():
        raise CronScriptError(f"{cron_dir} is a symlink")
    dest = cron_dir / _NAME
    try:
        dir_fd = os.open(cron_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CronScriptError(f"{cron_dir}: {exc}") from exc
    try:
        _install_at(dir_fd, dest, text)
    finally:
        os.close(dir_fd)
    return dest


def _install_at(dir_fd: int, dest: Path, text: str) -> None:
    try:
        info = os.lstat(_NAME, dir_fd=dir_fd)
    except FileNotFoundError:
        try:
            _create(dir_fd, text)
        except OSError as exc:
            raise CronScriptError(f"{dest}: {exc}") from exc
        return
    except OSError as exc:
        raise CronScriptError(f"{dest}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise CronScriptError(f"{dest} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise CronScriptError(f"{dest} is not a regular file and was left unchanged")
    try:
        current = _read_nofollow(dir_fd, len(text.encode("utf-8")) + 1)
    except OSError as exc:
        raise CronScriptError(f"{dest}: {exc}") from exc
    if current == text.encode("utf-8"):
        return
    raise CronScriptError(f"{dest} differs and was left unchanged")


def _create(dir_fd: int, text: str) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY
    fd = os.open(_NAME, flags, 0o755, dir_fd=dir_fd)
    try:
        _write_all(fd, text.encode("utf-8"))
        # open applies the process umask, so pin the mode the cron dirs expect.
        os.fchmod(fd, 0o755)
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.geteuid() == 0:
        os.chown(_NAME, 0, 0, dir_fd=dir_fd, follow_symlinks=False)


def _read_nofollow(dir_fd: int, nbytes: int) -> bytes:
    fd = os.open(_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        return os.read(fd, nbytes)
    finally:
        os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(f"short write to fd {fd}")
        view = view[written:]
