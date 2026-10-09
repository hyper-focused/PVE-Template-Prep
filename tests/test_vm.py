"""Unit tests for pve_prep.vm. No live qm calls."""

from __future__ import annotations

import unittest
from io import StringIO
from contextlib import redirect_stdout

from pve_prep.vm import (
    VmError,
    config_has_os_disk,
    create_template,
    existing_prep_commands,
    insert_disk,
    parse_imported_volid,
    parse_os_disk,
    parse_qm_status,
    vmid_config_missing,
    parse_storage_content,
    storage_lacks_images,
    parse_storage_ids,
    select_os_disk,
    template_commands,
    with_discard_ssd,
)


STORAGE = "NFS-SATA-SSD1"
IMAGE = "/var/lib/vz/template/cache/alma-910.qcow2"


class _Proc:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


class FakeRun:
    """Record argv and return scripted returncode/stdout pairs in order."""

    def __init__(self, steps: list[tuple[int, str]]) -> None:
        self.steps = list(steps)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> _Proc:
        self.calls.append(list(argv))
        if not self.steps:
            raise AssertionError(f"no scripted response for {argv}")
        code, out = self.steps.pop(0)
        return _Proc(code, out)


def _create(**overrides: object) -> dict:
    params = {
        "vmid": 910,
        "name": "tpl-910",
        "memory_mb": 2048,
        "cores": 2,
        "bridge": "vmbr0",
        "storage": STORAGE,
        "image_path": IMAGE,
        "dry_run": False,
        "destroy_ok": False,
        "run": overrides.get("run"),
    }
    params.update(overrides)
    return params


