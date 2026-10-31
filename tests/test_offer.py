# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Automated-update offer. Scripted answers, private temp dirs, no host."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# `python3 -m unittest tests.test_offer` does not put tests/ on sys.path.
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from privtmp import private_tempdir  # noqa: E402
from pve_prep.cronscript import cron_script, install_cron_script  # noqa: E402
from pve_prep.enroll import Enrollment, load_enrollment, save_enrollment  # noqa: E402
from pve_prep.offer import (  # noqa: E402
    apply_prior_enrollment,
    offer_created,
    plan_prior_enrollments,
)
from pve_prep.prompts import (  # noqa: E402
    PromptAbort,
    ask_prior_enrollment,
    ask_unattended_schedule,
)

_PROGRAM = "/usr/local/sbin/pve-template-prep"
_DIGEST = "ab" * 32
_CRON_NAME = "pve-template-prep"


class _Lines:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.written: list[str] = []
        self.reads = 0

    def read_line(self) -> str:
        self.reads += 1
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def write(self, text: str) -> None:
        self.written.append(text)

    @property
    def text(self) -> str:
        return "".join(self.written)


def _digest(distro: str, release: str) -> tuple[str, str]:
    return _DIGEST, "256"


def _pin(vmid: int) -> tuple[str, str]:
    return "nfs-templates", "cd" * 32


def _offer(lines: _Lines, enroll_dir: str, cron_dir: str, created, **kwargs):
    return offer_created(
        lines.read_line,
        lines.write,
        created=created,
        distro=kwargs.get("distro", "ubuntu"),
        password_auth=kwargs.get("password_auth", False),
        permit_root=kwargs.get("permit_root", True),
        digest_for=kwargs.get("digest_for", _digest),
        pin_for=kwargs.get("pin_for", _pin),
        program=kwargs.get("program", _PROGRAM),
        directory=enroll_dir,
        cron_directory=cron_dir,
        dry_run=kwargs.get("dry_run", False),
    )


def _names(directory: str) -> list[str]:
    return sorted(path.name for path in Path(directory).iterdir())


def _cron_path(cron_dir: str, schedule: str) -> Path:
    return Path(cron_dir) / f"cron.{schedule}" / _CRON_NAME


def _existing(enroll_dir: str, vmid: int, schedule: str = "monthly", release: str = "22.04") -> None:
    save_enrollment(
        Enrollment(
            vmid=vmid,
            distro="ubuntu",
            release=release,
            digest="11" * 32,
            checksum_alg="256",
            disk_digest="22" * 32,
            storage="nfs-old",
            password_auth=True,
            permit_root=False,
            schedule=schedule,
        ),
        directory=enroll_dir,
    )


class AskUnattendedScheduleTests(unittest.TestCase):
    def test_enter_n_no_and_1_decline(self) -> None:
        for answer in ("", "n", "no", "1"):
            lines = _Lines([answer])
            self.assertIsNone(ask_unattended_schedule(lines.read_line, lines.write))
            self.assertIn("Automated updates", lines.text)
            self.assertIn("file-based storage", lines.text)
            self.assertIn("One schedule applies to every enrolled template", lines.text)
            self.assertIn("No automated updates.", lines.text)

    def test_weekly_and_monthly(self) -> None:
        weekly = _Lines(["2"])
        self.assertEqual(ask_unattended_schedule(weekly.read_line, weekly.write), "weekly")
        monthly = _Lines(["3"])
        self.assertEqual(ask_unattended_schedule(monthly.read_line, monthly.write), "monthly")

    def test_eof_raises_prompt_abort(self) -> None:
        lines = _Lines([])
        with self.assertRaises(PromptAbort):
            ask_unattended_schedule(lines.read_line, lines.write)


class AskPriorEnrollmentTests(unittest.TestCase):
    def test_keep_is_default(self) -> None:
        record = Enrollment(
            vmid=9001,
            distro="ubuntu",
            release="22.04",
            digest="11" * 32,
            checksum_alg="256",
            disk_digest="22" * 32,
            storage="nfs-old",
            password_auth=True,
            permit_root=False,
            schedule="weekly",
        )
        for answer in ("", "1", "keep"):
            lines = _Lines([answer])
            self.assertEqual(
                ask_prior_enrollment(lines.read_line, lines.write, [record]),
                "keep",
            )
            self.assertIn("Already enrolled for weekly auto-updates", lines.text)
            self.assertIn("VMID 9001 (ubuntu 22.04): weekly", lines.text)
            self.assertIn("Keep current schedule.", lines.text)

    def test_change_to_monthly(self) -> None:
        record = Enrollment(
            vmid=9001,
            distro="ubuntu",
            release="22.04",
            digest="11" * 32,
            checksum_alg="256",
            disk_digest="22" * 32,
            storage="nfs-old",
            password_auth=True,
            permit_root=False,
            schedule="weekly",
        )
        lines = _Lines(["2", "2"])
        self.assertEqual(
            ask_prior_enrollment(lines.read_line, lines.write, [record]),
            "monthly",
        )
        self.assertIn("Change schedule.", lines.text)
        self.assertIn("Monthly automated updates.", lines.text)

    def test_turn_off(self) -> None:
        record = Enrollment(
            vmid=9001,
            distro="ubuntu",
            release="22.04",
            digest="11" * 32,
            checksum_alg="256",
            disk_digest="22" * 32,
            storage="nfs-old",
            password_auth=True,
            permit_root=False,
            schedule="monthly",
        )
        lines = _Lines(["3"])
        self.assertEqual(ask_prior_enrollment(lines.read_line, lines.write, [record]), "off")
        self.assertIn("Turn off automated updates.", lines.text)


