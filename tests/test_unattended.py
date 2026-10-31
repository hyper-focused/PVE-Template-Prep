# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Unattended disk swap. No live qm calls."""

from __future__ import annotations

import hashlib
import json
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
from pve_prep.download import DownloadError  # noqa: E402
from pve_prep.unattended import run_unattended, stale_reason  # noqa: E402


_OLD = "ab" * 32
_NEW = "cd" * 32
_OTHER = "ef" * 32
_STORAGE = "nfs-templates"
_DISK_BYTES = b"enrolled-boot-disk"
_DISK_DIGEST = hashlib.sha256(_DISK_BYTES).hexdigest()
_AFTER_BYTES = b"replacement-boot-disk"
_AFTER_DIGEST = hashlib.sha256(_AFTER_BYTES).hexdigest()
_CONFIG = f"template: 1\nscsi0: {_STORAGE}:base-910-disk-0.raw\n"
_PLAIN = f"name: plain\nscsi0: {_STORAGE}:vm-910-disk-0.raw\n"
_STOPPED = "status: stopped\n"
_ENROLLED_PATH = "__ENROLLED__"
_AFTER_PATH = "__AFTER__"


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
    disk_digest: str = _DISK_DIGEST,
    storage: str = _STORAGE,
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
        disk_digest=disk_digest,
        storage=storage,
        password_auth=password_auth,
        permit_root=permit_root,
        schedule=schedule,
    )


def _gate(config: str = _CONFIG, *, pct_code: int = 2, status: str = _STOPPED) -> list:
    """qm status, pct, config, pvesm list, then pvesm path for the enrolled disk fingerprint."""
    return [
        (0, status),
        (pct_code, ""),
        (0, config),
        (0, "[]"),
        (0, _ENROLLED_PATH + "\n"),
    ]


def _after(config: str = _CONFIG) -> list:
    """qm config and pvesm path after a successful swap, to store the new fingerprint."""
    return [
        (0, config),
        (0, _AFTER_PATH + "\n"),
    ]


def _commands(
    vmid: int,
    *,
    config: bool = True,
    pvesm: bool = True,
    pct: bool = True,
    path: bool = True,
    after: bool = False,
) -> list[list[str]]:
    calls = [["qm", "status", str(vmid)]]
    if pct:
        calls.append(["pct", "status", str(vmid)])
    if config:
        calls.append(["qm", "config", str(vmid)])
    if pvesm:
        calls.append(["pvesm", "list", _STORAGE, "--output-format", "json"])
    if path:
        calls.append(["pvesm", "path", f"{_STORAGE}:base-910-disk-0.raw"])
    if after:
        calls.append(["qm", "config", str(vmid)])
        calls.append(["pvesm", "path", f"{_STORAGE}:base-910-disk-0.raw"])
    return calls


