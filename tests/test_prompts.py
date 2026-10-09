"""Scripted interviews. No host, no sibling modules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep.job import DEFAULT_CACHE, Job  # noqa: E402
from pve_prep.prompts import PromptAbort, _ask_releases, interview  # noqa: E402


def _specs(distro: str):
    if distro != "debian":
        raise AssertionError(distro)
    return [
        SimpleNamespace(release="12", label="bookworm", eol=False),
        SimpleNamespace(release="13", label="trixie", eol=False),
    ]


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
        in_use: set[int] | None = None,
        disks: set[int] | None = None,
    ) -> Job:
        def list_storages() -> list[str]:
            if storages is None:
                raise AssertionError("storage list should not be consulted")
            return list(storages)

        def vmids_in_use(vmids: tuple[int, ...]) -> set[int] | None:
            if in_use is None:
                return None
            return {vmid for vmid in vmids if vmid in in_use}

        def vm_has_disks(vmid: int) -> bool | None:
            if disks is None:
                return None
            return vmid in disks

        return interview(
            self.read_line,
            self.write,
            dry_run=dry_run,
            list_storages=list_storages,
            releases_for=_specs,
            normalize_release=_normalize,
            vmids_in_use=vmids_in_use,
            vm_has_disks=vm_has_disks,
        )


class InterviewTest(unittest.TestCase):
    def test_image_debian_two_releases(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "2",
                "1",
                "",
                "/tmp/images",
                "yes",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False)
        self.assertEqual(
            job,
            Job(
                distro="debian",
                releases=("12", "13"),
                mode="image",
                disk_format="raw",
                dest_dir="/tmp/images",
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
            ),
        )
        blob = "".join(script.written)
        self.assertIn("debian 12 -> image raw /tmp/images/debian-12-pve.img guest-prep=yes", blob)
        self.assertIn("cache: keep /var/tmp/pve-template-prep/cache", blob)
        self.assertIn("debian 13 -> image raw /tmp/images/debian-13-pve.img guest-prep=yes", blob)
        self.assertIn("ZFS raw", blob)
        self.assertIn("Type YES: ", blob)
        self.assertNotIn("Disk:", blob)

    def test_template_replaces_only_the_typed_vmid(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "910",
                "2",
                "DELETE",
                "2",
                "",
                "",
                "",
                "",
                "yes",
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
        self.assertEqual(job.disk_format, "raw")
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
        blob = "".join(script.written)
        self.assertIn("Complete PVE VM template", blob)
        self.assertIn("VM Disk Storage Path:", blob)
        self.assertIn("Not in use: 911.", blob)
        self.assertIn("permanently deleted", blob)
        self.assertIn("Do not back up the existing template VM disk", blob)
        self.assertEqual(job.backup_vmids, frozenset())
        self.assertIn(
            "debian 12 -> template VMID 910 name debian-12-cloud storage nfs-templates raw guest-prep=yes",
            blob,
        )
        self.assertIn("VMID 910: the existing disk is not kept.", blob)
        self.assertIn("hardware: bridge vmbr0, memory 2048 MB, cores 2", blob)
        self.assertNotIn("non-template", blob)

    def test_empty_vmid_starts_at_9001_and_counts_up(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "",
                "1",
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
        self.assertIn("VMID [9001-9002]: ", blob)
        self.assertIn("debian 12 -> template VMID 9001", blob)
        self.assertIn("debian 13 -> template VMID 9002", blob)
        self.assertNotIn("Disk:", blob)

    def test_default_skips_vmids_that_are_in_use(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "",
                "1",
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
        self.assertIn("VMID [9003-9004]: ", blob)
        self.assertIn("Not in use: 9003-9004.", blob)
        self.assertNotIn("Disk:", blob)
        self.assertNotIn("Replace VMID", blob)

    def test_default_skips_a_hole(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "",
                "1",
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
        self.assertIn("VMID [9001, 9003]: ", blob)

    def test_free_vmids_skip_the_disk_prompt(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "",
                "1",
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
        self.assertIn("Not in use: 9001-9002.", blob)
        self.assertNotIn("Disk:", blob)
        self.assertNotIn("Replace VMID", blob)

    def test_confirm_no_aborts(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "2",
                "1",
                "",
                "/tmp/images",
                "y",
                "",
                "no",
            ]
        )
        with self.assertRaises(PromptAbort):
            script.run(dry_run=False)

    def test_confirm_typo_reasks(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "2",
                "1",
                "",
                "/tmp/images",
                "y",
                "",
                "yess",
                "yes",
            ]
        )
        job = script.run(dry_run=False)
        self.assertEqual(job.releases, ("12",))
        self.assertIn("type YES to proceed, or X to exit", "".join(script.written))

    def test_bad_release_then_good(self) -> None:
        script = _Script(
            [
                "0",
                "1",
                "nope",
                "",
                "12",
                "1",
                "2",
                "",
                "",
                "/tmp/x",
                "n",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False)
        self.assertEqual(job.distro, "debian")
        self.assertEqual(job.releases, ("12",))
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.disk_format, "raw")
        self.assertEqual(job.dest_dir, "/tmp/x")
        self.assertEqual(job.collision, "backup")
        self.assertFalse(job.prep)
        self.assertEqual(job.vmids, ())
        blob = "".join(script.written)
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
        self.assertIn("duplicate release: 12\n", written)
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
            [
                "1",
                "1, 2",
                "",
                "2",
                "910",
                "1",
                "nfs-templates",
                "n",
                "n",
                "vmbr1",
                "4096",
                "4",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=[], in_use={910}, disks={910})
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        self.assertEqual(job.collision, "backup")
        self.assertFalse(job.prep)
        self.assertEqual(job.bridge, "vmbr1")
        self.assertEqual(job.memory_mb, 4096)
        self.assertEqual(job.cores, 4)
        self.assertIn("copied into the cache", "".join(script.written))

    def test_disk_answer_empty_reasks(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "910",
                "",
                "2",
                "DELETE",
                "1",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use={910}, disks={910})
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        self.assertEqual(job.storage, "dir-templates")
        self.assertEqual(job.collision, "overwrite")
        self.assertIn("pick 1 to back up, or 2 to skip the backup", "".join(script.written))
        self.assertEqual(job.backup_vmids, frozenset())

    def test_no_disk_skips_the_policy_question(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "",
                "",
                "9001",
                "DELETE",
                "1",
                "",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use={9001}, disks=set())
        self.assertEqual(job.vmids, (9001,))
        self.assertEqual(job.destroy_vmids, frozenset({9001}))
        self.assertEqual(job.collision, "overwrite")
        blob = "".join(script.written)
        self.assertIn("VMID 9001 has no OS disk.", blob)
        self.assertNotIn("Backup: ", blob)

    def test_unknown_storage_reasks(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "",
                "",
                "",
                "nope",
                "1",
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

    def test_existing_skips_format_disk_and_hardware(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "3",
                "400",
                "1",
                "n",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use={400}, disks=set())
        self.assertEqual(
            job,
            Job(
                distro="debian",
                releases=("12",),
                mode="existing",
                disk_format="raw",
                dest_dir="",
                storage="dir-templates",
                cache_dir=DEFAULT_CACHE,
                collision="backup",
                vmids=(400,),
                prep=False,
                bridge="vmbr0",
                memory_mb=2048,
                cores=2,
                dry_run=False,
                destroy_vmids=frozenset(),
                make_template=False,
                clean_cache=False,
            ),
        )
        blob = "".join(script.written)
        self.assertIn("debian 12 -> existing VMID 400 storage dir-templates guest-prep=no", blob)
        self.assertNotIn("VM disk format:", blob)
        self.assertNotIn("Disk:", blob)
        self.assertNotIn("Hardware defaults:", blob)

    def test_image_inserts_into_an_existing_vmid(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "2",
                "",
                "910",
                "1",
                "2",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(
            dry_run=False,
            storages=["local", "nfs-templates"],
            in_use={910},
            disks={910},
        )
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.vmids, (910,))
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.dest_dir, "")
        self.assertEqual(job.collision, "backup")
        self.assertEqual(job.destroy_vmids, frozenset())
        blob = "".join(script.written)
        self.assertIn("The VM stays. The new disk is inserted.", blob)
        self.assertEqual(job.backup_vmids, frozenset({910}))
        self.assertIn("debian 12 -> insert VMID 910 storage nfs-templates raw disk=backup", blob)
        self.assertNotIn("Replace VMID", blob)
        self.assertNotIn("Hardware defaults:", blob)

    def test_image_free_vmid_reasks_then_publishes(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "2",
                "",
                "9001",
                "",
                "/tmp/images",
                "yes",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, in_use=set(), disks=set())
        self.assertEqual(job.vmids, ())
        self.assertEqual(job.dest_dir, "/tmp/images")
        self.assertIn("Not in use: 9001.", "".join(script.written))

    def test_delete_x_exits(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "",
                "",
                "9001",
                "2",
                "X",
            ]
        )
        with self.assertRaises(PromptAbort):
            script.run(dry_run=False, storages=["dir-templates"], in_use={9001}, disks={9001})

    def test_each_vmid_keeps_its_own_backup_choice(self) -> None:
        script = _Script(
            [
                "1",
                "1, 2",
                "",
                "",
                "910",
                "2",
                "DELETE",
                "1",
                "1",
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
        self.assertEqual(job.backup_vmids, frozenset({911}))
        blob = "".join(script.written)
        self.assertIn("VMID 910: the existing disk is not kept.", blob)
        self.assertIn("VMID 911: the existing disk is copied into the cache", blob)

    def test_template_can_stay_a_vm_and_can_drop_the_cache(self) -> None:
        script = _Script(
            [
                "1",
                "1",
                "",
                "",
                "",
                "1",
                "",
                "",
                "n",
                "y",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"], in_use=set(), disks=set())
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.vmids, (9001,))
        self.assertFalse(job.make_template)
        self.assertTrue(job.clean_cache)
        blob = "".join(script.written)
        self.assertIn(
            "debian 12 -> vm VMID 9001 name debian-12-cloud storage dir-templates raw guest-prep=yes",
            blob,
        )
        self.assertIn("Type YES to create the VM.", blob)
        self.assertIn("cache: delete the files in /var/tmp/pve-template-prep/cache", blob)
        self.assertIn("Enter converts to a template.", blob)


if __name__ == "__main__":
    unittest.main()
