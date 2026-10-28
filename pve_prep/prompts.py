# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Interview. A terminal draws questionary widgets. Anything else stays numbered.

read_line raises EOFError on EOF. A reader with use_questionary set, and a
vendored questionary that imports, takes the widget path. The questions and
the free-VMID rule stay. One yes accepts the guest defaults. n asks each one.
Backup and DELETE are one screen after the options. Cache cleanup is asked
after the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pve_prep import ui
from pve_prep.catalog import DISTROS
from pve_prep.job import (
    DEFAULT_BACKUPS,
    DEFAULT_CACHE,
    Job,
    parse_vmid,
    published_name,
    vm_name,
)
from pve_prep.vm import is_file_storage

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
    _answered(write)
    return str(line).strip()


# The last line of a prompt ends with "(y/n) [Yes]: " or "[9001]: ". The
# widget draws that tail as its own hint and puts the colon back after it.
_HINT_TAIL = re.compile(
    r"^(?P<question>.*?)\s*(?P<hint>(?:\(y/n\)\s*)?\[[^\]]*\])\s*:?\s*$"
)


def _split_hint(prompt: str) -> tuple[str, str | None]:
    """(question, hint). A prompt with no bracket tail is all question."""
    head, newline, last = prompt.rpartition("\n")
    found = _HINT_TAIL.match(last)
    if found is None:
        return prompt, None
    return head + newline + found.group("question"), found.group("hint")


def _ask_text(
    read_line, write, prompt: str, *, default: str = "", danger_word: str | None = None
) -> str:
    """Free text. The widget shows prompt; the plain path writes it."""
    if ui.enabled(read_line):
        _begin_widget(write)
        question, hint = _split_hint(prompt.strip())
        value = ui.text(question, default=default, hint=hint, danger_word=danger_word)
        if value is None:
            raise PromptAbort("exit")
        _answered(write)
        return value.strip()
    return _ask(read_line, write, prompt)


def _blank(write) -> None:
    """One blank line. The interview writer drops a second one in a row."""
    blank = getattr(write, "blank", None)
    if blank is None:
        write("\n")
    else:
        blank()


def _answered(write) -> None:
    """The answer line came from the terminal, not from write. A blank line may follow it."""
    answered = getattr(write, "answered", None)
    if answered is not None:
        answered()


def _say(read_line, write, text: str, role: str) -> None:
    """Color commentary on a terminal, one line at a time, at the margin. Pipes stay plain."""
    if not text.endswith("\n"):
        text += "\n"
    if not ui.enabled(read_line):
        write(text)
        return
    lines = text[:-1].split("\n")
    write("\n".join(ui.tone(ui.MARGIN + line, role) if line else "" for line in lines) + "\n")


def _paint(text: str) -> str:
    """Plain commentary goes to the margin in the body color.

    Text that already carries a color passes through. So does a line that is
    already at the margin, which is how an uncolored heading stays put.
    """
    if "\033" in text:
        return text

    def line_of(line: str) -> str:
        if not line:
            return line
        if not line.startswith(ui.MARGIN):
            line = ui.MARGIN + line
        return ui.tone(line, "body")

    return "\n".join(line_of(line) for line in text.split("\n"))


class _Output:
    """The interview's writer. Never two blank lines in a row.

    A terminal also gets the body color and the margin. A menu is erased
    when answered, so it leaves nothing behind. An answered text prompt
    leaves its line, so the next blank line is kept.
    """

    def __init__(self, write, *, paint: bool) -> None:
        self._write = write
        self._paint = paint
        self._tail = "\n"

    def __call__(self, text: str) -> None:
        if not text:
            return
        if self._paint:
            text = _paint(text)
        self._write(text)
        self._tail = (self._tail + text)[-2:]

    def blank(self) -> None:
        if self._tail != "\n\n":
            self("\n")

    def answered(self) -> None:
        self._tail = " \n"


def _begin_widget(write) -> None:
    """A blank line above every widget."""
    _blank(write)


def _section(read_line, write, title: str) -> None:
    """Blank line, heading, rule, blank line. The numbered path gets it uncolored."""
    _blank(write)
    if ui.enabled(read_line):
        write(ui.heading(title) + "\n")
        write(ui.rule() + "\n")
    else:
        write(ui.MARGIN + title + "\n")
        write(ui.rule(color=False) + "\n")
    _blank(write)


def _selected(read_line, write, text: str, role: str = "success") -> None:
    """The line that replaces an answered menu. Blank line above it."""
    _blank(write)
    _say(read_line, write, text, role)


# The question already says what to do. The newline leaves a blank line above the rows.
_MENU_GAP = ui.MENU_GAP


def _pick(read_line, write, message: str, choices, *, default=None):
    _begin_widget(write)
    picked = ui.select(message, choices, default=default, instruction=_MENU_GAP)
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


def distro_name(distro: str, releases_for) -> str:
    """The display name from the distro config. The id when the config has none."""
    try:
        specs = list(releases_for(distro))
    except Exception:
        specs = []
    for spec in specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if name:
            return name
    return distro