class _Outcome:
    def __init__(self) -> None:
        self.code = 0
        self.out = ""
        self.fake = FakeRun([])
        self.jobs: list[tuple[Job, str, int]] = []
        self.digests: dict[int, str | None] = {}
        self.algs: dict[int, str | None] = {}
        self.disk_digests: dict[int, str | None] = {}
        self.storages: dict[int, str | None] = {}
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
    vmlist: object = None,
    confs: dict[tuple[str, int], str] | None = None,
) -> _Outcome:
    """vmlist None leaves the cluster list unreadable. A str is written as is."""
    outcome = _Outcome()
    with private_tempdir() as tmp:
        root = Path(tmp)
        nodes = root / "nodes"
        nodes.mkdir()
        vmlist_path = root / ".vmlist"
        if vmlist is not None:
            text = vmlist if isinstance(vmlist, str) else json.dumps(vmlist)
            vmlist_path.write_text(text, encoding="utf-8")
        for (node, vmid), text in (confs or {}).items():
            conf = nodes / node / "qemu-server" / f"{vmid}.conf"
            conf.parent.mkdir(parents=True, exist_ok=True)
            conf.write_text(text, encoding="utf-8")
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
        enrolled = root / "enrolled.raw"
        enrolled.write_bytes(_DISK_BYTES)
        after = root / "after.raw"
        after.write_bytes(_AFTER_BYTES)
        expanded: list = []
        for step in steps:
            if isinstance(step, BaseException):
                expanded.append(step)
                continue
            code, out = step
            if isinstance(out, str):
                out = out.replace(_ENROLLED_PATH, str(enrolled)).replace(_AFTER_PATH, str(after))
            expanded.append((code, out))
        outcome.fake = FakeRun(expanded)
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
                vmlist_path=vmlist_path,
            )
        outcome.out = buffer.getvalue()
        if logs.is_dir():
            outcome.logs = {path.name: path.read_text(encoding="utf-8") for path in logs.glob("*.log")}
        for record in records:
            loaded = load_enrollment(record.vmid, directory=root)
            outcome.digests[record.vmid] = None if loaded is None else loaded.digest
            outcome.algs[record.vmid] = None if loaded is None else loaded.checksum_alg
            outcome.disk_digests[record.vmid] = None if loaded is None else loaded.disk_digest
            outcome.storages[record.vmid] = None if loaded is None else loaded.storage
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
        done = _run([record], _gate() + _after(), digest=(_NEW, "256"))
        self.assertEqual(done.code, 0)
        self.assertEqual(len(done.jobs), 1)
        job, release, vmid = done.jobs[0]
        self.assertEqual(release, record.release)
        self.assertEqual(vmid, record.vmid)
        self.assertEqual(job.storage, _STORAGE)
        self.assertIn(record.vmid, job.backup_vmids)
        self.assertEqual(job.kept_storage, ((record.vmid, _STORAGE),))
        self.assertEqual(job.password_auth, record.password_auth)
        self.assertTrue(job.prep)
        self.assertEqual(action_for(job, record.vmid), "insert")
        _assert_insert(self, job, record, _STORAGE, "raw")
        self.assertEqual(done.digests[910], _NEW)
        self.assertEqual(done.disk_digests[910], _AFTER_DIGEST)
        self.assertEqual(done.storages[910], _STORAGE)
        self.assertEqual(done.fake.calls, _commands(910, after=True))
        self.assertTrue(job.quiet)
        self.assertIn("Update of ubuntu 24.04 VM template disk image SUCCESSFUL.", done.out)
        self.assertIn("/var/backups/pve-template-prep/", done.out)
        self.assertNotIn("FAILED", done.out)
        self.assertEqual(done.logs, {})

    def test_algorithm_change_is_stored_with_the_new_digest(self) -> None:
        published = "cd" * 64
        record = _record(digest=_OLD, checksum_alg="256")
        done = _run([record], _gate() + _after(), digest=(published, "512"))
        self.assertEqual(done.code, 0)
        self.assertEqual(len(done.jobs), 1)
        self.assertEqual(done.digests[910], published)
        self.assertEqual(done.algs[910], "512")
        self.assertEqual(done.disk_digests[910], _AFTER_DIGEST)
        self.assertEqual(done.storages[910], _STORAGE)
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
            _gate() + (_gate() + _after()),
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
        self.assertEqual(done.fake.calls, _commands(910) + _commands(920, after=True))

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
        self.assertEqual(done.fake.calls, _commands(910, pvesm=False, path=False))
        self.assertEqual(done.asked, [])

    def test_container_id_is_not_swapped(self) -> None:
        done = _run([_record()], [(0, _STOPPED), (0, "status: stopped\n")])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("VMID 910 is a container, so it was left unchanged.", done.out)
        self.assertIn("VMID 910 is a container", done.logs["910.log"])
        self.assertEqual(done.fake.calls, _commands(910, config=False, pvesm=False, path=False))

    def test_missing_pct_is_a_failure(self) -> None:
        done = _run([_record()], [(0, _STOPPED), OSError("No such file or directory")])
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("A required program could not be run (pct).", done.out)
        self.assertIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("pct", done.logs["910.log"])
        self.assertEqual(done.fake.calls, _commands(910, config=False, pvesm=False, path=False))

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

        done = _run([_record()], _gate() + _after(), perform=perform)
        self.assertEqual(done.code, 0)
        self.assertEqual(done.digests[910], _NEW)
        self.assertEqual(done.disk_digests[910], _AFTER_DIGEST)
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