class VmParseTests(unittest.TestCase):
    def test_parse_storage_ids_ignores_inactive_and_header(self) -> None:
        text = """
Name             Type     Status           Total            Used       Available        %
local             dir     active        131541372        10485760      121055612    7.97%
NFS-SATA-SSD1     nfs     active       1000000000       500000000      500000000   50.00%
backup            dir     inactive             0               0              0      N/A

local-lvm     lvmthin     disabled      100000000               0      100000000    0.00%
"""
        self.assertEqual(parse_storage_ids(text), ["local", "NFS-SATA-SSD1"])

    def test_parse_qm_status_token_or_empty(self) -> None:
        self.assertEqual(parse_qm_status("status: stopped\n"), "stopped")
        self.assertEqual(parse_qm_status("stopped"), "stopped")
        self.assertEqual(parse_qm_status("status: running"), "running")
        self.assertEqual(parse_qm_status(""), "")
        self.assertEqual(parse_qm_status("   \n"), "")
        self.assertEqual(parse_qm_status("Configuration file does not exist\n"), "")
        self.assertEqual(parse_qm_status("status:"), "")

    def test_missing_vmid_is_not_in_use(self) -> None:
        missing = "Configuration file 'nodes/pve/qemu-server/9001.conf' does not exist\n"
        self.assertTrue(vmid_config_missing(2, missing))
        self.assertFalse(vmid_config_missing(0, "status: stopped\n"))
        self.assertFalse(vmid_config_missing(255, "Permission denied\n"))

    def test_parse_os_disk_skips_cloudinit_cdrom_and_wrong_storage(self) -> None:
        cfg = "\n".join(
            [
                "boot: order=scsi0",
                "ide2: NFS-SATA-SSD1:cloudinit,media=cdrom",
                "ide0: NFS-SATA-SSD1:iso/debian.iso,media=cdrom",
                "scsi0: other-store:vm-901-disk-0,size=3G",
                "virtio0: NFS-SATA-SSD1:vm-901-cloudinit,size=1G",
                "scsi1: NFS-SATA-SSD1:vm-901-disk-0,iothread=1,size=3G",
                "ide1: NFS-SATA-SSD1:vm-901-disk-1,size=1G",
            ]
        )
        self.assertEqual(
            parse_os_disk(cfg, STORAGE),
            ("scsi1", "NFS-SATA-SSD1:vm-901-disk-0"),
        )
        ide = "ide0: local-lvm:vm-100-disk-0,size=4G\n"
        self.assertEqual(
            parse_os_disk(ide, "local-lvm"),
            ("ide0", "local-lvm:vm-100-disk-0"),
        )
        scsi = "scsi0: local-lvm:vm-100-disk-0,size=4G\n"
        self.assertEqual(
            parse_os_disk(scsi, "local-lvm"),
            ("scsi0", "local-lvm:vm-100-disk-0"),
        )
        self.assertIsNone(parse_os_disk(ide, STORAGE))
        self.assertIsNone(parse_os_disk("scsi0: local-lvm:vm-100-disk-0\n", "local"))
        self.assertIsNone(parse_os_disk("boot: order=scsi0\n", STORAGE))

    def test_with_discard_ssd_adds_flags_once(self) -> None:
        volid = "store:vm-1-disk-0"
        self.assertEqual(
            with_discard_ssd(volid, ""),
            "store:vm-1-disk-0,discard=on,ssd=1",
        )
        self.assertEqual(
            with_discard_ssd(volid, "iothread=1,size=3G"),
            "store:vm-1-disk-0,iothread=1,size=3G,discard=on,ssd=1",
        )
        self.assertEqual(
            with_discard_ssd(volid, "discard=on,ssd=1"),
            "store:vm-1-disk-0,discard=on,ssd=1",
        )
        self.assertEqual(
            with_discard_ssd(volid, "discard=on,iothread=1,ssd=1"),
            "store:vm-1-disk-0,discard=on,iothread=1,ssd=1",
        )
        self.assertEqual(
            with_discard_ssd(volid, "ssd=1,discard=on,discard=on,ssd=1"),
            "store:vm-1-disk-0,ssd=1,discard=on",
        )

    def test_parse_imported_volid(self) -> None:
        self.assertEqual(
            parse_imported_volid("unused0: local-lvm:vm-100-disk-0\n"),
            "local-lvm:vm-100-disk-0",
        )
        raw = "name: t\nunused0: NFS-SATA-SSD1:100/vm-100-disk-0.raw\n"
        self.assertEqual(
            parse_imported_volid(raw),
            "NFS-SATA-SSD1:100/vm-100-disk-0.raw",
        )
        self.assertIsNone(parse_imported_volid("scsi0: local-lvm:vm-100-disk-0\n"))

    def test_template_commands_shape(self) -> None:
        cmds = template_commands(
            vmid=910,
            name="tpl-910",
            memory_mb=2048,
            cores=2,
            bridge="vmbr0",
            storage=STORAGE,
            image_path=IMAGE,
            imported_volid="NFS-SATA-SSD1:vm-910-disk-0",
        )
        self.assertEqual(
            cmds[0],
            [
                "qm",
                "create",
                "910",
                "--name",
                "tpl-910",
                "--memory",
                "2048",
                "--cores",
                "2",
                "--machine",
                "q35",
                "--cpu",
                "x86-64-v2-AES",
                "--net0",
                "virtio,bridge=vmbr0",
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
        )
        self.assertEqual(cmds[1], ["qm", "importdisk", "910", IMAGE, STORAGE])
        self.assertEqual(
            cmds[2],
            ["qm", "set", "910", "--scsi0", "NFS-SATA-SSD1:vm-910-disk-0,discard=on,ssd=1"],
        )
        self.assertEqual(cmds[3], ["qm", "set", "910", "--ide2", f"{STORAGE}:cloudinit"])
        self.assertEqual(cmds[4], ["qm", "set", "910", "--boot", "order=scsi0"])
        self.assertEqual(cmds[5], ["qm", "template", "910"])
        blob = " ".join(arg for cmd in cmds for arg in cmd)
        for banned in ("ciuser", "cipassword", "sshkeys", "password", "--bios", "ovmf"):
            self.assertNotIn(banned, blob)
        self.assertIn("--serial0", cmds[0])
        self.assertIn("discard=on", cmds[2][4])

    def test_template_commands_can_leave_a_normal_vm(self) -> None:
        cmds = template_commands(
            vmid=910,
            name="tpl-910",
            memory_mb=2048,
            cores=2,
            bridge="vmbr0",
            storage=STORAGE,
            image_path=IMAGE,
            imported_volid="NFS-SATA-SSD1:vm-910-disk-0",
            make_template=False,
        )
        self.assertEqual(cmds[-1], ["qm", "set", "910", "--boot", "order=scsi0"])
        self.assertEqual(len(cmds), 5)
        self.assertNotIn(["qm", "template", "910"], cmds)

    def test_existing_prep_commands_sets_discard_on_os_disk(self) -> None:
        cfg = "\n".join(
            [
                "scsi0: NFS-SATA-SSD1:vm-901-disk-0,iothread=1,size=3G",
                "ide2: NFS-SATA-SSD1:cloudinit,media=cdrom",
            ]
        )
        cmds, volid = existing_prep_commands(cfg, vmid=901, storage=STORAGE)
        self.assertEqual(volid, "NFS-SATA-SSD1:vm-901-disk-0")
        self.assertEqual(cmds[0], ["qm", "set", "901", "--agent", "enabled=1"])
        self.assertEqual(cmds[1], ["qm", "set", "901", "--rng0", "source=/dev/urandom"])
        self.assertEqual(
            cmds[2],
            ["qm", "set", "901", "--serial0", "socket", "--vga", "serial0"],
        )
        self.assertEqual(
            cmds[3],
            [
                "qm",
                "set",
                "901",
                "--scsi0",
                "NFS-SATA-SSD1:vm-901-disk-0,iothread=1,size=3G,discard=on,ssd=1",
            ],
        )
        with self.assertRaises(VmError):
            existing_prep_commands(
                "ide2: NFS-SATA-SSD1:cloudinit,media=cdrom\n",
                vmid=901,
                storage=STORAGE,
            )

    def test_boot_order_picks_the_boot_disk(self) -> None:
        cfg = "\n".join(
            [
                "boot: order=scsi1;net0",
                "scsi0: NFS-SATA-SSD1:vm-901-disk-1,size=10G",
                "scsi1: NFS-SATA-SSD1:vm-901-disk-0,size=3G",
            ]
        )
        bus, volid = select_os_disk(cfg, STORAGE)
        self.assertEqual((bus, volid), ("scsi1", "NFS-SATA-SSD1:vm-901-disk-0"))
        _cmds, chosen = existing_prep_commands(cfg, vmid=901, storage=STORAGE)
        self.assertEqual(chosen, "NFS-SATA-SSD1:vm-901-disk-0")

    def test_boot_disk_on_another_storage_is_refused(self) -> None:
        cfg = "\n".join(
            [
                "boot: order=scsi0",
                "scsi0: other:vm-901-disk-0",
                "scsi1: NFS-SATA-SSD1:vm-901-disk-1",
            ]
        )
        with self.assertRaises(VmError):
            select_os_disk(cfg, STORAGE)

    def test_storage_content_parser(self) -> None:
        text = "\n".join(
            [
                "dir: local",
                "\tcontent iso,backup",
                "",
                "nfs: NFS-SATA-SSD1",
                "\tcontent images,rootdir",
            ]
        )
        parsed = parse_storage_content(text)
        self.assertEqual(parsed["local"], {"iso", "backup"})
        self.assertEqual(parsed["NFS-SATA-SSD1"], {"images", "rootdir"})

    def test_images_token_is_not_a_storage_name(self) -> None:
        text = "\n".join(
            [
                "dir: local",
                "\tcontent iso,vztmpl,backup",
                "nfs: NFS-SATA-SSD2",
                "\texport /NFS-SATA-SSD2",
                "\tpath /mnt/pve/NFS-SATA-SSD2",
                "\tcontent iso,rootdir,vztmpl,backup,snippets,import,images",
            ]
        )
        self.assertFalse(storage_lacks_images(text, "NFS-SATA-SSD2"))
        self.assertTrue(storage_lacks_images(text, "local"))
        self.assertTrue(storage_lacks_images(text, "missing"))


class CreateTemplateTests(unittest.TestCase):
    def test_dry_run_does_not_call_run(self) -> None:
        def boom(argv: list[str]) -> None:
            raise AssertionError(f"run called: {argv}")

        buf = StringIO()
        with redirect_stdout(buf):
            volid = create_template(**_create(dry_run=True, destroy_ok=True, run=boom))
        self.assertEqual(volid, f"{STORAGE}:vm-910-disk-0")
        printed = buf.getvalue().splitlines()
        expected = template_commands(
            vmid=910,
            name="tpl-910",
            memory_mb=2048,
            cores=2,
            bridge="vmbr0",
            storage=STORAGE,
            image_path=IMAGE,
            imported_volid=volid,
        )
        self.assertEqual(
            printed[0],
            "# qm destroy 910  # only if this VMID exists and is stopped",
        )
        self.assertEqual(printed[1:], [" ".join(cmd) for cmd in expected])

    def test_refuses_running_even_if_destroy_ok(self) -> None:
        run = FakeRun([(0, "status: running\n")])
        with self.assertRaises(VmError) as caught:
            create_template(**_create(destroy_ok=True, run=run))
        self.assertIn("running", str(caught.exception))
        self.assertEqual(run.calls, [["qm", "status", "910"]])

    def test_refuses_stopped_when_destroy_ok_false(self) -> None:
        run = FakeRun([(0, "status: stopped\n")])
        with self.assertRaises(VmError) as caught:
            create_template(**_create(destroy_ok=False, run=run))
        self.assertIn("910", str(caught.exception))
        self.assertEqual(run.calls, [["qm", "status", "910"]])

    def test_destroy_ok_template_destroys_before_create(self) -> None:
        volid = "NFS-SATA-SSD1:910/vm-910-disk-0.raw"
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, f"unused0: {volid}\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, ""),
            ]
        )
        result = create_template(**_create(destroy_ok=True, run=run))
        self.assertEqual(result, volid)
        self.assertEqual(run.calls[0], ["qm", "status", "910"])
        self.assertEqual(run.calls[1], ["qm", "destroy", "910"])
        self.assertEqual(run.calls[2][:3], ["qm", "create", "910"])
        self.assertEqual(run.calls[3], ["qm", "importdisk", "910", IMAGE, STORAGE])
        self.assertEqual(run.calls[4], ["qm", "config", "910"])
        self.assertEqual(
            run.calls[5:],
            [
                ["qm", "set", "910", "--scsi0", f"{volid},discard=on,ssd=1"],
                ["qm", "set", "910", "--ide2", f"{STORAGE}:cloudinit"],
                ["qm", "set", "910", "--boot", "order=scsi0"],
                ["qm", "template", "910"],
            ],
        )

    def test_stopped_vm_is_replaced_even_when_not_a_template(self) -> None:
        volid = "NFS-SATA-SSD1:vm-910-disk-1"
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, f"unused0: {volid}\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, ""),
            ]
        )
        result = create_template(**_create(destroy_ok=True, run=run))
        self.assertEqual(result, volid)
        self.assertEqual(run.calls[1], ["qm", "destroy", "910"])
        self.assertEqual(run.calls[3], ["qm", "importdisk", "910", IMAGE, STORAGE])
        self.assertEqual(run.calls[-1], ["qm", "template", "910"])

    def test_make_template_false_skips_qm_template(self) -> None:
        volid = "NFS-SATA-SSD1:910/vm-910-disk-0.raw"
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, f"unused0: {volid}\n"),
                (0, ""),
                (0, ""),
                (0, ""),
            ]
        )
        result = create_template(**_create(destroy_ok=True, make_template=False, run=run))
        self.assertEqual(result, volid)
        self.assertEqual(run.calls[-1], ["qm", "set", "910", "--boot", "order=scsi0"])
        self.assertNotIn(["qm", "template", "910"], run.calls)

    def test_importdisk_failure_without_volume_destroys(self) -> None:
        run = FakeRun(
            [
                (2, "Configuration file does not exist\n"),
                (0, ""),
                (1, "import failed\n"),
                (0, "name: tpl-910\n"),
                (0, ""),
            ]
        )
        with self.assertRaises(VmError) as caught:
            create_template(**_create(run=run))
        self.assertIn("empty VM was removed", str(caught.exception))
        self.assertIn("910", str(caught.exception))
        self.assertEqual(
            [call[:2] for call in run.calls],
            [
                ["qm", "status"],
                ["qm", "create"],
                ["qm", "importdisk"],
                ["qm", "config"],
                ["qm", "destroy"],
            ],
        )
        self.assertEqual(run.calls[-1], ["qm", "destroy", "910"])

    def test_importdisk_failure_with_unused_does_not_destroy(self) -> None:
        volid = "NFS-SATA-SSD1:vm-910-disk-0"
        run = FakeRun(
            [
                (2, "missing\n"),
                (0, ""),
                (1, "import failed\n"),
                (0, f"unused0: {volid}\n"),
            ]
        )
        with self.assertRaises(VmError) as caught:
            create_template(**_create(run=run))
        message = str(caught.exception)
        self.assertIn("910", message)
        self.assertIn(volid, message)
        self.assertNotIn(["qm", "destroy", "910"], run.calls)
        self.assertEqual(run.calls[-1], ["qm", "config", "910"])

    def test_set_failure_after_volume_does_not_destroy(self) -> None:
        volid = "NFS-SATA-SSD1:vm-910-disk-0"
        run = FakeRun(
            [
                (2, "missing\n"),
                (0, ""),
                (0, ""),
                (0, f"unused0: {volid}\n"),
                (1, "set failed\n"),
            ]
        )
        with self.assertRaises(VmError) as caught:
            create_template(**_create(run=run))
        self.assertIn("910", str(caught.exception))
        self.assertNotIn(["qm", "destroy", "910"], run.calls)

    def test_backup_copies_the_disk_before_destroy(self) -> None:
        old = "NFS-SATA-SSD1:vm-910-disk-0.raw"
        volid = "NFS-SATA-SSD1:910/vm-910-disk-0.raw"
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, f"template: 1\nscsi0: {old}\n"),
                (0, "/mnt/pve/NFS/vm-910-disk-0.raw\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, f"unused0: {volid}\n"),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, ""),
            ]
        )
        result = create_template(
            **_create(
                destroy_ok=True,
                backup_disks=True,
                backup_dir="/var/tmp/pve-template-prep/cache",
                run=run,
            )
        )
        self.assertEqual(result, volid)
        self.assertEqual(run.calls[1], ["qm", "config", "910"])
        self.assertEqual(run.calls[2], ["pvesm", "path", old])
        self.assertEqual(run.calls[3][:4], ["qemu-img", "convert", "-O", "qcow2"])
        self.assertTrue(run.calls[3][4].endswith("vm-910-disk-0.raw"))
        self.assertIn("vm-910-scsi0.bak.", run.calls[3][5])
        self.assertEqual(run.calls[4], ["qm", "destroy", "910"])

    def test_cloudinit_alone_is_not_an_os_disk(self) -> None:
        text = "ide2: NFS-SATA-SSD1:9001/vm-9001-cloudinit.qcow2,media=cdrom\n"
        self.assertFalse(config_has_os_disk(text))
        self.assertTrue(config_has_os_disk("scsi0: NFS-SATA-SSD1:vm-910-disk-0\n"))