def _ask_distro(read_line, write, releases_for) -> str:
    rows = [(distro_name(distro, releases_for), distro) for distro in DISTROS]
    _section(read_line, write, "Distribution")
    if ui.enabled(read_line):
        picked = _pick(
            read_line,
            write,
            "Please choose a distro. Highlight a selection and press enter",
            rows,
        )
    else:
        write("Please choose a distro:\n")
        for index, (name, _distro) in enumerate(rows, start=1):
            write(f"  {index}) {name}\n")
        mapping = {str(index): distro for index, (_name, distro) in enumerate(rows, start=1)}
        picked = _ask_choice(
            read_line,
            write,
            "Select distro: ",
            mapping,
            None,
            "pick a distro number",
        )
    _selected(read_line, write, f"{dict((d, n) for n, d in rows)[picked]} selected.")
    return picked


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
    """Releases section. Returns release strings in menu order."""
    _section(read_line, write, "Releases")
    chosen = _pick_releases(read_line, write, distro, releases_for, normalize_release)
    name = distro_name(distro, releases_for)
    _selected(read_line, write, f"{name} {', '.join(chosen)} selected.")
    return chosen


def _pick_releases(read_line, write, distro: str, releases_for, normalize_release) -> tuple[str, ...]:
    """Menu indexes, comma-separated. A non-numeric token may be a codename.

    A terminal uses a checkbox instead. The values are still the release
    strings, in menu order. Codenames stay on the numbered path.
    """
    specs = list(releases_for(distro))
    rows = [(_release_title(spec).rstrip(), str(spec.release)) for spec in specs]
    limit = min(MAX_RELEASES, len(specs))
    if ui.enabled(read_line):
        order = [str(spec.release) for spec in specs]
        while True:
            _begin_widget(write)
            picked = ui.checkbox(
                "Please choose release(s). Highlight selection(s) and press enter",
                rows,
                instruction=_MENU_GAP,
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
    write("Please choose release(s):\n")
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


# Menu row, then the line shown once it is picked.
_BUILD_ROWS = (
    ("Create new Proxmox PVE VM templates", "template", "New VM templates selected."),
    ("Update existing Proxmox PVE template VM disk images", "update", "Update existing VM disk images selected."),
    ("Create standalone Proxmox PVE VM disk images", "standalone", "Standalone VM disk images selected."),
)


def _ask_build(read_line, write) -> str:
    _section(read_line, write, "PVE Template and Disk Image Builder")
    if ui.enabled(read_line):
        picked = _pick(
            read_line,
            write,
            "Highlight desired template or image type and press enter",
            [(title, value) for title, value, _done in _BUILD_ROWS],
            default="template",
        )
    else:
        for index, (title, _value, _done) in enumerate(_BUILD_ROWS, start=1):
            write(f"  {index}) {title}\n")
        picked = _ask_choice(
            read_line,
            write,
            "Select [1]: ",
            {"1": "template", "2": "update", "3": "standalone"},
            "1",
            "pick 1, 2, or 3",
        )
    done = {value: line for _title, value, line in _BUILD_ROWS}
    _selected(read_line, write, done[picked])
    return picked


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


def _storage_refusal(storage: str, kinds: dict[str, str]) -> str | None:
    """Why this storage cannot hold a disk image. None when it can.

    An empty kinds map means pvesm could not be read. The interview goes on,
    and the run refuses anything that is not file storage.
    """
    if not kinds:
        return None
    kind = kinds.get(storage, "")
    if is_file_storage(kind):
        return None
    if not kind:
        return f"{storage} is not an active storage."
    return f"{storage} is {_storage_label(kind)}. Disk images go on file storage only."


def _file_storage_ids(read_line, write, list_storages, kinds: dict[str, str]) -> list[str]:
    """Active file storages. ZFS, LVM, RBD, and iSCSI are named and left out.

    A host with storages and none of them file storage ends the interview.
    """
    try:
        found = [str(item).strip() for item in list(list_storages() or []) if str(item).strip()]
    except Exception:
        found = []
    kept = [storage for storage in found if _storage_refusal(storage, kinds) is None]
    skipped = [storage for storage in found if storage not in kept]
    if skipped:
        shown = ", ".join(
            f"{storage} ({_storage_label(kinds.get(storage, ''))})" for storage in skipped
        )
        _say(read_line, write, f"Not listed: {shown}. Disk images go on file storage only.", "note")
    if found and not kept:
        _say(read_line, write, "No file storage accepts the VM disk.", "danger")
        raise PromptAbort("no file storage")
    return kept


_FORMAT_ROWS = (("QEMU qcow2 (.qcow2)", "qcow2"), ("raw (.raw)", "raw"))


def _ask_format(read_line, write, question: str) -> str:
    """qcow2 or raw. Enter selects qcow2. The file extension is the format."""
    _section(read_line, write, "Disk Format")
    if ui.enabled(read_line):
        picked = _pick(
            read_line,
            write,
            f"{question} Highlight a selection and press enter",
            list(_FORMAT_ROWS),
            default="qcow2",
        )
    else:
        write(question + "\n")
        for index, (title, _value) in enumerate(_FORMAT_ROWS, start=1):
            write(f"  {index}) {title}\n")
        picked = _ask_choice(
            read_line,
            write,
            "Select format [1]: ",
            {"1": "qcow2", "2": "raw"},
            "1",
            "pick 1 qcow2 or 2 raw",
        )
    _selected(read_line, write, f"{picked} selected.")
    return picked


def _ask_volume_format(read_line, write, storage: str, storage_type: str) -> str:
    """Format importdisk allocates on this file storage.

    The storage menu has already left block storage out. An unreadable type
    is asked anyway, and a live run refuses it if it is not file storage.
    """
    kind = (storage_type or "").strip()
    if kind and not is_file_storage(kind):
        _say(
            read_line,
            write,
            f"{storage} is {_storage_label(kind)}. Disk images go on file storage only.",
            "danger",
        )
        raise PromptAbort("block storage")
    if not kind:
        _say(
            read_line,
            write,
            f"Could not read the type of {storage}. The run refuses it unless it is file storage.",
            "warning",
        )
    return _ask_format(read_line, write, f"Choose the volume format on {storage}.")


def _front(storages: list[str], recommended: str) -> list[str]:
    """Recommended storage is choice 1. Enter on a menu accepts that row."""
    rest = [item for item in storages if item != recommended]
    if recommended:
        return [recommended, *rest]
    return list(storages)


def _ask_storage(
    read_line,
    write,
    list_storages,
    *,
    recommended: str = "",
    notes: tuple[str, ...] = (),
    preface: str = "",
) -> str:
    _section(read_line, write, "VM Disk Storage")
    try:
        found = list(list_storages() or [])
    except PromptAbort:
        raise
    except Exception:
        found = []
    storages = _front(
        [str(item).strip() for item in found if str(item).strip()],
        recommended,
    )
    if preface:
        _say(read_line, write, preface, "note")
    for note in notes:
        _say(read_line, write, note, "note")
    if not storages:
        _say(read_line, write, "No storages detected.", "warning")
        write("Type the storage id that should hold the VM disk.\n")
        prompt = (
            f"VM Disk Storage Path [{recommended}]: "
            if recommended
            else "VM Disk Storage Path: "
        )
        while True:
            raw = _ask_text(read_line, write, prompt)
            if raw:
                return raw
            if recommended:
                return recommended
            prompt = "A storage id is required.\nVM Disk Storage Path: "
    if recommended:
        _say(read_line, write, f"Enter keeps {recommended}.", "note")
    if ui.enabled(read_line):
        picked = _pick(
            read_line,
            write,
            "Choose the storage for the new VM disk. Highlight a selection and press enter",
            [(storage_id, storage_id) for storage_id in storages],
        )
        _selected(read_line, write, f"{picked} selected.")
        return picked
    write("Storage for the VM disk. It has to accept images.\n")
    write("VM Disk Storage Path:\n")
    for index, storage_id in enumerate(storages, start=1):
        write(f"  {index}) {storage_id}\n")
    field = (
        f"VM Disk Storage Path [{recommended}]: "
        if recommended
        else "VM Disk Storage Path: "
    )
    prompt = field
    while True:
        raw = _ask(read_line, write, prompt)
        if not raw:
            if recommended:
                return recommended
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


def _ask_import_storage(read_line, write, import_storages) -> tuple[str, str]:
    """(storage id, import directory) for the image files. No import storage aborts."""
    rows: list[tuple[str, str]] = []
    if import_storages is not None:
        try:
            raw_rows = list(import_storages() or [])
        except Exception:
            raw_rows = []
        for item in raw_rows:
            storage, directory = item
            storage = str(storage).strip()
            directory = str(directory).strip()
            if storage and directory:
                rows.append((storage, directory))
    _section(read_line, write, "Import Storage")
    if not rows:
        _say(read_line, write, "No storage has import content.", "danger")
        raise PromptAbort("no import storage")
    if ui.enabled(read_line):
        picked = _pick(
            read_line,
            write,
            "Choose the storage that receives the image files. Highlight a selection and press enter",
            [(storage, storage) for storage, _directory in rows],
        )
        _selected(read_line, write, f"{picked} selected. Images go to {dict(rows)[picked]}.")
        return picked, dict(rows)[picked]
    write("Import storage:\n")
    for index, (storage, _directory) in enumerate(rows, start=1):
        write(f"  {index}) {storage}\n")
    mapping = {str(index): storage for index, (storage, _directory) in enumerate(rows, start=1)}
    picked = _ask_choice(
        read_line,
        write,
        "Select [1]: ",
        mapping,
        "1",
        f"pick 1-{len(rows)}",
    )
    return picked, dict(rows)[picked]


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


def _guest_role(guest_role, vmid: int, *, default: str) -> str:
    """template, vm, clone, unfinished, or blocked. An unreadable check is blocked."""
    if guest_role is None:
        return default
    role = guest_role(vmid)
    if role in {"template", "vm", "clone", "unfinished", "blocked"}:
        return role
    return "blocked"


_VMID_HINT = "Type one VMID, from 100 through 999999999."


def _replace_reason(vmid: int, role: str) -> str | None:
    """Why this VMID cannot replace a template or its disk."""
    if role == "template":
        return None
    if role == "vm":
        return (
            f"VMID {vmid} is a normal VM, not a template. "
            "It will not be changed. Type a template VMID."
        )
    if role == "clone":
        return (
            f"VMID {vmid} is a linked clone. "
            "It will not be changed. Type a template VMID."
        )
    if role == "unfinished":
        return (
            f"VMID {vmid} is not a template; its disk update did not finish. "
            f"It will not be changed. Run qm template {vmid}, then run the update again."
        )
    return (
        f"VMID {vmid} has linked clones, or the check could not be read. "
        "It will not be changed. Type another template VMID."
    )





def _vmid_parse_reason(exc: ValueError) -> str:
    detail = str(exc)
    if detail in {"type one VMID", "VMID is empty"}:
        return _VMID_HINT
    return f"{detail}. {_VMID_HINT}"


def _vmid_field(label: str, suggested: int | None) -> str:
    if suggested is None:
        return f"{label} VM Template ID: "
    return f"{label} VM Template ID [{suggested}]: "


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
    reason: str = "",
) -> int | None:
    """One VMID, or None when the line is empty.

    The default, when there is one, is in brackets in base. The field
    starts empty, so a typed ID replaces it. Enter returns None and the
    caller uses that default. A bad line asks again and does not leave
    the rejected text in the field.
    """
    current = f"{reason}\n{base}" if reason else base
    while True:
        raw = _ask_text(read_line, write, current)
        if not raw:
            return None
        try:
            return parse_vmid(raw)
        except ValueError as exc:
            current = f"{_vmid_parse_reason(exc)}\n{base}"


