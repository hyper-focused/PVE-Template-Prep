# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Per-VMID enrollment records. Stdlib only.

Each record is one {vmid}.json file in a private directory. A symlink at
the directory or at the JSON path is refused and is not followed.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from .safedir import UnsafeDirError, prepare_private_dir, verify_private_dir

ENROLL_DIR = "/var/lib/pve-template-prep"
SCHEDULES = ("weekly", "monthly")

_MIN_VMID = 100
_MAX_VMID = 999_999_999
_DIGEST_LEN = {"256": 64, "512": 128}
_HEX = re.compile(r"[0-9a-f]*")
_FILE_NAME = re.compile(r"([0-9]+)\.json\Z")
_FIELDS = (
    "checksum_alg",
    "digest",
    "distro",
    "password_auth",
    "permit_root",
    "release",
    "schedule",
    "vmid",
)


class EnrollmentError(Exception):
    """An enrollment file is invalid or not safe to write."""


@dataclass(frozen=True)
class Enrollment:
    """One enrolled guest. The JSON object has these fields and no others."""

    vmid: int
    distro: str
    release: str
    digest: str
    checksum_alg: str  # "256" or "512"
    password_auth: bool
    permit_root: bool
    schedule: str  # weekly or monthly

    def __post_init__(self) -> None:
        _check_vmid(self.vmid)
        _check_token(self.distro, "distro")
        _check_token(self.release, "release")
        _check_alg(self.checksum_alg)
        _check_digest(self.digest, self.checksum_alg)
        _check_bool(self.password_auth, "password_auth")
        _check_bool(self.permit_root, "permit_root")
        _check_schedule(self.schedule)


def _check_vmid(vmid: object) -> int:
    if isinstance(vmid, bool) or not isinstance(vmid, int):
        raise EnrollmentError("vmid must be an integer")
    if vmid < _MIN_VMID:
        raise EnrollmentError(f"VMID {vmid} is below {_MIN_VMID}")
    if vmid > _MAX_VMID:
        raise EnrollmentError(f"VMID {vmid} is above {_MAX_VMID}")
    return vmid


def _check_token(value: object, field: str) -> str:
    if not isinstance(value, str) or value == "":
        raise EnrollmentError(f"{field} must be a non-empty token")
    if any(char.isspace() for char in value) or "/" in value or ".." in value:
        raise EnrollmentError(f"{field} must be a single token")
    return value


def _check_alg(alg: object) -> str:
    if not isinstance(alg, str) or alg not in _DIGEST_LEN:
        raise EnrollmentError("checksum_alg must be 256 or 512")
    return alg


def _check_digest(digest: object, alg: str) -> str:
    if not isinstance(digest, str) or _HEX.fullmatch(digest) is None:
        raise EnrollmentError("digest must be lowercase hex")
    if len(digest) % 2 != 0:
        raise EnrollmentError("digest has an odd length")
    want = _DIGEST_LEN[alg]
    if len(digest) != want:
        raise EnrollmentError(f"digest must be {want} lowercase hex characters")
    return digest


def _check_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise EnrollmentError(f"{field} must be true or false")
    return value


def _check_schedule(schedule: object) -> str:
    if not isinstance(schedule, str) or schedule not in SCHEDULES:
        raise EnrollmentError(f"unknown schedule {schedule!r}")
    return schedule


def _named(vmid: int, exc: EnrollmentError) -> EnrollmentError:
    # Always name the file's VMID. A range error already says "VMID 1000...",
    # which must not hide the file that was being read.
    return EnrollmentError(f"VMID {vmid}: {exc}")


def _private_dir(directory: str | Path) -> Path:
    try:
        return prepare_private_dir(directory)
    except (UnsafeDirError, OSError) as exc:
        raise EnrollmentError(f"unsafe enrollment dir {directory}: {exc}") from exc


def _body(record: Enrollment) -> str:
    payload = {field: getattr(record, field) for field in _FIELDS}
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise EnrollmentError(f"{path} is a symlink")


def _write_file(root: Path, record: Enrollment) -> Path:
    dest = root / f"{record.vmid}.json"
    _refuse_symlink(dest)
    # A leading dot keeps a crashed temp file out of enrollments_for.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{record.vmid}.",
        suffix=".tmp",
        dir=os.fspath(root),
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_body(record))
            handle.flush()
            os.fsync(handle.fileno())
        # Swap the directory entry. Do not follow a symlink onto its target.
        _refuse_symlink(dest)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise
    return dest


def save_enrollment(record: Enrollment, *, directory: str | Path = ENROLL_DIR) -> Path:
    """Write {vmid}.json into directory and return its path."""
    if not isinstance(record, Enrollment):
        raise EnrollmentError("enrollment record is required")
    root = _private_dir(directory)
    try:
        return _write_file(root, record)
    except OSError as exc:
        raise EnrollmentError(f"VMID {record.vmid}: {exc}") from exc


