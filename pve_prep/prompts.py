"""Interview. A terminal draws questionary widgets. Anything else stays numbered.

read_line raises EOFError on EOF. A reader with use_questionary set, and a
vendored questionary that imports, takes the widget path. The questions and
the free-VMID rule stay. Backup and DELETE are one screen after the options.
"""

from __future__ import annotations

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
    current = prompt
    while True:
        raw = _ask(read_line, write, current)
        if raw == "" and default_key is not None:
            return mapping[default_key]
        if raw in mapping:
            return mapping[raw]
        current = error + "\n" + prompt


def _ask_distro(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
        _say(read_line, write, "Distro.\n", "header")
        write("One distro. Guest prep follows its family.\n")
        return _pick(read_line, write, "Distro", [(name, name) for name in DISTROS])
    write("One distro. Guest prep follows its family.\n")
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
        _say(read_line, write, f"Releases. Pick 1 to {limit}.\n", "header")
        order = [str(spec.release) for spec in specs]
        while True:
            _begin_widget(write)
            picked = ui.checkbox(
                "Releases",
                rows,
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
    field = f"Select releases, comma-separated (1-{limit}): "
    prompt = field
    while True:
        raw = _ask(read_line, write, prompt)
        tokens = [part.strip() for part in raw.split(",") if part.strip()]
        if not tokens:
            prompt = "need at least one release\n" + field
            continue
        if len(tokens) > MAX_RELEASES:
            prompt = f"pick 1 to {MAX_RELEASES} releases\n" + field
            continue
        chosen: list[str] = []
        failed = ""
        for token in tokens:
            if token.isdigit():
                number = int(token)
                if not 1 <= number <= len(specs):
                    failed = f"pick a release number: {token}"
                    break
                canonical = str(specs[number - 1].release)
            else:
                try:
                    canonical = str(normalize_release(distro, token))
                except Exception:
                    failed = f"unknown release: {token}"
                    break
            if canonical in chosen:
                failed = f"duplicate release: {canonical}"
                break
            chosen.append(canonical)
        if failed:
            prompt = failed + "\n" + field
            continue
        return tuple(chosen)


_BUILD_ROWS = (
    (
        "Complete PVE VM template",
        "template",
        "Create the VM, import the disk, then qm template.",
    ),
    (
        "PVE VM disk image only",
        "image",
        "Write a file, or replace a template boot disk.",
    ),
    (
        "Prep a stopped VM in place",
        "existing",
        "Customize the disk already attached. No download.",
    ),
)


def _ask_build(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
        _say(read_line, write, "Build.\n", "header")
        return _pick(
            read_line,
            write,
            "Build",
            [(title, value, False, detail) for title, value, detail in _BUILD_ROWS],
            default="template",
        )
    write("Build:\n")
    for index, (title, _value, detail) in enumerate(_BUILD_ROWS, start=1):
        write(f"  {index}) {title}\n")
        write(f"     {detail}\n")
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
        _say(read_line, write, "Image file format.\n", "header")
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
    write("Image file format.\n")
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
        prompt = "VM Disk Storage Path: "
        while True:
            raw = _ask_text(read_line, write, prompt)
            if raw:
                return raw
            prompt = "A storage id is required.\nVM Disk Storage Path: "
    if ui.enabled(read_line):
        _blank(write)
        _say(read_line, write, "Storage for the VM disk.\n", "header")
        write("It has to accept images.\n")
        return _pick(
            read_line,
            write,
            "VM Disk Storage Path",
            [(storage_id, storage_id) for storage_id in storages],
        )
    write("Storage for the VM disk. It has to accept images.\n")
    write("VM Disk Storage Path:\n")
    for index, storage_id in enumerate(storages, start=1):
        write(f"  {index}) {storage_id}\n")
    field = "VM Disk Storage Path: "
    prompt = field
    while True:
        raw = _ask(read_line, write, prompt)
        if not raw:
            prompt = "A storage id is required.\n" + field
            continue
        if raw.isdigit():
            number = int(raw)
            if 1 <= number <= len(storages):
                return storages[number - 1]
            prompt = f"Pick 1-{len(storages)}.\n" + field
            continue
        if raw not in storages:
            prompt = f"unknown storage: {raw}\n" + field
            continue
        return raw


def _ask_directory(read_line, write) -> str:
    if ui.enabled(read_line):
        _blank(write)
    _say(read_line, write, "Directory for the finished image file.\n", "header")
    write("This is a path, not a storage id.\n")
    prompt = "Image directory: "
    while True:
        raw = _ask_text(read_line, write, prompt)
        if raw:
            return raw
        prompt = "A directory is required.\nImage directory: "


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


def _confirm_delete(read_line, write, detail: str) -> None:
    _say(read_line, write, detail + "\n", "danger")
    prompt = "Please type DELETE to continue, or X to exit the script without any changes: "
    while True:
        raw = _ask_text(read_line, write, prompt)
        if raw == "DELETE":
            return
        if raw in {"X", "x"}:
            raise PromptAbort("exit")
        prompt = "Type DELETE to continue, or X to exit: "


def _guest_role(guest_role, vmid: int, *, default: str) -> str:
    """template, vm, clone, or blocked. An unreadable check is blocked."""
    if guest_role is None:
        return default
    role = guest_role(vmid)
    if role in {"template", "vm", "clone", "blocked"}:
        return role
    return "blocked"


_VMID_HINT = "Type one VMID, from 100 through 999999999."


def _replace_reason(vmid: int, role: str) -> str | None:
    """Why this VMID cannot replace a template or its disk."""
    if role == "template":
        return None
    if role == "vm":
        return f"VMID {vmid} is a VM, not a template. Type a template VMID."
    if role == "clone":
        return f"VMID {vmid} is a linked clone. It will not be changed. Type another VMID."
    return (
        f"VMID {vmid} has linked clones, or the check could not be read. "
        "Type another VMID."
    )


def _existing_reason(vmid: int, role: str) -> str | None:
    """Why stopped-VM prep must not touch this guest."""
    if role == "vm":
        return None
    if role == "clone":
        return f"VMID {vmid} is a linked clone. It will not be changed. Type another VMID."
    if role == "template":
        return (
            f"VMID {vmid} is a template. Existing mode does not change templates. "
            "Type a normal VM."
        )
    return (
        f"VMID {vmid} has linked clones, or the check could not be read. "
        "Type another VMID."
    )


def _vmid_parse_reason(exc: ValueError) -> str:
    detail = str(exc)
    if detail in {"type one VMID", "VMID is empty"}:
        return _VMID_HINT
    return f"{detail}. {_VMID_HINT}"


def _vmid_field(label: str, suggested: int | None) -> str:
    if suggested is None:
        return f"VMID for {label}: "
    return f"VMID for {label} [{suggested}]: "


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


def _read_one_vmid(
    read_line,
    write,
    base: str,
    *,
    default: str = "",
    suggested: int | None = None,
    reason: str = "",
) -> int | None:
    """One VMID, or None when the line is empty.

    A bad line asks again. The next prompt is the reason, then base.
    The rejected text is not left in the field.
    """
    current = f"{reason}\n{base}" if reason else base
    filled = default
    while True:
        raw = _ask_text(read_line, write, current, default=filled)
        if not raw:
            return None
        try:
            return parse_vmid(raw)
        except ValueError as exc:
            current = f"{_vmid_parse_reason(exc)}\n{base}"
            filled = str(suggested) if suggested is not None else ""


def _duplicate_reason(names: dict[int, str], vmid: int) -> str | None:
    owner = names.get(vmid)
    if owner is None:
        return None
    return f"VMID {vmid} is already assigned to {owner}. Type another VMID."


def _template_intro(read_line, write, label: str, default: int | None, checked: bool) -> None:
    if ui.enabled(read_line):
        _blank(write)
    if default is None:
        _say(read_line, write, f"VMID for {label}.\n", "header")
        write(f"No free VMID from {DEFAULT_VMID} in the next {_PROBE_LIMIT} IDs.\n")
    else:
        _say(read_line, write, f"VMID for {label}. Enter uses {default}.\n", "header")
    if not checked:
        write("Could not check which VMIDs are free. An existing VM will not be replaced.\n")
        return
    write(
        "In use: it has to be a template, and it is replaced. "
        "A normal VM or a linked clone is refused.\n"
    )


def _ask_template_vmids(read_line, write, named, vmids_in_use, guest_role=None):
    """One VMID per release. Enter takes the next free ID from 9001."""
    chosen: list[int] = []
    destroy: list[int] = []
    names: dict[int, str] = {}
    host_known = True
    for _release, label in named:
        if host_known:
            default, checked = _next_free(vmids_in_use, set(names))
            if not checked:
                host_known = False
        else:
            default, checked = _unchecked_default(set(names)), False
        _template_intro(read_line, write, label, default, checked)
        base = _vmid_field(label, default)
        filled = str(default) if default is not None else ""
        reason = ""
        while True:
            vmid = _read_one_vmid(
                read_line,
                write,
                base,
                default=filled,
                suggested=default,
                reason=reason,
            )
            reason = ""
            if vmid is None:
                if default is None:
                    reason = "A VMID is required."
                    continue
                vmid = default
            duplicate = _duplicate_reason(names, vmid)
            if duplicate:
                reason = duplicate
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
            if vmid in occupied:
                role = _guest_role(guest_role, vmid, default="template")
                refused = _replace_reason(vmid, role)
                if refused:
                    names.pop(vmid, None)
                    reason = refused
                    continue
                destroy.append(vmid)
            chosen.append(vmid)
            break
    return tuple(chosen), frozenset(destroy)


def _image_intro(read_line, write, label: str, *, offer_publish: bool, several: bool) -> None:
    if ui.enabled(read_line):
        _blank(write)
    if offer_publish and several:
        header = (
            f"VMID for {label}. Enter publishes a file for every selected release "
            "and leaves every VM alone.\n"
        )
    elif offer_publish:
        header = f"VMID for {label}. Enter publishes a file and leaves every VM alone.\n"
    else:
        header = f"VMID for {label}.\n"
    _say(read_line, write, header, "header")
    write(
        "In use: it has to be a template. The boot disk is replaced. "
        "A normal VM or a linked clone is refused.\n"
    )


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


def _ask_image_vmids(read_line, write, named, vmids_in_use, guest_role=None):
    """One existing VMID per release.

    Enter on the first release publishes a file for every release.
    A VMID that is not in use can create a new template at that ID.
    Returns (vmids, template VMID set). Backup is decided later, once.
    """
    chosen: list[int] = []
    templates: list[int] = []
    names: dict[int, str] = {}
    several = len(named) > 1
    for index, (_release, label) in enumerate(named):
        offer_publish = index == 0
        _image_intro(read_line, write, label, offer_publish=offer_publish, several=several)
        if offer_publish:
            base = f"VMID for {label}, or Enter to publish files: "
        else:
            base = _vmid_field(label, None)
        reason = ""
        while True:
            vmid = _read_one_vmid(read_line, write, base, reason=reason)
            reason = ""
            if vmid is None:
                if offer_publish:
                    return (), frozenset()
                reason = "A VMID is required."
                continue
            duplicate = _duplicate_reason(names, vmid)
            if duplicate:
                reason = duplicate
                continue
            occupied = _occupied((vmid,), vmids_in_use)
            if occupied is None:
                role = _guest_role(guest_role, vmid, default="template")
                refused = _replace_reason(vmid, role) if guest_role is not None else None
                if refused:
                    reason = refused
                    continue
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
            role = _guest_role(guest_role, vmid, default="template")
            refused = _replace_reason(vmid, role)
            if refused:
                reason = refused
                continue
            names[vmid] = label
            chosen.append(vmid)
            break
    return tuple(chosen), frozenset(templates)


def _ask_existing_vmids(read_line, write, named, vmids_in_use, guest_role=None) -> tuple[int, ...]:
    """One in-use VMID per release. There is no free-ID default."""
    chosen: list[int] = []
    names: dict[int, str] = {}
    for _release, label in named:
        if ui.enabled(read_line):
            _blank(write)
        _say(
            read_line,
            write,
            f"VMID for {label}. It has to already exist. "
            "A template or a linked clone is refused.\n",
            "header",
        )
        base = _vmid_field(label, None)
        reason = ""
        while True:
            vmid = _read_one_vmid(read_line, write, base, reason=reason)
            reason = ""
            if vmid is None:
                reason = "A VMID is required."
                continue
            duplicate = _duplicate_reason(names, vmid)
            if duplicate:
                reason = duplicate
                continue
            occupied = _occupied((vmid,), vmids_in_use)
            if occupied is None:
                role = _guest_role(guest_role, vmid, default="vm")
                refused = _existing_reason(vmid, role)
                if refused:
                    reason = refused
                    continue
                write("Could not check that VMID.\n")
                break
            if vmid not in occupied:
                reason = f"VMID {vmid} is not in use. Type an existing VMID."
                continue
            role = _guest_role(guest_role, vmid, default="vm")
            refused = _existing_reason(vmid, role)
            if refused:
                reason = refused
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
    current = prompt
    while True:
        raw = _ask_text(read_line, write, current)
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        current = f"{label} must be a positive integer.\n{prompt}"


def _ask_hardware(read_line, write) -> tuple[str, int, int]:
    if ui.enabled(read_line):
        _blank(write)
    accept = _ask_bool(
        read_line,
        write,
        f"Bridge {DEFAULT_BRIDGE}, memory {DEFAULT_MEMORY_MB} MB, cores {DEFAULT_CORES}.\n"
        "q35, virtio-scsi, SeaBIOS, serial, guest agent. Change those later with qm set.\n"
        "Enter keeps the three. n changes them. [Y/n]\n",
        True,
    )
    if accept:
        return DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES
    prompt = "Bridge: "
    while True:
        bridge = _ask_text(read_line, write, prompt)
        if bridge:
            break
        prompt = "A bridge is required.\nBridge: "
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
    permit_root: bool,
) -> str:
    flag = "yes" if prep else "no"
    root = ""
    if prep:
        root = " root-ssh=" + ("yes" if permit_root else "no")
    if mode == "image" and vmid is None:
        dest = Path(dest_dir) / published_name(distro, release, disk_format)
        return f"{distro} {release} -> image {disk_format} {dest} guest-prep={flag}{root}"
    if mode == "image":
        return (
            f"{distro} {release} -> insert VMID {vmid} storage {storage} "
            f"{disk_format} disk={collision} guest-prep={flag}{root}"
        )
    if mode == "template":
        name = vm_name(distro, release)
        kind = "template" if make_template else "vm"
        return (
            f"{distro} {release} -> {kind} VMID {vmid} name {name} "
            f"storage {storage} {disk_format} guest-prep={flag}{root}"
        )
    return f"{distro} {release} -> existing VMID {vmid} storage {storage} guest-prep={flag}{root}"


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
        f"Convert the new {noun} with qm template?\n"
        "Enter converts to a template. n leaves a normal VM. [Y/n]\n",
        True,
    )


def _ask_clean_cache(read_line, write) -> bool:
    return _ask_bool(
        read_line,
        write,
        "Delete working files in "
        f"{DEFAULT_CACHE} when the run finishes?\n"
        "Backups in that directory stay.\n"
        "Enter keeps them. y deletes them. [y/N]\n",
        False,
    )


def _diskless(vm_has_disks, vmid: int) -> bool:
    """True only when the host says this guest has no OS disk."""
    if vm_has_disks is None:
        return False
    try:
        found = vm_has_disks(vmid)
    except Exception:
        return False
    return found is False


def _confirm_copy(read_line, write, vmids: tuple[int, ...], cache: str) -> None:
    """One YES after the operator asked for a disk copy. No second gate."""
    lines = [
        f"A copy of the VM disks below will be saved to {cache}.",
        "A new image will then be created.",
        *[str(vmid) for vmid in vmids],
    ]
    write("\n".join(lines) + "\n")
    prompt = "Type YES to continue, or X to exit: "
    while True:
        answer = _ask_text(read_line, write, prompt)
        if answer in {"YES", "yes"}:
            return
        if answer in {"X", "x", "n", "no", "NO"}:
            raise PromptAbort("not confirmed")
        prompt = "Type YES to continue, or X to exit: "


def _delete_notice(*, whole_vm: bool, vmids: tuple[int, ...]) -> str:
    if whole_vm:
        head = "The templates listed below will be permanently deleted."
    else:
        head = "The existing disk for the templates listed below will be permanently deleted."
    return "\n".join([head, *[str(vmid) for vmid in vmids]])


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
    prompt = "Type YES: "
    while True:
        answer = _ask_text(read_line, write, prompt)
        if answer in {"YES", "yes"}:
            return
        if answer in {"X", "x", "n", "no", "NO"}:
            raise PromptAbort("not confirmed")
        prompt = "Type YES to proceed, or X to exit: "


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
    guest_role=None,
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
        vmids, destroy_vmids = _ask_template_vmids(
            read_line, write, named, vmids_in_use, guest_role
        )
        dest_dir = ""
        storage = _ask_storage(read_line, write, list_storages)
        disk_format = volume_format_for(storage)
    elif mode == "image":
        vmids, template_vmids = _ask_image_vmids(
            read_line, write, named, vmids_in_use, guest_role
        )
        destroy_vmids = frozenset()
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
        vmids = _ask_existing_vmids(read_line, write, named, vmids_in_use, guest_role)
        destroy_vmids = frozenset()
        dest_dir = ""
        storage = _ask_storage(read_line, write, list_storages)

    prep = _ask_bool(
        read_line,
        write,
        "Guest prep: agent, serial console, new machine-id and SSH host keys.\n"
        "Enter applies it. n leaves the published image. [Y/n]\n",
        True,
    )
    permit_root = False
    if prep:
        permit_root = _ask_bool(
            read_line,
            write,
            "Allow root SSH with a password? Cloud-init user must be root.\n"
            "Enter allows it. n leaves the image default. [Y/n]\n",
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

    if mode == "template":
        replace_ids = tuple(vmid for vmid in vmids if vmid in destroy_vmids)
        whole_vm = True
    elif mode == "image":
        replace_ids = tuple(vmid for vmid in vmids if vmid not in template_vmids)
        whole_vm = False
    else:
        replace_ids = ()
        whole_vm = False

    backup = False
    if replace_ids:
        for vmid in replace_ids:
            if _diskless(vm_has_disks, vmid):
                write(f"VMID {vmid} has no OS disk.\n")
        backup = _ask_bool(
            read_line,
            write,
            "Back up these disks into the cache first? "
            f"The copy stays in {DEFAULT_CACHE}.\n"
            "Enter copies them. n deletes with no copy. [Y/n]\n",
            True,
        )
    backup_vmids = frozenset(replace_ids) if backup else frozenset()
    collision = _collision_for(replace_ids, backup_vmids)

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
                permit_root,
            )
            + "\n"
        )
    for vmid in replace_ids:
        if mode == "template":
            if vmid in backup_vmids:
                write(f"{vmid}  copy, then delete the template\n")
            else:
                write(f"{vmid}  delete the template, no copy\n")
        elif vmid in backup_vmids:
            write(f"{vmid}  copy, then replace the boot disk\n")
        else:
            write(f"{vmid}  replace the boot disk, no copy\n")
    if mode == "template" or template_vmids:
        write(f"hardware: bridge {bridge}, memory {memory_mb} MB, cores {cores}\n")
    if clean_cache:
        write(
            f"cache: delete the working files in {DEFAULT_CACHE} "
            "after a successful run. Disk backups stay.\n"
        )
    else:
        write(f"cache: keep {DEFAULT_CACHE}\n")

    if replace_ids and backup:
        _confirm_copy(read_line, write, replace_ids, DEFAULT_CACHE)
    elif replace_ids:
        _confirm_delete(
            read_line,
            write,
            _delete_notice(whole_vm=whole_vm, vmids=replace_ids),
        )
    else:
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
        permit_root=permit_root,
    )
