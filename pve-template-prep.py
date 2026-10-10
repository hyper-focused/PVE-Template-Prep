#!/usr/bin/env python3
"""Prep one distro's cloud images or Proxmox templates."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from pve_prep import __version__
from pve_prep.catalog import normalize_release, releases_for
from pve_prep.customize import apply as customize_apply
from pve_prep.customize import argv as customize_argv
from pve_prep.download import DownloadError, clear_cache, convert, fetch_verified, publish
from pve_prep.job import Job, published_name, vm_name
from pve_prep.prompts import PromptAbort, interview
from pve_prep.ui import arm, tone
from pve_prep.vm import (
    VmDestroyedError,
    config_has_os_disk,
    create_template,
    existing_hw_commands,
    existing_prep_commands,
    insert_disk,
    is_template,
    parse_qm_status,
    vmid_config_missing,
    storage_lacks_images,
    parse_storage_ids,
)

_APT_HINT = "apt-get install libguestfs-tools qemu-utils"


def default_run(argv: list[str]) -> SimpleNamespace:
    """Run a host command. Caller decides what a nonzero status means."""
    proc = subprocess.run(argv, text=True, capture_output=True)
    return SimpleNamespace(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _stderr_tail(result) -> str:
    text = (getattr(result, "stderr", "") or "").strip()
    if not text:
        return f"exit {getattr(result, 'returncode', 1)}"
    return text.splitlines()[-1][:400]


def _checked(argv: list[str], run) -> SimpleNamespace:
    result = run([str(part) for part in argv])
    if result.returncode != 0:
        raise RuntimeError(_stderr_tail(result))
    return result


def vmids_in_use(vmids: tuple[int, ...]) -> set[int] | None:
    """VMIDs that already have a config.

    None means qm could not be run, so the caller must not guess.
    A nonzero status is "free" only when the output says the VM does not exist.
    """
    occupied: set[int] = set()
    for vmid in vmids:
        try:
            proc = subprocess.run(
                ["qm", "status", str(vmid)],
                text=True,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        output = f"{proc.stdout}\n{proc.stderr}"
        if not vmid_config_missing(proc.returncode, output):
            occupied.add(vmid)
    return occupied


def vm_has_disks(vmid: int) -> bool | None:
    """True when this VM has an OS disk. None when qm cannot answer."""
    try:
        proc = subprocess.run(
            ["qm", "config", str(vmid)],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        if vmid_config_missing(proc.returncode, output):
            return False
        return None
    return config_has_os_disk(proc.stdout or "")


def list_storages() -> list[str]:
    """Storage IDs from `pvesm status`. Empty when the host has no pvesm."""
    try:
        proc = subprocess.run(
            ["pvesm", "status"],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        return [str(item) for item in parse_storage_ids(proc.stdout)]
    except Exception:
        return []


def preflight(job: Job) -> None:
    """Require root and the host tools this mode actually calls.

    Not used for --dry-run. Existing disks are already on storage, so
    qemu-img is only required for image and template builds.
    """
    if os.geteuid() != 0:
        raise SystemExit("run as root on the PVE host (sudo is fine)")
    needed: list[str] = []
    if job.mode in {"image", "template"}:
        needed.append("qemu-img")
    if job.prep:
        needed.append("virt-customize")
    if job.mode in {"template", "existing"} or (job.mode == "image" and job.vmids):
        needed.extend(["qm", "pvesm"])
    for tool in needed:
        if shutil.which(tool) is None:
            raise SystemExit(f"missing {tool}; {_APT_HINT}")


def _storage_rejects_images(storage: str) -> bool:
    """True when storage.cfg is present and this id cannot hold VM disks."""
    path = Path("/etc/pve/storage.cfg")
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return storage_lacks_images(text, storage)


def _spec_for(distro: str, release: str):
    for spec in releases_for(distro):
        if spec.release == release:
            return spec
    raise RuntimeError(f"unknown release {release}")


def _existing_plan(config_text: str, vmid: int, storage: str) -> tuple[str, list]:
    # (qm argv lists, OS volid). vmid and storage are keyword-only on the vm helper.
    commands, volid = existing_prep_commands(config_text, vmid=vmid, storage=storage)
    return str(volid), list(commands)


def _product(spec, distro: str, release: str) -> str:
    label = str(getattr(spec, "label", "") or "").strip()
    return label or f"{distro} {release}"


def _opening(label: str, job: Job, vmid: int | None) -> str:
    if job.mode == "template":
        kind = "VM template" if job.make_template else "VM"
        return f"Creating {label} {kind}"
    if job.mode == "image" and vmid is None:
        return f"Creating {label} disk image"
    if job.mode == "image":
        return f"Creating {label} VM disk image"
    return f"Preparing {label} VM"


def _file_done(label: str, dest: Path, result) -> str:
    place = str(dest)
    if result.status == "skipped":
        return f"{label} disk image left unchanged at {place}"
    sentence = f"{label} disk image created at {place}"
    if result.backup:
        return f"{sentence}. Old disk image saved at {result.backup}."
    if result.replaced:
        return f"{sentence}. Old disk image deleted."
    return sentence


def _import_done(label: str, vmid: int, policy: str, result) -> str:
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


def _template_done(label: str, job: Job, vmid: int) -> str:
    kind = "VM template" if job.make_template else "VM"
    sentence = f"{label} {kind} created"
    if vmid in job.destroy_vmids:
        sentence += ". Previous VM deleted."
        if vmid in job.backup_vmids:
            sentence += f" Old disk saved in {job.cache_dir}."
    return sentence


def _announce(text: str, role: str) -> None:
    if sys.stdout.isatty():
        print(tone(text, role))
    else:
        print(text)


def _run_fetched(job: Job, release: str, vmid: int | None) -> str:
    spec = _spec_for(job.distro, release)
    label = _product(spec, job.distro, release)
    inserts = job.mode == "image" and vmid is not None
    if job.mode != "image" or inserts:
        if vmid is None:
            raise RuntimeError("VMID is required")
        if _storage_rejects_images(job.storage):
            raise RuntimeError(f"storage {job.storage} does not accept images")
    src = fetch_verified(spec, Path(job.cache_dir), dry_run=job.dry_run)
    work = Path(job.cache_dir) / (published_name(job.distro, release, job.disk_format) + ".work")
    convert(src, work, job.disk_format, dry_run=job.dry_run)
    if job.prep:
        # apply() wants a str. shlex.join rejects a Path on dry-run.
        customize_apply(str(work), spec.family, dry_run=job.dry_run)
    if job.mode == "image" and not inserts:
        dest = Path(job.dest_dir) / published_name(job.distro, release, job.disk_format)
        return _file_done(label, dest, publish(work, dest, job.collision, dry_run=job.dry_run))
    if inserts:
        policy = "backup" if vmid in job.backup_vmids else "overwrite"
        inserted = insert_disk(
            vmid=vmid,
            storage=job.storage,
            image_path=str(work),
            disk_policy=policy,
            dry_run=job.dry_run,
            backup_dir=job.cache_dir,
            run=default_run,
        )
        return _import_done(label, vmid, policy, inserted)
    create_template(
        vmid=vmid,
        name=vm_name(job.distro, release),
        memory_mb=job.memory_mb,
        cores=job.cores,
        bridge=job.bridge,
        storage=job.storage,
        image_path=str(work),
        dry_run=job.dry_run,
        destroy_ok=vmid in job.destroy_vmids,
        backup_disks=vmid in job.backup_vmids,
        backup_dir=job.cache_dir,
        make_template=job.make_template,
        run=default_run,
    )
    return _template_done(label, job, vmid)


def _first_path_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _run_existing(job: Job, release: str, vmid: int | None) -> None:
    if vmid is None:
        raise RuntimeError("VMID is required")
    prep = "yes" if job.prep else "no"
    # Dry-run stays off the host. The live disk path is unknown, so the token is literal.
    if job.dry_run:
        print(f"existing VMID {vmid} storage {job.storage} guest-prep={prep}")
        if job.prep:
            spec = _spec_for(job.distro, release)
            print()
            print(shlex.join(customize_argv("<os-disk>", spec.family)))
            print()
        for command in existing_hw_commands(
            vmid=vmid,
            bus_key="scsi0",
            disk_value="<os-disk>,discard=on,ssd=1",
        ):
            print(" ".join(command))
        return
    spec = _spec_for(job.distro, release)
    status_res = _checked(["qm", "status", vmid], default_run)
    status = str(parse_qm_status(status_res.stdout)).strip()
    if status != "stopped":
        raise RuntimeError(f"SKIP {vmid} status={status} (stop it first)")
    config_res = _checked(["qm", "config", vmid], default_run)
    if is_template(config_res.stdout):
        raise RuntimeError(
            f"VM {vmid} is a template; existing mode does not rewrite templates"
        )
    volid, commands = _existing_plan(config_res.stdout, vmid, job.storage)
    path_res = _checked(["pvesm", "path", volid], default_run)
    disk = _first_path_line(path_res.stdout)
    if not disk or not Path(disk).is_file():
        raise RuntimeError("disk is not a regular file")
    for command in commands:
        if not command:
            continue
        _checked(list(command), default_run)
    if job.prep:
        customize_apply(disk, spec.family, dry_run=job.dry_run)


def run_one(job: Job, release: str, vmid: int | None) -> None:
    spec = _spec_for(job.distro, release)
    label = _product(spec, job.distro, release)
    print()
    _announce(_opening(label, job, vmid), "cyan")
    print()
    if job.mode == "existing":
        _run_existing(job, release, vmid)
        done = f"{label} VM prepared"
    else:
        done = _run_fetched(job, release, vmid)
    print()
    _announce(done, "body")


def _pairs(job: Job) -> list[tuple[str, int | None]]:
    if job.mode == "image" and not job.vmids:
        return [(release, None) for release in job.releases]
    if len(job.vmids) != len(job.releases):
        raise RuntimeError("VMID count does not match releases")
    return list(zip(job.releases, job.vmids, strict=True))


def _clear_requested_cache(job: Job, failed: bool) -> bool:
    """Delete working files in the prep cache after a successful live run.

    Disk backups are never deleted. Returns True when the operator asked
    for a delete and it did not happen.
    """
    if not job.clean_cache:
        return False
    cache = job.cache_dir
    if job.dry_run:
        print(f"dry-run: would delete the working files in {cache}")
        print("disk backups would stay")
        return False
    if failed:
        print(f"cache kept: {cache}")
        print("a release failed, so the downloaded image stays. Disk backups are kept either way")
        return False
    try:
        clear_cache(cache)
    except DownloadError as exc:
        print(f"FAIL cache: {exc}")
        return True
    print(f"deleted the working files in {cache}")
    print("disk backups stay")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pve-template-prep",
        description="Download, prep, and publish PVE cloud images or templates.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print actions and do not change the host",
    )
    args = parser.parse_args(argv)
    print(f"{parser.prog} {__version__}")
    try:
        job = interview(
            arm(_read_line),
            _write,
            dry_run=args.dry_run,
            list_storages=list_storages,
            releases_for=releases_for,
            normalize_release=normalize_release,
            vmids_in_use=vmids_in_use,
            vm_has_disks=vm_has_disks,
        )
    except PromptAbort:
        print("aborted")
        return 1
    if not job.dry_run:
        preflight(job)
    failed = False
    try:
        pairs = _pairs(job)
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        failed = True
        pairs = []
    for release, vmid in pairs:
        try:
            run_one(job, release, vmid)
        except VmDestroyedError as exc:
            print(f"FAIL {release}: {exc}")
            print("stopped: a template was destroyed and the replacement did not finish")
            failed = True
            break
        except Exception as exc:
            print(f"FAIL {release}: {exc}")
            failed = True
    print("done")
    if _clear_requested_cache(job, failed):
        failed = True
    return 1 if failed else 0


def _read_line() -> str:
    return input()


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
