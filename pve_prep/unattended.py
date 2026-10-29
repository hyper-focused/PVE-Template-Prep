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
from datetime import datetime, timezone
from pathlib import Path

from .customize import CustomizeError
from .download import DownloadError
from .enroll import ENROLL_DIR, Enrollment, enrollments_for, replace_digest
from .job import DEFAULT_BACKUPS, DEFAULT_CACHE, Job
from .runlog import LOG_DIR, RunLogError, write_run_log
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
        quiet=not dry_run,
    )


def _guest(record: Enrollment) -> str:
    return f"{record.distro} {record.release}"


def current_line(record: Enrollment) -> str:
    """A published checksum that still matches. The disk is not touched."""
    return (
        f"VMID {record.vmid} is current. {_guest(record)} matches the published "
        "image, so the disk was left unchanged."
    )


def dry_run_line(record: Enrollment) -> str:
    return (
        f"Dry run: {_guest(record)} VM template VMID {record.vmid} would be updated. "
        "Nothing was changed."
    )


def success_line(
    record: Enrollment,
    *,
    backup_dir: str,
    cleanup: bool = False,
    log_path: str = "",
    log_error: str = "",
) -> str:
    """The new disk is the boot disk. cleanup means the old volume is still unused."""
    line = f"Update of {_guest(record)} VM template disk image SUCCESSFUL."
    if cleanup:
        line += " The new disk is in place. The previous disk could not be deleted and was left unused."
    line += f" A backup copy of your old disk image is available at {backup_dir}/."
    if log_path:
        line += f" See {log_path} for details."
    elif log_error:
        line += f" The detail log could not be written: {log_error}"
    return line


def failure_line(
    record: Enrollment,
    *,
    situation: str,
    log_path: str = "",
    log_error: str = "",
) -> str:
    """situation is the human sentence for this failure. The log holds the raw error."""
    head = f"Update of {_guest(record)} VM template disk image FAILED."
    if log_path:
        return f"{head} {situation} See {log_path} for details."
    return f"{head} {situation} The detail log could not be written: {log_error}"


def _failure_situation(record: Enrollment, exc: BaseException) -> str:
    """Say what this failure left in place. The detail log keeps the raw error."""
    text = str(exc).strip()
    vmid = record.vmid
    if "the new disk is attached" in text:
        return (
            f"The new disk is attached, but VMID {vmid} is not a template. "
            f"Run qm template {vmid}, then run the update again."
        )
    if "its disk update did not finish" in text:
        return (
            f"VMID {vmid} is not a template. Its last disk update did not finish. "
            f"Run qm template {vmid}, then run the update again. "
            "This run left the disk as it found it."
        )
    if f"VM {vmid} is running" in text:
        return f"VMID {vmid} is running, so the disk was left unchanged."
    if "cannot be replaced" in text:
        return f"VMID {vmid} is not stopped, so the disk was left unchanged."
    if f"VM {vmid} does not exist" in text:
        return f"VMID {vmid} was not found, so nothing was changed."
    if f"VMID {vmid} is a container" in text:
        return f"VMID {vmid} is a container, so it was left unchanged."
    if "is a linked clone" in text:
        return f"VMID {vmid} is a linked clone, so the disk was left unchanged."
    if "linked-clone check failed" in text:
        return (
            f"VMID {vmid} could not be checked for linked clones, "
            "so the disk was left unchanged."
        )
    if "has linked clones" in text:
        return f"VMID {vmid} has linked clones, so the disk was left unchanged."
    if "is not a template" in text:
        return f"VMID {vmid} is not a template, so the disk was left unchanged."
    if "not raw or qcow2" in text:
        return "The boot disk is not raw or qcow2, so it was left unchanged."
    if "file storage only" in text or "does not accept images" in text:
        return (
            "The boot disk is on block storage, which this update does not replace. "
            "The disk was left unchanged."
        )
    if "has no OS disk" in text:
        return f"VMID {vmid} has no boot disk this update can replace. Nothing was changed."
    if "config failed" in text:
        return "The guest configuration could not be read, so the disk was left unchanged."
    if isinstance(exc, CustomizeError) or "virt-customize failed" in text:
        return "Guest preparation failed. No changes to your existing disk image were made."
    if "the template flag was restored" in text:
        return "The new disk was not attached. No changes to your existing disk image were made."
    folded = text.casefold()
    if isinstance(exc, DownloadError) or "checksum" in folded or "fetch failed" in folded or "http " in folded:
        return (
            "The published image could not be checked or downloaded. "
            "No changes to your existing disk image were made."
        )
    if isinstance(exc, OSError):
        tool = text.split(":", 1)[0].strip() or "a required program"
        return (
            f"A required program could not be run ({tool}). "
            "No changes to your existing disk image were made."
        )
    cause = text.splitlines()[0].strip() if text else "The update stopped"
    if len(cause) > 240:
        cause = cause[:237] + "..."
    if cause[-1:] not in ".!?":
        cause += "."
    return f"{cause} No changes to your existing disk image were made."


