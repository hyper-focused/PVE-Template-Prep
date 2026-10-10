"""One function per build action.

The runner picks the function with job.action_for. Fetch and guest prep
are shared. Distro and release stay arguments, not copies of this module.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from pve_prep.job import action_for, converts_to_template, published_name, vm_name


def opening(action: str, label: str, job, vmid: int | None) -> str:
    if action == "template":
        kind = "VM template" if converts_to_template(job, vmid) else "VM"
        return f"Creating {label} {kind}"
    if action == "file":
        return f"Creating {label} disk image"
    if action == "insert":
        return f"Creating {label} VM disk image"
    return f"Preparing {label} VM"


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
        sentence += f". Template {vmid} kept"
        saved = str(getattr(result, "backup_dir", "") or "")
        if saved:
            return f"{sentence}. Old disk saved in {saved}."
        return f"{sentence}. Previous disk deleted."
    if policy == "backup":
        return f"{sentence}. Previous disk is still attached to VM {vmid}."
    return f"{sentence}. Previous disk deleted."


def _finish_template(label: str, job, vmid: int) -> str:
    kind = "VM template" if converts_to_template(job, vmid) else "VM"
    sentence = f"{label} {kind} created"
    if vmid in job.destroy_vmids:
        sentence += ". Previous VM deleted."
        if vmid in job.backup_vmids:
            sentence += f" Old disk saved in {job.cache_dir}."
    return sentence


def _prepare(job, spec, tools, *, cache_format: str) -> Path:
    src = tools.fetch_verified(spec, Path(job.cache_dir), dry_run=job.dry_run)
    work = Path(job.cache_dir) / (published_name(job.distro, spec.release, cache_format) + ".work")
    tools.convert(src, work, cache_format, dry_run=job.dry_run)
    if job.prep:
        # apply() wants a str. shlex.join rejects a Path on dry-run.
        tools.customize_apply(str(work), spec.family, dry_run=job.dry_run)
    return work


def publish_file(job, spec, label: str, tools) -> str:
    work = _prepare(job, spec, tools, cache_format=job.disk_format)
    dest = Path(job.dest_dir) / published_name(job.distro, spec.release, job.disk_format)
    return _finish_file(label, dest, tools.publish(work, dest, job.collision, dry_run=job.dry_run))


def insert_image(job, spec, vmid: int, label: str, tools) -> str:
    # The cache copy stays qcow2. importdisk allocates the volume format.
    work = _prepare(job, spec, tools, cache_format="qcow2")
    policy = "backup" if vmid in job.backup_vmids else "overwrite"
    inserted = tools.insert_disk(
        vmid=vmid,
        storage=job.storage,
        image_path=str(work),
        disk_policy=policy,
        dry_run=job.dry_run,
        backup_dir=job.cache_dir,
        volume_format=job.disk_format,
        run=tools.run,
    )
    return _finish_insert(label, vmid, policy, inserted)


def create_guest(job, spec, vmid: int, label: str, tools) -> str:
    # The cache copy stays qcow2. A raw ZFS or LVM volume is allocated at import.
    work = _prepare(job, spec, tools, cache_format="qcow2")
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
        backup_disks=vmid in job.backup_vmids,
        backup_dir=job.cache_dir,
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
        if tools.storage_rejects(job.storage):
            raise RuntimeError(f"storage {job.storage} does not accept images")
    if action == "file":
        return publish_file(job, spec, label, tools)
    if action == "insert":
        return insert_image(job, spec, vmid, label, tools)
    if action == "template":
        return create_guest(job, spec, vmid, label, tools)
    raise RuntimeError(f"unknown action {action}")


def _first_path_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def prep_existing(job, vmid: int | None, spec, tools) -> None:
    """Prep a stopped VM in place. Does not download an image."""
    if vmid is None:
        raise RuntimeError("VMID is required")
    prep = "yes" if job.prep else "no"
    # Dry-run stays off the host. The live disk path is unknown, so the token is literal.
    if job.dry_run:
        print(f"existing VMID {vmid} storage {job.storage} guest-prep={prep}")
        if job.prep:
            print()
            print(shlex.join(tools.customize_argv("<os-disk>", spec.family)))
            print()
        for command in tools.existing_hw_commands(
            vmid=vmid,
            bus_key="scsi0",
            disk_value="<os-disk>,discard=on,ssd=1",
        ):
            print(" ".join(command))
        return
    status_res = tools.checked(["qm", "status", vmid], tools.run)
    status = str(tools.parse_qm_status(status_res.stdout)).strip()
    if status != "stopped":
        raise RuntimeError(f"SKIP {vmid} status={status} (stop it first)")
    config_res = tools.checked(["qm", "config", vmid], tools.run)
    if tools.is_template(config_res.stdout):
        raise RuntimeError(
            f"VM {vmid} is a template; existing mode does not rewrite templates"
        )
    commands, volid = tools.existing_prep_commands(
        config_res.stdout, vmid=vmid, storage=job.storage
    )
    path_res = tools.checked(["pvesm", "path", str(volid)], tools.run)
    disk = _first_path_line(path_res.stdout)
    if not disk or not Path(disk).is_file():
        raise RuntimeError("disk is not a regular file")
    for command in commands:
        if not command:
            continue
        tools.checked(list(command), tools.run)
    if job.prep:
        tools.customize_apply(disk, spec.family, dry_run=job.dry_run)
