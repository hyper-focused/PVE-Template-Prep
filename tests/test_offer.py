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
from pve_prep.cronscript import cron_script  # noqa: E402
from pve_prep.enroll import Enrollment, load_enrollment, save_enrollment  # noqa: E402
from pve_prep.offer import offer_created  # noqa: E402
from pve_prep.prompts import PromptAbort, ask_unattended_schedule  # noqa: E402

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


def _offer(lines: _Lines, enroll_dir: str, cron_dir: str, created, **kwargs):
    return offer_created(
        lines.read_line,
        lines.write,
        created=created,
        distro=kwargs.get("distro", "ubuntu"),
        password_auth=kwargs.get("password_auth", False),
        permit_root=kwargs.get("permit_root", True),
        digest_for=kwargs.get("digest_for", _digest),
        program=kwargs.get("program", _PROGRAM),
        directory=enroll_dir,
        cron_directory=cron_dir,
        dry_run=kwargs.get("dry_run", False),
    )


def _names(directory: str) -> list[str]:
    return sorted(path.name for path in Path(directory).iterdir())


class AskUnattendedScheduleTests(unittest.TestCase):
    def test_enter_n_no_and_1_decline(self) -> None:
        for answer in ("", "n", "no", "1"):
            lines = _Lines([answer])
            self.assertIsNone(ask_unattended_schedule(lines.read_line, lines.write))
            self.assertIn("Automated updates", lines.text)
            self.assertIn(
                "These templates can get a cron script that replaces the boot disk "
                "when the upstream image checksum changes.",
                lines.text,
            )
            self.assertIn("It does not apply to a standalone image.", lines.text)
            self.assertIn(
                "Enter does not install anything, and turns off earlier automated updates for these VMIDs.",
                lines.text,
            )
            self.assertIn("  1) No automated updates\n", lines.text)
            self.assertIn("  2) Weekly automated updates\n", lines.text)
            self.assertIn("  3) Monthly automated updates\n", lines.text)
            self.assertIn("Select [1]: ", lines.text)
            self.assertIn("No automated updates.", lines.text)

    def test_weekly_and_monthly_words_and_numbers(self) -> None:
        weekly = _Lines(["2"])
        self.assertEqual(ask_unattended_schedule(weekly.read_line, weekly.write), "weekly")
        self.assertIn("Weekly automated updates.", weekly.text)
        word = _Lines(["weekly"])
        self.assertEqual(ask_unattended_schedule(word.read_line, word.write), "weekly")
        monthly = _Lines(["3"])
        self.assertEqual(ask_unattended_schedule(monthly.read_line, monthly.write), "monthly")
        self.assertIn("Monthly automated updates.", monthly.text)
        word = _Lines(["monthly"])
        self.assertEqual(ask_unattended_schedule(word.read_line, word.write), "monthly")

    def test_bad_answer_asks_again(self) -> None:
        lines = _Lines(["9", "2"])
        self.assertEqual(ask_unattended_schedule(lines.read_line, lines.write), "weekly")
        self.assertIn("pick 1, 2, or 3", lines.text)
        self.assertEqual(lines.reads, 2)

    def test_eof_raises_prompt_abort(self) -> None:
        lines = _Lines([])
        with self.assertRaises(PromptAbort):
            ask_unattended_schedule(lines.read_line, lines.write)
        self.assertNotIn("No automated updates.", lines.text)

    def test_widget_menu_defaults_to_no(self) -> None:
        lines = _Lines([])

        def read_line() -> str:
            raise AssertionError("numbered reader")

        read_line.use_questionary = True
        with (
            mock.patch("pve_prep.prompts.ui.enabled", return_value=True),
            mock.patch("pve_prep.prompts.ui.select", return_value="weekly") as select,
        ):
            picked = ask_unattended_schedule(read_line, lines.write)
        self.assertEqual(picked, "weekly")
        choices = select.call_args.args[1]
        self.assertEqual([value for _title, value in choices], ["no", "weekly", "monthly"])
        self.assertEqual(select.call_args.kwargs["default"], "no")
        self.assertIn("Weekly automated updates.", lines.text)

        with (
            mock.patch("pve_prep.prompts.ui.enabled", return_value=True),
            mock.patch("pve_prep.prompts.ui.select", return_value="no"),
        ):
            self.assertIsNone(ask_unattended_schedule(read_line, lines.write))