def _log_text(record: Enrollment, exc: BaseException, *, extra: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        stamp,
        f"VMID {record.vmid} {_guest(record)}",
        str(exc),
    ]
    captured = getattr(exc, "log", "") or ""
    if captured.strip():
        parts.append(captured.rstrip("\n"))
    if extra.strip():
        parts.append(extra.rstrip("\n"))
    return "\n".join(parts) + "\n"


def _write_detail(record: Enrollment, text: str, *, directory: str | Path) -> tuple[str, str]:
    """Return (path, error). One of them is empty."""
    try:
        path = write_run_log(record.vmid, text, directory=directory)
    except RunLogError as exc:
        return "", str(exc)
    return str(path), ""


def _swap_one(
    record: Enrollment,
    *,
    directory: str | Path,
    log_directory: str | Path,
    digest_for: Callable[[str, str], tuple[str, str]],
    run: Callable[[list[str]], object],
    perform: Callable[[Job, str, int], object],
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
        print(current_line(record))
        return

    result = perform(_insert_job(record, storage, fmt, dry_run=dry_run), record.release, vmid)
    if dry_run:
        print(dry_run_line(record))
        return
    text = result if isinstance(result, str) else ""
    cleanup = "Old disk cleanup failed" in text
    log_path = ""
    log_error = ""
    if cleanup:
        log_path, log_error = _write_detail(
            record, _log_text(record, RuntimeError(text)), directory=log_directory
        )
    print(
        success_line(
            record,
            backup_dir=DEFAULT_BACKUPS,
            cleanup=cleanup,
            log_path=log_path,
            log_error=log_error,
        )
    )
    replace_digest(vmid, digest, directory=directory, checksum_alg=alg)


def run_unattended(
    schedule: str,
    *,
    directory: str | Path = ENROLL_DIR,
    log_directory: str | Path = LOG_DIR,
    digest_for: Callable[[str, str], tuple[str, str]],
    run: Callable[[list[str]], object],
    perform: Callable[[Job, str, int], object],
    storage_kind: Callable[[str], str],
    nodes_root: Path,
    dry_run: bool = False,
) -> int:
    """Swap every enrolled guest on schedule whose published digest changed.

    One guest's failure is printed and the rest still run. Returns 1 when
    any guest failed, otherwise 0. dry_run still calls perform, with the
    job marked dry_run, and leaves the stored digest unchanged. A failure
    writes /var/log/pve-template-prep/<vmid>.log and keeps that path in the
    sentence. The virt-customize log stays in the file.
    """
    failed = False
    for record in enrollments_for(schedule, directory=directory):
        try:
            _swap_one(
                record,
                directory=directory,
                log_directory=log_directory,
                digest_for=digest_for,
                run=run,
                perform=perform,
                storage_kind=storage_kind,
                nodes_root=nodes_root,
                dry_run=dry_run,
            )
        except Exception as exc:
            path, error = _write_detail(
                record,
                _log_text(record, exc),
                directory=log_directory,
            )
            print(
                failure_line(
                    record,
                    situation=_failure_situation(record, exc),
                    log_path=path,
                    log_error=error,
                )
            )
            failed = True
    return 1 if failed else 0