class PlanPriorEnrollmentTests(unittest.TestCase):
    def test_none_when_no_records(self) -> None:
        lines = _Lines(["1"])
        with private_tempdir() as enroll_dir:
            plan = plan_prior_enrollments(
                lines.read_line, lines.write, (9001, 9002), directory=enroll_dir
            )
            self.assertIsNone(plan)
            self.assertEqual(lines.reads, 0)

    def test_records_choice_for_enrolled_vmids_only(self) -> None:
        lines = _Lines(["1"])
        with private_tempdir() as enroll_dir:
            _existing(enroll_dir, 9001, schedule="weekly")
            plan = plan_prior_enrollments(
                lines.read_line, lines.write, (9001, 9002), directory=enroll_dir
            )
            self.assertIsNotNone(plan)
            self.assertEqual(plan.vmids, frozenset({9001}))
            self.assertEqual(plan.action, "keep")


class ApplyPriorEnrollmentTests(unittest.TestCase):
    def test_keep_rewrites_digest_storage_and_disk(self) -> None:
        lines = _Lines([])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            _existing(enroll_dir, 9001, schedule="weekly")
            install_cron_script(_PROGRAM, "weekly", directory=cron_dir)
            plan = plan_prior_enrollments(
                _Lines(["1"]).read_line,
                _Lines([]).write,
                (9001,),
                directory=enroll_dir,
            )
            apply_prior_enrollment(
                lines.write,
                plan=plan,
                created=[("24.04", 9001)],
                distro="ubuntu",
                password_auth=False,
                permit_root=True,
                digest_for=_digest,
                pin_for=_pin,
                program=_PROGRAM,
                directory=enroll_dir,
                cron_directory=cron_dir,
            )
            record = load_enrollment(9001, directory=enroll_dir)
            self.assertEqual(record.schedule, "weekly")
            self.assertEqual(record.release, "24.04")
            self.assertEqual(record.digest, _DIGEST)
            self.assertEqual(record.storage, "nfs-templates")
            self.assertEqual(record.disk_digest, "cd" * 32)
            self.assertFalse(record.password_auth)
            self.assertTrue(_cron_path(cron_dir, "weekly").exists())

    def test_change_moves_schedule_and_drops_other_cron(self) -> None:
        lines = _Lines([])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            _existing(enroll_dir, 9001, schedule="monthly")
            install_cron_script(_PROGRAM, "monthly", directory=cron_dir)
            plan = plan_prior_enrollments(
                _Lines(["2", "1"]).read_line,
                lines.write,
                (9001,),
                directory=enroll_dir,
            )
            self.assertEqual(plan.action, "weekly")
            apply_prior_enrollment(
                lines.write,
                plan=plan,
                created=[("24.04", 9001)],
                distro="ubuntu",
                password_auth=False,
                permit_root=True,
                digest_for=_digest,
                pin_for=_pin,
                program=_PROGRAM,
                directory=enroll_dir,
                cron_directory=cron_dir,
            )
            self.assertEqual(load_enrollment(9001, directory=enroll_dir).schedule, "weekly")
            self.assertTrue(_cron_path(cron_dir, "weekly").exists())
            self.assertFalse(_cron_path(cron_dir, "monthly").exists())

    def test_off_removes_json_and_cron(self) -> None:
        lines = _Lines([])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            _existing(enroll_dir, 9001, schedule="weekly")
            install_cron_script(_PROGRAM, "weekly", directory=cron_dir)
            plan = plan_prior_enrollments(
                _Lines(["3"]).read_line,
                lines.write,
                (9001,),
                directory=enroll_dir,
            )
            self.assertEqual(plan.action, "off")
            apply_prior_enrollment(
                lines.write,
                plan=plan,
                created=[("24.04", 9001)],
                distro="ubuntu",
                password_auth=False,
                permit_root=True,
                digest_for=_digest,
                pin_for=_pin,
                program=_PROGRAM,
                directory=enroll_dir,
                cron_directory=cron_dir,
            )
            self.assertIsNone(load_enrollment(9001, directory=enroll_dir))
            self.assertFalse(_cron_path(cron_dir, "weekly").exists())
            self.assertIn("Automated updates for VMID 9001 were removed.", lines.text)