def _from_json(payload: object) -> Enrollment:
    if not isinstance(payload, dict):
        raise EnrollmentError("enrollment is not a JSON object")
    found = set(payload)
    expected = set(_FIELDS)
    missing = expected - found
    extra = found - expected
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append("missing " + ", ".join(sorted(missing)))
        if extra:
            parts.append("unknown " + ", ".join(sorted(extra)))
        raise EnrollmentError("enrollment is " + " and ".join(parts))
    return Enrollment(
        vmid=payload["vmid"],
        distro=payload["distro"],
        release=payload["release"],
        digest=payload["digest"],
        checksum_alg=payload["checksum_alg"],
        password_auth=payload["password_auth"],
        permit_root=payload["permit_root"],
        schedule=payload["schedule"],
    )


def _existing_private_dir(directory: str | Path) -> Path | None:
    """Real directory to read, or None when the path is absent.

    A symlink is refused and not followed. The directory is not created.
    """
    given = Path(directory)
    if given.is_symlink():
        raise EnrollmentError(f"{given} is a symlink")
    if not os.path.lexists(given):
        return None
    try:
        verified = verify_private_dir(given)
    except (UnsafeDirError, OSError) as exc:
        raise EnrollmentError(f"unsafe enrollment dir {given}: {exc}") from exc
    if given.is_symlink():
        raise EnrollmentError(f"{given} is a symlink")
    return verified


def _read_text(path: Path, vmid: int) -> str:
    """Read a regular file. O_NOFOLLOW refuses a symlink without opening it."""
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise EnrollmentError(f"VMID {vmid}: {path} is a symlink") from exc
        raise EnrollmentError(f"VMID {vmid}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EnrollmentError(f"VMID {vmid}: {path} is not a file")
        blobs: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            blobs.append(block)
        try:
            return b"".join(blobs).decode("utf-8")
        except UnicodeError as exc:
            raise EnrollmentError(f"VMID {vmid}: {exc}") from exc
    except OSError as exc:
        raise EnrollmentError(f"VMID {vmid}: {exc}") from exc
    finally:
        os.close(fd)


def _read_record(path: Path, vmid: int) -> Enrollment:
    text = _read_text(path, vmid)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnrollmentError(f"VMID {vmid}: invalid JSON") from exc
    try:
        record = _from_json(payload)
    except EnrollmentError as exc:
        raise _named(vmid, exc) from exc
    if record.vmid != vmid:
        raise EnrollmentError(f"VMID {vmid}: file records VMID {record.vmid}")
    return record


def load_enrollment(vmid: int, *, directory: str | Path = ENROLL_DIR) -> Enrollment | None:
    """Return the record for vmid, or None when the file or directory is absent."""
    checked = _check_vmid(vmid)
    root = _existing_private_dir(directory)
    if root is None:
        return None
    path = root / f"{checked}.json"
    if not os.path.lexists(path):
        return None
    return _read_record(path, checked)


def _record_paths(directory: Path) -> list[tuple[int, Path]]:
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise EnrollmentError(f"cannot read {directory}: {exc}") from exc
    chosen: list[tuple[int, Path]] = []
    for entry in entries:
        match = _FILE_NAME.fullmatch(entry.name)
        if match is None:
            continue
        stem = match.group(1)
        number = int(stem)
        if stem != str(number):
            raise EnrollmentError(
                f"VMID {number}: file name {entry.name} is not canonical"
            )
        chosen.append((number, entry))
    return chosen


def enrollments_for(schedule: str, *, directory: str | Path = ENROLL_DIR) -> list[Enrollment]:
    """Records on schedule, sorted by VMID."""
    _check_schedule(schedule)
    root = _existing_private_dir(directory)
    if root is None:
        return []
    records: list[Enrollment] = []
    for vmid, path in _record_paths(root):
        record = _read_record(path, vmid)
        if record.schedule == schedule:
            records.append(record)
    records.sort(key=lambda item: item.vmid)
    return records


def replace_digest(
    vmid: int,
    digest: str,
    *,
    directory: str | Path = ENROLL_DIR,
    checksum_alg: str | None = None,
) -> Enrollment:
    """Store a new digest on the enrolled VMID and return the record.

    checksum_alg defaults to the algorithm already stored. The digest is
    checked against the algorithm that will be written, and a bad pair is
    not saved.
    """
    current = load_enrollment(vmid, directory=directory)
    if current is None:
        raise EnrollmentError(f"VMID {vmid} is not enrolled")
    alg = current.checksum_alg if checksum_alg is None else checksum_alg
    _check_alg(alg)
    _check_digest(digest, alg)
    updated = replace(current, digest=digest, checksum_alg=alg)
    save_enrollment(updated, directory=directory)
    return updated
