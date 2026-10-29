# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Unattended disk swap. No live qm calls."""

from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

# discover -s tests puts tests/ on sys.path. `python3 -m unittest tests.test_unattended`
# does not, so privtmp would be missing. Insert the directory that holds it.
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from privtmp import private_tempdir  # noqa: E402
from pve_prep.enroll import Enrollment, load_enrollment, save_enrollment  # noqa: E402
from pve_prep.job import DEFAULT_BACKUPS, DEFAULT_CACHE, Job, action_for  # noqa: E402
from pve_prep.unattended import run_unattended  # noqa: E402


_OLD = "ab" * 32
_NEW = "cd" * 32
_OTHER = "ef" * 32
_STORAGE = "nfs-templates"
_CONFIG = f"template: 1\nscsi0: {_STORAGE}:base-910-disk-0.raw\n"
_PLAIN = f"name: plain\nscsi0: {_STORAGE}:vm-910-disk-0.raw\n"
_STOPPED = "status: stopped\n"


class _Proc:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


class FakeRun:
    """Record argv and pop scripted (returncode, stdout) pairs, or raise a scripted error."""

    def __init__(self, steps: list) -> None:
        self.steps = list(steps)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> _Proc:
        self.calls.append(list(argv))
        if not self.steps:
            raise AssertionError(f"no scripted response for {argv}")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        code, out = step
        return _Proc(code, out)