def _duplicate_reason(names: dict[int, str], vmid: int) -> str | None:
    owner = names.get(vmid)
    if owner is None:
        return None
    return f"VMID {vmid} is already assigned to {owner}. Type another VMID."


def _template_intro(read_line, write, default: int | None, checked: bool) -> None:
    """Warnings for one release. The rule itself is said once, before the first VMID."""
    if default is None:
        _say(read_line, write, f"No free VMID from {DEFAULT_VMID} in the next {_PROBE_LIMIT} IDs.", "warning")
    if not checked:
        _say(read_line, write, "Could not check which VMIDs are free. An existing VM will not be replaced.", "warning")


_TEMPLATE_VMID_NOTE = (
    "Provide a VMID for each VM template or press enter to use the default.\n"
    "If the VMID is in use, the existing template will be deleted and replaced."
)


def _ask_template_vmids(read_line, write, named, vmids_in_use, guest_role=None):
    """One VMID per release. Enter takes the next free ID from 9001."""
    chosen: list[int] = []
    destroy: list[int] = []
    names: dict[int, str] = {}
    host_known = True
    _section(read_line, write, "Template VMID(s)")
    _say(read_line, write, _TEMPLATE_VMID_NOTE, "note")
    for _release, label in named:
        if host_known:
            default, checked = _next_free(vmids_in_use, set(names))
            if not checked:
                host_known = False
        else:
            default, checked = _unchecked_default(set(names)), False
        _template_intro(read_line, write, default, checked)
        base = _vmid_field(label, default)
        reason = ""
        while True:
            vmid = _read_one_vmid(read_line, write, base, reason=reason)
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
                _say(read_line, write, "Could not check that VMID. An existing VM will not be replaced.", "warning")
                chosen.append(vmid)
                break
            occupied = _occupied((vmid,), vmids_in_use)
            if occupied is None:
                host_known = False
                _say(read_line, write, "Could not check that VMID. An existing VM will not be replaced.", "warning")
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


