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
    parse_vmid,
    published_name,
    vm_name,
)
from pve_prep.vm import volume_formats

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
        _begin_widget(write)
        value = ui.text(prompt.strip(), default=default)
        if value is None:
            raise PromptAbort("exit")
        return value.strip()
    return _ask(read_line, write, prompt)


def _blank(write) -> None:
    write("\n")


def _say(read_line, write, text: str, role: str) -> None:
    """Color one commentary line on a terminal. Pipes stay plain."""
    if not text.endswith("\n"):
        text += "\n"
    body = text[:-1]
    if ui.enabled(read_line):
        write(ui.tone(body, role) + "\n")
    else:
        write(text)


def _paint_body(write):
    """Color plain commentary ANSI white. Lines that already carry a color pass through."""

    def wrapped(text):
        if not text or text == "\n" or text.startswith("\033"):
            write(text)
            return
        if text.endswith("\n"):
            write(ui.tone(text[:-1], "body") + "\n")
        else:
            write(ui.tone(text, "body"))

    return wrapped


def _begin_widget(write) -> None:
    """Blank line, then a bright rule, then the menu."""
    _blank(write)
    write(ui.rule() + "\n")


def _pick(read_line, write, message: str, choices, *, default=None):
    _begin_widget(write)
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
        _say(read_line, write, "One distro per run.\n", "header")
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
        _say(read_line, write, f"Releases. {limit} available, pick 1 to {limit}.\n", "header")
        order = [str(spec.release) for spec in specs]
        while True:
            _begin_widget(write)
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
        _say(read_line, write, "What this run produces:\n", "header")
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


_STORAGE_LABEL = {
    "zfspool": "ZFS",
    "zfs": "ZFS",
    "lvm": "LVM",
    "lvmthin": "LVM-thin",
    "rbd": "RBD",
    "iscsi": "iSCSI",
    "iscsidirect": "iSCSI",
    "dir": "directory",
    "nfs": "NFS",
    "cifs": "CIFS",
    "btrfs": "btrfs",
    "cephfs": "CephFS",
}


def _storage_label(storage_type: str) -> str:
    kind = (storage_type or "").strip().casefold()
    return _STORAGE_LABEL.get(kind, kind or "this storage")


def _ask_file_format(read_line, write) -> str:
    """Format of a published file. There is no Proxmox storage involved."""
    if ui.enabled(read_line):
        _blank(write)
        _say(read_line, write, "Format of the image file.\n", "header")
        write("\n")
        write("  raw\n")
        write("  A .img file, full size.\n")
        write("\n")
        write("  QEMU qcow2\n")
        write("  Sparse.\n")
        return _pick(
            read_line,
            write,
            "Format",
            [
                ("raw (.img, full size)", "raw"),
                ("QEMU qcow2 (sparse)", "qcow2"),
            ],
            default="raw",
        )
    write("Format of the image file.\n")
    write("Image file format:\n")
    write("  1) raw (.img, full size)\n")
    write("  2) QEMU qcow2 (sparse)\n")
    return _ask_choice(
        read_line,
        write,
        "Select format [1]: ",
        {"1": "raw", "2": "qcow2"},
        "1",
        "pick 1 raw or 2 qcow2",
    )


