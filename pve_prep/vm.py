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


def parse_storage_ids(pvesm_status_text: str) -> list[str]:
    """Return storage names whose pvesm status is active."""
    names: list[str] = []
    for raw in pvesm_status_text.splitlines():
        fields = raw.split()
        if len(fields) < 3 or fields[0] == "Name":
            continue
        if fields[2] == "active":
            names.append(fields[0])
    return names


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


def _base_token(template_vmid: int) -> re.Pattern[str]:
    # base-9014- matches. base-90140- does not.
    return re.compile(rf"base-{template_vmid}-(?!\d)")


def _storage_of(volid: str) -> str:
    storage, sep, _name = volid.partition(":")
    if not sep or not storage or "/" in storage or " " in storage:
        return ""
    return storage


def _config_volids(config_text: str) -> list[str]:
    """Volume ids named in the guest config. Cloud-init and cdrom are skipped."""
    found: list[str] = []
    for raw in config_text.splitlines():
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


def _config_storages(config_text: str) -> list[str]:
    storages: list[str] = []
    seen: set[str] = set()
    for volid in _config_volids(config_text):
        storage = _storage_of(volid)
        if storage and storage not in seen:
            seen.add(storage)
            storages.append(storage)
    return storages


def parse_linked_clone_owners(
    list_json: str,
    *,
    template_vmid: int,
    parent_volids: set[str],
) -> list[str]:
    """Guests, other than this template, whose volumes are cloned from it.

    pvesm list JSON on shared storage includes volumes owned by guests on
    other cluster nodes. A same-VMID parent is a snapshot of the template.
    """
    try:
        payload = json.loads(list_json or "[]")
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
    token = _base_token(template_vmid)
    found: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        parent = str(item.get("parent") or "")
        if not parent:
            continue
        owned = parent in parent_volids or token.search(parent) is not None
        if not owned:
            continue
        owner = item.get("vmid")
        if owner is not None and str(owner) == str(template_vmid):
            continue
        volid = str(item.get("volid") or parent)
        if owner is None or str(owner).strip() == "":
            found.append(volid)
        else:
            found.append(f"VM {owner} ({volid})")
    return found


def cluster_config_links(nodes_root: Path, template_vmid: int) -> list[str]:
    """Other guests in the cluster whose config names this template's base volume.

    Configs under /etc/pve/nodes are replicated to every node, so a linked
    clone that lives on another cluster member is visible here.
    """
    if not nodes_root.is_dir():
        raise VmError(
            f"VM {template_vmid} linked-clone check cannot read {nodes_root}; "
            "the template was not changed"
        )
    token = _base_token(template_vmid)
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
        if token.search(text) is None:
            continue
        node = conf.parent.parent.name
        found.append(f"VM {conf.stem} on {node}")
    return found


def _nodes_root(nodes_root: Path | None) -> Path:
    if nodes_root is None:
        return _CLUSTER_NODES
    return Path(nodes_root)


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
    parent_volids = set(_config_volids(config_text))
    for storage in _config_storages(config_text):
        listed = run(["pvesm", "list", storage, "--output-format", "json"])
        if getattr(listed, "returncode", 1) != 0:
            raise VmError(
                f"VM {vmid} linked-clone check failed for {storage}; "
                f"the template was not changed: {_detail(listed)}"
            )
        hits.extend(
            parse_linked_clone_owners(
                getattr(listed, "stdout", "") or "",
                template_vmid=vmid,
                parent_volids=parent_volids,
            )
        )
    if not hits:
        return
    shown = ", ".join(dict.fromkeys(hits))
    raise VmError(f"VM {vmid} has linked clones ({shown}); the template was not changed")


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