def _lookup_disk_storage(disk_storage, vmid: int) -> str | None:
    """Storage id, empty when there is no OS disk, None when it cannot be read."""
    try:
        found = disk_storage(vmid)
    except Exception:
        return None
    if found is None:
        return None
    return str(found).strip()


def _insert_storage_problem(vmid: int, found: str | None) -> str | None:
    if found is None:
        return f"Could not read the disk storage for VMID {vmid}. Type another VMID."
    if not found:
        return f"VMID {vmid} has no OS disk. Type another VMID."
    return None


def _lookup_disk_format(disk_format_of, vmid: int) -> str | None:
    """raw or qcow2, empty when the disk has neither, None when it cannot be read."""
    try:
        found = disk_format_of(vmid)
    except Exception:
        return None
    if found is None:
        return None
    return str(found).strip().casefold()


def _insert_format_problem(vmid: int, found: str | None) -> str | None:
    if found is None:
        return f"Could not read the disk format for VMID {vmid}. Type another VMID."
    if found not in {"raw", "qcow2"}:
        return f"VMID {vmid} disk format is not raw or qcow2. Type another VMID."
    return None


def _keep_insert(
    disk_storage,
    disk_format_of,
    vmid: int,
    placed: list[tuple[int, str]],
    formats: list[tuple[int, str]],
    kinds: dict[str, str],
) -> str | None:
    """Record a kept boot disk. None accepts. A string asks for another VMID.

    No storage callback means the interview still asks for a storage id.
    No format callback means the interview still asks for a volume format.
    A boot disk on ZFS, LVM, RBD, or iSCSI is refused.
    """
    if disk_storage is None:
        return None
    found = _lookup_disk_storage(disk_storage, vmid)
    problem = _insert_storage_problem(vmid, found)
    if problem:
        return problem
    refused = _storage_refusal(str(found), kinds)
    if refused:
        return f"VMID {vmid} boot disk: {refused} Type another VMID."
    if disk_format_of is not None:
        fmt = _lookup_disk_format(disk_format_of, vmid)
        problem = _insert_format_problem(vmid, fmt)
        if problem:
            return problem
        formats.append((vmid, str(fmt)))
    placed.append((vmid, str(found)))
    return None