class OfferCreatedTests(unittest.TestCase):
    def test_empty_created_does_not_ask_without_enrollments(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [])
            self.assertIsNone(result)
            self.assertEqual(lines.reads, 0)

    def test_empty_created_offers_turn_off_when_enrollments_exist(self) -> None:
        lines = _Lines(["y"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            _existing(enroll_dir, 9001, schedule="weekly")
            install_cron_script(_PROGRAM, "weekly", directory=cron_dir)
            result = _offer(lines, enroll_dir, cron_dir, [])
            self.assertIsNone(result)
            self.assertIsNone(load_enrollment(9001, directory=enroll_dir))
            self.assertFalse(_cron_path(cron_dir, "weekly").exists())

    def test_weekly_writes_enrollment_and_exact_cron_script(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(result, "weekly")
            self.assertEqual(
                load_enrollment(9001, directory=enroll_dir),
                Enrollment(
                    vmid=9001,
                    distro="ubuntu",
                    release="24.04",
                    digest=_DIGEST,
                    checksum_alg="256",
                    disk_digest="cd" * 32,
                    storage="nfs-templates",
                    password_auth=False,
                    permit_root=True,
                    schedule="weekly",
                ),
            )
            cron_path = _cron_path(cron_dir, "weekly")
            self.assertEqual(cron_path.read_text(encoding="utf-8"), cron_script(_PROGRAM, "weekly"))

    def test_second_offer_same_program_leaves_the_cron_script(self) -> None:
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            self.assertEqual(
                _offer(_Lines(["2"]), enroll_dir, cron_dir, [("24.04", 9001)]),
                "weekly",
            )
            cron_path = _cron_path(cron_dir, "weekly")
            pinned = 1_700_000_000_000_000_000
            os.utime(cron_path, ns=(pinned, pinned))
            before = cron_path.stat()
            # New enrollment path (no prior ask here): pick weekly again.
            self.assertEqual(
                _offer(_Lines(["2"]), enroll_dir, cron_dir, [("24.04", 9002)]),
                "weekly",
            )
            after = cron_path.stat()
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_ino, before.st_ino)

    def test_monthly_schedule(self) -> None:
        lines = _Lines(["3"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(result, "monthly")
            self.assertEqual(load_enrollment(9001, directory=enroll_dir).schedule, "monthly")

    def test_new_weekly_moves_existing_monthly_and_drops_monthly_cron(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            _existing(enroll_dir, 9001, schedule="monthly")
            install_cron_script(_PROGRAM, "monthly", directory=cron_dir)
            self.assertEqual(
                _offer(lines, enroll_dir, cron_dir, [("24.04", 9002)]),
                "weekly",
            )
            self.assertEqual(load_enrollment(9001, directory=enroll_dir).schedule, "weekly")
            self.assertEqual(load_enrollment(9002, directory=enroll_dir).schedule, "weekly")
            self.assertTrue(_cron_path(cron_dir, "weekly").exists())
            self.assertFalse(_cron_path(cron_dir, "monthly").exists())

    def test_decline_writes_nothing_for_brand_new_vmids(self) -> None:
        lines = _Lines(["no"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertIsNone(result)
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])

    def test_dry_run_prints_script_and_json_and_writes_no_files(self) -> None:
        lines = _Lines(["2"])
        with (
            private_tempdir() as enroll_dir,
            private_tempdir() as cron_dir,
            mock.patch("pve_prep.offer.save_enrollment") as save,
            mock.patch("pve_prep.offer.install_cron_script") as install,
        ):
            result = _offer(
                lines,
                enroll_dir,
                cron_dir,
                [("24.04", 9001)],
                dry_run=True,
            )
            save.assert_not_called()
            install.assert_not_called()
            self.assertEqual(result, "weekly")
        self.assertIn(cron_script(_PROGRAM, "weekly"), lines.text)
        start = lines.text.index("{")
        payload, _end = json.JSONDecoder().raw_decode(lines.text[start:])
        self.assertEqual(payload["vmid"], 9001)
        self.assertEqual(payload["schedule"], "weekly")

    def test_pin_failure_skips_that_vmid_and_continues(self) -> None:
        def pin_for(vmid: int) -> tuple[str, str]:
            if vmid == 9001:
                raise RuntimeError("disk path failed")
            return "nfs-templates", "cd" * 32

        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(
                lines,
                enroll_dir,
                cron_dir,
                [("12", 9001), ("13", 9002)],
                pin_for=pin_for,
            )
            self.assertEqual(result, "weekly")
            self.assertIn("Enrollment for 12 was not saved: disk path failed", lines.text)
            self.assertIsNone(load_enrollment(9001, directory=enroll_dir))
            self.assertIsNotNone(load_enrollment(9002, directory=enroll_dir))

    def test_cron_conflict_keeps_saved_enrollment(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            cron_path = _cron_path(cron_dir, "weekly")
            cron_path.parent.mkdir()
            cron_path.write_text("other\n", encoding="utf-8")
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(result, "weekly")
            self.assertEqual(cron_path.read_text(encoding="utf-8"), "other\n")
            self.assertIn("left unchanged", lines.text)
            self.assertIsNotNone(load_enrollment(9001, directory=enroll_dir))

    def test_prompt_abort_propagates_and_writes_no_file(self) -> None:
        lines = _Lines([])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            with self.assertRaises(PromptAbort):
                _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(_names(enroll_dir), [])


if __name__ == "__main__":
    unittest.main()
