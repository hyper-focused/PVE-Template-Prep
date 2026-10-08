"""Proxmox qm command builders and config parsers. No live qm calls."""

from __future__ import annotations

import re
from collections.abc import Callable


class VmError(Exception):
    """Proxmox VM operation failed."""


class VmDestroyedError(VmError):
    """A template VM was destroyed and the replacement did not finish."""


_BUS_KEY = re.compile(r"^(?:scsi|virtio|sata|ide|nvme)[0-9]+$")
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


def is_template(config_text: str) -> bool:
    """True when the guest config is a Proxmox template."""
    for raw in config_text.splitlines():
        parsed = _key_rest(raw)
        if parsed is None or parsed[0] != "template":
            continue
        token = parsed[1].split(None, 1)[0]
        return token == "1"
    return False


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
) -> list[list[str]]:
    """Return qm argv lists that build a cloud-init template from an image."""
    vid = str(vmid)
    return [
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
        ["qm", "template", vid],
    ]


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


def _reuse_commands(
    *,
    vmid: int,
    name: str,
    memory_mb: int,
    cores: int,
    bridge: str,
    storage: str,
    imported_volid: str,
) -> list[list[str]]:
    vid = str(vmid)
    return [
        [
            "qm",
            "set",
            vid,
            "--name",
            name,
            "--memory",
            str(memory_mb),
            "--cores",
            str(cores),
            "--net0",
            f"virtio,bridge={bridge}",
        ],
        ["qm", "set", vid, "--agent", "enabled=1"],
        ["qm", "set", vid, "--rng0", "source=/dev/urandom"],
        ["qm", "set", vid, "--serial0", "socket", "--vga", "serial0"],
        ["qm", "set", vid, "--scsi0", f"{imported_volid},discard=on,ssd=1"],
        ["qm", "set", vid, "--ide2", f"{storage}:cloudinit"],
        ["qm", "set", vid, "--boot", "order=scsi0"],
    ]


def _drop_stale_unused(run: Run, *, vmid: int, keep: str, destroyed: bool) -> None:
    cfg = run(["qm", "config", str(vmid)])
    if getattr(cfg, "returncode", 1) != 0:
        raise VmError(f"VM {vmid} config failed before unused cleanup: {_detail(cfg)}")
    for key, volid in unused_volumes(getattr(cfg, "stdout", "") or ""):
        if volid == keep:
            continue
        _must(run, ["qm", "set", str(vmid), "--delete", key], vmid=vmid, destroyed=destroyed)


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
) -> str:
    """Create a cloud template via run, or print qm commands when dry_run is set."""
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
    )
    if dry_run:
        if destroy_ok:
            print(
                f"# qm destroy {vmid}  "
                "# only if this VMID exists, is stopped, and is a template"
            )
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
        if is_template(getattr(cfg, "stdout", "") or ""):
            _must(run, ["qm", "destroy", str(vmid)], vmid=vmid, destroyed=False)
            destroyed = True
        else:
            return _replace_existing_disk(
                run,
                vmid=vmid,
                name=name,
                memory_mb=memory_mb,
                cores=cores,
                bridge=bridge,
                storage=storage,
                image_path=image_path,
            )

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
    )
    for cmd in finished[2:]:
        _must(run, cmd, vmid=vmid, destroyed=destroyed)
    return volid


def _replace_existing_disk(
    run: Run,
    *,
    vmid: int,
    name: str,
    memory_mb: int,
    cores: int,
    bridge: str,
    storage: str,
    image_path: str,
) -> str:
    """Import a new disk onto a stopped non-template VM, then swap scsi0."""
    volid = _import_volume(
        run,
        vmid=vmid,
        image_path=image_path,
        storage=storage,
        created_now=False,
        destroyed=False,
    )
    for cmd in _reuse_commands(
        vmid=vmid,
        name=name,
        memory_mb=memory_mb,
        cores=cores,
        bridge=bridge,
        storage=storage,
        imported_volid=volid,
    ):
        _must(run, cmd, vmid=vmid, destroyed=False)
    _drop_stale_unused(run, vmid=vmid, keep=volid, destroyed=False)
    _must(run, ["qm", "template", str(vmid)], vmid=vmid, destroyed=False)
    return volid


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
