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
        SimpleNamespace(release="11", label="bullseye", eol=True),
        SimpleNamespace(release="12", label="bookworm", eol=False),
        SimpleNamespace(release="13", label="trixie", eol=False),
    ]


def _normalize(distro: str, raw: str) -> str:
    known = {"11": "11", "12": "12", "13": "13"}
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

    def run(self, *, dry_run: bool, storages: list[str] | None = None) -> Job:
        def list_storages() -> list[str]:
            if storages is None:
                raise AssertionError("storage list should not be consulted")
            return list(storages)

        return interview(
            self.read_line,
            self.write,
            dry_run=dry_run,
            list_storages=list_storages,
            releases_for=_specs,
            normalize_release=_normalize,
        )


class InterviewTest(unittest.TestCase):
    def test_image_debian_two_releases(self) -> None:
        script = _Script(
            [
                "1",
                "12, 13",
                "1",
                "1",
                "/tmp/images",
                "1",
                "yes",
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
            ),
        )
        blob = "".join(script.written)
        self.assertIn("debian 12 -> image raw /tmp/images/debian-12-pve.img guest-prep=yes", blob)
        self.assertIn("debian 13 -> image raw /tmp/images/debian-13-pve.img guest-prep=yes", blob)
        self.assertIn("Type yes to run: ", blob)

    def test_template_defaults_and_destroy_retype(self) -> None:
        script = _Script(
            [
                "1",
                "12, 13",
                "",
                "",
                "2",
                "910",
                "910",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=True, storages=["local-dir", "nfs-templates"])
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
        self.assertEqual(job.collision, "backup")
        self.assertTrue(job.dry_run)
        self.assertEqual(job.cache_dir, DEFAULT_CACHE)
        blob = "".join(script.written)
        self.assertIn(
            "debian 12 -> template VMID 910 name debian-12-cloud storage nfs-templates raw guest-prep=yes",
            blob,
        )
        self.assertIn("DESTROY stopped template VMID 910", blob)
        self.assertIn("hardware: bridge vmbr0, memory 2048 MB, cores 2", blob)
        self.assertIn("Type yes to run: ", blob)

    def test_confirm_no_aborts(self) -> None:
        script = _Script(
            [
                "1",
                "12",
                "1",
                "1",
                "/tmp/images",
                "1",
                "y",
                "no",
            ]
        )
        with self.assertRaises(PromptAbort):
            script.run(dry_run=False)

    def test_bad_release_then_good(self) -> None:
        script = _Script(
            [
                "0",
                "1",
                "nope",
                "",
                "12",
                "1",
                "",
                "/tmp/x",
                "",
                "n",
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
        self.assertIn("need at least one release", blob)
        self.assertIn("pick a distro number", blob)

    def test_duplicate_canonical_release_reasks(self) -> None:
        answers = ["12, bookworm", "13"]
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
            )

    def test_destroy_drops_ids_outside_the_set(self) -> None:
        script = _Script(
            [
                "1",
                "12, 13",
                "",
                "2",
                "nfs-templates",
                "910",
                "910, 999",
                "n",
                "n",
                "vmbr1",
                "4096",
                "4",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=[])
        self.assertEqual(job.mode, "template")
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.storage, "nfs-templates")
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset({910}))
        self.assertFalse(job.prep)
        self.assertEqual(job.bridge, "vmbr1")
        self.assertEqual(job.memory_mb, 4096)
        self.assertEqual(job.cores, 4)
        self.assertIn("ignoring VMID 999 (not selected)", "".join(script.written))

    def test_empty_destroy_refuses(self) -> None:
        script = _Script(
            [
                "1",
                "12, 13",
                "",
                "",
                "1",
                "910",
                "",
                "",
                "",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"])
        self.assertEqual(job.vmids, (910, 911))
        self.assertEqual(job.destroy_vmids, frozenset())
        self.assertEqual(job.storage, "dir-templates")

    def test_existing_skips_format_destroy_and_hardware(self) -> None:
        script = _Script(
            [
                "1",
                "12",
                "3",
                "1",
                "400",
                "n",
                "yes",
            ]
        )
        job = script.run(dry_run=False, storages=["dir-templates"])
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
            ),
        )
        blob = "".join(script.written)
        self.assertIn("debian 12 -> existing VMID 400 storage dir-templates guest-prep=no", blob)


if __name__ == "__main__":
    unittest.main()
