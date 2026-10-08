"""Numbered plain-text interview. read_line raises EOFError on EOF."""

from __future__ import annotations

import re
from pathlib import Path

from pve_prep.catalog import DISTROS
from pve_prep.job import (
    DEFAULT_CACHE,
    Job,
    parse_vmids,
    published_name,
    vm_name,
)

DEFAULT_BRIDGE = "vmbr0"
DEFAULT_MEMORY_MB = 2048
DEFAULT_CORES = 2
DEFAULT_VMID = 9001
MAX_RELEASES = 3


class PromptAbort(Exception):
    """EOF or a non-yes at the final confirm."""


def _ask(read_line, write, prompt: str) -> str:
    write(prompt)
    try:
        line = read_line()
    except EOFError as exc:
        raise PromptAbort("eof") from exc
    if line is None:
        raise PromptAbort("eof")
    return str(line).strip()


def _ask_choice(read_line, write, prompt: str, mapping: dict[str, str], default_key: str | None, error: str) -> str:
    while True:
        raw = _ask(read_line, write, prompt)
        if raw == "" and default_key is not None:
            return mapping[default_key]
        if raw in mapping:
            return mapping[raw]
        write(error + "\n")


def _ask_distro(read_line, write) -> str:
    write("Distro:\n")
    for index, name in enumerate(DISTROS, start=1):
        write(f"  {index}) {name}\n")
    mapping = {str(index): name for index, name in enumerate(DISTROS, start=1)}
    return _ask_choice(
        read_line,
        write,
        "Select distro: ",
        mapping,
        None,
        "pick a distro number",
    )


def _ask_releases(read_line, write, distro: str, releases_for, normalize_release) -> tuple[str, ...]:
    write("Releases:\n")
    specs = list(releases_for(distro))
    for spec in specs:
        label = str(getattr(spec, "label", ""))
        eol = bool(getattr(spec, "eol", False)) and "EOL" not in label
        flag = " EOL" if eol else ""
        write(f"  {spec.release}  {label}{flag}\n")
    while True:
        raw = _ask(read_line, write, f"Releases, comma-separated (1-{MAX_RELEASES}): ")
        tokens = [part.strip() for part in raw.split(",") if part.strip()]
        if not tokens:
            write("need at least one release\n")
            continue
        if len(tokens) > MAX_RELEASES:
            write(f"pick 1 to {MAX_RELEASES} releases\n")
            continue
        chosen: list[str] = []
        failed = False
        for token in tokens:
            try:
                canonical = str(normalize_release(distro, token))
            except Exception:
                write(f"unknown release: {token}\n")
                failed = True
                break
            if canonical in chosen:
                write(f"duplicate release: {canonical}\n")
                failed = True
                break
            chosen.append(canonical)
        if failed:
            continue
        return tuple(chosen)


def _ask_storage(read_line, write, list_storages) -> str:
    try:
        found = list(list_storages() or [])
    except Exception:
        found = []
    storages = [str(item).strip() for item in found if str(item).strip()]
    if not storages:
        write("No storages detected.\n")
        while True:
            raw = _ask(read_line, write, "Storage id: ")
            if raw:
                return raw
            write("storage id is required\n")
    write("Storage:\n")
    for index, storage_id in enumerate(storages, start=1):
        write(f"  {index}) {storage_id}\n")
    write("or type an id\n")
    while True:
        raw = _ask(read_line, write, "Storage: ")
        if not raw:
            write("storage id is required\n")
            continue
        if raw.isdigit():
            number = int(raw)
            if 1 <= number <= len(storages):
                return storages[number - 1]
            write(f"pick 1-{len(storages)} or type a storage id\n")
            continue
        return raw


def _ask_directory(read_line, write) -> str:
    while True:
        raw = _ask(read_line, write, "Image directory: ")
        if raw:
            return raw
        write("directory is required\n")


def _ask_vmids(read_line, write, count: int) -> tuple[int, ...]:
    if count == 1:
        shown = str(DEFAULT_VMID)
    else:
        shown = f"{DEFAULT_VMID}-{DEFAULT_VMID + count - 1}"
    write(
        f"Need {count} VMID(s). One number starts a sequence, "
        "or give a comma-separated list, or an inclusive range. "
        f"Empty uses {shown}.\n"
    )
    while True:
        raw = _ask(read_line, write, f"VMIDs [{shown}]: ")
        if not raw:
            raw = str(DEFAULT_VMID)
        try:
            return parse_vmids(raw, count)
        except ValueError as exc:
            write(f"{exc}\n")


def _parse_destroy_text(text: str, chosen: set[int]) -> tuple[int, ...]:
    # A single retyped ID is that VM only. A range is intersected with the
    # selected VMIDs and is never expanded.
    raw = text.strip()
    if not raw:
        return ()
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", raw)
    if match:
        start = int(match.group(1))
        end = int(match.group(2))
        if end < start:
            raise ValueError("range is reversed")
        return tuple(vmid for vmid in sorted(chosen) if start <= vmid <= end)
    parts = [part for part in re.split(r"[,\s]+", raw) if part]
    found: list[int] = []
    for part in parts:
        found.extend(parse_vmids(part, 1))
    return tuple(found)


def _ask_destroy(read_line, write, vmids: tuple[int, ...]) -> frozenset[int]:
    write(
        "Type a VMID again to replace it. A template at that ID is destroyed. "
        "A stopped non-template keeps the VMID and gets a new scsi0. "
        "Empty means refuse.\n"
    )
    chosen = set(vmids)
    while True:
        raw = _ask(read_line, write, "Destroy VMIDs: ")
        try:
            parsed = _parse_destroy_text(raw, chosen)
        except ValueError as exc:
            write(f"{exc}\n")
            continue
        keep: list[int] = []
        for vmid in parsed:
            if vmid not in chosen:
                write(f"ignoring VMID {vmid} (not selected)\n")
                continue
            keep.append(vmid)
        return frozenset(keep)