def _ask_volume_format(read_line, write, storage: str, storage_type: str) -> str:
    """Format allocated on this storage. Block storage is raw and is not asked."""
    formats = volume_formats(storage_type)
    if formats == ("raw",):
        if ui.enabled(read_line):
            _blank(write)
        label = _storage_label(storage_type)
        write(f"{storage} is {label}. The volume will be raw.\n")
        return "raw"
    if ui.enabled(read_line):
        _blank(write)
        _say(read_line, write, f"Volume format on {storage}.\n", "header")
        write("\n")
        write("  QEMU qcow2\n")
        write("  Sparse. Directory, NFS, and CIFS can hold it.\n")
        write("\n")
        write("  raw\n")
        write("  Full size on this storage.\n")
        return _pick(
            read_line,
            write,
            "Format",
            [
                ("QEMU qcow2 (sparse)", "qcow2"),
                ("raw (full size)", "raw"),
            ],
            default="qcow2",
        )
    write(f"Volume format on {storage}.\n")
    write("  1) QEMU qcow2 (sparse)\n")
    write("  2) raw (full size)\n")
    return _ask_choice(
        read_line,
        write,
        "Select format [1]: ",
        {"1": "qcow2", "2": "raw"},
        "1",
        "pick 1 qcow2 or 2 raw",
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
        _say(read_line, write, "No storages detected.\n", "header")
        write("Type the storage id that should hold the VM disk.\n")
        while True:
            raw = _ask_text(read_line, write, "VM Disk Storage Path: ")
            if raw:
                return raw
            write("storage id is required\n")
    if ui.enabled(read_line):
        _blank(write)
        _say(read_line, write, "Proxmox storage for the VM disk.\n", "header")
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
    _say(read_line, write, "Directory for the finished image file.\n", "header")
    write("This is a path, not a storage id.\n")
    while True:
        raw = _ask_text(read_line, write, "Image directory: ")
        if raw:
            return raw
        write("directory is required\n")


def _next_free(vmids_in_use, reserved: set[int]) -> tuple[int | None, bool]:
    """Next VMID from 9001 that is not reserved.

    (id, True) is free. (id, False) could not be checked.
    (None, True) means the probe window was full.
    """
    start = DEFAULT_VMID
    scanned = 0
    while scanned < _PROBE_LIMIT:
        window = tuple(range(start, start + _PROBE_BATCH))
        scanned += len(window)
        start += _PROBE_BATCH
        occupied = vmids_in_use(window)
        if occupied is None:
            for vmid in range(DEFAULT_VMID, DEFAULT_VMID + _PROBE_LIMIT):
                if vmid not in reserved:
                    return vmid, False
            return None, False
        blocked = set(occupied) | reserved
        for vmid in window:
            if vmid not in blocked:
                return vmid, True
    return None, True


def _occupied(vmids: tuple[int, ...], vmids_in_use) -> set[int] | None:
    found = vmids_in_use(vmids)
    if found is None:
        return None
    return {vmid for vmid in vmids if vmid in set(found)}


def _danger(text: str) -> str:
    """Red when stdout is a terminal. Pipes and tests stay plain."""
    if sys.stdout.isatty():
        return ui.tone(text, "danger")
    return text


def _confirm_delete(read_line, write, detail: str) -> None:
    _say(read_line, write, detail + "\n", "danger")
    while True:
        raw = _ask_text(read_line, write, "Type DELETE to proceed, or X to exit: ")
        if raw == "DELETE":
            return
        if raw in {"X", "x"}:
            raise PromptAbort("exit")
        write("type DELETE to proceed, or X to exit\n")


def _disk_title(vmid: int, names: dict[int, str] | None) -> str:
    label = (names or {}).get(vmid, "")
    if label:
        return f"{label}, VMID {vmid}"
    return f"VMID {vmid}"


def _ask_each_disk(
    read_line,
    write,
    vmids: tuple[int, ...],
    vm_has_disks,
    *,
    replacing: bool,
    names: dict[int, str] | None = None,
) -> frozenset[int]:
    """Per VMID: backup, or DELETE to drop the disk. Returns VMIDs to back up."""
    if ui.enabled(read_line):
        _blank(write)
    _say(read_line, write, "One or more of the selected VMIDs is currently in use.\n", "header")
    write("\n")
    if replacing:
        _say(read_line, write, "If you continue, those VMs will be permanently deleted.\n", "danger")
        write(
            "A template that has linked clones is left unchanged, "
            "including clones on other cluster nodes.\n"
        )
    else:
        write("The VM stays. The new disk is inserted.\n")
        write(
            "A template keeps its config and gets a new boot disk. "
            "Backup copies that old disk into the cache instead of leaving it attached. "
            "A template with linked clones is left unchanged, "
            "including clones on other cluster nodes.\n"
        )
    backups: list[int] = []
    for vmid in vmids:
        present = vm_has_disks(vmid)
        title = _disk_title(vmid, names)
        if present is False:
            if replacing:
                _say(
                    read_line,
                    write,
                    f"{title} has no OS disk. The VM will still be deleted.\n",
                    "danger",
                )
                _confirm_delete(read_line, write, f"All data for VMID {vmid} will be removed.")
            else:
                write(f"{title} has no OS disk. The new image is inserted.\n")
            continue
        if replacing:
            keep = "Back up the existing template VM disk"
            drop = "Do not back up the existing template VM disk"
        else:
            keep = "Back up the existing VM disk"
            drop = "Do not back up the existing VM disk"
        if ui.enabled(read_line):
            _blank(write)
            write(f"{title}\n")
            choice = _pick(
                read_line,
                write,
                title,
                [(keep, "backup"), (drop, "overwrite", True)],
            )
        else:
            write(f"{title}:\n")
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
                f"The existing disk on VMID {vmid} will be deleted. The VM stays. "
                "A template stays a template.",
            )
    return frozenset(backups)


