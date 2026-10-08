"""Job description for one pve-template-prep run. No I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_CACHE = "/var/tmp/pve-template-prep/cache"

_MIN_VMID = 100
_MAX_VMID = 999_999_999
_RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")


@dataclass(frozen=True)
class Job:
    distro: str
    releases: tuple[str, ...]
    mode: str  # image | template | existing
    disk_format: str  # raw | qcow2  (existing keeps "raw")
    dest_dir: str  # image mode directory, else ""
    storage: str  # template/existing storage id, else ""
    cache_dir: str
    collision: str  # backup | overwrite | skip
    vmids: tuple[int, ...]  # aligned with releases; empty for image
    prep: bool
    bridge: str
    memory_mb: int
    cores: int
    dry_run: bool
    destroy_vmids: frozenset[int]


def published_name(distro: str, release: str, disk_format: str) -> str:
    """raw -> <distro>-<release>-pve.img ; qcow2 -> <distro>-<release>-pve.qcow2."""
    if disk_format == "qcow2":
        ext = "qcow2"
    elif disk_format == "raw":
        ext = "img"
    else:
        raise ValueError(f"unknown disk format {disk_format}")
    return f"{distro}-{release}-pve.{ext}"


def vm_name(distro: str, release: str) -> str:
    """Guest name: <distro>-<release>-cloud."""
    return f"{distro}-{release}-cloud"


def _parse_id(token: str) -> int:
    if not token.isdigit():
        raise ValueError(f"not an integer: {token}")
    value = int(token)
    if value < _MIN_VMID:
        raise ValueError(f"VMID {value} is below {_MIN_VMID}")
    if value > _MAX_VMID:
        raise ValueError(f"VMID {value} is above {_MAX_VMID}")
    return value


def _reject_dupes(ids: list[int]) -> tuple[int, ...]:
    seen: set[int] = set()
    for value in ids:
        if value in seen:
            raise ValueError(f"duplicate VMID {value}")
        seen.add(value)
    return tuple(ids)


def parse_vmids(text: str, count: int) -> tuple[int, ...]:
    """Accept:

    - one integer: start, then start+1 ... until count IDs (auto-increment)
    - comma and/or whitespace separated list whose length == count
    - inclusive range A-B (single span) whose length == count

    Reject count<1, IDs < 100 (PVE reserves low IDs; use 100 as minimum),
    IDs > 999999999, duplicates, and length mismatch. Raise ValueError with
    a short reason.
    """
    if count < 1:
        raise ValueError("need at least one VMID")
    raw = text.strip()
    if not raw:
        raise ValueError("VMID list is empty")

    match = _RANGE_RE.fullmatch(raw)
    if match:
        start = _parse_id(match.group(1))
        end = _parse_id(match.group(2))
        if end < start:
            raise ValueError("range is reversed")
        span = end - start + 1
        if span != count:
            raise ValueError(f"range length {span} != {count}")
        return tuple(range(start, end + 1))

    parts = [part for part in re.split(r"[,\s]+", raw) if part]
    if len(parts) == 1:
        start = _parse_id(parts[0])
        ids = [start + offset for offset in range(count)]
        for value in ids:
            if value > _MAX_VMID:
                raise ValueError(f"VMID {value} is above {_MAX_VMID}")
        return tuple(ids)
    if len(parts) != count:
        raise ValueError(f"got {len(parts)} VMIDs, need {count}")
    return _reject_dupes([_parse_id(part) for part in parts])