def _ask_bool(read_line, write, prompt: str, default: bool) -> bool:
    while True:
        raw = _ask(read_line, write, prompt).lower()
        if raw == "":
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        write("answer y or n\n")


def _ask_positive(read_line, write, prompt: str, label: str) -> int:
    while True:
        raw = _ask(read_line, write, prompt)
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        write(f"{label} must be a positive integer\n")


def _ask_hardware(read_line, write) -> tuple[str, int, int]:
    accept = _ask_bool(
        read_line,
        write,
        "Hardware defaults: bridge vmbr0, memory 2048 MB, cores 2. Enter accepts, n changes.\n",
        True,
    )
    if accept:
        return DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES
    while True:
        bridge = _ask(read_line, write, "Bridge: ")
        if bridge:
            break
        write("bridge is required\n")
    memory_mb = _ask_positive(read_line, write, "Memory MB: ", "memory")
    cores = _ask_positive(read_line, write, "Cores: ", "cores")
    return bridge, memory_mb, cores


def _summary_line(
    distro: str,
    release: str,
    mode: str,
    disk_format: str,
    dest_dir: str,
    storage: str,
    vmid: int | None,
    prep: bool,
) -> str:
    flag = "yes" if prep else "no"
    if mode == "image":
        dest = Path(dest_dir) / published_name(distro, release, disk_format)
        return f"{distro} {release} -> image {disk_format} {dest} guest-prep={flag}"
    if mode == "template":
        name = vm_name(distro, release)
        return (
            f"{distro} {release} -> template VMID {vmid} name {name} "
            f"storage {storage} {disk_format} guest-prep={flag}"
        )
    return f"{distro} {release} -> existing VMID {vmid} storage {storage} guest-prep={flag}"


def interview(read_line, write, *, dry_run: bool, list_storages, releases_for, normalize_release) -> Job:
    """Walk the operator through one job.

    read_line() -> str, and raises EOFError on EOF. None is also EOF.
    Empty answers accept the default shown in that question, when it has one.
    """
    distro = _ask_distro(read_line, write)
    releases = _ask_releases(read_line, write, distro, releases_for, normalize_release)

    write("Product:\n")
    write("  1) image\n")
    write("  2) template\n")
    write("  3) existing\n")
    mode = _ask_choice(
        read_line,
        write,
        "Select product [2]: ",
        {"1": "image", "2": "template", "3": "existing"},
        "2",
        "pick 1 image, 2 template, or 3 existing",
    )

    if mode == "existing":
        disk_format = "raw"
    else:
        write("Disk format:\n")
        write("  1) raw\n")
        write("  2) qcow2\n")
        disk_format = _ask_choice(
            read_line,
            write,
            "Select format [1]: ",
            {"1": "raw", "2": "qcow2"},
            "1",
            "pick 1 raw or 2 qcow2",
        )

    if mode == "image":
        dest_dir = _ask_directory(read_line, write)
        storage = ""
    else:
        dest_dir = ""
        storage = _ask_storage(read_line, write, list_storages)

    if mode == "image":
        write("If the image exists:\n")
        write("  1) backup\n")
        write("  2) overwrite\n")
        write("  3) skip\n")
        collision = _ask_choice(
            read_line,
            write,
            "Collision [1]: ",
            {"1": "backup", "2": "overwrite", "3": "skip"},
            "1",
            "pick 1 backup, 2 overwrite, or 3 skip",
        )
    else:
        collision = "backup"

    if mode == "image":
        vmids: tuple[int, ...] = ()
        destroy_vmids: frozenset[int] = frozenset()
    else:
        vmids = _ask_vmids(read_line, write, len(releases))
        if mode == "template":
            destroy_vmids = _ask_destroy(read_line, write, vmids)
        else:
            destroy_vmids = frozenset()

    prep = _ask_bool(read_line, write, "Apply PVE 9 guest prep? [Y/n]\n", True)

    if mode == "template":
        bridge, memory_mb, cores = _ask_hardware(read_line, write)
    else:
        bridge, memory_mb, cores = DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES

    if dry_run:
        write("dry-run: commands only, no changes\n")
    paired: list[tuple[str, int | None]]
    if mode == "image":
        paired = [(release, None) for release in releases]
    else:
        paired = list(zip(releases, vmids))
    for release, vmid in paired:
        write(
            _summary_line(
                distro,
                release,
                mode,
                disk_format,
                dest_dir,
                storage,
                vmid,
                prep,
            )
            + "\n"
        )
    if destroy_vmids:
        ids = ", ".join(str(vmid) for vmid in sorted(destroy_vmids))
        write(
            f"DESTROY stopped template VMID {ids} and its disks, "
            "if that VM exists and is a template.\n"
        )
        write(
            "A stopped non-template at that VMID is kept: "
            "the new disk is imported and scsi0 is swapped.\n"
        )
    if mode == "template":
        write(f"hardware: bridge {bridge}, memory {memory_mb} MB, cores {cores}\n")

    answer = _ask(read_line, write, "Type yes to run: ")
    if answer != "yes":
        raise PromptAbort("not confirmed")

    return Job(
        distro=distro,
        releases=releases,
        mode=mode,
        disk_format=disk_format,
        dest_dir=dest_dir,
        storage=storage,
        cache_dir=DEFAULT_CACHE,
        collision=collision,
        vmids=vmids,
        prep=prep,
        bridge=bridge,
        memory_mb=memory_mb,
        cores=cores,
        dry_run=dry_run,
        destroy_vmids=destroy_vmids,
    )