def _named_releases(distro: str, releases: tuple[str, ...], releases_for):
    specs = {str(spec.release): spec for spec in releases_for(distro)}
    named = []
    for release in releases:
        spec = specs.get(release)
        label = ""
        if spec is not None:
            label = str(getattr(spec, "label", "") or "").strip()
        named.append((release, label or release))
    return tuple(named)


def _unchecked_default(reserved: set[int]) -> int | None:
    for vmid in range(DEFAULT_VMID, DEFAULT_VMID + _PROBE_LIMIT):
        if vmid not in reserved:
            return vmid
    return None


def _read_one_vmid(read_line, write, prompt: str, *, default: str = "") -> int | None:
    """One VMID, or None when the line is empty. A bad line asks again."""
    while True:
        raw = _ask_text(read_line, write, prompt, default=default)
        if not raw:
            return None
        try:
            return parse_vmid(raw)
        except ValueError as exc:
            write(f"{exc}\n")


def _already_used(names: dict[int, str], vmid: int, write) -> bool:
    owner = names.get(vmid)
    if owner is None:
        return False
    write(f"VMID {vmid} is already assigned to {owner}.\n")
    return True


def _template_intro(read_line, write, label: str, default: int | None, checked: bool) -> None:
    if ui.enabled(read_line):
        _blank(write)
    _say(read_line, write, f"Provide a VMID for {label}.\n", "header")
    if default is None:
        write(f"No free VMID from {DEFAULT_VMID} in the next {_PROBE_LIMIT} IDs.\n")
    elif checked:
        write(f"If none is entered, {default} is assigned.\n")
    else:
        write(f"If none is entered, {default} is assigned.\n")
        write("Could not check which VMIDs are free. An existing VM will not be replaced.\n")
        return
    _say(
        read_line,
        write,
        "If that VMID is in use, the existing VM is deleted and replaced.\n",
        "danger",
    )
    write("A template with linked clones is left unchanged.\n")


def _ask_template_vmids(read_line, write, named, vmids_in_use, vm_has_disks):
    """One VMID per release. Enter takes the next free ID from 9001."""
    chosen: list[int] = []
    destroy: list[int] = []
    names: dict[int, str] = {}
    host_known = True
    for _release, label in named:
        while True:
            if host_known:
                default, checked = _next_free(vmids_in_use, set(names))
                if not checked:
                    host_known = False
            else:
                default, checked = _unchecked_default(set(names)), False
            _template_intro(read_line, write, label, default, checked)
            prompt = f"VMID [{default}]: " if default is not None else "VMID: "
            filled = str(default) if default is not None else ""
            vmid = _read_one_vmid(read_line, write, prompt, default=filled)
            if vmid is None:
                if default is None:
                    write("VMID is required\n")
                    continue
                vmid = default
            if _already_used(names, vmid, write):
                continue
            names[vmid] = label
            if vmid == default and checked:
                chosen.append(vmid)
                break
            if not checked:
                write("Could not check that VMID. An existing VM will not be replaced.\n")
                chosen.append(vmid)
                break
            occupied = _occupied((vmid,), vmids_in_use)
            if occupied is None:
                host_known = False
                write("Could not check that VMID. An existing VM will not be replaced.\n")
                chosen.append(vmid)
                break
            chosen.append(vmid)
            if vmid in occupied:
                destroy.append(vmid)
            break
    vmids = tuple(chosen)
    destroy_set = frozenset(destroy)
    if not destroy:
        return vmids, destroy_set, frozenset()
    ordered = tuple(vmid for vmid in vmids if vmid in destroy_set)
    backups = _ask_each_disk(
        read_line, write, ordered, vm_has_disks, replacing=True, names=names
    )
    return vmids, destroy_set, backups