class InsertDiskTests(unittest.TestCase):
    def test_overwrite_replaces_the_boot_disk(self) -> None:
        old = "NFS-SATA-SSD1:vm-910-disk-0"
        new = "NFS-SATA-SSD1:vm-910-disk-1"
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, f"boot: order=scsi0\nscsi0: {old}\n"),
                (0, ""),
                (0, f"unused0: {new}\nscsi0: {old}\n"),
                (0, ""),
                (0, f"unused0: {old}\nscsi0: {new}\n"),
                (0, ""),
            ]
        )
        result = insert_disk(
            vmid=910,
            storage=STORAGE,
            image_path=IMAGE,
            disk_policy="overwrite",
            dry_run=False,
            run=run,
        )
        self.assertEqual(result, new)
        self.assertEqual(run.calls[4], ["qm", "set", "910", "--scsi0", f"{new},discard=on,ssd=1"])
        self.assertEqual(run.calls[6], ["qm", "set", "910", "--delete", "unused0"])
        self.assertNotIn(["qm", "destroy", "910"], run.calls)
        self.assertNotIn(["qm", "template", "910"], run.calls)

    def test_backup_keeps_the_old_disk(self) -> None:
        old = "NFS-SATA-SSD1:vm-910-disk-0"
        new = "NFS-SATA-SSD1:vm-910-disk-1"
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, f"scsi0: {old}\n"),
                (0, ""),
                (0, f"unused0: {new}\nscsi0: {old}\n"),
                (0, ""),
            ]
        )
        result = insert_disk(
            vmid=910,
            storage=STORAGE,
            image_path=IMAGE,
            disk_policy="backup",
            dry_run=False,
            run=run,
        )
        self.assertEqual(result, new)
        self.assertEqual(run.calls[4], ["qm", "set", "910", "--scsi1", f"{new},discard=on,ssd=1"])
        self.assertNotIn(["qm", "destroy", "910"], run.calls)

    def test_refuses_a_template(self) -> None:
        run = FakeRun(
            [
                (0, "status: stopped\n"),
                (0, "template: 1\nscsi0: NFS-SATA-SSD1:vm-910-disk-0\n"),
            ]
        )
        with self.assertRaises(VmError) as caught:
            insert_disk(
                vmid=910,
                storage=STORAGE,
                image_path=IMAGE,
                disk_policy="overwrite",
                dry_run=False,
                run=run,
            )
        self.assertIn("template", str(caught.exception))
        self.assertEqual(run.calls, [["qm", "status", "910"], ["qm", "config", "910"]])


if __name__ == "__main__":
    unittest.main()