def _ask_image_vmids(
    read_line,
    write,
    named,
    vmids_in_use,
    guest_role=None,
    disk_storage=None,
    disk_format_of=None,
    kinds: dict[str, str] | None = None,
):
    """One existing template VMID per release.

    A free VMID is refused. Enter does not publish a file.
    Returns (vmids, template VMID set, kept storage, kept format).
    The template set stays empty. A new template is the other build choice.
    """
    chosen: list[int] = []
    names: dict[int, str] = {}
    placed: list[tuple[int, str]] = []
    formats: list[tuple[int, str]] = []
    known_kinds = dict(kinds or {})
    _section(read_line, write, "Template VMID(s)")
    for _release, label in named:
        base = f"{label} VM Template ID: "
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
                role = _guest_role(guest_role, vmid, default="template")
                refused = _replace_reason(vmid, role) if guest_role is not None else None
                if refused:
                    reason = refused
                    continue
                problem = _keep_insert(
                    disk_storage, disk_format_of, vmid, placed, formats, known_kinds
                )
                if problem:
                    reason = problem
                    continue
                _say(
                    read_line,
                    write,
                    "Could not check that VMID. The run inserts the disk if the VM exists.",
                    "warning",
                )
                names[vmid] = label
                chosen.append(vmid)
                break
            if vmid not in occupied:
                reason = f"VMID {vmid} is not in use. Type a template VMID."
                continue
            role = _guest_role(guest_role, vmid, default="template")
            refused = _replace_reason(vmid, role)
            if refused:
                reason = refused
                continue
            problem = _keep_insert(
                    disk_storage, disk_format_of, vmid, placed, formats, known_kinds
                )
            if problem:
                reason = problem
                continue
            names[vmid] = label
            chosen.append(vmid)
            break
    return tuple(chosen), frozenset(), tuple(placed), tuple(formats)


def _yn_prompt(body: str, default: bool) -> str:
    """Question, then (y/n) and the bracket default. The field stays empty."""
    word = "Yes" if default else "No"
    text = body.rstrip("\n")
    if "\n" in text:
        head, _, last = text.rpartition("\n")
        last = last.rstrip().removesuffix(":").rstrip()
        return f"{head}\n{last} (y/n) [{word}]: "
    return f"{text.rstrip().removesuffix(':').rstrip()} (y/n) [{word}]: "


def _ask_bool(read_line, write, prompt: str, default: bool) -> bool:
    """Yes or no. Enter accepts the bracket. y and n answer it."""
    field = _yn_prompt(prompt, default)
    while True:
        raw = _ask_text(read_line, write, field).lower()
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
    _section(read_line, write, "Hardware")
    _say(
        read_line,
        write,
        f"Bridge {DEFAULT_BRIDGE}, memory {DEFAULT_MEMORY_MB} MB, cores {DEFAULT_CORES}.\n"
        "q35, virtio-scsi, SeaBIOS, serial, guest agent. Change those later with qm set.",
        "note",
    )
    accept = _ask_bool(read_line, write, "Use this VM template config?", True)
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


def _collision_for(targets, backups: frozenset[int]) -> str:
    ids = set(targets)
    if not ids or ids <= set(backups):
        return "backup"
    return "overwrite"


def _ask_make_template(read_line, write, count: int) -> bool:
    _section(read_line, write, "Template Conversion")
    noun = "VM" if count == 1 else "VMs"
    return _ask_bool(read_line, write, f"Convert the new {noun} with qm template?", True)


_PASSWORD_AUTH_PROMPT = "Allow password authentication for SSH?"
_CLOUD_INIT_ROOT = "Cloud-init user must be set to 'root'."
_ROOT_SSH_PROMPT = "Enable direct SSH login for 'root' user?"


def _ask_password_auth(read_line, write) -> bool:
    _section(read_line, write, "SSH Password Authentication")
    return _ask_bool(read_line, write, _PASSWORD_AUTH_PROMPT, True)


def _ask_root_ssh(read_line, write) -> bool:
    _section(read_line, write, "Root SSH Access")
    _say(read_line, write, _CLOUD_INIT_ROOT, "note")
    return _ask_bool(read_line, write, _ROOT_SSH_PROMPT, True)


@dataclass(frozen=True)
class GuestSettings:
    """What the defaults step decided. Guest prep is always on."""

    password_auth: bool
    permit_root: bool
    bridge: str
    memory_mb: int
    cores: int
    make_template: bool


def _defaults_table(*, hardware: bool, convert: bool) -> list[tuple[str, str]]:
    """Every remaining default this path would have asked, one row each. Guest prep always runs."""
    table = [("Setting", "Default"), ("Password SSH", "yes"), ("Root SSH", "yes")]
    if hardware:
        table.extend(
            (
                ("Bridge", DEFAULT_BRIDGE),
                ("Memory", f"{DEFAULT_MEMORY_MB} MB"),
                ("Cores", str(DEFAULT_CORES)),
            )
        )
    if convert:
        table.append(("qm template", "yes"))
    return table


def _defaults_notes(*, hardware: bool, convert: bool) -> str:
    lines = [_CLOUD_INIT_ROOT]
    if hardware and not convert:
        lines.append("A new VM at an unused VMID is still created with qm template.")
    return "\n".join(lines)