def _image_intro(read_line, write, label: str, *, offer_publish: bool, several: bool) -> None:
    if ui.enabled(read_line):
        _blank(write)
    _say(read_line, write, f"Provide a VMID for {label}.\n", "header")
    if not offer_publish:
        return
    if several:
        write("Enter publishes a file for every selected release and leaves every VM alone.\n")
    else:
        write("Enter publishes a file and leaves every VM alone.\n")
    write("An existing VM stays. A template keeps its config and gets a new boot disk.\n")
    write("A template with linked clones is left unchanged.\n")


def _ask_missing_vmid(read_line, write, vmid: int) -> str:
    """A disk-image VMID that does not exist. Returns again or template."""
    again = "Try another VMID"
    create = f"Create a new VM template at {vmid}"
    detail = f"VMID {vmid} is not in use. There is no VM there to receive a disk.\n"
    if ui.enabled(read_line):
        _blank(write)
        write(detail)
        return _pick(
            read_line,
            write,
            f"VMID {vmid}",
            [(again, "again"), (create, "template")],
        )
    write(detail)
    write(f"  1) {again}\n")
    write(f"  2) {create}\n")
    return _ask_choice(
        read_line,
        write,
        "Select: ",
        {"1": "again", "2": "template"},
        None,
        "pick 1 to try another VMID, or 2 to create a template",
    )


def _ask_image_vmids(read_line, write, named, vmids_in_use, vm_has_disks):
    """One existing VMID per release.

    Enter on the first release publishes a file for every release.
    A VMID that is not in use can create a new template at that ID.
    Returns (vmids, backup set, template VMID set).
    """
    chosen: list[int] = []
    templates: list[int] = []
    names: dict[int, str] = {}
    several = len(named) > 1
    for index, (_release, label) in enumerate(named):
        offer_publish = index == 0
        while True:
            _image_intro(read_line, write, label, offer_publish=offer_publish, several=several)
            if offer_publish:
                prompt = f"VMID for {label}, or Enter to publish files: "
            else:
                prompt = f"VMID for {label}: "
            vmid = _read_one_vmid(read_line, write, prompt)
            if vmid is None:
                if offer_publish:
                    return (), frozenset(), frozenset()
                write("VMID is required\n")
                continue
            if _already_used(names, vmid, write):
                continue
            occupied = _occupied((vmid,), vmids_in_use)
            if occupied is None:
                write("Could not check that VMID. The run inserts the disk if the VM exists.\n")
                names[vmid] = label
                chosen.append(vmid)
                break
            if vmid not in occupied:
                if _ask_missing_vmid(read_line, write, vmid) == "again":
                    continue
                names[vmid] = label
                chosen.append(vmid)
                templates.append(vmid)
                break
            names[vmid] = label
            chosen.append(vmid)
            break
    vmids = tuple(chosen)
    template_set = frozenset(templates)
    inserts = tuple(vmid for vmid in vmids if vmid not in template_set)
    if not inserts:
        return vmids, frozenset(), template_set
    backups = _ask_each_disk(
        read_line, write, inserts, vm_has_disks, replacing=False, names=names
    )
    return vmids, backups, template_set


def _ask_existing_vmids(read_line, write, named, vmids_in_use) -> tuple[int, ...]:
    """One in-use VMID per release. There is no free-ID default."""
    chosen: list[int] = []
    names: dict[int, str] = {}
    for _release, label in named:
        if ui.enabled(read_line):
            _blank(write)
        _say(read_line, write, f"Provide a VMID for {label}.\n", "header")
        write("The VM has to already exist.\n")
        while True:
            vmid = _read_one_vmid(read_line, write, f"VMID for {label}: ")
            if vmid is None:
                write("VMID is required\n")
                continue
            if _already_used(names, vmid, write):
                continue
            occupied = _occupied((vmid,), vmids_in_use)
            if occupied is None:
                write("Could not check that VMID.\n")
                break
            if vmid not in occupied:
                write(f"Not in use: {vmid}. Type an existing VMID.\n")
                continue
            break
        names[vmid] = label
        chosen.append(vmid)
    return tuple(chosen)


def _ask_bool(read_line, write, prompt: str, default: bool) -> bool:
    if ui.enabled(read_line):
        _begin_widget(write)
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
    _say(read_line, write, "Hardware applied to each new VM. Same settings for every distro.\n", "header")
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
        "Those are the downloaded image and the converted copy.\n"
        "A disk backup in that directory is never deleted.\n"
        "\n"
        "Delete the working files when the run finishes? "
        "Enter keeps them. y deletes them. [y/N]\n",
        False,
    )