class OfferCreatedTests(unittest.TestCase):
    def test_empty_created_does_not_ask(self) -> None:
        lines = _Lines(["2"])

        def digest_for(distro: str, release: str) -> tuple[str, str]:
            raise AssertionError("digest")

        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [], digest_for=digest_for)
            self.assertIsNone(result)
            self.assertEqual(lines.reads, 0)
            self.assertEqual(lines.written, [])
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])

    def test_enter_returns_none_and_writes_no_file(self) -> None:
        lines = _Lines([""])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertIsNone(result)
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])
            self.assertIn("No automated updates.", lines.text)

    def test_no_on_a_created_list_writes_nothing(self) -> None:
        lines = _Lines(["no"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertIsNone(result)
            self.assertGreater(lines.reads, 0)
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])

    def test_weekly_writes_enrollment_and_exact_cron_script(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(result, "weekly")
            self.assertEqual(_names(enroll_dir), ["9001.json"])
            self.assertEqual(
                load_enrollment(9001, directory=enroll_dir),
                Enrollment(
                    vmid=9001,
                    distro="ubuntu",
                    release="24.04",
                    digest=_DIGEST,
                    checksum_alg="256",
                    password_auth=False,
                    permit_root=True,
                    schedule="weekly",
                ),
            )
            cron_path = Path(cron_dir) / _CRON_NAME
            self.assertEqual(cron_path.read_text(encoding="utf-8"), cron_script(_PROGRAM, "weekly"))
            self.assertIn("Weekly automated updates.", lines.text)

    def test_second_offer_same_program_leaves_the_cron_script(self) -> None:
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            first = _Lines(["2"])
            self.assertEqual(
                _offer(first, enroll_dir, cron_dir, [("24.04", 9001)]),
                "weekly",
            )
            cron_path = Path(cron_dir) / _CRON_NAME
            self.assertEqual(cron_path.read_text(encoding="utf-8"), cron_script(_PROGRAM, "weekly"))
            pinned = 1_700_000_000_000_000_000
            os.utime(cron_path, ns=(pinned, pinned))
            before = cron_path.stat()
            second = _Lines(["2"])
            self.assertEqual(
                _offer(second, enroll_dir, cron_dir, [("24.04", 9001)]),
                "weekly",
            )
            after = cron_path.stat()
            self.assertEqual(cron_path.read_text(encoding="utf-8"), cron_script(_PROGRAM, "weekly"))
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_ino, before.st_ino)

    def test_monthly_schedule(self) -> None:
        lines = _Lines(["3"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(result, "monthly")
            self.assertEqual(load_enrollment(9001, directory=enroll_dir).schedule, "monthly")
            cron_path = Path(cron_dir) / _CRON_NAME
            self.assertEqual(cron_path.read_text(encoding="utf-8"), cron_script(_PROGRAM, "monthly"))
            self.assertIn("Monthly automated updates.", lines.text)

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
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])
        self.assertIn(cron_script(_PROGRAM, "weekly"), lines.text)
        start = lines.text.index("{")
        payload, _end = json.JSONDecoder().raw_decode(lines.text[start:])
        self.assertEqual(
            payload,
            {
                "checksum_alg": "256",
                "digest": _DIGEST,
                "distro": "ubuntu",
                "password_auth": False,
                "permit_root": True,
                "release": "24.04",
                "schedule": "weekly",
                "vmid": 9001,
            },
        )

    def test_digest_failure_skips_that_vmid_and_continues(self) -> None:
        def digest_for(distro: str, release: str) -> tuple[str, str]:
            if release == "12":
                raise RuntimeError("missing checksum")
            return _DIGEST, "256"

        lines = _Lines(["2"])
        created = [("12", 9001), ("13", 9002)]
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(lines, enroll_dir, cron_dir, created, digest_for=digest_for)
            self.assertEqual(result, "weekly")
            self.assertIn("Enrollment for 12 was not saved: missing checksum", lines.text)
            self.assertIn("was not saved", lines.text)
            self.assertNotIn("FAIL ", lines.text)
            self.assertIsNone(load_enrollment(9001, directory=enroll_dir))
            kept = load_enrollment(9002, directory=enroll_dir)
            self.assertIsNotNone(kept)
            self.assertEqual(kept.release, "13")
            cron_path = Path(cron_dir) / _CRON_NAME
            self.assertEqual(cron_path.read_text(encoding="utf-8"), cron_script(_PROGRAM, "weekly"))

    def test_dry_run_omits_json_when_digest_fails(self) -> None:
        def digest_for(distro: str, release: str) -> tuple[str, str]:
            if release == "12":
                raise RuntimeError("missing checksum")
            return "cd" * 32, "256"

        lines = _Lines(["weekly"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            result = _offer(
                lines,
                enroll_dir,
                cron_dir,
                [("12", 9001), ("13", 9002)],
                dry_run=True,
                digest_for=digest_for,
            )
            self.assertEqual(result, "weekly")
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])
        self.assertIn("Enrollment for 12 was not saved: missing checksum", lines.text)
        self.assertNotIn("FAIL ", lines.text)
        self.assertNotIn("9001", lines.text[lines.text.index("was not saved") :])
        self.assertIn(cron_script(_PROGRAM, "weekly"), lines.text)
        start = lines.text.index("{")
        payload, _end = json.JSONDecoder().raw_decode(lines.text[start:])
        self.assertEqual(payload["vmid"], 9002)
        self.assertEqual(payload["digest"], "cd" * 32)
        self.assertNotIn('"vmid": 9001', lines.text)

    def test_cron_conflict_keeps_saved_enrollment(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            cron_path = Path(cron_dir) / _CRON_NAME
            cron_path.write_text("other\n", encoding="utf-8")
            result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertEqual(result, "weekly")
            self.assertEqual(cron_path.read_text(encoding="utf-8"), "other\n")
            self.assertIn("left unchanged", lines.text)
            self.assertIsNotNone(load_enrollment(9001, directory=enroll_dir))

    def test_prompt_abort_propagates_and_writes_no_file(self) -> None:
        lines = _Lines([])

        def digest_for(distro: str, release: str) -> tuple[str, str]:
            raise AssertionError("digest")

        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            with self.assertRaises(PromptAbort):
                _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)], digest_for=digest_for)
            self.assertEqual(_names(enroll_dir), [])
            self.assertEqual(_names(cron_dir), [])

    def _existing(self, enroll_dir: str, vmid: int, schedule: str = "monthly") -> None:
        save_enrollment(
            Enrollment(
                vmid=vmid,
                distro="ubuntu",
                release="22.04",
                digest="cd" * 32,
                checksum_alg="256",
                password_auth=True,
                permit_root=False,
                schedule=schedule,
            ),
            directory=enroll_dir,
        )

    def test_decline_removes_an_existing_record_for_that_vmid_only(self) -> None:
        for answer in ("", "no", "1"):
            with self.subTest(answer=answer):
                lines = _Lines([answer])
                with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
                    self._existing(enroll_dir, 9001)
                    self._existing(enroll_dir, 9002)
                    result = _offer(lines, enroll_dir, cron_dir, [("24.04", 9001), ("24.04", 9003)])
                    self.assertIsNone(result)
                    self.assertIsNone(load_enrollment(9001, directory=enroll_dir))
                    self.assertIsNotNone(load_enrollment(9002, directory=enroll_dir))
                    self.assertEqual(_names(cron_dir), [])
                self.assertIn("Automated updates for VMID 9001 were removed.", lines.text)
                self.assertNotIn("VMID 9003", lines.text)

    def test_weekly_replaces_an_existing_record_with_this_run(self) -> None:
        lines = _Lines(["2"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            self._existing(enroll_dir, 9001, schedule="monthly")
            self.assertEqual(_offer(lines, enroll_dir, cron_dir, [("24.04", 9001)]), "weekly")
            record = load_enrollment(9001, directory=enroll_dir)
            self.assertEqual(record.schedule, "weekly")
            self.assertEqual(record.release, "24.04")
            self.assertEqual(record.digest, _DIGEST)
            self.assertFalse(record.password_auth)

    def test_dry_run_decline_removes_nothing(self) -> None:
        lines = _Lines(["no"])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            self._existing(enroll_dir, 9001)
            _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)], dry_run=True)
            self.assertIsNotNone(load_enrollment(9001, directory=enroll_dir))
        self.assertIn("Dry run: any automated update record for VMID 9001 would be removed.", lines.text)

    def test_prompt_abort_keeps_an_existing_record(self) -> None:
        lines = _Lines([])
        with private_tempdir() as enroll_dir, private_tempdir() as cron_dir:
            self._existing(enroll_dir, 9001)
            with self.assertRaises(PromptAbort):
                _offer(lines, enroll_dir, cron_dir, [("24.04", 9001)])
            self.assertIsNotNone(load_enrollment(9001, directory=enroll_dir))


if __name__ == "__main__":
    unittest.main()
