# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Root-only per-VMID detail logs, and the install-time logging policy.

Unattended refreshes always write a log for a VMID that changed or failed.
Interactive runs follow the policy in /etc/pve-template-prep/logging (auto
by default) and optional --logs=on|off for that session.
"""

from __future__ import annotations

import os
from pathlib import Path

from .safedir import UnsafeDirError, prepare_private_dir

LOG_DIR = "/var/log/pve-template-prep"
POLICY_PATH = Path("/etc/pve-template-prep/logging")
POLICIES = ("auto", "on", "off")
DEFAULT_POLICY = "auto"

_MIN_VMID = 100
_MAX_VMID = 999_999_999


class RunLogError(OSError):
    """The detail log could not be written."""


def read_policy(path: str | Path = POLICY_PATH) -> str:
    """Return auto, on, or off. Missing, symlink, or junk text becomes auto."""
    dest = Path(path)
    if dest.is_symlink():
        return DEFAULT_POLICY
    try:
        text = dest.read_text(encoding="utf-8").strip().casefold()
    except OSError:
        return DEFAULT_POLICY
    if text in POLICIES:
        return text
    return DEFAULT_POLICY


def should_write_logs(
    *,
    policy: str,
    cli: str | None,
    unattended: bool,
) -> bool:
    """Whether this run writes /var/log/pve-template-prep/<vmid>.log files.

    Unattended always writes for VMIDs that changed or failed. Interactive
    uses the install policy; --logs=on forces on; --logs=off only suppresses
    when the install policy is on.
    """
    if unattended:
        return True
    if cli == "on":
        return True
    if cli == "off":
        # Meaningful when install policy is on; otherwise interactive already
        # writes nothing, so this is a no-op.
        return False
    return policy == "on"


def write_run_log(vmid: int, text: str, *, directory: str | Path = LOG_DIR) -> Path:
    """Replace <vmid>.log in directory and return its path.

    A symlink directory or file is refused and is not followed. The file
    mode is 0600.
    """
    if isinstance(vmid, bool) or not isinstance(vmid, int) or not _MIN_VMID <= vmid <= _MAX_VMID:
        raise RunLogError(f"VMID {vmid} cannot name a log")
    try:
        root = prepare_private_dir(directory)
    except (UnsafeDirError, OSError) as exc:
        raise RunLogError(f"unsafe log dir {directory}: {exc}") from exc
    dest = root / f"{vmid}.log"
    if dest.is_symlink():
        raise RunLogError(f"{dest} is a symlink")
    payload = text if text.endswith("\n") else text + "\n"
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW
    try:
        fd = os.open(dest, flags, 0o600)
    except OSError as exc:
        raise RunLogError(f"{dest}: {exc}") from exc
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, payload.encode("utf-8"))
        os.fsync(fd)
    except OSError as exc:
        raise RunLogError(f"{dest}: {exc}") from exc
    finally:
        os.close(fd)
    if os.geteuid() == 0:
        os.chown(dest, 0, 0, follow_symlinks=False)
    return dest


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(f"short write to fd {fd}")
        view = view[written:]