def _ask_guest_settings(read_line, write, *, hardware: bool, convert: bool, count: int) -> GuestSettings:
    """The two SSH questions, and hardware or qm template when this path has them."""
    defaults = (DEFAULT_BRIDGE, DEFAULT_MEMORY_MB, DEFAULT_CORES)
    if not hardware and not convert:
        password_auth = _ask_password_auth(read_line, write)
        permit_root = _ask_root_ssh(read_line, write)
        return GuestSettings(password_auth, permit_root, *defaults, False)
    _section(read_line, write, "Defaults")
    _write_table(read_line, write, _defaults_table(hardware=hardware, convert=convert))
    _blank(write)
    _say(read_line, write, _defaults_notes(hardware=hardware, convert=convert), "note")
    if _ask_bool(read_line, write, "Accept these defaults?", True):
        return GuestSettings(True, True, *defaults, convert)
    password_auth = _ask_password_auth(read_line, write)
    permit_root = _ask_root_ssh(read_line, write)
    if hardware:
        bridge, memory_mb, cores = _ask_hardware(read_line, write)
    else:
        bridge, memory_mb, cores = defaults
    make_template = _ask_make_template(read_line, write, count) if convert else False
    return GuestSettings(password_auth, permit_root, bridge, memory_mb, cores, make_template)


def ask_cache_cleanup(read_line, write, *, cache: str, failed: bool) -> bool:
    """After the builds. Enter keeps working files. y deletes them.

    Disk backups are not in the cache and are never deleted. A failed
    release says the download is still in the cache, and Enter keeps it.
    """
    if failed:
        prompt = (
            f"A release failed. Working files are still in {cache}, including the download.\n"
            f"Delete working files in {cache}?\n"
        )
    else:
        prompt = f"Delete working files in {cache}?\n"
    return _ask_bool(read_line, write, prompt, False)


def _diskless(vm_has_disks, vmid: int) -> bool:
    """True only when the host says this guest has no OS disk."""
    if vm_has_disks is None:
        return False
    try:
        found = vm_has_disks(vmid)
    except Exception:
        return False
    return found is False


def _confirm_message(*, mode: str, make_template: bool, whole_vm: bool, replacing: bool, backup: bool) -> str:
    noun = "templates" if whole_vm else "disk images"
    if replacing and backup:
        return f"Your existing {noun} will be backed up, then new {'templates' if whole_vm else 'images'} created."
    if replacing:
        return f"Your existing {noun} will now be DELETED and new {'templates' if whole_vm else 'images'} created."
    if mode == "template":
        return "Your new VM templates will now be created." if make_template else "Your new VMs will now be created."
    return "Your new disk images will now be created."


def _confirm_run(read_line, write, *, message: str, word: str, danger: bool) -> None:
    """One gate. word is YES or DELETE. X exits with nothing changed."""
    _say(read_line, write, message, "danger" if danger else "body")
    if word == "DELETE":
        prompt = "Type 'DELETE' to continue or 'X' to exit now without changes: "
        accepted = {"DELETE"}
        refused = {"X", "x"}
    else:
        prompt = "Type 'YES' to continue or 'X' to exit now: "
        accepted = {"YES", "yes"}
        refused = {"X", "x", "n", "no", "NO"}
    while True:
        answer = _ask_text(read_line, write, prompt, danger_word=word if danger else None)
        if answer in accepted:
            return
        if answer in refused:
            raise PromptAbort("not confirmed")


def _one_storage(pairs: tuple[tuple[int, str], ...]) -> str:
    ids = {storage for _vmid, storage in pairs}
    if len(ids) == 1:
        return next(iter(ids))
    return ""


def _formats_cover(inserts, kept_format: tuple[tuple[int, str], ...]) -> bool:
    found = {vmid for vmid, fmt in kept_format if fmt in {"raw", "qcow2"}}
    return bool(inserts) and set(inserts) <= found


def _one_format(pairs: tuple[tuple[int, str], ...]) -> str:
    """One format for the job field. Imports still use each VMID's own."""
    if not pairs:
        return "qcow2"
    return pairs[0][1]


def _format_for_storages(read_line, write, storages: list[str], kinds: dict[str, str]) -> str:
    """One format question for the kept boot disks, when their format could not be read."""
    first = storages[0] if storages else ""
    return _ask_volume_format(read_line, write, first, kinds.get(first, ""))


def _replacement_storage(disk_storage, vmids, destroy) -> tuple[tuple[str, ...], str]:
    """Notes and the storage Enter keeps when a template VMID already exists.

    No callback means no recommendation. Differing disks: Enter keeps the first
    one that could be read.
    """
    if disk_storage is None:
        return (), ""
    notes: list[str] = []
    found: list[str] = []
    for vmid in vmids:
        if vmid not in destroy:
            continue
        storage = _lookup_disk_storage(disk_storage, vmid)
        if storage is None:
            notes.append(f"Could not read the disk storage for VMID {vmid}.")
            continue
        if not storage:
            notes.append(f"VMID {vmid} has no OS disk.")
            continue
        notes.append(f"VMID {vmid} is on {storage}.")
        found.append(storage)
    if not found:
        return tuple(notes), ""
    return tuple(notes), found[0]


