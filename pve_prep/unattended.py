# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Unattended disk swap for enrolled templates.

One schedule walks the enrollment files. A guest whose published digest
matches the record is left alone. Anything else is handed to perform as
an insert of that one VMID. This module does not download an image and
does not call qm importdisk.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .enroll import ENROLL_DIR, Enrollment, enrollments_for, replace_digest
from .job import DEFAULT_BACKUPS, DEFAULT_CACHE, Job
from .vm import (
    is_file_storage,
    os_disk_format,
    os_disk_storage,
    parse_qm_status,
    refuse_replace_target,
)


def _invoke(run: Callable[[list[str]], object], argv: list[str]) -> object:
    """Run one host command. A missing binary names the tool and is not success."""
    tool = argv[0]
    try:
        return run(argv)
    except OSError as exc:
        raise OSError(f"{tool}: {exc}") from exc


def _insert_job(record: Enrollment, storage: str, fmt: str, *, dry_run: bool) -> Job:
    """One image-mode insert that keeps this guest's storage, format, and prep flags."""
    vmid = record.vmid
    return Job(
        distro=record.distro,
        releases=(record.release,),
        mode="image",
        disk_format=fmt,
        dest_dir="",
        storage=storage,
        cache_dir=DEFAULT_CACHE,
        collision="overwrite",
        vmids=(vmid,),
        prep=True,
        bridge="vmbr0",
        memory_mb=2048,
        cores=2,
        dry_run=dry_run,
        destroy_vmids=frozenset(),
        backup_vmids=frozenset({vmid}),
        backup_dir=DEFAULT_BACKUPS,
        make_template=False,
        clean_cache=False,
        template_vmids=frozenset(),
        password_auth=record.password_auth,
        permit_root=record.permit_root,
        kept_storage=((vmid, storage),),
        verbose=False,
        kept_format=((vmid, fmt),),
    )


def _swap_one(
    record: Enrollment,
    *,
    directory: str | Path,
    digest_for: Callable[[str, str], tuple[str, str]],
    run: Callable[[list[str]], object],
    perform: Callable[[Job, str, int], None],
    storage_kind: Callable[[str], str],
    nodes_root: Path,
    dry_run: bool,
) -> None:
    vmid = record.vmid
    status = _invoke(run, ["qm", "status", str(vmid)])
    if getattr(status, "returncode", 1) != 0:
        raise RuntimeError(f"VM {vmid} does not exist")
    state = parse_qm_status(getattr(status, "stdout", "") or "")
    if state == "running":
        raise RuntimeError(f"VM {vmid} is running")
    if state != "stopped":
        raise RuntimeError(f"VM {vmid} status {state or 'unknown'} cannot be replaced")

    pct = _invoke(run, ["pct", "status", str(vmid)])
    if getattr(pct, "returncode", 1) == 0:
        raise RuntimeError(f"VMID {vmid} is a container")

    cfg = _invoke(run, ["qm", "config", str(vmid)])
    if getattr(cfg, "returncode", 1) != 0:
        raise RuntimeError(f"VM {vmid} config failed")
    config = getattr(cfg, "stdout", "") or ""
    refuse_replace_target(run, vmid, config, nodes_root)

    storage = os_disk_storage(config)
    fmt = os_disk_format(config)
    if not storage:
        raise RuntimeError(f"VM {vmid} has no OS disk storage")
    if fmt not in {"raw", "qcow2"}:
        raise RuntimeError(f"VM {vmid} OS disk format {fmt or 'unknown'} is not raw or qcow2")
    kind = storage_kind(storage)
    kind_text = "" if kind is None else str(kind)
    if not is_file_storage(kind_text):
        shown = kind_text.strip() or "an unreadable type"
        raise RuntimeError(
            f"storage {storage} is {shown}. Disk images go on file storage only "
            "(directory, NFS, CIFS, btrfs, CephFS, GlusterFS)"
        )

    digest, alg = digest_for(record.distro, record.release)
    if digest == record.digest and alg == record.checksum_alg:
        print(f"VMID {vmid} is current")
        return

    perform(_insert_job(record, storage, fmt, dry_run=dry_run), record.release, vmid)
    # A dry run prints the swap and must not record it as done.
    if not dry_run:
        replace_digest(vmid, digest, directory=directory, checksum_alg=alg)


def run_unattended(
    schedule: str,
    *,
    directory: str | Path = ENROLL_DIR,
    digest_for: Callable[[str, str], tuple[str, str]],
    run: Callable[[list[str]], object],
    perform: Callable[[Job, str, int], None],
    storage_kind: Callable[[str], str],
    nodes_root: Path,
    dry_run: bool = False,
) -> int:
    """Swap every enrolled guest on schedule whose published digest changed.

    One guest's failure is printed and the rest still run. Returns 1 when
    any guest failed, otherwise 0. dry_run still calls perform, with the
    job marked dry_run, and leaves the stored digest unchanged.
    """
    failed = False
    for record in enrollments_for(schedule, directory=directory):
        try:
            _swap_one(
                record,
                directory=directory,
                digest_for=digest_for,
                run=run,
                perform=perform,
                storage_kind=storage_kind,
                nodes_root=nodes_root,
                dry_run=dry_run,
            )
        except Exception as exc:
            print(f"FAIL {record.release}: {exc}")
            failed = True
    return 1 if failed else 0