def select_os_disk(config_text: str, storage: str) -> tuple[str, str] | None:
    """OS disk named by boot order. Falls back to the first disk when boot is absent."""
    disks: dict[str, tuple[str, str]] = {}
    for bus_key, volid, _options in _iter_os_disks(config_text, storage):
        disks.setdefault(bus_key, (bus_key, volid))
    order = boot_devices(config_text)
    if order is None:
        if not disks:
            return None
        return next(iter(disks.values()))
    for bus in order:
        if bus in disks:
            return disks[bus]
    if order:
        names = ", ".join(order)
        raise VmError(f"boot order has no OS disk on {storage}: {names}")
    if not disks:
        return None
    return next(iter(disks.values()))


def storage_lacks_images(cfg_text: str, storage: str) -> bool:
    """True when this storage id is missing, or its content tokens omit images.

    The tokens belong to the storage. They are not storage ids themselves.
    """
    found = parse_storage_content(cfg_text)
    if storage not in found:
        return True
    return "images" not in found[storage]


def parse_storage_content(cfg_text: str) -> dict[str, set[str]]:
    """Map storage id to content tokens from storage.cfg."""
    current: str | None = None
    found: dict[str, set[str]] = {}
    for raw in cfg_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[:1].isspace():
            if current and stripped.startswith("content "):
                tokens = stripped.split(None, 1)[1].replace(",", " ").split()
                found[current].update(tokens)
            continue
        if ":" not in stripped:
            current = None
            continue
        _kind, _, name = stripped.partition(":")
        current = name.strip()
        found.setdefault(current, set())
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


def with_discard_ssd(volid: str, rest_after_vol: str) -> str:
    """Return volid plus options, with discard=on and ssd=1 present once."""
    options = [part.strip() for part in rest_after_vol.split(",") if part.strip()]
    kept: list[str] = []
    seen_discard = False
    seen_ssd = False
    for opt in options:
        if opt == "discard=on":
            if seen_discard:
                continue
            seen_discard = True
        elif opt == "ssd=1":
            if seen_ssd:
                continue
            seen_ssd = True
        kept.append(opt)
    if not seen_discard:
        kept.append("discard=on")
    if not seen_ssd:
        kept.append("ssd=1")
    return ",".join([volid, *kept])


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
        ["qm", "importdisk", vid, image_path, storage],
        ["qm", "set", vid, "--scsi0", f"{imported_volid},discard=on,ssd=1"],
        ["qm", "set", vid, "--ide2", f"{storage}:cloudinit"],
        ["qm", "set", vid, "--boot", "order=scsi0"],
    ]
    if make_template:
        commands.append(["qm", "template", vid])
    return commands


def existing_hw_commands(*, vmid: int, bus_key: str, disk_value: str) -> list[list[str]]:
    """Return qm set argv lists for agent, rng, serial console, and the OS disk."""
    vid = str(vmid)
    return [
        ["qm", "set", vid, "--agent", "enabled=1"],
        ["qm", "set", vid, "--rng0", "source=/dev/urandom"],
        ["qm", "set", vid, "--serial0", "socket", "--vga", "serial0"],
        ["qm", "set", vid, f"--{bus_key}", disk_value],
    ]


def _options_after(config_text: str, bus_key: str, volid: str) -> str:
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None:
            continue
        key, rest = parsed
        if key != bus_key:
            continue
        volume, _sep, options = rest.partition(",")
        if volume == volid:
            return options
    return ""


def _detail(result: object) -> str:
    stderr = getattr(result, "stderr", "") or ""
    stdout = getattr(result, "stdout", "") or ""
    text = (stderr or stdout).strip()
    code = getattr(result, "returncode", 1)
    if not text:
        return f"exit {code}"
    return f"{text.splitlines()[-1][:400]} (exit {code})"


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
    created_now: bool,
    destroyed: bool,
) -> str:
    imported = run(["qm", "importdisk", str(vmid), image_path, storage])
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


