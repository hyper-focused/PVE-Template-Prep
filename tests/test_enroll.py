# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Enrollment files: save, load, list, and replace."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

# discover -s tests puts tests/ on sys.path. `python3 -m unittest tests.test_enroll`
# does not, so privtmp would be missing. Insert the directory that holds it.
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from privtmp import private_tempdir  # noqa: E402
from pve_prep.enroll import (  # noqa: E402
    Enrollment,
    EnrollmentError,
    enrollments_for,
    load_enrollment,
    remove_enrollment,
    replace_digest,
    save_enrollment,
)


def _record(
    *,
    vmid: int = 910,
    distro: str = "ubuntu",
    release: str = "24.04",
    digest: str = "ab" * 32,
    checksum_alg: str = "256",
    password_auth: bool = True,
    permit_root: bool = False,
    schedule: str = "weekly",
) -> Enrollment:
    return Enrollment(
        vmid=vmid,
        distro=distro,
        release=release,
        digest=digest,
        checksum_alg=checksum_alg,
        password_auth=password_auth,
        permit_root=permit_root,
        schedule=schedule,
    )


class EnrollmentTests(unittest.TestCase):
    def test_round_trip_save_and_load(self) -> None:
        with private_tempdir() as tmp:
            record = _record()
            path = save_enrollment(record, directory=tmp)
            self.assertEqual(path, Path(tmp).resolve() / "910.json")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                text,
                json.dumps(
                    {
                        "checksum_alg": "256",
                        "digest": record.digest,
                        "distro": "ubuntu",
                        "password_auth": True,
                        "permit_root": False,
                        "release": "24.04",
                        "schedule": "weekly",
                        "vmid": 910,
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            )
            self.assertEqual(load_enrollment(910, directory=tmp), record)
            self.assertEqual(load_enrollment(910, directory=Path(tmp)), record)
            names = sorted(entry.name for entry in Path(tmp).iterdir())
            self.assertEqual(names, ["910.json"])

    def test_enrollments_for_returns_one_schedule_sorted_by_vmid(self) -> None:
        with private_tempdir() as tmp:
            low = _record(vmid=150, digest="11" * 32)
            high = _record(vmid=1000, digest="22" * 32, password_auth=False)
            monthly = _record(
                vmid=200,
                schedule="monthly",
                checksum_alg="512",
                digest="33" * 64,
                permit_root=True,
            )
            save_enrollment(high, directory=tmp)
            save_enrollment(monthly, directory=tmp)
            save_enrollment(low, directory=tmp)
            root = Path(tmp)
            (root / "notes.txt").write_text("ignore", encoding="utf-8")
            (root / "abc.json").write_text("not json", encoding="utf-8")
            (root / "100.json.bak").write_text("{}", encoding="utf-8")
            self.assertEqual(enrollments_for("weekly", directory=tmp), [low, high])
            self.assertEqual(enrollments_for("monthly", directory=Path(tmp)), [monthly])
            self.assertEqual(enrollments_for("weekly", directory=root / "missing"), [])

    def test_bad_digest_vmid_and_schedule_rejected(self) -> None:
        good = _record()
        digests = (
            "AB" * 32,  # uppercase
            "a" * 63,  # odd length
            "g" * 64,  # non-hex
            "ab" * 31,  # even, wrong length
            "ab" * 64,  # length for the other alg
            "",
        )
        for digest in digests:
            with self.assertRaises(EnrollmentError):
                replace(good, digest=digest)
        for vmid in (99, 0, -1, 1_000_000_000, True):
            with self.assertRaises(EnrollmentError):
                replace(good, vmid=vmid)
        _record(vmid=100)
        _record(vmid=999_999_999)
        for schedule in ("daily", "Weekly", "weekly ", ""):
            with self.assertRaises(EnrollmentError):
                replace(good, schedule=schedule)
        for distro in ("", "deb ian", "a/b", "a..b", "foo/../bar"):
            with self.assertRaises(EnrollmentError):
                replace(good, distro=distro)
        for release in ("", "24..04", "12/13", "12 13"):
            with self.assertRaises(EnrollmentError):
                replace(good, release=release)
        with private_tempdir() as tmp:
            (Path(tmp) / "0100.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(EnrollmentError) as caught:
                enrollments_for("weekly", directory=tmp)
            self.assertTrue(str(caught.exception).startswith("VMID 100:"))
            (Path(tmp) / "0100.json").unlink()
            path = Path(tmp) / "910.json"
            payload = {
                "vmid": 910,
                "distro": "ubuntu",
                "release": "24.04",
                "digest": "AB" * 32,
                "checksum_alg": "256",
                "password_auth": True,
                "permit_root": False,
                "schedule": "weekly",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=tmp)
            self.assertTrue(str(caught.exception).startswith("VMID 910:"))
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=tmp)
            self.assertTrue(str(caught.exception).startswith("VMID 910:"))
            payload["digest"] = "ab" * 32
            payload["vmid"] = 1_000_000_000
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=tmp)
            self.assertTrue(str(caught.exception).startswith("VMID 910:"))
            payload["vmid"] = 911
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=tmp)
            self.assertTrue(str(caught.exception).startswith("VMID 910:"))
            path.unlink()
            (Path(tmp) / "200.json").write_text("{", encoding="utf-8")
            with self.assertRaises(EnrollmentError) as caught:
                enrollments_for("weekly", directory=tmp)
            self.assertTrue(str(caught.exception).startswith("VMID 200:"))
            with self.assertRaises(EnrollmentError):
                load_enrollment(99, directory=tmp)
            with self.assertRaises(EnrollmentError):
                enrollments_for("daily", directory=Path(tmp) / "missing")

    def test_replace_digest_changes_only_the_digest(self) -> None:
        with private_tempdir() as tmp:
            original = _record(digest="ab" * 32, permit_root=True, schedule="monthly")
            path = save_enrollment(original, directory=tmp)
            updated = replace_digest(original.vmid, "cd" * 32, directory=tmp)
            self.assertEqual(updated.digest, "cd" * 32)
            self.assertEqual(replace(updated, digest=original.digest), original)
            self.assertEqual(load_enrollment(original.vmid, directory=tmp), updated)
            before = path.read_bytes()
            for digest in ("AB" * 32, "a" * 63, "g" * 64, "ab" * 64):
                with self.assertRaises(EnrollmentError):
                    replace_digest(original.vmid, digest, directory=tmp)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(load_enrollment(original.vmid, directory=tmp), updated)
            with self.assertRaises(EnrollmentError):
                replace_digest(4242, "ab" * 32, directory=tmp)

    def test_symlink_at_json_path_is_refused(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp)
            target = root / "kept.txt"
            original = b"original-bytes"
            target.write_bytes(original)
            link = root / "910.json"
            link.symlink_to(target)
            with self.assertRaises(EnrollmentError):
                save_enrollment(_record(), directory=tmp)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_bytes(), original)
            with self.assertRaises(EnrollmentError):
                load_enrollment(910, directory=tmp)
            with self.assertRaises(EnrollmentError):
                enrollments_for("weekly", directory=tmp)
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.readlink(), target)

    def test_missing_vmid_loads_as_none(self) -> None:
        with private_tempdir() as tmp:
            self.assertIsNone(load_enrollment(910, directory=tmp))
            self.assertIsNone(load_enrollment(910, directory=Path(tmp) / "missing"))
            save_enrollment(_record(), directory=tmp)
            self.assertIsNone(load_enrollment(911, directory=tmp))

    def test_symlink_directory_is_refused(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp)
            missing = root / "missing-target"
            link = root / "linked"
            link.symlink_to(missing)
            with self.assertRaises(EnrollmentError) as caught:
                enrollments_for("weekly", directory=link)
            self.assertIn("symlink", str(caught.exception))
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=link)
            self.assertIn("symlink", str(caught.exception))
            self.assertFalse(missing.exists())
            self.assertTrue(link.is_symlink())
            real = root / "real"
            real.mkdir()
            save_enrollment(_record(), directory=real)
            followed = root / "followed"
            followed.symlink_to(real)
            with self.assertRaises(EnrollmentError) as caught:
                enrollments_for("weekly", directory=followed)
            self.assertIn("symlink", str(caught.exception))
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=followed)
            self.assertIn("symlink", str(caught.exception))
            self.assertTrue((real / "910.json").is_file())

    def test_remove_enrollment_deletes_only_that_record(self) -> None:
        with private_tempdir() as tmp:
            save_enrollment(_record(vmid=910), directory=tmp)
            save_enrollment(_record(vmid=920), directory=tmp)
            self.assertTrue(remove_enrollment(910, directory=tmp))
            self.assertIsNone(load_enrollment(910, directory=tmp))
            self.assertIsNotNone(load_enrollment(920, directory=tmp))
            self.assertFalse(remove_enrollment(910, directory=tmp))
        self.assertFalse(remove_enrollment(910, directory="/no/such/enroll/dir"))
        with self.assertRaises(EnrollmentError):
            remove_enrollment(0, directory="/no/such/enroll/dir")

    def test_remove_enrollment_unlinks_a_symlink_and_keeps_its_target(self) -> None:
        with private_tempdir() as tmp, private_tempdir() as other:
            target = Path(other) / "keep.json"
            target.write_text("{}", encoding="utf-8")
            (Path(tmp) / "910.json").symlink_to(target)
            self.assertTrue(remove_enrollment(910, directory=tmp))
            self.assertFalse((Path(tmp) / "910.json").is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_symlink_record_is_refused(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp)
            target = root / "elsewhere.json"
            original = b"{}"
            target.write_bytes(original)
            link = root / "910.json"
            link.symlink_to(target)
            with self.assertRaises(EnrollmentError) as caught:
                load_enrollment(910, directory=root)
            self.assertIn("symlink", str(caught.exception))
            with self.assertRaises(EnrollmentError) as caught:
                enrollments_for("weekly", directory=root)
            self.assertIn("symlink", str(caught.exception))
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