def _cluster(*entries: tuple[int, str, str]) -> dict:
    return {"version": 7, "ids": {str(vmid): {"node": node, "type": kind, "version": 1} for vmid, node, kind in entries}}


class PruneTests(unittest.TestCase):
    def test_vmid_gone_from_the_cluster_is_removed_without_any_qm_call(self) -> None:
        done = _run([_record()], [], vmlist=_cluster((100, "pve", "qemu")))
        self.assertEqual(done.code, 0)
        self.assertIsNone(done.digests[910])
        self.assertEqual(done.fake.calls, [])
        self.assertEqual(done.asked, [])
        self.assertIn(
            "VMID 910 no longer exists on this cluster, so its automated updates were removed.",
            done.out,
        )

    def test_container_is_removed(self) -> None:
        done = _run([_record()], [], vmlist=_cluster((910, "pve", "lxc")))
        self.assertIsNone(done.digests[910])
        self.assertIn("VMID 910 is now an LXC container", done.out)

    def test_normal_vm_on_another_node_is_removed(self) -> None:
        done = _run(
            [_record()],
            [],
            vmlist=_cluster((910, "pve2", "qemu")),
            confs={("pve2", 910): _PLAIN},
        )
        self.assertIsNone(done.digests[910])
        self.assertIn("VMID 910 is now a normal VM, not a template", done.out)
        self.assertEqual(done.fake.calls, [])

    def test_linked_clone_is_removed(self) -> None:
        clone = f"scsi0: {_STORAGE}:9001/base-9001-disk-0.raw/910/vm-910-disk-0.qcow2\n"
        done = _run(
            [_record()],
            [],
            vmlist=_cluster((910, "pve", "qemu")),
            confs={("pve", 910): clone},
        )
        self.assertIsNone(done.digests[910])
        self.assertIn("linked clone", done.out)

    def test_template_and_unfinished_swap_are_kept(self) -> None:
        swap = f"tags: pve-prep-swap-910\nscsi0: {_STORAGE}:base-910-disk-0.raw\n"
        for conf in (_CONFIG, swap):
            done = _run(
                [_record()],
                _gate(),
                digest=(_OLD, "256"),
                vmlist=_cluster((910, "pve", "qemu")),
                confs={("pve", 910): conf},
            )
            self.assertEqual(done.digests[910], _OLD)
            self.assertNotIn("removed", done.out)

    def test_unreadable_cluster_state_keeps_the_record(self) -> None:
        for vmlist, confs in (
            (None, None),
            ("{not json", None),
            ({"ids": []}, None),
            (_cluster((910, "pve", "qemu")), None),
            (_cluster((910, "../etc", "qemu")), None),
            (_cluster((910, "pve", "openvz")), None),
        ):
            with self.subTest(vmlist=vmlist):
                done = _run([_record()], _gate(), digest=(_OLD, "256"), vmlist=vmlist, confs=confs)
                self.assertEqual(done.digests[910], _OLD)
                self.assertNotIn("removed", done.out)

    def test_dry_run_only_says_what_would_be_removed(self) -> None:
        done = _run([_record()], [], vmlist=_cluster(), dry_run=True)
        self.assertEqual(done.digests[910], _OLD)
        self.assertIn("Dry run: VMID 910 no longer exists on this cluster", done.out)

    def test_symlinked_guest_config_is_not_read(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp)
            real = root / "real.conf"
            real.write_text(_PLAIN, encoding="utf-8")
            conf = root / "nodes" / "pve" / "qemu-server" / "910.conf"
            conf.parent.mkdir(parents=True)
            conf.symlink_to(real)
            vmlist = root / ".vmlist"
            vmlist.write_text(json.dumps(_cluster((910, "pve", "qemu"))), encoding="utf-8")
            self.assertIsNone(stale_reason(910, vmlist_path=vmlist, nodes_root=root / "nodes"))