def _confirm(
    read_line,
    write,
    *,
    mode: str,
    count: int,
    make_template: bool,
    new_templates: int = 0,
) -> None:
    inserts = count - new_templates
    if mode == "image" and new_templates:
        if inserts <= 0:
            action = "create the template" if new_templates == 1 else "create the templates"
        elif inserts == 1 and new_templates == 1:
            action = "insert the disk and create the template"
        else:
            action = "insert the disks and create the templates"
    elif mode == "template" and make_template:
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
    storage_kinds=None,
) -> Job:
    """Walk the operator through one job.

    read_line() -> str, and raises EOFError on EOF. None is also EOF.
    Empty answers accept the default shown in that question, when it has one.
    A bad answer asks the same question again. YES starts the run. X exits.
    """
    if ui.enabled(read_line):
        write = _paint_body(write)
    distro = _ask_distro(read_line, write)
    releases = _ask_releases(read_line, write, distro, releases_for, normalize_release)
    mode = _ask_build(read_line, write)
    count = len(releases)

    kinds: dict[str, str] = {}
    if storage_kinds is not None:
        try:
            kinds = {str(key): str(value) for key, value in dict(storage_kinds() or {}).items()}
        except Exception:
            kinds = {}

    def volume_format_for(storage: str) -> str:
        return _ask_volume_format(read_line, write, storage, kinds.get(storage, ""))

    named = _named_releases(distro, releases, releases_for)
    template_vmids: frozenset[int] = frozenset()
    if mode == "template":
        vmids, destroy_vmids, backup_vmids = _ask_template_vmids(
            read_line, write, named, vmids_in_use, vm_has_disks
        )
        collision = _collision_for(destroy_vmids, backup_vmids)
        dest_dir = ""
        storage = _ask_storage(read_line, write, list_storages)
        disk_format = volume_format_for(storage)
    elif mode == "image":
        vmids, backup_vmids, template_vmids = _ask_image_vmids(
            read_line, write, named, vmids_in_use, vm_has_disks
        )
        destroy_vmids = frozenset()
        inserts = tuple(vmid for vmid in vmids if vmid not in template_vmids)
        collision = _collision_for(inserts, backup_vmids)
        if vmids:
            dest_dir = ""
            storage = _ask_storage(read_line, write, list_storages)
            disk_format = volume_format_for(storage)
        else:
            disk_format = _ask_file_format(read_line, write)
            dest_dir = _ask_directory(read_line, write)
            storage = ""
    else:
        disk_format = "raw"
        vmids = _ask_existing_vmids(read_line, write, named, vmids_in_use)
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

    if mode == "template" or template_vmids:
        bridge, memory_mb, cores = _ask_hardware(read_line, write)
    else:
        bridge, memory_mb, cores = DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES
    if mode == "template":
        make_template = _ask_make_template(read_line, write, count)
    else:
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
        line_mode = "template" if vmid in template_vmids else mode
        line_template = True if vmid in template_vmids else make_template
        write(
            _summary_line(
                distro,
                release,
                line_mode,
                disk_format,
                dest_dir,
                storage,
                vmid,
                prep,
                collision,
                line_template,
            )
            + "\n"
        )
    for vmid in sorted(destroy_vmids):
        if vmid in backup_vmids:
            write(
                f"VMID {vmid}: the existing disk is copied into the cache, "
                "then the VM is deleted. Cache cleanup does not remove that copy.\n"
            )
        else:
            write(f"VMID {vmid}: the existing disk is not kept. The VM is deleted.\n")
    if mode == "template" or template_vmids:
        write(f"hardware: bridge {bridge}, memory {memory_mb} MB, cores {cores}\n")
    if clean_cache:
        write(
            f"cache: delete the working files in {DEFAULT_CACHE} "
            "after a successful run. Disk backups stay.\n"
        )
    else:
        write(f"cache: keep {DEFAULT_CACHE}\n")

    _confirm(
        read_line,
        write,
        mode=mode,
        count=count,
        make_template=make_template,
        new_templates=len(template_vmids),
    )

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
        template_vmids=template_vmids,
    )
