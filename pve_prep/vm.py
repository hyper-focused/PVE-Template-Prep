"""Proxmox qm command builders and config parsers. No live qm calls."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class VmError(Exception):
    """Proxmox VM operation failed."""


class VmDestroyedError(VmError):
    """A template VM was destroyed and the replacement did not finish."""


_BUS_KEY = re.compile(r"^(?:scsi|virtio|sata|ide|nvme)[0-9]+$")
_DISK_BACKUP_NAME = re.compile(
    r"^vm-[0-9]+-(?:scsi|virtio|sata|ide|nvme)[0-9]+"
    r"\.bak\.[0-9]{8}T[0-9]{6}Z\.qcow2$"
)
_UNUSED_KEY = re.compile(r"^unused[0-9]+$")
_SKIP_MARKERS = ("cloudinit", "media=cdrom")
Run = Callable[[list[str]], object]


# File plugins hold a disk as a .qcow2 or .raw file. CIFS is one of them: a share,
# not a block device. Block storage (ZFS, LVM, RBD, iSCSI) is not supported.
_FILE_STORAGE_TYPES = frozenset({"dir", "nfs", "cifs", "btrfs", "cephfs", "glusterfs"})


def is_file_storage(storage_type: str) -> bool:
    """True when this pvesm type stores disks as files. Unknown is False."""
    return (storage_type or "").strip().casefold() in _FILE_STORAGE_TYPES


def parse_storage_ids(pvesm_status_text: str) -> list[str]:
    """Return storage names whose pvesm status is active."""
    return list(parse_storage_kinds(pvesm_status_text))


def parse_storage_kinds(pvesm_status_text: str) -> dict[str, str]:
    """Active storage id to its pvesm type. Header and inactive rows are skipped."""
    found: dict[str, str] = {}
    for raw in pvesm_status_text.splitlines():
        fields = raw.split()
        if len(fields) < 3 or fields[0] == "Name":
            continue
        if fields[2] == "active":
            found[fields[0]] = fields[1]
    return found


def vmid_config_missing(returncode: int, output: str) -> bool:
    """True when qm status failed because this VMID has no config."""
    if returncode == 0:
        return False
    return "does not exist" in output.casefold()


def parse_qm_status(text: str) -> str:
    """Return a qm status token, or '' when text is empty or garbage."""
    if not text or not text.strip():
        return ""
    line = text.strip().splitlines()[0].strip()
    if ":" in line:
        key, _, value = line.partition(":")
        if key.strip() != "status":
            return ""
        tokens = value.split()
        return tokens[0] if tokens else ""
    parts = line.split()
    if len(parts) == 1:
        return parts[0]
    return ""


def _key_rest(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if ": " not in stripped:
        return None
    key, rest = stripped.split(": ", 1)
    key = key.strip()
    rest = rest.strip()
    if not key or not rest:
        return None
    return key, rest


def _iter_os_disks(config_text: str, storage: str):
    prefix = f"{storage}:"
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key, rest = parsed
        if _BUS_KEY.fullmatch(key) is None:
            continue
        volid, _sep, options = rest.partition(",")
        if any(marker in volid or marker in rest for marker in _SKIP_MARKERS):
            continue
        if not volid.startswith(prefix):
            continue
        yield key, volid, options


def _os_disk_parts(config_text: str, storage: str) -> tuple[str, str, str] | None:
    return next(_iter_os_disks(config_text, storage), None)


def parse_os_disk(config_text: str, storage: str) -> tuple[str, str] | None:
    """Return the first (bus key, volid) OS disk on storage, or None."""
    found = _os_disk_parts(config_text, storage)
    if found is None:
        return None
    bus_key, volid, _options = found
    return bus_key, volid


def list_os_disks(config_text: str) -> list[tuple[str, str]]:
    """Return (bus key, volid) for OS disks on any storage.

    Cloud-init and cdrom volumes are not OS disks.
    """
    found: list[tuple[str, str]] = []
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key, rest = parsed
        if _BUS_KEY.fullmatch(key) is None:
            continue
        volid = rest.split(",", 1)[0].strip()
        if any(marker in volid or marker in rest for marker in _SKIP_MARKERS):
            continue
        found.append((key, volid))
    return found


def config_has_os_disk(config_text: str) -> bool:
    """True when the guest config has an OS disk on any storage."""
    return bool(list_os_disks(config_text))


def is_template(config_text: str) -> bool:
    """True when the guest config is a Proxmox template."""
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None or parsed[0] != "template":
            continue
        token = parsed[1].split(None, 1)[0]
        return token == "1"
    return False


_CLUSTER_NODES = Path("/etc/pve/nodes")
_VOLUME_KEY = re.compile(
    r"^(?:scsi|virtio|sata|ide|nvme|efidisk|tpmstate|unused)[0-9]+$"
)


@dataclass(frozen=True)
class InsertResult:
    """Where an imported disk landed.

    kept_template means the guest was already a template and still is.
    backup_dir is set when the old disk was copied into the prep cache.
    """

    volid: str
    kept_template: bool = False
    backup_dir: str = ""


def _linked_child(template_vmid: int) -> re.Pattern[str]:
    """Child of this template's base volume. The template disk itself does not match.

    Directory: NFS:9001/base-9001-disk-0.raw/122/vm-122-disk-0.qcow2
    ZFS: vm-data:base-910-disk-0/vm-201-disk-0
    The template disk stops at the base name: NFS:9001/base-9001-disk-0.raw
    base-901-disk does not match base-9010-disk.
    """
    return re.compile(rf"base-{int(template_vmid)}-disk-\d+(?:\.[A-Za-z0-9]+)?/")


def _is_linked_child(volid: str, template_vmid: int) -> bool:
    return _linked_child(template_vmid).search(volid) is not None


_LIST_ROW = re.compile(r"^(\S+)\s+\S+\s+\S+\s+~?\d+(?:\s+(\d+))?\s*$")


def _storage_of(volid: str) -> str:
    storage, sep, _name = volid.partition(":")
    if not sep or not storage or "/" in storage or " " in storage:
        return ""
    return storage


def _config_volids(config_text: str) -> list[str]:
    """Live volume ids. Snapshot sections, cloud-init, and cdrom are skipped."""
    found: list[str] = []
    in_section = False
    for raw in config_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = True
            continue
        if in_section:
            continue
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key, rest = parsed
        if _VOLUME_KEY.fullmatch(key) is None:
            continue
        volid = rest.split(",", 1)[0].strip()
        if not volid or any(marker in volid or marker in rest for marker in _SKIP_MARKERS):
            continue
        found.append(volid)
    return found


def is_linked_clone(config_text: str) -> bool:
    """True when a volume is a child of a template base, not the base itself.

    The template disk is NFS:9001/base-9001-disk-0.raw.
    A clone continues past that name: NFS:9001/base-9001-disk-0.raw/122/vm-122-disk-0.qcow2.
    """
    child = re.compile(r"base-\d+-disk-\d+(?:\.[A-Za-z0-9]+)?/")
    return any(child.search(volid) for volid in _config_volids(config_text))


def _config_storages(config_text: str) -> list[str]:
    storages: list[str] = []
    seen: set[str] = set()
    for volid in _config_volids(config_text):
        storage = _storage_of(volid)
        if storage and storage not in seen:
            seen.add(storage)
            storages.append(storage)
    return storages


def _owner_label(owner: object, volid: str, template_vmid: int) -> str | None:
    """None when this volume belongs to the template. Snapshots use its VMID."""
    if owner is not None and str(owner).strip() != "":
        if str(owner) == str(template_vmid):
            return None
        return f"VM {owner} ({volid})"
    return volid


def _owners_from_json(list_json: str, template_vmid: int) -> list[str]:
    try:
        payload = json.loads(list_json)
    except json.JSONDecodeError as exc:
        raise VmError(
            f"VM {template_vmid} linked-clone list was not json; "
            "the template was not changed"
        ) from exc
    if isinstance(payload, dict):
        payload = payload.get("data", [])
    if not isinstance(payload, list):
        raise VmError(
            f"VM {template_vmid} linked-clone list was not a list; "
            "the template was not changed"
        )
    found: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        volid = str(item.get("volid") or "")
        if not _is_linked_child(volid, template_vmid):
            continue
        label = _owner_label(item.get("vmid"), volid, template_vmid)
        if label:
            found.append(label)
    return found


def parse_linked_clone_owners(list_text: str, *, template_vmid: int) -> list[str]:
    """Guests, other than this template, whose volumes are linked clones of it.

    A hit is a child volid: the base disk is a path component and the clone
    disk follows it. A parent field alone is not a hit. Full clones are
    vm-<id>-disk-N and do not match. pvesm list on PVE 9.2 prints a table.
    JSON is accepted when a caller already has it. A same-VMID volume is a
    snapshot of the template.
    """
    stripped = (list_text or "").strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        return _owners_from_json(stripped, template_vmid)
    if not stripped.startswith("Volid"):
        raise VmError(
            f"VM {template_vmid} linked-clone list was not readable; "
            "the template was not changed"
        )
    found: list[str] = []
    for raw in stripped.splitlines():
        match = _LIST_ROW.match(raw.strip())
        if match is None:
            continue
        volid, owner = match.group(1), match.group(2)
        if not _is_linked_child(volid, template_vmid):
            continue
        label = _owner_label(owner, volid, template_vmid)
        if label:
            found.append(label)
    return found


def cluster_config_links(nodes_root: Path, template_vmid: int) -> list[str]:
    """Other guests whose live disk is a linked clone of this template.

    Configs under /etc/pve/nodes are replicated, so a linked clone on another
    node is visible here. A full clone is not. A snapshot section that still
    names the old base volume is not.
    """
    if not nodes_root.is_dir():
        raise VmError(
            f"VM {template_vmid} linked-clone check cannot read {nodes_root}; "
            "the template was not changed"
        )
    found: list[str] = []
    for conf in sorted(nodes_root.glob("*/qemu-server/*.conf")):
        if conf.is_symlink() or not conf.is_file():
            continue
        if conf.stem == str(template_vmid):
            continue
        try:
            text = conf.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise VmError(
                f"VM {template_vmid} linked-clone check cannot read {conf}; "
                "the template was not changed"
            ) from exc
        if not any(_is_linked_child(volid, template_vmid) for volid in _config_volids(text)):
            continue
        node = conf.parent.parent.name
        found.append(f"VM {conf.stem} on {node}")
    return found


def _nodes_root(nodes_root: Path | None) -> Path:
    if nodes_root is None:
        return _CLUSTER_NODES
    return Path(nodes_root)


def _command_text(result: object) -> str:
    stderr = getattr(result, "stderr", "") or ""
    stdout = getattr(result, "stdout", "") or ""
    return f"{stderr}\n{stdout}"


def _storage_listing(run: Run, storage: str) -> object:
    """Volume list for one storage.

    PVE 9.2 pvesm list rejects --output-format and then prints a usage line.
    The option name is not on that last line, so the full output is checked.
    The table from a plain list still has the volid. Any other failure is
    returned as-is so the caller can refuse the change.
    """
    listed = run(["pvesm", "list", storage, "--output-format", "json"])
    if getattr(listed, "returncode", 1) == 0:
        return listed
    detail = _command_text(listed).casefold()
    if "output-format" in detail or "unable to parse option" in detail:
        return run(["pvesm", "list", storage])
    return listed


def _refuse_linked_clones(
    run: Run,
    vmid: int,
    config_text: str,
    nodes_root: Path | None,
) -> None:
    """Raise before any change when a linked clone exists anywhere in the cluster."""
    hits = cluster_config_links(_nodes_root(nodes_root), vmid)
    if hits:
        shown = ", ".join(dict.fromkeys(hits))
        raise VmError(f"VM {vmid} has linked clones ({shown}); the template was not changed")
    for storage in _config_storages(config_text):
        listed = _storage_listing(run, storage)
        if getattr(listed, "returncode", 1) != 0:
            raise VmError(
                f"VM {vmid} linked-clone check failed for {storage}; "
                f"the template was not changed: {_detail(listed)}"
            )
        hits.extend(
            parse_linked_clone_owners(
                getattr(listed, "stdout", "") or "",
                template_vmid=vmid,
            )
        )
    if not hits:
        return
    shown = ", ".join(dict.fromkeys(hits))
    raise VmError(f"VM {vmid} has linked clones ({shown}); the template was not changed")


def refuse_replace_target(
    run: Run,
    vmid: int,
    config_text: str,
    nodes_root: Path | None,
) -> None:
    """Raise before a template replace or a template disk replace.

    A normal VM is refused. A guest that is itself a linked clone is refused.
    A template that still has linked clones, on this node or another, is refused.
    An unreadable check is a refusal.
    """
    if is_linked_clone(config_text):
        raise VmError(f"VM {vmid} is a linked clone; it was not changed")
    if not is_template(config_text):
        raise VmError(f"VM {vmid} is not a template; it was not changed")
    _refuse_linked_clones(run, vmid, config_text, nodes_root)


def boot_devices(config_text: str) -> list[str] | None:
    """Disk bus keys from `boot:`, or None when the guest has no boot line."""
    devices: list[str] | None = None
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None or parsed[0] != "boot":
            continue
        devices = []
        rest = parsed[1]
        if "order=" in rest:
            rest = rest.split("order=", 1)[1]
        rest = rest.split(",", 1)[0]
        for part in rest.split(";"):
            bus = part.strip()
            if _BUS_KEY.fullmatch(bus):
                devices.append(bus)
    return devices


def select_boot_disk(config_text: str) -> tuple[str, str] | None:
    """Boot OS disk on any storage. Falls back to the first OS disk."""
    disks: dict[str, tuple[str, str]] = {}
    for bus, volid in list_os_disks(config_text):
        disks.setdefault(bus, (bus, volid))
    order = boot_devices(config_text)
    if order:
        for bus in order:
            if bus in disks:
                return disks[bus]
    if not disks:
        return None
    return next(iter(disks.values()))


def os_disk_storage(config_text: str) -> str:
    """Storage id of the boot OS disk. Empty when the guest has no OS disk."""
    found = select_boot_disk(config_text)
    if found is None:
        return ""
    return _storage_of(found[1])


def volume_format_of(volid: str, options: str = "") -> str:
    """raw or qcow2 from one qm disk. Empty when the format is not one of those.

    format= wins over the filename. A volume with no suffix is a block
    volume, and Proxmox allocates those as raw.
    """
    for part in options.split(","):
        token = part.strip()
        if not token.startswith("format="):
            continue
        value = token.split("=", 1)[1].strip().casefold()
        if value in {"raw", "qcow2"}:
            return value
        return ""
    name = volid.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if name.endswith(".qcow2"):
        return "qcow2"
    if name.endswith(".raw"):
        return "raw"
    if "." not in name:
        return "raw"
    return ""


def os_disk_format(config_text: str) -> str:
    """Format of the boot OS disk. Empty when it is missing or not raw or qcow2."""
    found = select_boot_disk(config_text)
    if found is None:
        return ""
    bus, volid = found
    options = ""
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None or parsed[0] != bus:
            continue
        line_volid, _sep, rest = parsed[1].partition(",")
        if line_volid.strip() != volid:
            continue
        options = rest
        break
    return volume_format_of(volid, options)


def storage_lacks_images(cfg_text: str, storage: str) -> bool:
    """True when this storage id is missing, or its content tokens omit images.

    The tokens belong to the storage. They are not storage ids themselves.
    """
    found = parse_storage_content(cfg_text)
    if storage not in found:
        return True
    return "images" not in found[storage]


def _storage_sections(cfg_text: str):
    """Yield (storage id, indented body lines) in file order."""
    current: str | None = None
    body: list[str] = []
    for raw in cfg_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[:1].isspace():
            if current:
                body.append(stripped)
            continue
        if current:
            yield current, body
        body = []
        if ":" not in stripped:
            current = None
            continue
        _kind, _, name = stripped.partition(":")
        current = name.strip()
    if current:
        yield current, body


def parse_storage_content(cfg_text: str) -> dict[str, set[str]]:
    """Map storage id to content tokens from storage.cfg."""
    found: dict[str, set[str]] = {}
    for name, body in _storage_sections(cfg_text):
        tokens: set[str] = set()
        for stripped in body:
            if stripped.startswith("content "):
                tokens.update(stripped.split(None, 1)[1].replace(",", " ").split())
        found[name] = tokens
    return found


def parse_storage_paths(cfg_text: str) -> dict[str, str]:
    """Map storage id to its path. Ids without a path are omitted."""
    found: dict[str, str] = {}
    for name, body in _storage_sections(cfg_text):
        for stripped in body:
            if stripped.startswith("path "):
                path = stripped.split(None, 1)[1].strip()
                if path:
                    found[name] = path
    return found


def import_directories(cfg_text: str) -> list[tuple[str, str]]:
    """Storages whose content includes import, as (id, {path}/import).

    File order. A storage with import content and no path is skipped.
    """
    content = parse_storage_content(cfg_text)
    paths = parse_storage_paths(cfg_text)
    found: list[tuple[str, str]] = []
    for storage, tokens in content.items():
        if "import" not in tokens:
            continue
        path = paths.get(storage, "").strip()
        if not path:
            continue
        found.append((storage, str(Path(path) / "import")))
    return found


def unused_volumes(config_text: str) -> list[tuple[str, str]]:
    """Return (unused key, volid) pairs. Options after the volid are dropped."""
    found: list[tuple[str, str]] = []
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key, rest = parsed
        if _UNUSED_KEY.fullmatch(key):
            found.append((key, rest.split(",", 1)[0].strip()))
    return found


def parse_imported_volid(config_text: str) -> str | None:
    """Return the volid from the first unusedN line, or None."""
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key, rest = parsed
        if _UNUSED_KEY.fullmatch(key):
            return rest
    return None


def importdisk_command(
    vmid: int,
    image_path: str,
    storage: str,
    volume_format: str,
) -> list[str]:
    """qm importdisk with the format the storage should allocate."""
    if volume_format not in {"raw", "qcow2"}:
        raise VmError(f"unsupported volume format: {volume_format}")
    return ["qm", "importdisk", str(vmid), image_path, storage, "--format", volume_format]


def template_commands(
    *,
    vmid: int,
    name: str,
    memory_mb: int,
    cores: int,
    bridge: str,
    storage: str,
    image_path: str,
    imported_volid: str,
    make_template: bool = True,
    volume_format: str = "raw",
) -> list[list[str]]:
    """Return qm argv lists that build a cloud-init VM from an image.

    make_template appends qm template. Without it the VM stays bootable.
    """
    vid = str(vmid)
    commands = [
        [
            "qm",
            "create",
            vid,
            "--name",
            name,
            "--memory",
            str(memory_mb),
            "--cores",
            str(cores),
            "--machine",
            "q35",
            "--cpu",
            "x86-64-v2-AES",
            "--net0",
            f"virtio,bridge={bridge}",
            "--scsihw",
            "virtio-scsi-single",
            "--ostype",
            "l26",
            "--agent",
            "enabled=1",
            "--serial0",
            "socket",
            "--vga",
            "serial0",
            "--rng0",
            "source=/dev/urandom",
        ],
        importdisk_command(vmid, image_path, storage, volume_format),
        ["qm", "set", vid, "--scsi0", f"{imported_volid},discard=on,ssd=1"],
        ["qm", "set", vid, "--ide2", f"{storage}:cloudinit"],
        ["qm", "set", vid, "--boot", "order=scsi0"],
    ]
    if make_template:
        commands.append(["qm", "template", vid])
    return commands


def _detail(result: object) -> str:
    stderr = getattr(result, "stderr", "") or ""
    stdout = getattr(result, "stdout", "") or ""
    text = (stderr or stdout).strip()
    code = getattr(result, "returncode", 1)
    if not text:
        return f"exit {code}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chosen = lines[-1]
    # pvesm appends its usage synopsis. The useful line is the one above it.
    if chosen.startswith("pvesm ") and "[OPTIONS]" in chosen:
        for line in lines:
            if not (line.startswith("pvesm ") and "[OPTIONS]" in line):
                chosen = line
                break
    return f"{chosen[:400]} (exit {code})"


def _must(run: Run, argv: list[str], *, vmid: int, destroyed: bool) -> object:
    result = run(argv)
    if getattr(result, "returncode", 1) != 0:
        message = f"VM {vmid} failed at {' '.join(argv)}: {_detail(result)}"
        if destroyed:
            raise VmDestroyedError(message)
        raise VmError(message)
    return result


def _fail_import(
    run: Run,
    *,
    vmid: int,
    storage: str,
    imported: object,
    config_text: str,
    created_now: bool,
    destroyed: bool,
) -> None:
    volid = parse_imported_volid(config_text)
    if not created_now:
        extra = f"; volume {volid} left unused" if volid else "; existing disks left in place"
        raise VmError(f"VM {vmid} import failed{extra}: {_detail(imported)}")
    disk = parse_os_disk(config_text, storage)
    if volid or disk:
        shown = volid or disk[1]
        raise VmError(f"VM {vmid} import failed; volume {shown} left in place: {_detail(imported)}")
    removed = run(["qm", "destroy", str(vmid)])
    if destroyed:
        raise VmDestroyedError(
            "VM "
            f"{vmid} was destroyed and the replacement import failed; "
            f"empty VM removed: {_detail(imported)}"
        )
    if getattr(removed, "returncode", 1) != 0:
        raise VmError(
            f"VM {vmid} import failed and the empty VM could not be removed: {_detail(removed)}"
        )
    raise VmError(f"VM {vmid} import failed; the empty VM was removed: {_detail(imported)}")


def _import_volume(
    run: Run,
    *,
    vmid: int,
    image_path: str,
    storage: str,
    volume_format: str,
    created_now: bool,
    destroyed: bool,
) -> str:
    imported = run(importdisk_command(vmid, image_path, storage, volume_format))
    cfg = run(["qm", "config", str(vmid)])
    config_text = getattr(cfg, "stdout", "") or ""
    volid = parse_imported_volid(config_text)
    if getattr(imported, "returncode", 1) != 0 or not volid:
        _fail_import(
            run,
            vmid=vmid,
            storage=storage,
            imported=imported,
            config_text=config_text,
            created_now=created_now,
            destroyed=destroyed,
        )
    return volid


def _path_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def is_disk_backup(name: str) -> bool:
    """True for a template OS-disk copy written into the prep cache.

    Cache cleanup uses this so a later delete cannot remove the only copy
    of a VM that was destroyed.
    """
    return _DISK_BACKUP_NAME.fullmatch(name) is not None


def disk_backup_name(vmid: int, bus: str, stamp: str) -> str:
    """File name of one OS-disk backup. Refuses a name cleanup would delete."""
    name = f"vm-{vmid}-{bus}.bak.{stamp}.qcow2"
    if not is_disk_backup(name):
        raise VmError(f"refusing disk backup name {name}")
    return name


def _backup_dest(backup_dir: str, vmid: int, bus: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return str(Path(backup_dir) / disk_backup_name(vmid, bus, stamp))


def stdout_is_tty() -> bool:
    """True when qemu-img can print its own progress on this stdout."""
    import sys

    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def qemu_img_convert_argv(
    src: str,
    dest: str,
    disk_format: str,
    *,
    progress: bool | None = None,
) -> list[str]:
    """qemu-img convert argv. -p only when stdout is a terminal."""
    if progress is None:
        progress = stdout_is_tty()
    argv = ["qemu-img", "convert"]
    if progress:
        argv.append("-p")
    argv.extend(["-O", disk_format, src, dest])
    return argv


def _backup_os_disks(run: Run, *, vmid: int, config_text: str, backup_dir: str) -> int:
    """Copy each OS disk to backup_dir. The VM is left alone.

    Returns how many disks were copied. Zero disks is success and copies nothing.
    A convert that exits 0 still fails when the destination is missing or empty.
    """
    disks = list_os_disks(config_text)
    if not disks:
        return 0
    if not backup_dir:
        raise VmError(f"VM {vmid} disk backup needs a cache directory")
    for bus, volid in disks:
        located = run(["pvesm", "path", volid])
        if getattr(located, "returncode", 1) != 0:
            raise VmError(f"VM {vmid} disk path failed for {volid}: {_detail(located)}")
        source = _path_line(getattr(located, "stdout", "") or "")
        if not source:
            raise VmError(f"VM {vmid} disk path for {volid} was empty")
        dest = _backup_dest(backup_dir, vmid, bus)
        copied = run(qemu_img_convert_argv(source, dest, "qcow2"))
        if getattr(copied, "returncode", 1) != 0:
            raise VmError(f"VM {vmid} disk backup failed for {volid}: {_detail(copied)}")
        written = Path(dest)
        if not written.is_file() or written.stat().st_size <= 0:
            raise VmError(f"VM {vmid} disk backup for {volid} is missing or empty: {dest}")
        print(f"backup {volid} -> {dest}")
    return len(disks)


def next_scsi_index(config_text: str) -> int:
    """First scsi index that is not already in the config. scsi0 through scsi30."""
    used: set[int] = set()
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key = parsed[0]
        if not key.startswith("scsi"):
            continue
        suffix = key[4:]
        if suffix.isdigit():
            used.add(int(suffix))
    for index in range(31):
        if index not in used:
            return index
    raise VmError("no free scsi slot")


def _delete_unused_volid(run: Run, *, vmid: int, volid: str) -> None:
    cfg = run(["qm", "config", str(vmid)])
    if getattr(cfg, "returncode", 1) != 0:
        raise VmError(f"VM {vmid} config failed before disk cleanup: {_detail(cfg)}")
    for key, found in unused_volumes(getattr(cfg, "stdout", "") or ""):
        if found != volid:
            continue
        _must(run, ["qm", "set", str(vmid), "--delete", key], vmid=vmid, destroyed=False)


def prepare_replace(
    *,
    vmid: int,
    run: Run,
    backup: bool,
    backup_dir: str = "",
    nodes_root: Path | None = None,
) -> int:
    """Check the guest, and copy its OS disks when backup is set.

    This runs before the new image is downloaded. A failed copy leaves the
    guest alone and does not build the new image. Returns how many disks
    were copied. Zero means there was nothing to copy.
    """
    status = run(["qm", "status", str(vmid)])
    if getattr(status, "returncode", 1) != 0:
        raise VmError(f"VM {vmid} does not exist")
    state = parse_qm_status(getattr(status, "stdout", "") or "")
    if state == "running":
        raise VmError(f"VM {vmid} is running")
    if state != "stopped":
        raise VmError(f"VM {vmid} status {state or 'unknown'} cannot be replaced")
    cfg = run(["qm", "config", str(vmid)])
    if getattr(cfg, "returncode", 1) != 0:
        raise VmError(f"VM {vmid} config failed: {_detail(cfg)}")
    config_text = getattr(cfg, "stdout", "") or ""
    refuse_replace_target(run, vmid, config_text, nodes_root)
    if not backup:
        return 0
    return _backup_os_disks(run, vmid=vmid, config_text=config_text, backup_dir=backup_dir)


def create_template(
    *,
    vmid: int,
    name: str,
    memory_mb: int,
    cores: int,
    bridge: str,
    storage: str,
    image_path: str,
    dry_run: bool,
    destroy_ok: bool,
    run: Run,
    backup_disks: bool = False,
    backup_dir: str = "",
    make_template: bool = True,
    nodes_root: Path | None = None,
    volume_format: str = "raw",
) -> str:
    """Create a cloud VM via run, or print qm commands when dry_run is set.

    An existing stopped template is destroyed first when destroy_ok is set.
    A normal VM is not destroyed. A linked clone is not destroyed. A template
    that still has linked clones, on this node or another, is not destroyed.
    backup_disks copies OS disks into backup_dir before that destroy.
    make_template runs qm template after the disk is attached.
    """
    guessed = f"{storage}:vm-{vmid}-disk-0"
    planned = template_commands(
        vmid=vmid,
        name=name,
        memory_mb=memory_mb,
        cores=cores,
        bridge=bridge,
        storage=storage,
        image_path=image_path,
        imported_volid=guessed,
        make_template=make_template,
        volume_format=volume_format,
    )
    if dry_run:
        if backup_disks:
            dest = backup_dir or "."
            shown = " ".join(
                qemu_img_convert_argv(
                    "<os-disk>",
                    f"{dest}/vm-{vmid}-os.bak.qcow2",
                    "qcow2",
                )
            )
            print(f"# {shown}")
        if destroy_ok:
            print(
                f"# qm destroy {vmid}  "
                "# only if this VMID exists and is stopped"
            )
            print("# a template with linked clones on any cluster node is left unchanged")
        for cmd in planned:
            print(" ".join(cmd))
        return guessed

    status = run(["qm", "status", str(vmid)])
    destroyed = False
    if status.returncode == 0:
        state = parse_qm_status(getattr(status, "stdout", "") or "")
        if state == "running":
            raise VmError(f"VM {vmid} is running")
        if not destroy_ok:
            raise VmError(f"VM {vmid} already exists")
        if state != "stopped":
            raise VmError(f"VM {vmid} status {state or 'unknown'} cannot be replaced")
        cfg = run(["qm", "config", str(vmid)])
        if getattr(cfg, "returncode", 1) != 0:
            raise VmError(f"VM {vmid} config failed: {_detail(cfg)}")
        config_text = getattr(cfg, "stdout", "") or ""
        refuse_replace_target(run, vmid, config_text, nodes_root)
        if backup_disks:
            _backup_os_disks(
                run,
                vmid=vmid,
                config_text=config_text,
                backup_dir=backup_dir,
            )
        _must(run, ["qm", "destroy", str(vmid)], vmid=vmid, destroyed=False)
        destroyed = True

    _must(run, planned[0], vmid=vmid, destroyed=destroyed)
    volid = _import_volume(
        run,
        vmid=vmid,
        image_path=image_path,
        storage=storage,
        volume_format=volume_format,
        created_now=True,
        destroyed=destroyed,
    )
    finished = template_commands(
        vmid=vmid,
        name=name,
        memory_mb=memory_mb,
        cores=cores,
        bridge=bridge,
        storage=storage,
        image_path=image_path,
        imported_volid=volid,
        make_template=make_template,
        volume_format=volume_format,
    )
    for cmd in finished[2:]:
        _must(run, cmd, vmid=vmid, destroyed=destroyed)
    return volid


def _overwrite_boot_disk(
    run: Run,
    *,
    vmid: int,
    storage: str,
    image_path: str,
    config_text: str,
    volume_format: str,
) -> str:
    """Import image_path and point the boot disk at it. The old volume is deleted."""
    current = select_boot_disk(config_text)
    volid = _import_volume(
        run,
        vmid=vmid,
        image_path=image_path,
        storage=storage,
        volume_format=volume_format,
        created_now=False,
        destroyed=False,
    )
    attached = f"{volid},discard=on,ssd=1"
    if current is not None:
        bus, old_volid = current
        _must(run, ["qm", "set", str(vmid), f"--{bus}", attached], vmid=vmid, destroyed=False)
        _delete_unused_volid(run, vmid=vmid, volid=old_volid)
        return volid
    slot = next_scsi_index(config_text)
    _must(run, ["qm", "set", str(vmid), f"--scsi{slot}", attached], vmid=vmid, destroyed=False)
    return volid


def _replace_template_disk(
    run: Run,
    *,
    vmid: int,
    storage: str,
    image_path: str,
    disk_policy: str,
    config_text: str,
    backup_dir: str,
    volume_format: str,
    copies_done: bool = False,
) -> InsertResult:
    """Swap the boot disk and leave the guest a template.

    The template flag is cleared only for the disk edit, then qm template
    puts it back and turns the new disk into a base volume. Name, network,
    firewall, and the other options are not rewritten.
    """
    saved = ""
    if disk_policy == "backup" and (copies_done or list_os_disks(config_text)):
        if not backup_dir:
            raise VmError(f"VM {vmid} disk backup needs a cache directory")
        if not copies_done:
            _backup_os_disks(
                run,
                vmid=vmid,
                config_text=config_text,
                backup_dir=backup_dir,
            )
        saved = backup_dir
    _must(run, ["qm", "set", str(vmid), "--template", "0"], vmid=vmid, destroyed=False)
    try:
        volid = _overwrite_boot_disk(
            run,
            vmid=vmid,
            storage=storage,
            image_path=image_path,
            config_text=config_text,
            volume_format=volume_format,
        )
    except VmError as exc:
        restored = run(["qm", "template", str(vmid)])
        if getattr(restored, "returncode", 1) != 0:
            raise VmError(f"{exc}; the template flag could not be restored") from exc
        raise VmError(f"{exc}; the template flag was restored") from exc
    try:
        _must(run, ["qm", "template", str(vmid)], vmid=vmid, destroyed=False)
    except VmError as exc:
        raise VmError(f"{exc}; the VM is no longer a template") from exc
    return InsertResult(volid, kept_template=True, backup_dir=saved)


def insert_disk(
    *,
    vmid: int,
    storage: str,
    image_path: str,
    disk_policy: str,
    dry_run: bool,
    run: Run,
    backup_dir: str = "",
    nodes_root: Path | None = None,
    volume_format: str = "raw",
    copies_done: bool = False,
) -> InsertResult:
    """Replace the boot disk of a stopped template. The rest of the config stays.

    A normal VM is refused. A linked clone is refused. A template that still
    has linked clones, including clones on other cluster nodes, is not changed.
    backup copies the old disk into backup_dir unless copies_done is set.
    """
    if disk_policy not in {"backup", "overwrite"}:
        raise VmError(f"unknown disk policy {disk_policy}")
    guessed = f"{storage}:vm-{vmid}-disk-0"
    if dry_run:
        print(" ".join(importdisk_command(vmid, image_path, storage, volume_format)))
        if disk_policy == "backup":
            print(
                f"# old disk copied into the cache; "
                f"qm set {vmid} --<boot-disk> {guessed},discard=on,ssd=1"
            )
        else:
            print(
                f"# qm set {vmid} --<boot-disk> {guessed},discard=on,ssd=1  "
                "# previous disk is deleted"
            )
        print(
            f"# a template keeps its config: qm set {vmid} --template 0, "
            "replace the boot disk, qm template. "
            "Linked clones on any cluster node leave the template unchanged."
        )
        return InsertResult(guessed)

    status = run(["qm", "status", str(vmid)])
    if getattr(status, "returncode", 1) != 0:
        raise VmError(f"VM {vmid} does not exist")
    state = parse_qm_status(getattr(status, "stdout", "") or "")
    if state == "running":
        raise VmError(f"VM {vmid} is running")
    if state != "stopped":
        raise VmError(f"VM {vmid} status {state or 'unknown'} cannot take a disk")
    cfg = run(["qm", "config", str(vmid)])
    if getattr(cfg, "returncode", 1) != 0:
        raise VmError(f"VM {vmid} config failed: {_detail(cfg)}")
    config_text = getattr(cfg, "stdout", "") or ""
    refuse_replace_target(run, vmid, config_text, nodes_root)
    return _replace_template_disk(
        run,
        vmid=vmid,
        storage=storage,
        image_path=image_path,
        disk_policy=disk_policy,
        config_text=config_text,
        backup_dir=backup_dir,
        volume_format=volume_format,
        copies_done=copies_done,
    )