class PinGuardTests(unittest.TestCase):
    def test_storage_mismatch_leaves_the_disk_and_keeps_the_record(self) -> None:
        other = "NFS-SATA-HDD1"
        config = f"template: 1\nscsi0: {other}:base-910-disk-0.raw\n"
        done = _run(
            [_record(storage=_STORAGE)],
            [
                (0, _STOPPED),
                (2, ""),
                (0, config),
                (0, "[]"),
                (0, _ENROLLED_PATH + "\n"),
            ],
            digest=(_NEW, "256"),
        )
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertEqual(done.storages[910], _STORAGE)
        self.assertIn("no longer on the storage recorded at enrollment", done.out)
        self.assertIn(other, done.logs["910.log"])

    def test_disk_fingerprint_mismatch_leaves_the_disk(self) -> None:
        done = _run(
            [_record(disk_digest="11" * 32)],
            _gate(),
            digest=(_NEW, "256"),
        )
        self.assertEqual(done.code, 1)
        self.assertEqual(done.jobs, [])
        self.assertEqual(done.digests[910], _OLD)
        self.assertEqual(done.disk_digests[910], "11" * 32)
        self.assertIn("boot disk no longer matches the image recorded at enrollment", done.out)
        self.assertIn("so the disk was left unchanged", done.out)

    def test_insert_uses_enrolled_storage_not_a_guess(self) -> None:
        done = _run([_record()], _gate() + _after(), digest=(_NEW, "256"))
        self.assertEqual(done.code, 0)
        self.assertEqual(done.jobs[0][0].storage, _STORAGE)
        self.assertEqual(done.jobs[0][0].kept_storage, ((910, _STORAGE),))


class IncompletePruneTests(unittest.TestCase):
    def test_incomplete_record_is_removed(self) -> None:
        with private_tempdir() as tmp:
            root = Path(tmp)
            nodes = root / "nodes"
            nodes.mkdir()
            path = root / "910.json"
            path.write_text(
                json.dumps(
                    {
                        "checksum_alg": "256",
                        "digest": _OLD,
                        "distro": "ubuntu",
                        "password_auth": True,
                        "permit_root": True,
                        "release": "24.04",
                        "schedule": "weekly",
                        "vmid": 910,
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            vmlist = root / ".vmlist"
            vmlist.write_text(json.dumps(_cluster((910, "pve", "qemu"))), encoding="utf-8")
            conf = nodes / "pve" / "qemu-server" / "910.conf"
            conf.parent.mkdir(parents=True)
            conf.write_text(_CONFIG, encoding="utf-8")
            from pve_prep.unattended import prune_enrollments

            lines = prune_enrollments(
                directory=root, vmlist_path=vmlist, nodes_root=nodes, dry_run=False
            )
            self.assertTrue(any("missing its storage or disk fingerprint" in line for line in lines))
            self.assertFalse(path.exists())


class ChecksumPolicyTests(unittest.TestCase):
    def _failing(self, exc: BaseException, *, dry_run: bool = False) -> _Outcome:
        def perform(job: Job, release: str, vmid: int) -> None:
            raise exc

        return _run([_record()], _gate(), digest=(_NEW, "256"), perform=perform, dry_run=dry_run)

    def test_checksum_mismatch_removes_the_record(self) -> None:
        done = self._failing(DownloadError(f"checksum mismatch for img.qcow2: got {_OTHER} expected {_NEW}"))
        self.assertEqual(done.code, 1)
        self.assertIsNone(done.digests[910])
        self.assertIn("did not match its published checksum", done.out)
        self.assertIn("No changes to your existing disk image were made.", done.out)
        self.assertIn("It was removed from automated updates.", done.out)
        self.assertIn("checksum mismatch", done.logs["910.log"])

    def test_dry_run_mismatch_keeps_the_record(self) -> None:
        done = self._failing(DownloadError("checksum mismatch for img.qcow2: got a expected b"), dry_run=True)
        self.assertEqual(done.digests[910], _OLD)
        self.assertNotIn("removed from automated updates", done.out)

    def test_transient_download_failures_keep_the_record(self) -> None:
        for exc in (
            DownloadError("fetch failed for https://example.test/img.qcow2: HTTP Error 404: Not Found"),
            DownloadError("could not fetch checksum file"),
            RuntimeError("disk swap failed"),
        ):
            with self.subTest(exc=str(exc)):
                done = self._failing(exc)
                self.assertEqual(done.code, 1)
                self.assertEqual(done.digests[910], _OLD)
                self.assertNotIn("removed from automated updates", done.out)