def _record(
    *,
    vmid: int = 910,
    distro: str = "ubuntu",
    release: str = "24.04",
    digest: str = _OLD,
    checksum_alg: str = "256",
    password_auth: bool = True,
    permit_root: bool = True,
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


def _gate(config: str = _CONFIG, *, pct_code: int = 2, status: str = _STOPPED) -> list:
    """qm status, pct status, qm config, then the pvesm list inside refuse_replace_target."""
    return [
        (0, status),
        (pct_code, ""),
        (0, config),
        (0, "[]"),
    ]


def _commands(vmid: int, *, config: bool = True, pvesm: bool = True, pct: bool = True) -> list[list[str]]:
    calls = [["qm", "status", str(vmid)]]
    if pct:
        calls.append(["pct", "status", str(vmid)])
    if config:
        calls.append(["qm", "config", str(vmid)])
    if pvesm:
        calls.append(["pvesm", "list", _STORAGE, "--output-format", "json"])
    return calls


class _Outcome:
    def __init__(self) -> None:
        self.code = 0
        self.out = ""
        self.fake = FakeRun([])
        self.jobs: list[tuple[Job, str, int]] = []
        self.digests: dict[int, str | None] = {}
        self.algs: dict[int, str | None] = {}
        self.asked: list[tuple[str, str]] = []
        self.kinds: list[str] = []
        self.logs: dict[str, str] = {}


def _run(
    records: list[Enrollment],
    steps: list,
    *,
    digest: tuple[str, str] | None = (_NEW, "256"),
    storage_kind: str = "dir",
    perform=None,
    digest_error: BaseException | None = None,
    dry_run: bool = False,
    log_directory: Path | str | None = None,
) -> _Outcome:
    outcome = _Outcome()
    with private_tempdir() as tmp:
        root = Path(tmp)
        nodes = root / "nodes"
        nodes.mkdir()
        for record in records:
            save_enrollment(record, directory=root)

        def digest_for(distro: str, release: str) -> tuple[str, str]:
            outcome.asked.append((distro, release))
            if digest_error is not None:
                raise digest_error
            assert digest is not None
            return digest

        def kind_of(storage: str) -> str:
            outcome.kinds.append(storage)
            return storage_kind

        def wrapped(job: Job, release: str, vmid: int):
            outcome.jobs.append((job, release, vmid))
            if perform is not None:
                return perform(job, release, vmid)
            return None

        logs = root / "log" if log_directory is None else Path(log_directory)
        outcome.fake = FakeRun(steps)
        buffer = StringIO()
        with redirect_stdout(buffer):
            outcome.code = run_unattended(
                "weekly",
                directory=root,
                log_directory=logs,
                digest_for=digest_for,
                run=outcome.fake,
                perform=wrapped,
                storage_kind=kind_of,
                nodes_root=nodes,
                dry_run=dry_run,
            )
        outcome.out = buffer.getvalue()
        if logs.is_dir():
            outcome.logs = {path.name: path.read_text(encoding="utf-8") for path in logs.glob("*.log")}
        for record in records:
            loaded = load_enrollment(record.vmid, directory=root)
            outcome.digests[record.vmid] = None if loaded is None else loaded.digest
            outcome.algs[record.vmid] = None if loaded is None else loaded.checksum_alg
    return outcome


def _assert_insert(test: unittest.TestCase, job: Job, record: Enrollment, storage: str, fmt: str) -> None:
    test.assertEqual(job.mode, "image")
    test.assertEqual(job.distro, record.distro)
    test.assertEqual(job.releases, (record.release,))
    test.assertEqual(job.vmids, (record.vmid,))
    test.assertTrue(job.prep)
    test.assertEqual(job.password_auth, record.password_auth)
    test.assertEqual(job.permit_root, record.permit_root)
    test.assertEqual(job.backup_vmids, frozenset({record.vmid}))
    test.assertEqual(job.backup_dir, DEFAULT_BACKUPS)
    test.assertEqual(job.kept_storage, ((record.vmid, storage),))
    test.assertEqual(job.kept_format, ((record.vmid, fmt),))
    test.assertEqual(job.storage, storage)
    test.assertEqual(job.disk_format, fmt)
    test.assertEqual(job.collision, "overwrite")
    test.assertEqual(job.destroy_vmids, frozenset())
    test.assertEqual(job.template_vmids, frozenset())
    test.assertFalse(job.make_template)
    test.assertFalse(job.dry_run)
    test.assertEqual(job.cache_dir, DEFAULT_CACHE)
    test.assertEqual(job.dest_dir, "")
    test.assertEqual(job.bridge, "vmbr0")
    test.assertEqual(job.memory_mb, 2048)
    test.assertEqual(job.cores, 2)
    test.assertFalse(job.clean_cache)
    test.assertFalse(job.verbose)
    test.assertEqual(action_for(job, record.vmid), "insert")


class UnattendedTests(unittest.TestCase):
    def test_digest_match_skips_perform_and_keeps_the_file(self) -> None:
        record = _record()
        done = _run([record], _gate(), digest=(_OLD, "256"))
        self.assertEqual(done.code, 0)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("VMID 910 is current", done.out)
        self.assertNotIn("FAIL", done.out)
        self.assertEqual(done.fake.calls, _commands(910))
        self.assertEqual(done.fake.steps, [])
        self.assertEqual(done.asked, [("ubuntu", "24.04")])

    def test_digest_differs_inserts_and_stores_the_new_digest(self) -> None:
        record = _record()
        done = _run([record], _gate(), digest=(_NEW, "256"))
        self.assertEqual(done.code, 0)
        self.assertEqual(len(done.jobs), 1)
        job, release, vmid = done.jobs[0]
        self.assertEqual(release, record.release)
        self.assertEqual(vmid, record.vmid)
        self.assertIn(record.vmid, job.backup_vmids)
        self.assertEqual(job.kept_storage, ((record.vmid, _STORAGE),))
        self.assertEqual(job.password_auth, record.password_auth)
        self.assertTrue(job.prep)
        self.assertEqual(action_for(job, record.vmid), "insert")
        _assert_insert(self, job, record, _STORAGE, "raw")
        self.assertEqual(done.digests[910], _NEW)
        self.assertEqual(done.fake.calls, _commands(910))
        self.assertTrue(job.quiet)
        self.assertIn("Update of ubuntu 24.04 VM template disk image SUCCESSFUL.", done.out)
        self.assertIn("/var/backups/pve-template-prep/", done.out)
        self.assertNotIn("FAILED", done.out)
        self.assertEqual(done.logs, {})

    def test_algorithm_change_is_stored_with_the_new_digest(self) -> None:
        published = "cd" * 64
        record = _record(digest=_OLD, checksum_alg="256")
        done = _run([record], _gate(), digest=(published, "512"))
        self.assertEqual(done.code, 0)
        self.assertEqual(len(done.jobs), 1)
        self.assertEqual(done.digests[910], published)
        self.assertEqual(done.algs[910], "512")
        self.assertNotIn("FAIL", done.out)

    def test_dry_run_invokes_perform_and_keeps_the_digest(self) -> None:
        record = _record()
        done = _run([record], _gate(), digest=(_NEW, "256"), dry_run=True)
        self.assertEqual(done.code, 0)
        self.assertEqual(len(done.jobs), 1)
        job, release, vmid = done.jobs[0]
        self.assertEqual(release, record.release)
        self.assertEqual(vmid, record.vmid)
        self.assertTrue(job.dry_run)
        self.assertFalse(job.quiet)
        self.assertEqual(action_for(job, record.vmid), "insert")
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("would be updated", done.out)
        self.assertIn("Nothing was changed.", done.out)
        self.assertNotIn("SUCCESSFUL", done.out)

    def test_perform_failure_keeps_the_digest_and_continues(self) -> None:
        first = _record(vmid=910, release="24.04", digest=_OLD, password_auth=True)
        second = _record(
            vmid=920,
            release="22.04",
            digest=_OTHER,
            password_auth=False,
            permit_root=False,
        )

        def perform(job: Job, release: str, vmid: int) -> None:
            if vmid == 910:
                raise RuntimeError("disk swap failed")

        done = _run(
            [second, first],
            _gate() + _gate(),
            digest=(_NEW, "256"),
            perform=perform,
        )
        self.assertEqual(done.code, 1)
        self.assertEqual([vmid for _job, _release, vmid in done.jobs], [910, 920])
        self.assertEqual(done.digests[910], _OLD)
        self.assertEqual(done.digests[920], _NEW)
        self.assertIn("Update of ubuntu 24.04 VM template disk image FAILED.", done.out)
        self.assertIn("disk swap failed.", done.out)
        self.assertIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("910.log", done.out)
        self.assertIn("disk swap failed", done.logs["910.log"])
        self.assertIn("Update of ubuntu 22.04 VM template disk image SUCCESSFUL.", done.out)
        failed_job = done.jobs[0][0]
        continued = done.jobs[1][0]
        self.assertTrue(failed_job.password_auth)
        self.assertFalse(continued.password_auth)
        self.assertEqual(continued.kept_storage, ((920, _STORAGE),))
        self.assertEqual(action_for(continued, 920), "insert")
        self.assertEqual(done.fake.calls, _commands(910) + _commands(920))

    def test_running_guest_is_not_swapped(self) -> None:
        done = _run([_record()], [(0, "status: running\n")])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("VMID 910 is running, so the disk was left unchanged.", done.out)
        self.assertIn("VM 910 is running", done.logs["910.log"])
        self.assertEqual(done.fake.calls, [["qm", "status", "910"]])
        self.assertEqual(done.asked, [])

    def test_normal_vm_is_not_swapped(self) -> None:
        done = _run([_record()], _gate(_PLAIN)[:3])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("VMID 910 is not a template, so the disk was left unchanged.", done.out)
        self.assertIn("not a template", done.logs["910.log"])
        self.assertEqual(done.fake.calls, _commands(910, pvesm=False))
        self.assertEqual(done.asked, [])

    def test_container_id_is_not_swapped(self) -> None:
        done = _run([_record()], [(0, _STOPPED), (0, "status: stopped\n")])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("VMID 910 is a container, so it was left unchanged.", done.out)
        self.assertIn("VMID 910 is a container", done.logs["910.log"])
        self.assertEqual(done.fake.calls, _commands(910, config=False, pvesm=False))

    def test_missing_pct_is_a_failure(self) -> None:
        done = _run([_record()], [(0, _STOPPED), OSError("No such file or directory")])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("A required program could not be run (pct).", done.out)
        self.assertIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("pct", done.logs["910.log"])
        self.assertEqual(done.fake.calls, _commands(910, config=False, pvesm=False))

    def test_missing_vm_is_a_failure(self) -> None:
        done = _run([_record()], [(2, "Configuration file does not exist\n")])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertIn("VMID 910 was not found, so nothing was changed.", done.out)
        self.assertIn("VM 910 does not exist", done.logs["910.log"])
        self.assertEqual(done.fake.calls, [["qm", "status", "910"]])

    def test_no_enrollment_file_does_nothing(self) -> None:
        done = _run([], [])
        self.assertEqual(done.code, 0)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.fake.calls, [])
        self.assertEqual(done.asked, [])
        self.assertEqual(done.out, "")

    def test_block_storage_is_not_swapped(self) -> None:
        done = _run([_record()], _gate(), storage_kind="zfspool")
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("The boot disk is on block storage, which this update does not replace.", done.out)
        self.assertIn(_STORAGE, done.logs["910.log"])
        self.assertIn("zfspool", done.logs["910.log"])
        self.assertEqual(done.kinds, [_STORAGE])
        self.assertEqual(done.asked, [])
        self.assertEqual(done.fake.calls, _commands(910))

    def test_digest_lookup_failure_skips_perform(self) -> None:
        done = _run([_record()], _gate(), digest_error=RuntimeError("checksum host down"))
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("The published image could not be checked or downloaded.", done.out)
        self.assertIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("checksum host down", done.logs["910.log"])
        self.assertEqual(done.asked, [("ubuntu", "24.04")])

    def test_bad_disk_format_is_not_swapped(self) -> None:
        config = f"template: 1\nscsi0: {_STORAGE}:base-910-disk-0.vmdk\n"
        done = _run([_record()], _gate(config))
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertEqual(done.kinds, [])
        self.assertIn("The boot disk is not raw or qcow2, so it was left unchanged.", done.out)
        self.assertIn("not raw or qcow2", done.logs["910.log"])

    def test_attached_failure_says_the_new_disk_is_in_place(self) -> None:
        def perform(_job: Job, _release: str, _vmid: int) -> None:
            raise RuntimeError("qm template failed; the new disk is attached. Run qm template 910")

        done = _run([_record()], _gate(), perform=perform)
        self.assertEqual(done.code, 1)
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("The new disk is attached, but VMID 910 is not a template.", done.out)
        self.assertIn("Run qm template 910", done.out)
        self.assertNotIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("the new disk is attached", done.logs["910.log"])

    def test_cleanup_failure_is_still_a_successful_update(self) -> None:
        detail = (
            "ubuntu 24.04 VM disk image created and imported. Template 910 updated. "
            "Old disk saved in /var/backups/pve-template-prep. Old disk cleanup failed: volume is locked."
        )

        def perform(_job: Job, _release: str, _vmid: int) -> str:
            return detail

        done = _run([_record()], _gate(), perform=perform)
        self.assertEqual(done.code, 0)
        self.assertEqual(done.digests[910], _NEW)
        self.assertIn("SUCCESSFUL.", done.out)
        self.assertIn("The previous disk could not be deleted and was left unused.", done.out)
        self.assertIn("910.log", done.out)
        self.assertIn("volume is locked", done.logs["910.log"])

    def test_unfinished_swap_tells_the_operator_to_finish(self) -> None:
        config = f"tags: pve-prep-swap-910\nscsi0: {_STORAGE}:base-910-disk-0.raw\n"
        done = _run([_record()], _gate(config))
        self.assertEqual(done.code, 1)
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("Its last disk update did not finish.", done.out)
        self.assertIn("Run qm template 910", done.out)
        self.assertIn("This run left the disk as it found it.", done.out)
        self.assertNotIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("its disk update did not finish", done.logs["910.log"])

    def test_log_write_failure_keeps_the_reason_in_the_sentence(self) -> None:
        def perform(_job: Job, _release: str, _vmid: int) -> None:
            raise RuntimeError("disk swap failed")

        done = _run([_record()], _gate(), perform=perform, log_directory="logs")
        self.assertEqual(done.code, 1)
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("disk swap failed.", done.out)
        self.assertIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("The detail log could not be written:", done.out)
        self.assertEqual(done.logs, {})


if __name__ == "__main__":
    unittest.main()
