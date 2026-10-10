"""parse_vmid, published names, guest names, release actions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep.job import Job, action_for, parse_vmid, published_name, vm_name  # noqa: E402


class PublishedNameTest(unittest.TestCase):
    def test_raw_uses_img(self) -> None:
        self.assertEqual(published_name("debian", "12", "raw"), "debian-12-pve.img")

    def test_qcow2_keeps_extension(self) -> None:
        self.assertEqual(
            published_name("ubuntu", "24.04", "qcow2"),
            "ubuntu-24.04-pve.qcow2",
        )

    def test_unknown_format_rejected(self) -> None:
        with self.assertRaises(ValueError):
            published_name("debian", "12", "vmdk")


class VmNameTest(unittest.TestCase):
    def test_cloud_suffix(self) -> None:
        self.assertEqual(vm_name("alma", "9"), "alma-9-cloud")
        self.assertEqual(vm_name("fedora", "43"), "fedora-43-cloud")


def _job(**kwargs) -> Job:
    base = dict(
        distro="debian",
        releases=("12",),
        mode="image",
        disk_format="raw",
        dest_dir="",
        storage="dir-templates",
        cache_dir="/tmp",
        collision="backup",
        vmids=(910,),
        prep=True,
        bridge="vmbr0",
        memory_mb=2048,
        cores=2,
        dry_run=True,
        destroy_vmids=frozenset(),
    )
    base.update(kwargs)
    return Job(**base)


class ParseVmidTest(unittest.TestCase):
    def test_one_id(self) -> None:
        self.assertEqual(parse_vmid("910"), 910)
        self.assertEqual(parse_vmid("  100  "), 100)

    def test_list_range_and_bounds_rejected(self) -> None:
        for text in ("910,911", "910 911", "910-912", "99", "1000000000", "", "   ", "abc"):
            with self.assertRaises(ValueError):
                parse_vmid(text)


class ActionForTest(unittest.TestCase):
    def test_image_publish_insert_and_new_template(self) -> None:
        published = _job(vmids=(), dest_dir="/tmp/images", storage="")
        self.assertEqual(action_for(published, None), "file")
        inserted = _job()
        self.assertEqual(action_for(inserted, 910), "insert")
        created = _job(template_vmids=frozenset({910}))
        self.assertEqual(action_for(created, 910), "template")

    def test_template_and_existing_stay_on_their_functions(self) -> None:
        template = _job(mode="template", make_template=True)
        self.assertEqual(action_for(template, 910), "template")
        existing = _job(mode="existing")
        self.assertEqual(action_for(existing, 910), "existing")


if __name__ == "__main__":
    unittest.main()
