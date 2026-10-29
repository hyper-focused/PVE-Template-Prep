# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""One function per build action.

The runner picks the function with job.action_for. Fetch and guest prep
are shared. Distro and release stay arguments, not copies of this module.
"""

from __future__ import annotations

from pathlib import Path

from pve_prep.job import (
    action_for,
    converts_to_template,
    format_for,
    published_name,
    storage_for,
    vm_name,
)
from pve_prep.vm import is_file_storage, qemu_img_convert_argv, stdout_is_tty


def opening(action: str, label: str, job, vmid: int | None) -> str:
    if action == "template":
        kind = "VM template" if converts_to_template(job, vmid) else "VM"
        return f"Creating {label} {kind}"
    if action == "file":
        return f"Creating {label} disk image"
    if action == "insert":
        return f"Creating {label} VM disk image"
    raise ValueError(f"unknown action {action}")


def _finish_file(label: str, dest: Path, result) -> str:
    place = str(dest)
    if result.status == "skipped":
        return f"{label} disk image left unchanged at {place}"
    sentence = f"{label} disk image created at {place}"
    if result.backup:
        return f"{sentence}. Old disk image saved at {result.backup}."
    if result.replaced:
        return f"{sentence}. Old disk image deleted."
    return sentence


def _finish_insert(label: str, vmid: int, policy: str, result) -> str:
    sentence = f"{label} VM disk image created and imported"
    if getattr(result, "kept_template", False):
        saved = str(getattr(result, "backup_dir", "") or "")
        cleanup = str(getattr(result, "cleanup_error", "") or "")
        if cleanup:
            sentence += f". Template {vmid} updated"
            if saved:
                sentence += f". Old disk saved in {saved}"
            return f"{sentence}. Old disk cleanup failed: {cleanup}."
        sentence += f". Template {vmid} kept"
        if saved:
            return f"{sentence}. Old disk saved in {saved}."
        return f"{sentence}. Previous disk deleted."
    if policy == "backup":
        saved = str(getattr(result, "backup_dir", "") or "")
        if saved:
            return f"{sentence}. Old disk saved in {saved}."
        return f"{sentence}. Old disk saved in the backup directory."
    return f"{sentence}. Previous disk deleted."


def _finish_template(label: str, job, vmid: int) -> str:
    kind = "VM template" if converts_to_template(job, vmid) else "VM"
    sentence = f"{label} {kind} created"
    if vmid in job.destroy_vmids:
        sentence += ". Previous VM deleted."
        if vmid in job.backup_vmids:
            sentence += f" Old disk saved in {job.backup_dir}."
    return sentence


def _stage_replace(job, vmid: int, tools, *, backup: bool) -> None:
    """Refuse a bad target. Copy disks before the new image is built."""
    if job.dry_run:
        if backup:
            shown = " ".join(
                qemu_img_convert_argv(
                    "<os-disk>",
                    f"{job.backup_dir}/vm-{vmid}-os.bak.qcow2",
                    "qcow2",
                )
            )
            print(f"# {shown}")
        return
    copied = tools.prepare_replace(
        vmid=vmid,
        backup=backup,
        backup_dir=job.backup_dir,
        run=tools.run,
    )
    if backup and not job.quiet:
        if copied:
            print("VM disk copy completed, building new template images now...")
        else:
            print("No OS disk to copy. Building the new template image now...")


def _prepare(job, spec, tools, *, cache_format: str, label: str) -> Path:
    src = tools.fetch_verified(spec, Path(job.cache_dir), dry_run=job.dry_run, quiet=job.quiet)
    work = Path(job.cache_dir) / (published_name(job.distro, spec.release, cache_format) + ".work")
    # qemu-img -p draws the bar. The label is the only name that bar gets.
    if not job.dry_run and stdout_is_tty():
        print(f"Converting {label} image...")
    tools.convert(src, work, cache_format, dry_run=job.dry_run)
    if job.prep:
        # apply() wants a str. shlex.join rejects a Path on dry-run.
        tools.customize_apply(
            str(work),
            spec.family,
            disk_format=cache_format,
            password_auth=job.password_auth,
            permit_root=job.permit_root,
            dry_run=job.dry_run,
            verbose=job.verbose,
            label=label,
            quiet=job.quiet,
        )
    return work


def publish_file(job, spec, label: str, tools) -> str:
    work = _prepare(job, spec, tools, cache_format=job.disk_format, label=label)
    dest = Path(job.dest_dir) / published_name(job.distro, spec.release, job.disk_format)
    return _finish_file(label, dest, tools.publish(work, dest, job.collision, dry_run=job.dry_run))


def _storage_kind(tools, storage: str) -> str:
    kind_of = getattr(tools, "storage_kind", None)
    if kind_of is None:
        return ""
    try:
        return str(kind_of(storage) or "")
    except Exception:
        return ""


def _require_file_storage(job, tools, storage: str) -> None:
    """Refuse ZFS, LVM, RBD, iSCSI, and a type that cannot be read.

    A dry-run may not be able to read pvesm, so it only prints the commands.
    """
    if job.dry_run:
        return
    kind = _storage_kind(tools, storage)
    if not is_file_storage(kind):
        shown = kind or "an unreadable type"
        raise RuntimeError(
            f"storage {storage} is {shown}. Disk images go on file storage only "
            "(directory, NFS, CIFS, btrfs, CephFS, GlusterFS)"
        )


def insert_image(job, spec, vmid: int, label: str, tools) -> str:
    # The cache copy stays qcow2. importdisk allocates the volume format.
    # A kept boot disk is imported onto its own storage, in its own format.
    storage = storage_for(job, vmid)
    backup = vmid in job.backup_vmids
    _stage_replace(job, vmid, tools, backup=backup)
    work = _prepare(job, spec, tools, cache_format="qcow2", label=label)
    policy = "backup" if backup else "overwrite"
    inserted = tools.insert_disk(
        vmid=vmid,
        storage=storage,
        image_path=str(work),
        disk_policy=policy,
        dry_run=job.dry_run,
        backup_dir=job.backup_dir,
        volume_format=format_for(job, vmid),
        copies_done=backup and not job.dry_run,
        run=tools.run,
    )
    return _finish_insert(label, vmid, policy, inserted)


def create_guest(job, spec, vmid: int, label: str, tools) -> str:
    # The cache copy stays qcow2. importdisk writes the asked format on file storage.
    replacing = vmid in job.destroy_vmids
    backup = replacing and vmid in job.backup_vmids
    if replacing:
        _stage_replace(job, vmid, tools, backup=backup)
    work = _prepare(job, spec, tools, cache_format="qcow2", label=label)
    tools.create_template(
        vmid=vmid,
        name=vm_name(job.distro, spec.release),
        memory_mb=job.memory_mb,
        cores=job.cores,
        bridge=job.bridge,
        storage=job.storage,
        image_path=str(work),
        dry_run=job.dry_run,
        destroy_ok=vmid in job.destroy_vmids,
        backup_disks=False,
        backup_dir=job.backup_dir,
        make_template=converts_to_template(job, vmid),
        volume_format=job.disk_format,
        run=tools.run,
    )
    return _finish_template(label, job, vmid)


def run_fetched(job, spec, vmid: int | None, label: str, tools) -> str:
    """Download, prep, then call the action for this release."""
    action = action_for(job, vmid)
    if action != "file":
        if vmid is None:
            raise RuntimeError("VMID is required")
        storage = storage_for(job, vmid) if action == "insert" else job.storage
        if not storage:
            raise RuntimeError("storage is required")
        if tools.storage_rejects(storage):
            raise RuntimeError(f"storage {storage} does not accept images")
        _require_file_storage(job, tools, storage)
    if action == "file":
        return publish_file(job, spec, label, tools)
    if action == "insert":
        return insert_image(job, spec, vmid, label, tools)
    if action == "template":
        return create_guest(job, spec, vmid, label, tools)
    raise RuntimeError(f"unknown action {action}")
