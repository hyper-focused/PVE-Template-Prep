"""Job description for one pve-template-prep run. No I/O."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CACHE = "/var/tmp/pve-template-prep/cache"

_MIN_VMID = 100
_MAX_VMID = 999_999_999


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
    # VMIDs whose OS disk is copied before a replace, or kept when a disk is inserted.
    backup_vmids: frozenset[int] = frozenset()
    # Template mode only. False leaves a normal VM and skips qm template.
    make_template: bool = False
    # Delete the prep-cache children after every release succeeds. Never on failure.
    clean_cache: bool = False
    # Image-mode VMIDs created as new templates instead of receiving a disk.
    template_vmids: frozenset[int] = frozenset()


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


def action_for(job: Job, vmid: int | None) -> str:
    """Which build function this release calls.

    file, insert, template, or existing. Image mode can create a template
    for a VMID that was not in use. The other releases keep their own action.
    """
    if job.mode == "existing":
        return "existing"
    if job.mode == "template":
        return "template"
    if job.mode != "image":
        raise ValueError(f"unknown mode {job.mode}")
    if vmid is not None and vmid in job.template_vmids:
        return "template"
    if vmid is None:
        return "file"
    return "insert"


def converts_to_template(job: Job, vmid: int | None) -> bool:
    """True when this release ends as a PVE template."""
    if action_for(job, vmid) != "template":
        return False
    if vmid is not None and vmid in job.template_vmids:
        return True
    return job.make_template


def parse_vmid(text: str) -> int:
    """One VMID. Lists, ranges, and counting up are not accepted."""
    raw = text.strip()
    if not raw:
        raise ValueError("VMID is empty")
    if not raw.isdigit():
        raise ValueError("type one VMID")
    return _parse_id(raw)
