"""Interview. A terminal draws questionary widgets. Anything else stays numbered.

read_line raises EOFError on EOF. A reader with use_questionary set, and a
vendored questionary that imports, takes the widget path. The questions,
the free-VMID rule, and the DELETE gate do not move.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pve_prep import ui
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
_PROBE_BATCH = 32
_PROBE_LIMIT = 256


class PromptAbort(Exception):
    """EOF, or an explicit no at the final confirm."""


def _ask(read_line, write, prompt: str) -> str:
    write(prompt)
    try:
        line = read_line()
    except EOFError as exc:
        raise PromptAbort("eof") from exc
    if line is None:
        raise PromptAbort("eof")
    return str(line).strip()


def _ask_text(read_line, write, prompt: str, *, default: str = "") -> str:
    """Free text. The widget shows prompt; the plain path writes it."""
    if ui.enabled(read_line):
        _blank(write)
        value = ui.text(prompt.strip(), default=default)
        if value is None:
            raise PromptAbort("exit")
        return value.strip()
    return _ask(read_line, write, prompt)


def _blank(write) -> None:
    write("\n")


def _pick(read_line, write, message: str, choices, *, default=None):
    _blank(write)
    picked = ui.select(message, choices, default=default)
    if picked is None:
        raise PromptAbort("exit")
    return picked


def _ask_choice(read_line, write, prompt: str, mapping: dict[str, str], default_key: str | None, error: str) -> str:
    while True:
        raw = _ask(read_line, write, prompt)
        if raw == "" and default_key is not None:
            return mapping[default_key]
        if raw in mapping:
            return mapping[raw]
        write(error + "\n")


def _ask_distro(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
        write("One distro per run.\n")
        write("Guest prep later follows that family.\n")
        return _pick(read_line, write, "Distro", [(name, name) for name in DISTROS])
    write("One distro per run. Guest prep later follows that family.\n")
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


def _release_limit(selected) -> bool | str:
    if not selected:
        return "need at least one release"
    if len(selected) > MAX_RELEASES:
        return f"pick 1 to {MAX_RELEASES} releases"
    return True


def _release_title(spec) -> str:
    label = str(getattr(spec, "label", ""))
    eol = bool(getattr(spec, "eol", False)) and "EOL" not in label
    flag = " EOL" if eol else ""
    return f"{spec.release}  {label}{flag}"


def _ask_releases(read_line, write, distro: str, releases_for, normalize_release) -> tuple[str, ...]:
    """Menu indexes, comma-separated. A non-numeric token may be a codename.

    A terminal uses a checkbox instead. The values are still the release
    strings, in menu order. Codenames stay on the numbered path.
    """
    specs = list(releases_for(distro))
    rows = [(_release_title(spec).rstrip(), str(spec.release)) for spec in specs]
    limit = min(MAX_RELEASES, len(specs))
    if ui.enabled(read_line):
        _blank(write)
        write(f"Releases. {limit} available, pick 1 to {limit}.\n")
        order = [str(spec.release) for spec in specs]
        while True:
            _blank(write)
            picked = ui.checkbox(
                "Releases",
                rows,
                instruction="\n  Space marks a release. Enter accepts.",
                validate=_release_limit,
            )
            if picked is None:
                raise PromptAbort("exit")
            chosen = [release for release in order if release in set(picked)]
            if not chosen:
                write("need at least one release\n")
                continue
            if len(chosen) > MAX_RELEASES:
                write(f"pick 1 to {MAX_RELEASES} releases\n")
                continue
            return tuple(chosen)
    write("Releases:\n")
    for index, (title, _release) in enumerate(rows, start=1):
        write(f"  {index}) {title}\n")
    write(f"Select the releases you want ({limit} available, pick 1 to {limit}).\n")
    write("Use the menu numbers, separated by commas. A codename also works.\n")
    while True:
        raw = _ask(read_line, write, f"Select releases, comma-separated (1-{limit}): ")
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
            if token.isdigit():
                number = int(token)
                if not 1 <= number <= len(specs):
                    write(f"pick a release number: {token}\n")
                    failed = True
                    break
                canonical = str(specs[number - 1].release)
            else:
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


def _ask_build(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
        write("What this run produces:\n")
        write("\n")
        write("  Complete PVE VM template\n")
        write("  Create a VM, import the disk, and ask before qm template converts it.\n")
        write("\n")
        write("  PVE VM disk image only\n")
        write("  Write a file. An existing VMID keeps that VM and receives the new disk.\n")
        write("\n")
        write("  Prep a stopped VM in place\n")
        write("  No download. Customize the disk that is already attached.\n")
        return _pick(
            read_line,
            write,
            "Build",
            [
                ("Complete PVE VM template", "template"),
                ("PVE VM disk image only", "image"),
                ("Prep a stopped VM in place", "existing"),
            ],
            default="template",
        )
    write("What this run produces:\n")
    write("  1) Complete PVE VM template\n")
    write("     Create a VM, import the disk, and ask before qm template converts it.\n")
    write("  2) PVE VM disk image only\n")
    write("     Write a file. An existing VMID keeps that VM and receives the new disk.\n")
    write("  3) Prep a stopped VM in place\n")
    write("     No download. Customize the disk that is already attached.\n")
    return _ask_choice(
        read_line,
        write,
        "Select [1]: ",
        {"1": "template", "2": "image", "3": "existing"},
        "1",
        "pick 1 template, 2 disk image, or 3 stopped VM",
    )


def _ask_format(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
        write("How the disk is stored before Proxmox imports it.\n")
        write("\n")
        write("  ZFS raw\n")
        write("  A file-based raw image is .img and full size.\n")
        write("\n")
        write("  QEMU qcow2\n")
        write("  Stays sparse until import.\n")
        return _pick(
            read_line,
            write,
            "Format",
            [
                ("ZFS raw", "raw"),
                ("QEMU qcow2", "qcow2"),
            ],
            default="raw",
        )
    write("How the disk is stored before Proxmox imports it.\n")
    write("VM disk format:\n")
    write("  1) ZFS raw (a file-based raw image is .img and full size)\n")
    write("  2) QEMU qcow2 (stays sparse until import)\n")
    return _ask_choice(
        read_line,
        write,
        "Select format [1]: ",
        {"1": "raw", "2": "qcow2"},
        "1",
        "pick 1 ZFS raw or 2 QEMU qcow2",
    )


def _ask_storage(read_line, write, list_storages) -> str:
    try:
        found = list(list_storages() or [])
    except Exception:
        found = []
    storages = [str(item).strip() for item in found if str(item).strip()]
    if not storages:
        if ui.enabled(read_line):
            _blank(write)
        write("No storages detected.\n")
        write("Type the storage id that should hold the VM disk.\n")
        while True:
            raw = _ask_text(read_line, write, "VM Disk Storage Path: ")
            if raw:
                return raw
            write("storage id is required\n")
    if ui.enabled(read_line):
        _blank(write)
        write("Proxmox storage for the VM disk.\n")
        write("It has to accept images.\n")
        return _pick(
            read_line,
            write,
            "VM Disk Storage Path",
            [(storage_id, storage_id) for storage_id in storages],
        )
    write("Proxmox storage for the VM disk. It has to accept images.\n")
    write("VM Disk Storage Path:\n")
    for index, storage_id in enumerate(storages, start=1):
        write(f"  {index}) {storage_id}\n")
    write("Enter a number from the list.\n")
    while True:
        raw = _ask(read_line, write, "VM Disk Storage Path: ")
        if not raw:
            write("storage id is required\n")
            continue
        if raw.isdigit():
            number = int(raw)
            if 1 <= number <= len(storages):
                return storages[number - 1]
            write(f"pick 1-{len(storages)}\n")
            continue
        if raw not in storages:
            write(f"unknown storage: {raw}\n")
            continue
        return raw


def _ask_directory(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
    write("Directory for the finished image file.\n")
    write("This is a path, not a storage id.\n")
    while True:
        raw = _ask_text(read_line, write, "Image directory: ")
        if raw:
            return raw
        write("directory is required\n")


def _format_ids(ids: tuple[int, ...]) -> str:
    if not ids:
        return ""
    if len(ids) == 1:
        return str(ids[0])
    if list(ids) == list(range(ids[0], ids[0] + len(ids))):
        return f"{ids[0]}-{ids[-1]}"
    return ", ".join(str(vmid) for vmid in ids)


def _free_defaults(count: int, vmids_in_use) -> tuple[int, ...] | None:
    """Next free VMIDs from 9001.

    None means the probe cannot see the host. () means the window was full.
    """
    found: list[int] = []
    start = DEFAULT_VMID
    scanned = 0
    while len(found) < count and scanned < _PROBE_LIMIT:
        window = tuple(range(start, start + _PROBE_BATCH))
        scanned += len(window)
        start += _PROBE_BATCH
        occupied = vmids_in_use(window)
        if occupied is None:
            return None
        for vmid in window:
            if vmid not in occupied:
                found.append(vmid)
            if len(found) == count:
                return tuple(found)
    if len(found) == count:
        return tuple(found)
    return ()


def _occupied(vmids: tuple[int, ...], vmids_in_use) -> set[int] | None:
    found = vmids_in_use(vmids)
    if found is None:
        return None
    return {vmid for vmid in vmids if vmid in set(found)}


def _danger(text: str) -> str:
    """Red when stdout is a terminal. Pipes and tests stay plain."""
    if sys.stdout.isatty():
        return f"\033[31m{text}\033[0m"
    return text


def _confirm_delete(read_line, write, detail: str) -> None:
    write(detail + "\n")
    while True:
        raw = _ask_text(read_line, write, "Type DELETE to proceed, or X to exit: ")
        if raw == "DELETE":
            return
        if raw in {"X", "x"}:
            raise PromptAbort("exit")
        write("type DELETE to proceed, or X to exit\n")


def _ask_each_disk(read_line, write, vmids: tuple[int, ...], vm_has_disks, *, replacing: bool) -> frozenset[int]:
    """Per VMID: backup, or DELETE to drop the disk. Returns VMIDs to back up."""
    if ui.enabled(read_line):
        _blank(write)
    write("One or more of the selected VMIDs is currently in use.\n")
    write("\n")
    if replacing:
        write("If you continue, those VMs will be permanently deleted.\n")
    else:
        write("The VM stays. The new disk is inserted.\n")
    backups: list[int] = []
    for vmid in vmids:
        present = vm_has_disks(vmid)
        if present is False:
            if replacing:
                write(f"VMID {vmid} has no OS disk. The VM will still be deleted.\n")
                _confirm_delete(read_line, write, f"All data for VMID {vmid} will be removed.")
            else:
                write(f"VMID {vmid} has no OS disk. The new image is inserted.\n")
            continue
        if replacing:
            keep = "Back up the existing template VM disk"
            drop = "Do not back up the existing template VM disk"
        else:
            keep = "Back up the existing VM disk"
            drop = "Do not back up the existing VM disk"
        if ui.enabled(read_line):
            _blank(write)
            write(f"VMID {vmid}\n")
            choice = _pick(
                read_line,
                write,
                f"VMID {vmid}",
                [(keep, "backup"), (drop, "overwrite", True)],
            )
        else:
            write(f"VMID {vmid}:\n")
            write(f"  1) {keep}\n")
            write(f"  2) {_danger(drop)}\n")
            choice = _ask_choice(
                read_line,
                write,
                "Backup: ",
                {"1": "backup", "2": "overwrite"},
                None,
                "pick 1 to back up, or 2 to skip the backup",
            )
        if choice == "backup":
            backups.append(vmid)
            continue
        if replacing:
            _confirm_delete(read_line, write, f"All data for VMID {vmid} will be removed.")
        else:
            _confirm_delete(
                read_line,
                write,
                f"The existing disk on VMID {vmid} will be deleted. The VM stays.",
            )
    return frozenset(backups)


def _parse_vmid_line(
    read_line,
    write,
    count: int,
    prompt: str,
    *,
    default: str = "",
) -> tuple[int, ...] | None:
    raw = _ask_text(read_line, write, prompt, default=default)
    if not raw:
        return None
    try:
        return parse_vmids(raw, count)
    except ValueError as exc:
        write(f"{exc}\n")
        return ()


def _ask_template_vmids(read_line, write, count: int, vmids_in_use, vm_has_disks):
    """Return (vmids, destroy set, backup set).

    Empty input uses the next free IDs from 9001. A typed ID that is already
    in use replaces that whole VM. Each such VMID is asked about its disk.
    """
    defaults = _free_defaults(count, vmids_in_use)
    unknown = defaults is None
    if unknown:
        defaults = tuple(DEFAULT_VMID + offset for offset in range(count))
    shown = _format_ids(defaults) if defaults else ""
    label = "VMID" if count == 1 else "VMIDs"
    if ui.enabled(read_line):
        _blank(write)
    write(f"Select the {label} you would like to assign ({count} required).\n")
    write("\n")
    if unknown:
        write(
            "Could not check which VMIDs are free. "
            f"Press enter to accept {shown}. An existing VM will not be replaced.\n"
        )
    elif not defaults:
        write(
            f"No {count} free VMID(s) from {DEFAULT_VMID} "
            f"in the next {_PROBE_LIMIT} IDs.\n"
            f"Specify {count} VMIDs separated by commas, spaces, or as a range.\n"
        )
    else:
        write(
            f"Press enter to accept the {label} below, or specify {count} "
            "separated by commas, spaces, or as a range.\n"
        )
        write("An ID that is already in use replaces that VM.\n")
    while True:
        prompt = f"VMID [{shown}]: " if shown else "VMID: "
        parsed = _parse_vmid_line(read_line, write, count, prompt, default=shown or "")
        if parsed is None:
            if not defaults:
                write("VMID list is empty\n")
                continue
            vmids = defaults
        elif parsed == ():
            continue
        else:
            vmids = parsed
        occupied = _occupied(vmids, vmids_in_use)
        if occupied is None:
            write("Could not check those VMIDs. An existing VM will not be replaced.\n")
            return vmids, frozenset(), frozenset()
        free = tuple(vmid for vmid in vmids if vmid not in occupied)
        in_use = tuple(vmid for vmid in vmids if vmid in occupied)
        if free:
            write(f"Not in use: {_format_ids(free)}.\n")
        if not in_use:
            return vmids, frozenset(), frozenset()
        backups = _ask_each_disk(read_line, write, in_use, vm_has_disks, replacing=True)
        return vmids, frozenset(in_use), backups


def _ask_image_vmids(read_line, write, count: int, vmids_in_use, vm_has_disks):
    """Return (vmids, backup set). Empty vmids publish a file and touch no VM."""
    label = "VMID" if count == 1 else "VMIDs"
    if ui.enabled(read_line):
        _blank(write)
    write(f"Select the {label} to receive the disk ({count} required).\n")
    write("Press enter to publish a file and leave every VM alone.\n")
    write("\n")
    write(f"Specify {count} existing VMIDs separated by commas, spaces, or as a range.\n")
    while True:
        parsed = _parse_vmid_line(read_line, write, count, "VMID: ")
        if parsed is None:
            return (), frozenset()
        if parsed == ():
            continue
        vmids = parsed
        occupied = _occupied(vmids, vmids_in_use)
        if occupied is None:
            write("Could not check those VMIDs. The run inserts the disk if the VM exists.\n")
            backups = _ask_each_disk(read_line, write, vmids, vm_has_disks, replacing=False)
            return vmids, backups
        free = tuple(vmid for vmid in vmids if vmid not in occupied)
        if free:
            write(
                f"Not in use: {_format_ids(free)}. "
                "Press enter to publish a file, or type an existing VMID.\n"
            )
            continue
        backups = _ask_each_disk(read_line, write, vmids, vm_has_disks, replacing=False)
        return vmids, backups


def _ask_existing_vmids(read_line, write, count: int, vmids_in_use) -> tuple[int, ...]:
    label = "VMID" if count == 1 else "VMIDs"
    if ui.enabled(read_line):
        _blank(write)
    write(f"Select the stopped {label} ({count} required).\n")
    write("The VM has to already exist.\n")
    write("\n")
    write(f"Specify {count} separated by commas, spaces, or as a range.\n")
    while True:
        parsed = _parse_vmid_line(read_line, write, count, "VMID: ")
        if parsed is None:
            write("VMID list is empty\n")
            continue
        if parsed == ():
            continue
        occupied = _occupied(parsed, vmids_in_use)
        if occupied is None:
            write("Could not check those VMIDs.\n")
            return parsed
        free = tuple(vmid for vmid in parsed if vmid not in occupied)
        if free:
            write(f"Not in use: {_format_ids(free)}. Type an existing VMID.\n")
            continue
        return parsed


def _ask_bool(read_line, write, prompt: str, default: bool) -> bool:
    if ui.enabled(read_line):
        _blank(write)
        message = prompt.strip()
        for suffix in ("[Y/n]", "[y/N]"):
            if message.endswith(suffix):
                message = message[: -len(suffix)].strip()
        answer = ui.confirm(message, default=default)
        if answer is None:
            raise PromptAbort("exit")
        return answer
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
        raw = _ask_text(read_line, write, prompt)
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        write(f"{label} must be a positive integer\n")


def _ask_hardware(read_line, write) -> tuple[str, int, int]:
    if ui.enabled(read_line):
        _blank(write)
    write("Hardware applied to each new VM. Same settings for every distro.\n")
    write("\n")
    write(f"  Memory: {DEFAULT_MEMORY_MB} MB\n")
    write(f"  Cores: {DEFAULT_CORES}\n")
    write(f"  NIC: virtio on bridge {DEFAULT_BRIDGE}\n")
    write("\n")
    write("  Machine: q35\n")
    write("  CPU: x86-64-v2-AES\n")
    write("  SCSI controller: virtio-scsi-single\n")
    write("  OS disk: scsi0, discard=on, ssd=1\n")
    write("  Cloud-init drive: ide2\n")
    write("\n")
    write("  BIOS: SeaBIOS (Proxmox default)\n")
    write("  Display: serial console\n")
    write("  Guest agent: enabled\n")
    write("  RNG: /dev/urandom\n")
    write("  OS type: Linux 2.6+\n")
    write("\n")
    write("Bridge, memory, and cores can be changed here.\n")
    write("The rest can be changed later in the web UI or with qm set.\n")
    accept = _ask_bool(
        read_line,
        write,
        "Press enter to keep bridge vmbr0, memory 2048 MB, cores 2. Type n to change those three.\n",
        True,
    )
    if accept:
        return DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES
    while True:
        bridge = _ask_text(read_line, write, "Bridge: ")
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
    collision: str,
    make_template: bool,
) -> str:
    flag = "yes" if prep else "no"
    if mode == "image" and vmid is None:
        dest = Path(dest_dir) / published_name(distro, release, disk_format)
        return f"{distro} {release} -> image {disk_format} {dest} guest-prep={flag}"
    if mode == "image":
        return (
            f"{distro} {release} -> insert VMID {vmid} storage {storage} "
            f"{disk_format} disk={collision} guest-prep={flag}"
        )
    if mode == "template":
        name = vm_name(distro, release)
        kind = "template" if make_template else "vm"
        return (
            f"{distro} {release} -> {kind} VMID {vmid} name {name} "
            f"storage {storage} {disk_format} guest-prep={flag}"
        )
    return f"{distro} {release} -> existing VMID {vmid} storage {storage} guest-prep={flag}"


def _collision_for(targets, backups: frozenset[int]) -> str:
    ids = set(targets)
    if not ids or ids <= set(backups):
        return "backup"
    return "overwrite"


def _ask_make_template(read_line, write, count: int) -> bool:
    noun = "VM" if count == 1 else "VMs"
    return _ask_bool(
        read_line,
        write,
        "Convert the new "
        f"{noun} to a template with qm template.\n"
        "A template is cloned, not booted. n leaves a normal VM.\n"
        "\n"
        "Enter converts to a template. n leaves a normal VM. [Y/n]\n",
        True,
    )


def _ask_clean_cache(read_line, write) -> bool:
    return _ask_bool(
        read_line,
        write,
        "The working files are in "
        f"{DEFAULT_CACHE}.\n"
        "That includes the downloaded image and any disk backups from this run.\n"
        "\n"
        "Delete those files when the run finishes? "
        "Enter keeps them. y deletes them. [y/N]\n",
        False,
    )


def _confirm(read_line, write, *, mode: str, count: int, make_template: bool) -> None:
    if mode == "template" and make_template:
        action = "create the template" if count == 1 else "create the templates"
    elif mode == "template":
        action = "create the VM" if count == 1 else "create the VMs"
    elif mode == "image":
        action = "write the image" if count == 1 else "write the images"
    else:
        action = "prep the VM" if count == 1 else "prep the VMs"
    if ui.enabled(read_line):
        _blank(write)
    write(f"Nothing has been changed yet. Type YES to {action}. Type X to exit.\n")
    while True:
        answer = _ask_text(read_line, write, "Type YES: ")
        if answer in {"YES", "yes"}:
            return
        if answer in {"X", "x", "n", "no", "NO"}:
            raise PromptAbort("not confirmed")
        write("type YES to proceed, or X to exit\n")


def interview(
    read_line,
    write,
    *,
    dry_run: bool,
    list_storages,
    releases_for,
    normalize_release,
    vmids_in_use,
    vm_has_disks,
) -> Job:
    """Walk the operator through one job.

    read_line() -> str, and raises EOFError on EOF. None is also EOF.
    Empty answers accept the default shown in that question, when it has one.
    A bad answer asks the same question again. YES starts the run. X exits.
    """
    distro = _ask_distro(read_line, write)
    releases = _ask_releases(read_line, write, distro, releases_for, normalize_release)
    mode = _ask_build(read_line, write)
    count = len(releases)

    if mode == "existing":
        disk_format = "raw"
    else:
        disk_format = _ask_format(read_line, write)

    if mode == "template":
        vmids, destroy_vmids, backup_vmids = _ask_template_vmids(
            read_line, write, count, vmids_in_use, vm_has_disks
        )
        collision = _collision_for(destroy_vmids, backup_vmids)
        dest_dir = ""
        storage = _ask_storage(read_line, write, list_storages)
    elif mode == "image":
        vmids, backup_vmids = _ask_image_vmids(
            read_line, write, count, vmids_in_use, vm_has_disks
        )
        destroy_vmids = frozenset()
        collision = _collision_for(vmids, backup_vmids)
        if vmids:
            dest_dir = ""
            storage = _ask_storage(read_line, write, list_storages)
        else:
            dest_dir = _ask_directory(read_line, write)
            storage = ""
    else:
        vmids = _ask_existing_vmids(read_line, write, count, vmids_in_use)
        destroy_vmids = frozenset()
        backup_vmids = frozenset()
        collision = "backup"
        dest_dir = ""
        storage = _ask_storage(read_line, write, list_storages)

    prep = _ask_bool(
        read_line,
        write,
        "Guest prep installs the agent, sets a serial console, and resets\n"
        "machine-id and SSH host keys.\n"
        "\n"
        "Enter applies it. n leaves the image as published. [Y/n]\n",
        True,
    )

    if mode == "template":
        bridge, memory_mb, cores = _ask_hardware(read_line, write)
        make_template = _ask_make_template(read_line, write, count)
    else:
        bridge, memory_mb, cores = DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES
        make_template = False
    clean_cache = _ask_clean_cache(read_line, write)

    if ui.enabled(read_line):
        _blank(write)
    if dry_run:
        write("dry-run: commands only, no changes\n")
    if mode == "image" and not vmids:
        paired: list[tuple[str, int | None]] = [(release, None) for release in releases]
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
                collision,
                make_template,
            )
            + "\n"
        )
    for vmid in sorted(destroy_vmids):
        if vmid in backup_vmids:
            write(
                f"VMID {vmid}: the existing disk is copied into the cache, "
                "then the VM is deleted.\n"
            )
        else:
            write(f"VMID {vmid}: the existing disk is not kept. The VM is deleted.\n")
    if mode == "template":
        write(f"hardware: bridge {bridge}, memory {memory_mb} MB, cores {cores}\n")
    if clean_cache:
        write(f"cache: delete the files in {DEFAULT_CACHE} after a successful run\n")
    else:
        write(f"cache: keep {DEFAULT_CACHE}\n")

    _confirm(read_line, write, mode=mode, count=count, make_template=make_template)

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
        backup_vmids=backup_vmids,
        make_template=make_template,
        clean_cache=clean_cache,
    )
