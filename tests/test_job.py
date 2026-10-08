"""parse_vmids, published names, guest names."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep.job import parse_vmids, published_name, vm_name  # noqa: E402


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


class ParseVmidsTest(unittest.TestCase):
    def test_single_start_auto_increments(self) -> None:
        self.assertEqual(parse_vmids("910", 3), (910, 911, 912))
        self.assertEqual(parse_vmids("  910  ", 2), (910, 911))

    def test_list_comma_and_whitespace(self) -> None:
        self.assertEqual(parse_vmids("910, 912", 2), (910, 912))
        self.assertEqual(parse_vmids("910,912", 2), (910, 912))
        self.assertEqual(parse_vmids("910 912", 2), (910, 912))
        self.assertEqual(parse_vmids("100, 101 102", 3), (100, 101, 102))

    def test_inclusive_range(self) -> None:
        self.assertEqual(parse_vmids("910-912", 3), (910, 911, 912))
        self.assertEqual(parse_vmids("100 - 102", 3), (100, 101, 102))

    def test_duplicate_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("910,910", 2)

    def test_too_short_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("910,911", 3)

    def test_range_length_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("910-912", 2)

    def test_id_99_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("99", 1)

    def test_id_100_accepted(self) -> None:
        self.assertEqual(parse_vmids("100", 1), (100,))
        self.assertEqual(parse_vmids("100", 2), (100, 101))

    def test_above_max_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("1000000000", 1)

    def test_auto_increment_past_max_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("999999999", 2)

    def test_count_below_one_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("100", 0)

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("   ", 1)

    def test_reversed_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vmids("912-910", 3)


if __name__ == "__main__":
    unittest.main()
