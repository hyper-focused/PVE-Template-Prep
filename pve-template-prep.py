#!/usr/bin/env python3
"""Prep one distro's cloud images or Proxmox templates."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from pve_prep import __version__
from pve_prep.build import opening, prep_existing, run_fetched
from pve_prep.catalog import normalize_release, releases_for
from pve_prep.customize import apply as customize_apply
from pve_prep.customize import argv as customize_argv
from pve_prep.download import DownloadError, clear_cache, convert, fetch_verified, publish
from pve_prep.job import Job, action_for
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
        run=default_run,
        storage_rejects=_storage_rejects_images,
        checked=_checked,
        parse_qm_status=parse_qm_status,
        is_template=is_template,
        existing_prep_commands=existing_prep_commands,
        existing_hw_commands=existing_hw_commands,
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
    _announce(opening(action, label, job, vmid), "cyan")
    print()
    tools = _tools()
    if action == "existing":
        prep_existing(job, vmid, spec, tools)
        done = f"{label} VM prepared"
    else:
        done = run_fetched(job, spec, vmid, label, tools)
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
