#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Prep one distro's cloud images or Proxmox templates."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from pve_prep import __version__
from pve_prep import update as tool_update
from pve_prep.build import opening, run_fetched
from pve_prep.catalog import normalize_release, releases_for
from pve_prep.customize import apply as customize_apply
from pve_prep.customize import argv as customize_argv
from pve_prep.download import DownloadError, clear_cache, convert, fetch_verified, publish
from pve_prep.job import Job, action_for
from pve_prep.prompts import PromptAbort, ask_cache_cleanup, interview
from pve_prep.ui import arm, quiet_echo, tone
from pve_prep.vm import (
    VmDestroyedError,
    backups_since,
    VmError,
    config_has_os_disk,
    create_template,
    insert_disk,
    os_disk_format,
    os_disk_storage,
    is_linked_clone,
    is_template,
    unfinished_swap,
    prepare_replace,
    refuse_replace_target,
    vmid_config_missing,
    parse_vmlist,
    storage_lacks_images,
    parse_storage_ids,
    parse_storage_kinds,
    import_directories,
)

_APT_HINT = "apt-get install --no-install-recommends guestfs-tools libguestfs-xfs"


def default_run(argv: list[str]) -> SimpleNamespace:
    """Run a host command. Caller decides what a nonzero status means."""
    proc = subprocess.run(argv, text=True, capture_output=True)
    return SimpleNamespace(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


# The cluster filesystem's list of every VM and container. One read answers
# for any number of VMIDs; qm status costs about a second per VMID.
VMLIST = Path("/etc/pve/.vmlist")


def vmids_in_use(vmids: tuple[int, ...]) -> set[int] | None:
    """VMIDs already taken by a VM or a container, on any node.

    Reads VMLIST. When it cannot be read or parsed, asks qm and pct per VMID.
    None means neither worked, so the caller must not guess.
    """
    try:
        taken = parse_vmlist(VMLIST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _vmids_in_use_by_status(vmids)
    return {vmid for vmid in vmids if vmid in taken}


# qm only sees VMs and pct only sees containers. An ID is free only when
# neither has a config for it.
_GUEST_PROBES = ("qm", "pct")


def _vmids_in_use_by_status(vmids: tuple[int, ...]) -> set[int] | None:
    """The slow path: qm status and pct status per VMID.

    A nonzero status is "free" only when the output says the config does
    not exist. A container ID is taken even though qm has never heard of it.
    """
    occupied: set[int] = set()
    for vmid in vmids:
        for tool in _GUEST_PROBES:
            try:
                proc = subprocess.run(
                    [tool, "status", str(vmid)],
                    text=True,
                    capture_output=True,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            output = f"{proc.stdout}\n{proc.stderr}"
            if not vmid_config_missing(proc.returncode, output):
                occupied.add(vmid)
                break
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


def guest_role(vmid: int) -> str | None:
    """template, vm, clone, unfinished, blocked, or None when unreadable.

    None and any other unreadable result are treated as blocked. A template
    that still has linked clones is blocked. A normal VM is vm. A normal VM
    carrying its own swap marker is unfinished, and is refused like a VM.
    """
    try:
        proc = subprocess.run(
            ["qm", "config", str(vmid)],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout or ""
    if is_linked_clone(text):
        return "clone"
    if unfinished_swap(text, vmid):
        return "unfinished"
    if not is_template(text):
        return "vm"
    try:
        refuse_replace_target(default_run, vmid, text, None)
    except VmError:
        return "blocked"
    return "template"


def _pvesm_status() -> str:
    try:
        proc = subprocess.run(
            ["pvesm", "status"],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def list_storages() -> list[str]:
    """Storage IDs from `pvesm status`. Empty when the host has no pvesm."""
    try:
        return [str(item) for item in parse_storage_ids(_pvesm_status())]
    except Exception:
        return []


def storage_kinds() -> dict[str, str]:
    """Active storage id to pvesm type. Empty when status cannot be read."""
    try:
        return {str(key): str(value) for key, value in parse_storage_kinds(_pvesm_status()).items()}
    except Exception:
        return {}


def storage_kind(storage: str) -> str:
    """pvesm type for one storage id. Empty when status cannot be read."""
    return storage_kinds().get(storage, "")


def import_storages() -> list[tuple[str, str]]:
    """Active storages that can hold an import file. (id, import directory).

    An empty pvesm status keeps every import storage from storage.cfg.
    """
    path = Path("/etc/pve/storage.cfg")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    targets = import_directories(text)
    active = set(list_storages())
    if not active:
        return targets
    return [(storage, directory) for storage, directory in targets if storage in active]


def disk_storage(vmid: int) -> str | None:
    """Storage id of the boot OS disk.

    None when qm config cannot be read. Empty when the guest has no OS disk.
    """
    try:
        proc = subprocess.run(
            ["qm", "config", str(vmid)],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return os_disk_storage(proc.stdout or "")


def disk_format(vmid: int) -> str | None:
    """raw or qcow2 of the boot OS disk.

    None when qm config cannot be read. Empty when the format is not raw or qcow2.
    """
    try:
        proc = subprocess.run(
            ["qm", "config", str(vmid)],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return os_disk_format(proc.stdout or "")


# Packages that ship no command to look for. libguestfs-xfs is only a Recommends
# of libguestfs0, so virt-customize can be present without it; XFS guests (the
# EL family) then fail to mount after the whole download.
_PREP_PACKAGES = ("libguestfs-xfs",)


def package_installed(name: str) -> bool | None:
    """dpkg status of one package. None when dpkg-query cannot answer."""
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", name],
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode == 0 and proc.stdout.strip() == "install ok installed"


def preflight(job: Job) -> None:
    """Require root and the host tools this mode actually calls.

    Not used for --dry-run. A published file does not need qm.
    """
    if os.geteuid() != 0:
        raise SystemExit("run as root on the PVE host (sudo is fine)")
    needed: list[str] = []
    if job.mode in {"image", "template"}:
        needed.append("qemu-img")
    if job.prep:
        needed.append("virt-customize")
    if job.mode == "template" or (job.mode == "image" and job.vmids):
        needed.extend(["qm", "pvesm"])
    for tool in needed:
        if shutil.which(tool) is None:
            raise SystemExit(f"missing {tool}; {_APT_HINT}")
    if job.prep:
        for package in _PREP_PACKAGES:
            if package_installed(package) is False:
                raise SystemExit(f"missing {package}; {_APT_HINT}")


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


def _product(spec, distro: str, release: str) -> str:
    label = str(getattr(spec, "label", "") or "").strip()
    return label or f"{distro} {release}"


def _tools() -> SimpleNamespace:
    """Host calls, read at use time so tests can replace the script bindings."""
    return SimpleNamespace(
        fetch_verified=fetch_verified,
        convert=convert,
        customize_apply=customize_apply,
        customize_argv=customize_argv,
        publish=publish,
        insert_disk=insert_disk,
        create_template=create_template,
        prepare_replace=prepare_replace,
        run=default_run,
        storage_rejects=_storage_rejects_images,
        storage_kind=storage_kind,
    )


def _announce(text: str, role: str) -> None:
    if sys.stdout.isatty():
        print(tone(text, role))
    else:
        print(text)


def run_one(job: Job, release: str, vmid: int | None) -> None:
    spec = _spec_for(job.distro, release)
    label = _product(spec, job.distro, release)
    action = action_for(job, vmid)
    print()
    _announce(opening(action, label, job, vmid), "accent")
    print()
    tools = _tools()
    done = run_fetched(job, spec, vmid, label, tools)
    print()
    _announce(done, "body")


def _pairs(job: Job) -> list[tuple[str, int | None]]:
    if job.mode == "image" and not job.vmids:
        return [(release, None) for release in job.releases]
    if len(job.vmids) != len(job.releases):
        raise RuntimeError("VMID count does not match releases")
    return list(zip(job.releases, job.vmids, strict=True))


def _settle_cache(job: Job, failed: bool, read_line) -> bool:
    """Ask about working files once the run is over.

    Enter keeps them. y deletes them. Disk backups are elsewhere and stay.
    A failed release still asks, and Enter keeps the download. Dry-run asks
    and does not delete. EOF keeps the files. Returns True when a delete was
    asked for and did not happen.
    """
    cache = job.cache_dir
    print()
    try:
        delete = ask_cache_cleanup(read_line, _write, cache=cache, failed=failed)
    except PromptAbort:
        delete = False
    if job.dry_run:
        if delete:
            print(f"dry-run: would delete the working files in {cache}")
        else:
            print(f"dry-run: leaving {cache} alone")
        return False
    if not delete:
        print(f"cache kept: {cache}")
        if failed:
            print("a release failed, so the download stays")
        return False
    try:
        clear_cache(cache)
    except DownloadError as exc:
        print(f"FAIL cache: {exc}")
        return True
    print(f"deleted the working files in {cache}")
    return False


def _report_backups(job: Job, started: float) -> None:
    """The last line, and only when this run wrote a disk backup."""
    if job.dry_run or not job.backup_vmids:
        return
    if backups_since(job.backup_dir, started):
        print(f"VM Disk image backups located at {job.backup_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pve-template-prep",
        description="Download, prep, and publish PVE cloud images or templates.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print actions and do not change the host",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show each virt-customize command instead of the progress bar",
    )
    args = parser.parse_args(argv)
    print(f"{parser.prog} {__version__}")
    failed_update = _consider_update(Path(__file__).resolve())
    if failed_update:
        return failed_update
    reader = arm(_read_line)
    try:
        with quiet_echo(reader):
            job = interview(
                reader,
                _write,
                dry_run=args.dry_run,
                list_storages=list_storages,
                storage_kinds=storage_kinds,
                releases_for=releases_for,
                normalize_release=normalize_release,
                vmids_in_use=vmids_in_use,
                vm_has_disks=vm_has_disks,
                guest_role=guest_role,
                disk_storage=disk_storage,
                disk_format_of=disk_format,
                import_storages=import_storages,
            )
    except PromptAbort:
        print("aborted")
        return 1
    if args.verbose:
        job = replace(job, verbose=True)
    if not job.dry_run:
        preflight(job)
    failed = False
    started = time.time()
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
    if _settle_cache(job, failed, reader):
        failed = True
    _report_backups(job, started)
    return 1 if failed else 0


def _consider_update(script: Path) -> int | None:
    """Offer a newer main. None continues. 1 means the install did not finish.

    A checkout, a skipped check, and a failed lookup do not ask. Enter keeps
    this copy. Yes replaces the install directory and restarts this command.
    """
    if os.environ.get("PVE_PREP_SKIP_UPDATE") == "1":
        return None
    if tool_update.is_checkout(script):
        return None
    remote = tool_update.fetch_version()
    if remote is None or not tool_update.is_newer(__version__, remote):
        return None
    print(tool_update.notice(__version__, remote))
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        return None
    print("Fetch it now? (y/n) [No]: ", end="")
    try:
        answer = input()
    except EOFError:
        return None
    if not tool_update.wants_fetch(answer):
        return None
    if os.geteuid() != 0:
        print("Run as root to update. Continuing with this version.")
        return None
    work = Path(tempfile.mkdtemp(prefix="pve-prep-update-"))
    try:
        try:
            proc = subprocess.run(
                tool_update.install_argv(work / "install.sh", script.parent)
            )
        except OSError as exc:
            print(f"update failed: {exc}")
            return 1
        if proc.returncode != 0:
            print("update failed")
            return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # One restart. The new process does not ask again during this handoff.
    os.environ["PVE_PREP_SKIP_UPDATE"] = "1"
    try:
        os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
    except OSError as exc:
        print(f"Updated, but the new copy did not start: {exc}")
        return 1
    return None


def _read_line() -> str:
    return input()


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