def _image_storage(
    read_line,
    write,
    list_storages,
    volume_format_for,
    kinds: dict[str, str],
    vmids,
    template_vmids,
    kept: tuple[tuple[int, str], ...],
    kept_format: tuple[tuple[int, str], ...],
    disk_storage,
):
    """Storage and format for an image run that names VMIDs.

    A replaced boot disk keeps the storage and the format read from its
    config. A new VM in the same run still asks. Without a disk read, the
    old questions stay.
    """
    inserts = [vmid for vmid in vmids if vmid not in template_vmids]
    known = {vmid for vmid, _name in kept}
    covered = _formats_cover(inserts, kept_format)
    if disk_storage is None or not inserts or not set(inserts) <= known:
        storage = _ask_storage(read_line, write, list_storages)
        return storage, volume_format_for(storage), (), ()
    if template_vmids:
        storage = _ask_storage(
            read_line,
            write,
            list_storages,
            recommended=kept[0][1],
            notes=tuple(f"VMID {vmid} is on {name}" for vmid, name in kept),
            preface=(
                "A replaced boot disk stays on its storage. "
                "This choice is for the new VM.\n"
            ),
        )
        asked = volume_format_for(storage)
        return storage, asked, kept, kept_format if covered else ()
    formats = dict(kept_format)
    _blank(write)
    for vmid, name in kept:
        if covered:
            _say(read_line, write, f"VMID {vmid} is on {name}. The volume is {formats[vmid]}.", "note")
        else:
            _say(read_line, write, f"VMID {vmid} is on {name}.", "note")
    if covered:
        return _one_storage(kept), _one_format(kept_format), kept, kept_format
    names = [name for _vmid, name in kept]
    return _one_storage(kept), _format_for_storages(read_line, write, names, kinds), kept, ()


@dataclass(frozen=True)
class SummaryRow:
    """One release in the summary. Empty strings are columns this path does not have."""

    release: str
    vmid: int | None
    name: str
    storage: str
    disk_format: str
    file: str
    existing: str


# What happens to a VMID that already exists. Without a backup it is drawn as danger.
_EXISTING = {
    (True, True): "back up, delete VM",
    (True, False): "delete VM",
    (False, True): "back up, replace",
    (False, False): "replace",
}


def _existing(vmid: int | None, replace_ids, backup_vmids, *, whole_vm: bool) -> str:
    if vmid is None:
        return ""
    if vmid not in replace_ids:
        return "new"
    return _EXISTING[(whole_vm, vmid in backup_vmids)]


def _summary_table(mode: str, rows: list[SummaryRow]) -> list[tuple[str, ...]]:
    """Header row, then one row per release. Columns follow the path."""
    if rows and all(row.vmid is None for row in rows):
        table = [("Release", "Format", "File")]
        table.extend((row.release, row.disk_format, row.file) for row in rows)
        return table
    if mode == "template":
        table = [("Release", "VMID", "Name", "Storage", "Format", "Existing")]
        table.extend(
            (row.release, str(row.vmid), row.name, row.storage, row.disk_format, row.existing)
            for row in rows
        )
        return table
    table = [("Release", "VMID", "Storage", "Format", "Existing disk")]
    table.extend(
        (row.release, str(row.vmid), row.storage, row.disk_format, row.existing) for row in rows
    )
    return table


