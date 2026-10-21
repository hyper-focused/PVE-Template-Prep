"""Scripted interviews. No host, no sibling modules."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep.job import DEFAULT_BACKUPS, DEFAULT_CACHE, Job  # noqa: E402
from pve_prep import ui  # noqa: E402
from pve_prep.prompts import (  # noqa: E402
    PromptAbort,
    _answered,
    _ask_releases,
    _blank,
    _Output,
    _write_table,
    ask_cache_cleanup,
    distro_name,
    interview,
)


def _specs(distro: str):
    if distro != "debian":
        raise AssertionError(distro)
    return [
        SimpleNamespace(release="12", label="bookworm", eol=False),
        SimpleNamespace(release="13", label="trixie", eol=False),
    ]


def _summary(blob: str, title: str = "Summary") -> list[tuple[str, ...]]:
    """The table under a section heading, header row first. Cells split on runs of two or more spaces."""
    lines = blob.split("\n")
    start = lines.index(f"  {title}") + 3
    rows = []
    for line in lines[start:]:
        if not line.strip():
            break
        rows.append(tuple(re.split(r"\s{2,}", line.strip())))
    return rows


def _column(blob: str, name: str) -> list[str]:
    table = _summary(blob)
    index = table[0].index(name)
    return [row[index] for row in table[1:]]


YES_GATE = "Type 'YES' to continue or 'X' to exit now: "
DELETE_GATE = "Type 'DELETE' to continue or 'X' to exit now without changes: "
PASSWORD_AUTH = "Allow password authentication for SSH?"
ROOT_SSH = "Enable direct SSH login for 'root' user?"


def _normalize(distro: str, raw: str) -> str:
    known = {"12": "12", "13": "13", "bookworm": "12", "trixie": "13"}
    if distro != "debian" or raw not in known:
        raise RuntimeError(f"unknown release {raw}")
    return known[raw]


class _Script:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.written: list[str] = []

    def read_line(self) -> str:
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def write(self, text: str) -> None:
        self.written.append(text)

    def run(
        self,
        *,
        dry_run: bool,
        storages: list[str] | None = None,
        kinds: dict[str, str] | None = None,
        in_use: set[int] | None = None,
        disks: set[int] | None = None,
        roles: dict[int, str] | None = None,
        placed: dict[int, str] | None = None,
        disk_storage=None,
        formats: dict[int, str] | None = None,
        disk_format_of=None,
        imports: list[tuple[str, str]] | None = None,
    ) -> Job:
        def list_storages() -> list[str]:
            if storages is None:
                raise AssertionError("storage list should not be consulted")
            return list(storages)

        def storage_kinds() -> dict[str, str]:
            return dict(kinds or {})

        def vmids_in_use(vmids: tuple[int, ...]) -> set[int] | None:
            if in_use is None:
                return None
            return {vmid for vmid in vmids if vmid in in_use}

        def vm_has_disks(vmid: int) -> bool | None:
            if disks is None:
                return None
            return vmid in disks

        def guest_role(vmid: int) -> str | None:
            if roles is None:
                return None
            return roles.get(vmid)

        callback = disk_storage
        if callback is None and placed is not None:
            def callback(vmid: int):
                if vmid not in placed:
                    return None
                return placed[vmid]

        format_cb = disk_format_of
        if format_cb is None and formats is not None:
            def format_cb(vmid: int):
                if vmid not in formats:
                    return None
                return formats[vmid]

        return interview(
            self.read_line,
            self.write,
            dry_run=dry_run,
            list_storages=list_storages,
            storage_kinds=storage_kinds,
            releases_for=_specs,
            normalize_release=_normalize,
            vmids_in_use=vmids_in_use,
            vm_has_disks=vm_has_disks,
            guest_role=guest_role if roles is not None else None,
            disk_storage=callback,
            disk_format_of=format_cb,
            import_storages=lambda: list(imports or []),
        )


class InterviewTest(unittest.TestCase):
    def test_image_debian_two_releases(self) -> None:
        script = _Script(["3", "1", "1, 2", "1", "", "", "", "yes"])
        job = script.run(
            dry_run=False,
            imports=[("local", "/var/lib/vz/import"), ("vm-data", "/not/used")],
        )
        self.assertEqual(
            job,
            Job(
                distro="debian",
                releases=("12", "13"),
                mode="image",
                disk_format="qcow2",
                dest_dir="/var/lib/vz/import",
                storage="",
                cache_dir=DEFAULT_CACHE,
                collision="backup",
                vmids=(),
                prep=True,
                bridge="vmbr0",
                memory_mb=2048,
                cores=2,
                dry_run=False,
                destroy_vmids=frozenset(),
                make_template=False,
                clean_cache=False,
                password_auth=True,
                permit_root=True,
            ),
        )
        blob = "".join(script.written)
        self.assertEqual(
            _summary(blob),
            [
                ("Release", "Format", "File"),
                ("bookworm", "qcow2", "/var/lib/vz/import/debian-12-pve.qcow2"),
                ("trixie", "qcow2", "/var/lib/vz/import/debian-13-pve.qcow2"),
            ],
        )
        self.assertIn("Guest prep: yes   Password SSH: yes   Root SSH: yes", blob)
        self.assertNotIn("Guest prep is applied:", blob)
        self.assertIn(PASSWORD_AUTH, blob)
        self.assertIn(ROOT_SSH, blob)
        self.assertLess(blob.index(PASSWORD_AUTH), blob.index(ROOT_SSH))
        self.assertIn("Your new disk images will now be created.", blob)
        self.assertNotIn("Accept these defaults?", blob)
        self.assertNotIn("cache: keep", blob)
        self.assertNotIn("raw (.img, full size)", blob)
        self.assertNotIn("Image directory", blob)
        self.assertNotIn("Please enter an existing", blob)
        self.assertIn("1) local", blob)
        self.assertIn("2) vm-data", blob)
        self.assertIn(YES_GATE, blob)
        self.assertNotIn("Disk:", blob)

    def test_template_replaces_only_the_typed_vmid(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "910",
                "911",
                "2",
                "",
                "",
                "n",
                "DELETE",
            ]
        )
        job = script.run(
            dry_run=True,
            storages=["local-dir", "nfs-templates"],
            in_use={910},
            disks={910},
        )
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.releases, ("12", "13"))
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.dest_dir, "")
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        self.assertTrue(job.prep)
        self.assertEqual(job.bridge, "vmbr0")
        self.assertEqual(job.memory_mb, 2048)
        self.assertEqual(job.cores, 2)
        self.assertEqual(job.collision, "overwrite")
        self.assertTrue(job.dry_run)
        self.assertEqual(job.cache_dir, DEFAULT_CACHE)
        self.assertEqual(job.backup_dir, DEFAULT_BACKUPS)
        blob = "".join(script.written)
        self.assertIn("Create new Proxmox PVE VM templates", blob)
        self.assertNotIn("Create the VM, import the disk", blob)
        self.assertIn("VM Disk Storage Path:", blob)
        self.assertIn("bookworm VM Template ID [9001]: ", blob)
        self.assertIn("trixie VM Template ID [9001]: ", blob)
        self.assertEqual(blob.count("  Template VMID(s)\n"), 1)
        self.assertEqual(
            blob.count("Provide a VMID for each VM template or press enter to use the default."), 1
        )
        self.assertIn("If the VMID is in use, the existing template will be deleted and replaced.", blob)
        self.assertLess(blob.index("  Template VMID(s)\n"), blob.index("bookworm VM Template ID [9001]: "))
        self.assertNotIn("In use:", blob)
        self.assertNotIn("including a clone on another node", blob)
        self.assertNotIn("Not in use:", blob)
        self.assertIn("No backup images will be created.", blob)
        self.assertIn("Your existing templates will now be DELETED and new templates created.", blob)
        self.assertIn(DELETE_GATE, blob)
        self.assertNotIn("Do not back up the existing template VM disk", blob)
        self.assertEqual(job.backup_vmids, frozenset())
        self.assertEqual(
            _summary(blob),
            [
                ("Release", "VMID", "Name", "Storage", "Format", "Existing"),
                ("bookworm", "910", "debian-12-cloud", "nfs-templates", "qcow2", "delete VM"),
                ("trixie", "911", "debian-13-cloud", "nfs-templates", "qcow2", "new"),
            ],
        )
        self.assertIn("Guest prep: yes   Password SSH: yes   Root SSH: yes   qm template: yes", blob)
        self.assertIn("Hardware: bridge vmbr0, memory 2048 MB, cores 2", blob)
        self.assertNotIn("non-template", blob)
        self.assertEqual(
            _summary(blob, "Defaults"),
            [
                ("Setting", "Default"),
                ("Password SSH", "yes"),
                ("Root SSH", "yes"),
                ("Bridge", "vmbr0"),
                ("Memory", "2048 MB"),
                ("Cores", "2"),
                ("qm template", "yes"),
            ],
        )
        defaults = blob[blob.index("  Defaults"):blob.index("Accept these defaults?")]
        self.assertIn("Cloud-init user must be set to 'root'.", defaults)

    def test_empty_vmid_starts_at_9001_and_counts_up(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use=set(), disks=set())
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.vmids, (9001, 9002))
        self.assertEqual(job.destroy_vmids, frozenset())
        self.assertEqual(job.collision, "backup")
        self.assertTrue(job.make_template)
        self.assertFalse(job.clean_cache)
        blob = "".join(script.written)
        self.assertIn("Provide a VMID for each VM template or press enter to use the default.", blob)
        self.assertIn("bookworm VM Template ID [9001]: ", blob)
        self.assertIn("trixie VM Template ID [9002]: ", blob)
        self.assertEqual(_column(blob, "VMID"), ["9001", "9002"])
        self.assertEqual(_column(blob, "Existing"), ["new", "new"])
        self.assertIn("Your new VM templates will now be created.", blob)
        self.assertNotIn("Disk:", blob)

    def test_default_skips_vmids_that_are_in_use(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["dir-templates"],
            in_use={9001, 9002},
            disks=set(),
        )
        self.assertEqual(job.vmids, (9003, 9004))
        self.assertEqual(job.destroy_vmids, frozenset())
        blob = "".join(script.written)
        self.assertIn("bookworm VM Template ID [9003]: ", blob)
        self.assertIn("trixie VM Template ID [9004]: ", blob)
        self.assertNotIn("Not in use:", blob)
        self.assertNotIn("Disk:", blob)
        self.assertNotIn("Replace VMID", blob)

    def test_default_skips_a_hole(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["dir-templates"],
            in_use={9002},
            disks=set(),
        )
        self.assertEqual(job.vmids, (9001, 9003))
        blob = "".join(script.written)
        self.assertIn("bookworm VM Template ID [9001]: ", blob)
        self.assertIn("trixie VM Template ID [9003]: ", blob)

    def test_free_vmids_skip_the_disk_prompt(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use=set(), disks=set())
        self.assertEqual(job.vmids, (9001, 9002))
        self.assertEqual(job.destroy_vmids, frozenset())
        blob = "".join(script.written)
        self.assertNotIn("Not in use:", blob)
        self.assertNotIn("Disk:", blob)
        self.assertNotIn("Replace VMID", blob)

    def test_confirm_no_aborts(self) -> None:
        script = _Script(["3", "1", "1", "1", "y", "", "", "", "no"])
        with self.assertRaises(PromptAbort):
            script.run(dry_run=False, imports=[("local", "/var/lib/vz/import")])

    def test_confirm_typo_reasks(self) -> None:
        script = _Script(["3", "1", "1", "1", "y", "", "", "", "yess", "yes"])
        job = script.run(dry_run=False, imports=[("local", "/var/lib/vz/import")])
        self.assertEqual(job.releases, ("12",))
        self.assertEqual("".join(script.written).count(YES_GATE), 2)

    def test_bad_release_then_good(self) -> None:
        script = _Script(
            ["3", "0", "1", "nope", "", "12", "1", "", "1", "", "n", "yes"]
        )
        job = script.run(dry_run=False, imports=[("local", "/var/lib/vz/import")])
        self.assertEqual(job.distro, "debian")
        self.assertEqual(job.releases, ("12",))
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.dest_dir, "/var/lib/vz/import")
        self.assertEqual(job.collision, "backup")
        self.assertTrue(job.prep)
        self.assertTrue(job.password_auth)
        self.assertFalse(job.permit_root)
        self.assertEqual(job.vmids, ())
        blob = "".join(script.written)
        self.assertNotIn("00-enableroot.conf", blob)
        self.assertIn("Root SSH: no", blob)
        self.assertIn("unknown release: nope", blob)
        self.assertIn("pick a release number: 12", blob)
        self.assertIn("need at least one release", blob)
        self.assertIn("pick a distro number", blob)

    def test_duplicate_canonical_release_reasks(self) -> None:
        answers = ["1, bookworm", "2"]
        written: list[str] = []

        def read_line() -> str:
            if not answers:
                raise EOFError
            return answers.pop(0)

        def write(text: str) -> None:
            written.append(text)

        def normalize(_distro: str, raw: str) -> str:
            aliases = {"12": "12", "bookworm": "12", "13": "13"}
            try:
                return aliases[raw]
            except KeyError as exc:
                raise RuntimeError(f"unknown release {raw}") from exc

        chosen = _ask_releases(read_line, write, "debian", _specs, normalize)
        self.assertEqual(chosen, ("13",))
        self.assertIn("duplicate release: 12\n", "".join(written))
        self.assertEqual(answers, [])

    def test_eof_aborts(self) -> None:
        def read_line() -> str:
            raise EOFError

        with self.assertRaises(PromptAbort):
            interview(
                read_line,
                lambda _text: None,
                dry_run=False,
                list_storages=lambda: [],
                releases_for=_specs,
                normalize_release=_normalize,
                vmids_in_use=lambda vmids: set(vmids),
                vm_has_disks=lambda _vmid: False,
            )

    def test_none_is_eof(self) -> None:
        def read_line():
            return None

        with self.assertRaises(PromptAbort):
            interview(
                read_line,
                lambda _text: None,
                dry_run=True,
                list_storages=lambda: [],
                releases_for=_specs,
                normalize_release=_normalize,
                vmids_in_use=lambda vmids: set(vmids),
                vm_has_disks=lambda _vmid: False,
            )

    def test_backup_policy_and_custom_hardware(self) -> None:
        script = _Script(
            ["", "1", "1, 2", "910", "911", "nfs-templates", "", "n", "", "n", "n", "vmbr1", "4096", "4", "", "", "yes"]
        )
        job = script.run(dry_run=False, storages=[], in_use={910}, disks={910})
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        self.assertEqual(job.collision, "backup")
        self.assertTrue(job.prep)
        self.assertTrue(job.password_auth)
        self.assertFalse(job.permit_root)
        self.assertEqual(job.bridge, "vmbr1")
        self.assertEqual(job.memory_mb, 4096)
        self.assertEqual(job.cores, 4)
        self.assertEqual(_column("".join(script.written), "Existing"), ["back up, delete VM", "new"])

    def test_backup_answer_empty_keeps_the_copy(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "910",
                "911",
                "1",
                "",
                "",
                "maybe",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use={910}, disks={910})
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        self.assertEqual(job.storage, "dir-templates")
        self.assertEqual(job.collision, "backup")
        blob = "".join(script.written)
        self.assertIn("answer y or n", blob)
        self.assertIn("Image backups will be created at /var/backups/pve-template-prep", blob)
        self.assertIn("Your existing templates will be backed up, then new templates created.", blob)
        self.assertEqual(job.backup_vmids, frozenset({910}))

    def test_no_disk_still_asks_before_delete(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "9001",
                "1",
                "",
                "",
                "n",
                "DELETE",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use={9001}, disks=set())
        self.assertEqual(job.vmids, (9001,))
        self.assertEqual(job.destroy_vmids, frozenset({9001}))
        self.assertEqual(job.collision, "overwrite")
        blob = "".join(script.written)
        self.assertIn("VMID 9001 has no OS disk.", blob)
        self.assertIn("Your existing templates will now be DELETED and new templates created.", blob)
        self.assertNotIn("Backup: ", blob)

    def test_unknown_storage_reasks(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "",
                "nope",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["NFS-SATA-SSD2"], in_use=set(), disks=set())
        self.assertEqual(job.storage, "NFS-SATA-SSD2")
        self.assertEqual(job.vmids, (9001,))
        self.assertIn("unknown storage: nope", "".join(script.written))

    def test_image_inserts_into_an_existing_vmid(self) -> None:
        script = _Script(
            ["2", "1", "1", "910", "", "", "", "yes"]
        )
        job = script.run(
            dry_run=False,
            in_use={910},
            disks={910},
            placed={910: "nfs-templates"},
            formats={910: "raw"},
            kinds={"nfs-templates": "nfs"},
        )
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.vmids, (910,))
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.disk_format, "raw")
        self.assertEqual(job.kept_storage, ((910, "nfs-templates"),))
        self.assertEqual(job.kept_format, ((910, "raw"),))
        self.assertEqual(job.dest_dir, "")
        self.assertEqual(job.collision, "backup")
        self.assertEqual(job.destroy_vmids, frozenset())
        blob = "".join(script.written)
        self.assertNotIn("Please enter an existing", blob)
        self.assertIn("bookworm VM Template ID: ", blob)
        self.assertIn("VMID 910 is on nfs-templates. The volume is raw.", blob)
        self.assertNotIn("VM Disk Storage Path", blob)
        self.assertNotIn("Disk Format", blob)
        self.assertNotIn("The VM stays", blob)
        self.assertEqual(job.backup_vmids, frozenset({910}))
        self.assertIn("  Backup existing images\n", blob)
        self.assertIn("Create a backup copy of existing images? (y/n) [Yes]: ", blob)
        self.assertIn("Image backups will be created at /var/backups/pve-template-prep", blob)
        self.assertIn("Your existing disk images will be backed up, then new images created.", blob)
        self.assertEqual(
            _summary(blob),
            [
                ("Release", "VMID", "Storage", "Format", "Existing disk"),
                ("bookworm", "910", "nfs-templates", "raw", "back up, replace"),
            ],
        )
        self.assertNotIn("Replace VMID", blob)
        self.assertNotIn("Hardware defaults:", blob)

    def test_unreadable_disk_format_reasks_the_vmid(self) -> None:
        calls = {"n": 0}

        def disk_format_of(vmid: int):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return "raw"

        script = _Script(
            ["2", "1", "1", "910", "910", "", "", "", "yes"]
        )
        job = script.run(
            dry_run=False,
            in_use={910},
            disks={910},
            placed={910: "nfs-templates"},
            kinds={"nfs-templates": "nfs"},
            disk_format_of=disk_format_of,
        )
        self.assertEqual(job.vmids, (910,))
        self.assertEqual(job.kept_format, ((910, "raw"),))
        blob = "".join(script.written)
        self.assertIn("Could not read the disk format for VMID 910.", blob)
        self.assertNotIn("Disk Format", blob)

    def test_image_inserts_keep_each_disk_format(self) -> None:
        script = _Script(
            ["2", "1", "1, 2", "910", "911", "", "", "", "yes"]
        )
        job = script.run(
            dry_run=False,
            in_use={910, 911},
            disks={910, 911},
            placed={910: "nfs-templates", 911: "local"},
            formats={910: "qcow2", 911: "raw"},
            kinds={"nfs-templates": "nfs", "local": "dir"},
        )
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.kept_format, ((910, "qcow2"), (911, "raw")))
        self.assertEqual(job.storage, "")
        blob = "".join(script.written)
        self.assertEqual(_column(blob, "Storage"), ["nfs-templates", "local"])
        self.assertEqual(_column(blob, "Format"), ["qcow2", "raw"])
        self.assertNotIn("Disk Format", blob)

    def test_template_replace_recommends_the_current_storage(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "910",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["local", "nfs-templates"],
            kinds={"local": "dir", "nfs-templates": "nfs"},
            in_use={910},
            disks={910},
            placed={910: "nfs-templates"},
        )
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.kept_storage, ())
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        blob = "".join(script.written)
        self.assertIn("Enter keeps nfs-templates.", blob)
        self.assertIn("VMID 910 is on nfs-templates.", blob)
        self.assertLess(blob.index("  1) nfs-templates\n"), blob.index("  2) local\n"))

    def test_template_replace_enter_keeps_the_first_when_disks_differ(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "910",
                "911",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["local", "nfs-templates"],
            kinds={"local": "dir", "nfs-templates": "nfs"},
            in_use={910, 911},
            disks={910, 911},
            placed={910: "nfs-templates", 911: "local"},
        )
        self.assertEqual(job.storage, "nfs-templates")
        blob = "".join(script.written)
        self.assertIn("VMID 910 is on nfs-templates.", blob)
        self.assertIn("VMID 911 is on local.", blob)
        self.assertIn("Enter keeps nfs-templates.", blob)

    def test_unreadable_disk_storage_reasks_the_vmid(self) -> None:
        calls = {"n": 0}

        def disk_storage(vmid: int):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return "nfs-templates"

        script = _Script(
            ["2", "1", "1", "910", "910", "", "", "", "", "yes"]
        )
        job = script.run(
            dry_run=False,
            in_use={910},
            disks={910},
            kinds={"nfs-templates": "nfs"},
            disk_storage=disk_storage,
        )
        self.assertEqual(job.vmids, (910,))
        self.assertEqual(job.kept_storage, ((910, "nfs-templates"),))
        self.assertIn("Could not read the disk storage for VMID 910.", "".join(script.written))
        self.assertNotIn("VM Disk Storage Path", "".join(script.written))

    def test_update_refuses_a_free_vmid(self) -> None:
        script = _Script(["2", "1", "1", "9001", "910", "", "", "", "yes"])
        job = script.run(
            dry_run=False,
            in_use={910},
            disks={910},
            placed={910: "nfs-templates"},
            formats={910: "raw"},
        )
        self.assertEqual(job.vmids, (910,))
        self.assertEqual(job.template_vmids, frozenset())
        blob = "".join(script.written)
        self.assertIn("VMID 9001 is not in use. Type a template VMID.", blob)
        self.assertNotIn("Try another VMID", blob)
        self.assertNotIn("Create a new VM template", blob)

    def test_delete_x_exits(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "9001",
                "1",
                "",
                "",
                "n",
                "X",
            ]
        )
        with self.assertRaises(PromptAbort):
            script.run(dry_run=False, storages=["dir-templates"], in_use={9001}, disks={9001})

    def test_one_backup_choice_covers_every_replaced_vmid(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1, 2",
                "910",
                "911",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["dir-templates"],
            in_use={910, 911},
            disks={910, 911},
        )
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910, 911}))
        self.assertEqual(job.backup_vmids, frozenset({910, 911}))
        blob = "".join(script.written)
        self.assertEqual(_column(blob, "Existing"), ["back up, delete VM", "back up, delete VM"])
        self.assertNotIn("DELETE", blob)

    def test_template_can_stay_a_vm(self) -> None:
        script = _Script(
            ["", "1", "1", "", "1", "", "n", "", "", "", "n", "yes"]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use=set(), disks=set())
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.vmids, (9001,))
        self.assertFalse(job.make_template)
        self.assertFalse(job.clean_cache)
        blob = "".join(script.written)
        self.assertEqual(
            _summary(blob)[1],
            ("bookworm", "9001", "debian-12-cloud", "dir-templates", "qcow2", "new"),
        )
        self.assertIn("qm template: no", blob)
        self.assertIn("Your new VMs will now be created.", blob)
        self.assertIn("Convert the new VM with qm template? (y/n) [Yes]: ", blob)
        self.assertNotIn("cache: delete", blob)

    def test_image_pairs_each_release_with_its_own_vmid(self) -> None:
        script = _Script(
            ["2", "1", "1, 2", "911", "910", "2", "", "", "", "n", "DELETE"]
        )
        job = script.run(
            dry_run=False,
            storages=["local", "nfs-templates"],
            in_use={910, 911},
            disks={910, 911},
        )
        self.assertEqual(job.vmids, (911, 910))
        self.assertEqual(job.backup_vmids, frozenset())
        self.assertEqual(job.template_vmids, frozenset())
        self.assertEqual(job.collision, "overwrite")
        blob = "".join(script.written)
        self.assertIn("bookworm VM Template ID: ", blob)
        self.assertIn("trixie VM Template ID: ", blob)
        self.assertIn("No backup images will be created.", blob)
        self.assertIn("Your existing disk images will now be DELETED and new images created.", blob)
        self.assertIn(DELETE_GATE, blob)
        self.assertEqual(_column(blob, "VMID"), ["911", "910"])
        self.assertEqual(_column(blob, "Existing disk"), ["replace", "replace"])

    def test_update_refuses_a_free_vmid_on_a_later_release(self) -> None:
        script = _Script(["2", "1", "1, 2", "910", "920", "911", "", "", "", "yes"])
        job = script.run(
            dry_run=False,
            in_use={910, 911},
            disks={910, 911},
            placed={910: "nfs-templates", 911: "local"},
            formats={910: "qcow2", 911: "raw"},
        )
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.template_vmids, frozenset())
        blob = "".join(script.written)
        self.assertIn("VMID 920 is not in use. Type a template VMID.", blob)
        self.assertNotIn("Create a new VM template", blob)
        self.assertEqual(_column(blob, "VMID"), ["910", "911"])

    def test_update_refuses_a_boot_disk_on_zfs(self) -> None:
        script = _Script(["2", "1", "1", "910", "911", "", "", "", "yes"])
        job = script.run(
            dry_run=False,
            in_use={910, 911},
            disks={910, 911},
            placed={910: "vm-data", 911: "local"},
            formats={910: "raw", 911: "qcow2"},
            kinds={"vm-data": "zfspool", "local": "dir"},
        )
        self.assertEqual(job.vmids, (911,))
        self.assertEqual(job.kept_storage, ((911, "local"),))
        self.assertEqual(job.kept_format, ((911, "qcow2"),))
        blob = "".join(script.written)
        self.assertIn(
            "VMID 910 boot disk: vm-data is ZFS. Disk images go on file storage only. "
            "Type another VMID.",
            blob,
        )

    def test_block_storage_is_not_listed_for_a_new_template(self) -> None:
        script = _Script(["", "1", "1", "", "1", "2", "", "", "", "", "", "yes"])
        job = script.run(
            dry_run=False,
            storages=["vm-data", "local-lvm", "local"],
            kinds={"vm-data": "zfspool", "local-lvm": "lvmthin", "local": "dir"},
            in_use=set(),
            disks=set(),
        )
        self.assertEqual(job.storage, "local")
        self.assertEqual(job.disk_format, "raw")
        blob = "".join(script.written)
        self.assertIn(
            "Not listed: vm-data (ZFS), local-lvm (LVM-thin). "
            "Disk images go on file storage only.",
            blob,
        )
        self.assertIn("  1) local\n", blob)
        self.assertNotIn(") vm-data", blob)
        self.assertEqual(_column(blob, "Storage"), ["local"])
        self.assertEqual(_column(blob, "Format"), ["raw"])

    def test_only_block_storage_ends_the_interview(self) -> None:
        script = _Script(["", "1", "1", ""])
        with self.assertRaises(PromptAbort):
            script.run(
                dry_run=False,
                storages=["vm-data"],
                kinds={"vm-data": "zfspool"},
                in_use=set(),
                disks=set(),
            )
        self.assertIn("No file storage accepts the VM disk.", "".join(script.written))

    def test_unreadable_storage_type_still_asks_the_format(self) -> None:
        script = _Script(["", "1", "1", "", "1", "", "", "", "", "", "", "yes"])
        job = script.run(dry_run=True, storages=["local"], kinds={}, in_use=set(), disks=set())
        self.assertEqual(job.disk_format, "qcow2")
        blob = "".join(script.written)
        self.assertIn("Could not read the type of local.", blob)
        self.assertIn("Choose the volume format on local.", blob)

    def test_standalone_raw_is_named_raw(self) -> None:
        script = _Script(["3", "1", "1", "1", "2", "", "", "yes"])
        job = script.run(dry_run=False, imports=[("local", "/var/lib/vz/import")])
        self.assertEqual(job.disk_format, "raw")
        blob = "".join(script.written)
        self.assertIn("Choose the image format in local import/.", blob)
        self.assertEqual(_column(blob, "File"), ["/var/lib/vz/import/debian-12-pve.raw"])

    def test_cifs_storage_asks_and_enter_is_qcow2(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["share"],
            kinds={"share": "cifs"},
            in_use=set(),
            disks=set(),
        )
        self.assertEqual(job.storage, "share")
        self.assertEqual(job.disk_format, "qcow2")
        blob = "".join(script.written)
        self.assertIn("Choose the volume format on share.", blob)
        self.assertIn("1) QEMU qcow2 (.qcow2)", blob)
        self.assertIn("2) raw (.raw)", blob)

    def test_cifs_raw_is_still_a_file_format(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "",
                "1",
                "2",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["share"],
            kinds={"share": "cifs"},
            in_use=set(),
            disks=set(),
        )
        self.assertEqual(job.disk_format, "raw")
        blob = "".join(script.written)
        self.assertEqual(
            _summary(blob)[1],
            ("bookworm", "9001", "debian-12-cloud", "share", "raw", "new"),
        )
        self.assertIn("Guest prep: yes   Password SSH: yes   Root SSH: yes", blob)

    def test_root_ssh_no_leaves_the_image_default(self) -> None:
        script = _Script(["3", "1", "1", "1", "", "", "n", "yes"])
        job = script.run(dry_run=False, imports=[("local", "/var/lib/vz/import")])
        self.assertTrue(job.prep)
        self.assertFalse(job.permit_root)
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.dest_dir, "/var/lib/vz/import")
        blob = "".join(script.written)
        self.assertIn("  Root SSH Access\n", blob)
        self.assertIn("Cloud-init user must be set to 'root'.", blob)
        self.assertNotIn("rm /etc/ssh/sshd_config.d/00-enableroot.conf", blob)
        self.assertNotIn("and restart SSHd.", blob)
        self.assertIn(ROOT_SSH + " (y/n) [Yes]: ", blob)
        self.assertEqual(_column(blob, "File"), ["/var/lib/vz/import/debian-12-pve.qcow2"])
        self.assertIn("Guest prep: yes   Password SSH: yes   Root SSH: no", blob)

    def test_password_ssh_no_is_asked_on_its_own(self) -> None:
        script = _Script(["3", "1", "1", "1", "", "n", "", "yes"])
        job = script.run(dry_run=False, imports=[("local", "/var/lib/vz/import")])
        self.assertFalse(job.password_auth)
        self.assertTrue(job.permit_root)
        blob = "".join(script.written)
        self.assertIn("  SSH Password Authentication\n", blob)
        self.assertIn(PASSWORD_AUTH + " (y/n) [Yes]: ", blob)
        self.assertLess(blob.index("  SSH Password Authentication\n"), blob.index("  Root SSH Access\n"))
        self.assertIn("Guest prep: yes   Password SSH: no   Root SSH: yes", blob)

    def test_normal_vm_is_refused_on_template_replace(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "910",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["dir-templates"],
            in_use={910},
            disks={910},
            roles={910: "vm"},
        )
        self.assertEqual(job.vmids, (9001,))
        self.assertEqual(job.destroy_vmids, frozenset())
        blob = "".join(script.written)
        self.assertIn(
            "VMID 910 is a normal VM, not a template. It will not be changed. Type a template VMID.",
            blob,
        )
        self.assertIn("bookworm VM Template ID [9001]: ", blob)

    def test_linked_clone_is_refused_on_every_action(self) -> None:
        image = _Script(["2", "1", "1", "910", "911", "", "", "", "yes"])
        published = image.run(
            dry_run=False,
            in_use={910, 911},
            disks={911},
            roles={910: "clone", 911: "template"},
            placed={911: "nfs-templates"},
            formats={911: "qcow2"},
        )
        self.assertEqual(published.vmids, (911,))
        self.assertIn("VMID 910 is a linked clone.", "".join(image.written))

    def test_unreadable_check_is_refused(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "910",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["dir-templates"],
            in_use={910},
            roles={910: "blocked"},
        )
        self.assertEqual(job.destroy_vmids, frozenset())
        self.assertIn("could not be read", "".join(script.written))

    def test_bad_vmid_reasks_with_the_reason(self) -> None:
        script = _Script(
            [
                "",
                "1",
                "1",
                "abc",
                "",
                "1",
                "",
                "",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use=set(), disks=set())
        self.assertEqual(job.vmids, (9001,))
        blob = "".join(script.written)
        self.assertIn("Type one VMID, from 100 through 999999999.", blob)
        self.assertIn("bookworm VM Template ID [9001]: ", blob)

    def test_standalone_without_import_storage_aborts(self) -> None:
        script = _Script(["3", "1", "1"])
        with self.assertRaises(PromptAbort):
            script.run(dry_run=False, imports=[])
        blob = "".join(script.written)
        self.assertIn("No storage has import content.", blob)
        self.assertNotIn("Image directory", blob)

    def test_cache_question_is_separate_from_the_interview(self) -> None:
        kept = _Script([""])
        self.assertFalse(
            ask_cache_cleanup(
                kept.read_line,
                kept.write,
                cache=DEFAULT_CACHE,
                failed=False,
            )
        )
        blob = "".join(kept.written)
        self.assertNotIn("backups", blob)
        self.assertIn(f"Delete working files in {DEFAULT_CACHE}?", blob)
        self.assertIn(
            f"Delete working files in {DEFAULT_CACHE}? (y/n) [No]: ",
            blob,
        )
        failed = _Script(["y"])
        self.assertTrue(
            ask_cache_cleanup(
                failed.read_line,
                failed.write,
                cache=DEFAULT_CACHE,
                failed=True,
            )
        )
        blob = "".join(failed.written)
        self.assertIn("A release failed.", blob)
        self.assertIn("including the download", blob)
        self.assertNotIn("backups", blob)


class LayoutTest(unittest.TestCase):
    def test_output_never_writes_two_blank_lines(self) -> None:
        written: list[str] = []
        out = _Output(written.append, paint=False)
        _blank(out)
        out("  Heading\n")
        _blank(out)
        _blank(out)
        out("Select [1]: ")
        _answered(out)
        _blank(out)
        _blank(out)
        out("done\n")
        self.assertEqual("".join(written), "\n  Heading\n\nSelect [1]: \ndone\n")

    def test_painted_output_goes_to_the_margin(self) -> None:
        written: list[str] = []
        out = _Output(written.append, paint=True)
        with mock.patch.object(ui, "_depth", "16"), mock.patch.object(ui, "_theme", "dark"):
            out("one\n  two\n")
            out("\033[90m  colored\033[0m\n")
        self.assertEqual(
            "".join(written),
            "\033[37m  one\033[0m\n\033[37m  two\033[0m\n\033[90m  colored\033[0m\n",
        )

    def test_only_a_destructive_cell_is_drawn_as_danger(self) -> None:
        def read_line() -> str:
            return ""

        read_line.use_questionary = True
        written: list[str] = []
        table = [
            ("Release", "VMID", "Existing disk"),
            ("bookworm", "910", "replace"),
            ("trixie", "911", "back up, replace"),
        ]
        with mock.patch.object(ui, "_depth", "16"), mock.patch.object(ui, "_theme", "dark"):
            _write_table(read_line, written.append, table)
        blob = "".join(written)
        self.assertIn("\033[1;91mreplace\033[0m", blob)
        self.assertNotIn("\033[1;91mback up", blob)
        self.assertEqual(blob.count("\033[1;91m"), 1)
        plain: list[str] = []
        _write_table(lambda: "", plain.append, table)
        self.assertEqual(
            "".join(plain),
            "  Release   VMID  Existing disk\n"
            "  bookworm  910   replace\n"
            "  trixie    911   back up, replace\n",
        )

    def test_distro_menu_uses_the_config_name(self) -> None:
        def named(distro: str):
            return [SimpleNamespace(release="9", label="AlmaLinux 9", eol=False, name="AlmaLinux")]

        self.assertEqual(distro_name("alma", named), "AlmaLinux")
        self.assertEqual(distro_name("debian", _specs), "debian")

        def broken(distro: str):
            raise RuntimeError("no config")

        self.assertEqual(distro_name("fedora", broken), "fedora")


if __name__ == "__main__":
    unittest.main()