def _backup_os_disks(run: Run, *, vmid: int, config_text: str, backup_dir: str) -> None:
    """Copy each OS disk to backup_dir. The VM is left alone."""
    disks = list_os_disks(config_text)
    if not disks:
        return
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
        copied = run(["qemu-img", "convert", "-O", "qcow2", source, dest])
        if getattr(copied, "returncode", 1) != 0:
            raise VmError(f"VM {vmid} disk backup failed for {volid}: {_detail(copied)}")
        print(f"backup {volid} -> {dest}")


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
) -> str:
    """Create a cloud VM via run, or print qm commands when dry_run is set.

    An existing stopped VM is destroyed first when destroy_ok is set, template
    or not. backup_disks copies OS disks into backup_dir before that destroy.
    A template that has linked clones, on this node or another, is not destroyed.
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
    )
    if dry_run:
        if backup_disks:
            dest = backup_dir or "."
            print(f"# qemu-img convert -O qcow2 <os-disk> {dest}/vm-{vmid}-os.bak.qcow2")
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
        if is_template(config_text):
            _refuse_linked_clones(run, vmid, config_text, nodes_root)
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
) -> str:
    """Import image_path and point the boot disk at it. The old volume is deleted."""
    current = select_boot_disk(config_text)
    volid = _import_volume(
        run,
        vmid=vmid,
        image_path=image_path,
        storage=storage,
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
) -> InsertResult:
    """Swap the boot disk and leave the guest a template.

    The template flag is cleared only for the disk edit, then qm template
    puts it back and turns the new disk into a base volume. Name, network,
    firewall, and the other options are not rewritten.
    """
    saved = ""
    if disk_policy == "backup" and list_os_disks(config_text):
        if not backup_dir:
            raise VmError(f"VM {vmid} disk backup needs a cache directory")
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
) -> InsertResult:
    """Import a disk into a stopped guest. The guest config is not replaced.

    On a normal VM, backup leaves the current OS disk attached and adds the
    new one on the next scsi slot. overwrite replaces the boot disk and
    deletes the old volume.

    On a template, the boot disk is replaced and the template flag is kept.
    backup copies the old disk into backup_dir instead of leaving it attached.
    A template with linked clones, including clones on other cluster nodes,
    is not changed.
    """
    if disk_policy not in {"backup", "overwrite"}:
        raise VmError(f"unknown disk policy {disk_policy}")
    guessed = f"{storage}:vm-{vmid}-disk-0"
    if dry_run:
        print(" ".join(["qm", "importdisk", str(vmid), image_path, storage]))
        if disk_policy == "backup":
            print(
                f"# qm set {vmid} --scsiN {guessed},discard=on,ssd=1  "
                "# next free scsi slot; the current disk stays"
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
    if is_template(config_text):
        _refuse_linked_clones(run, vmid, config_text, nodes_root)
        return _replace_template_disk(
            run,
            vmid=vmid,
            storage=storage,
            image_path=image_path,
            disk_policy=disk_policy,
            config_text=config_text,
            backup_dir=backup_dir,
        )
    if disk_policy == "overwrite":
        volid = _overwrite_boot_disk(
            run,
            vmid=vmid,
            storage=storage,
            image_path=image_path,
            config_text=config_text,
        )
        return InsertResult(volid)
    volid = _import_volume(
        run,
        vmid=vmid,
        image_path=image_path,
        storage=storage,
        created_now=False,
        destroyed=False,
    )
    attached = f"{volid},discard=on,ssd=1"
    slot = next_scsi_index(config_text)
    _must(run, ["qm", "set", str(vmid), f"--scsi{slot}", attached], vmid=vmid, destroyed=False)
    return InsertResult(volid)


def existing_prep_commands(
    config_text: str,
    *,
    vmid: int,
    storage: str,
) -> tuple[list[list[str]], str]:
    """Return hardware qm commands and the OS volid for an existing VM."""
    disk = select_os_disk(config_text, storage)
    if disk is None:
        raise VmError(f"VM {vmid} has no OS disk on {storage}")
    bus_key, volid = disk
    disk_value = with_discard_ssd(volid, _options_after(config_text, bus_key, volid))
    commands = existing_hw_commands(vmid=vmid, bus_key=bus_key, disk_value=disk_value)
    return commands, volid