def _write_table(read_line, write, table: list[tuple[str, ...]]) -> None:
    """Aligned columns at the margin. The header row is note grey. A destructive cell is danger."""
    widths = [max(len(row[index]) for row in table) for index in range(len(table[0]))]
    colored = ui.enabled(read_line)
    for number, row in enumerate(table):
        cells = [cell.ljust(widths[index]) for index, cell in enumerate(row)]
        line = ui.MARGIN + "  ".join(cells).rstrip()
        if not colored:
            write(line + "\n")
        elif number == 0:
            write(ui.tone(line, "note") + "\n")
        elif row[-1] in {_EXISTING[(True, False)], _EXISTING[(False, False)]}:
            head = ui.MARGIN + "  ".join(cells[:-1]) + "  "
            write(ui.tone(head, "body") + ui.tone(row[-1], "danger") + "\n")
        else:
            write(ui.tone(line, "body") + "\n")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _summary_footer(
    *,
    password_auth: bool,
    permit_root: bool,
    hardware: tuple[str, int, int] | None,
    make_template: bool | None,
) -> str:
    """Guest prep always runs. Hardware and qm template only where the path asks them."""
    parts = [
        "Guest prep: yes",
        f"Password SSH: {_yes_no(password_auth)}",
        f"Root SSH: {_yes_no(permit_root)}",
    ]
    if make_template is not None:
        parts.append(f"qm template: {_yes_no(make_template)}")
    line = "   ".join(parts)
    if hardware is None:
        return line
    bridge, memory_mb, cores = hardware
    return f"{line}\nHardware: bridge {bridge}, memory {memory_mb} MB, cores {cores}"


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
    disk_storage=None,
    disk_format_of=None,
    import_storages=None,
) -> Job:
    """Walk the operator through one job.

    read_line() -> str, and raises EOFError on EOF. None is also EOF.
    Empty answers accept the default shown in that question, when it has one.
    A bad answer asks the same question again. YES starts the run. X exits.
    """
    write = _Output(write, paint=ui.enabled(read_line))
    build = _ask_build(read_line, write)
    distro = _ask_distro(read_line, write, releases_for)
    releases = _ask_releases(read_line, write, distro, releases_for, normalize_release)
    mode = "template" if build == "template" else "image"
    count = len(releases)

    kinds: dict[str, str] = {}
    if storage_kinds is not None:
        try:
            kinds = {str(key): str(value) for key, value in dict(storage_kinds() or {}).items()}
        except Exception:
            kinds = {}

    def volume_format_for(storage: str) -> str:
        return _ask_volume_format(read_line, write, storage, kinds.get(storage, ""))

    def file_storages() -> list[str]:
        return _file_storage_ids(read_line, write, list_storages, kinds)

    named = _named_releases(distro, releases, releases_for)
    template_vmids: frozenset[int] = frozenset()
    kept: tuple[tuple[int, str], ...] = ()
    kept_format: tuple[tuple[int, str], ...] = ()
    if build == "template":
        vmids, destroy_vmids = _ask_template_vmids(
            read_line, write, named, vmids_in_use, guest_role
        )
        dest_dir = ""
        notes, recommended = _replacement_storage(disk_storage, vmids, destroy_vmids)
        if recommended and _storage_refusal(recommended, kinds):
            # The old template is destroyed. Its replacement goes on file storage.
            notes = (*notes, f"{recommended} is not file storage. Pick where the new disk goes.")
            recommended = ""
        storage = _ask_storage(
            read_line,
            write,
            file_storages,
            recommended=recommended,
            notes=notes,
        )
        disk_format = volume_format_for(storage)
    elif build == "update":
        vmids, template_vmids, kept, kept_format = _ask_image_vmids(
            read_line,
            write,
            named,
            vmids_in_use,
            guest_role,
            disk_storage,
            disk_format_of,
            kinds,
        )
        destroy_vmids = frozenset()
        dest_dir = ""
        storage, disk_format, kept, kept_format = _image_storage(
            read_line,
            write,
            file_storages,
            volume_format_for,
            kinds,
            vmids,
            template_vmids,
            kept,
            kept_format,
            disk_storage,
        )
    elif build == "standalone":
        vmids = ()
        template_vmids = frozenset()
        kept = ()
        kept_format = ()
        destroy_vmids = frozenset()
        import_id, dest_dir = _ask_import_storage(read_line, write, import_storages)
        storage = ""
        disk_format = _ask_format(read_line, write, f"Choose the image format in {import_id} import/.")
    else:
        raise ValueError(f"unknown build {build}")

    hardware = mode == "template" or bool(template_vmids)
    convert = mode == "template"
    guest = _ask_guest_settings(
        read_line,
        write,
        hardware=hardware,
        convert=convert,
        count=count,
    )
    clean_cache = False

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
        _section(read_line, write, "Backup existing images")
        for vmid in replace_ids:
            if _diskless(vm_has_disks, vmid):
                _say(read_line, write, f"VMID {vmid} has no OS disk.", "warning")
        backup = _ask_bool(read_line, write, "Create a backup copy of existing images?", True)
        if backup:
            _selected(read_line, write, f"Image backups will be created at {DEFAULT_BACKUPS}")
        else:
            _selected(read_line, write, "No backup images will be created.", "warning")
    backup_vmids = frozenset(replace_ids) if backup else frozenset()
    collision = _collision_for(replace_ids, backup_vmids)

    if mode == "image" and not vmids:
        paired: list[tuple[str, int | None]] = [(release, None) for release in releases]
    else:
        paired = list(zip(releases, vmids))
    labels = dict(named)
    rows = []
    for release, vmid in paired:
        rows.append(
            SummaryRow(
                release=labels.get(release, release),
                vmid=vmid,
                name=vm_name(distro, release) if vmid is not None else "",
                storage=dict(kept).get(vmid, storage) if vmid is not None else "",
                disk_format=dict(kept_format).get(vmid, disk_format) if vmid is not None else disk_format,
                file=str(Path(dest_dir) / published_name(distro, release, disk_format)) if vmid is None else "",
                existing=_existing(vmid, replace_ids, backup_vmids, whole_vm=whole_vm),
            )
        )
    _section(read_line, write, "Summary")
    _write_table(read_line, write, _summary_table(mode, rows))
    _blank(write)
    _say(
        read_line,
        write,
        _summary_footer(
            password_auth=guest.password_auth,
            permit_root=guest.permit_root,
            hardware=(guest.bridge, guest.memory_mb, guest.cores) if hardware else None,
            make_template=guest.make_template if mode == "template" else None,
        ),
        "note",
    )

    _section(read_line, write, "Confirm Selections")
    if dry_run:
        _say(read_line, write, "Dry run: commands are printed, nothing changes.", "note")
        _blank(write)
    replacing = bool(replace_ids)
    _confirm_run(
        read_line,
        write,
        message=_confirm_message(
            mode=mode,
            make_template=guest.make_template,
            whole_vm=whole_vm,
            replacing=replacing,
            backup=backup,
        ),
        word="DELETE" if replacing and not backup else "YES",
        danger=replacing and not backup,
    )

    return Job(
        distro=distro,
        releases=releases,
        mode=mode,
        disk_format=disk_format,
        dest_dir=dest_dir,
        storage=storage,
        cache_dir=DEFAULT_CACHE,
        backup_dir=DEFAULT_BACKUPS,
        collision=collision,
        vmids=vmids,
        prep=True,
        bridge=guest.bridge,
        memory_mb=guest.memory_mb,
        cores=guest.cores,
        dry_run=dry_run,
        destroy_vmids=destroy_vmids,
        backup_vmids=backup_vmids,
        make_template=guest.make_template,
        clean_cache=clean_cache,
        template_vmids=template_vmids,
        password_auth=guest.password_auth,
        permit_root=guest.permit_root,
        kept_storage=kept,
        kept_format=kept_format,
    )
