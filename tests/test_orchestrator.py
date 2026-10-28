# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Orchestrator tests. Sibling modules are stubbed only while the script loads."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pve_prep  # noqa: E402,F401  real package must win over a stub
from pve_prep.download import PublishResult  # noqa: E402


def _stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__package__ = "pve_prep"
    module.__file__ = f"<stub {name}>"
    sys.modules[name] = module
    return module


def _install_sibling_stubs(real_customize, real_vm, real_catalog, real_download) -> None:
    catalog = _stub("pve_prep.catalog")
    # prompts.py imports this name while the orchestrator is loading.
    catalog.DISTROS = real_catalog.DISTROS
    catalog.releases_for = mock.MagicMock(name="releases_for")
    catalog.normalize_release = mock.MagicMock(name="normalize_release")

    download = _stub("pve_prep.download")
    download.fetch_verified = mock.MagicMock(name="fetch_verified")
    download.convert = mock.MagicMock(name="convert")
    download.publish = mock.MagicMock(name="publish", return_value="written")
    download.clear_cache = mock.MagicMock(name="clear_cache")
    download.DownloadError = real_download.DownloadError

    customize = _stub("pve_prep.customize")
    customize.apply = mock.MagicMock(name="apply")
    # Dry-run prints this. The orchestrator must bind the real builder, not a mock.
    customize.argv = real_customize.argv

    vm = _stub("pve_prep.vm")
    vm.create_template = mock.MagicMock(name="create_template")
    vm.parse_storage_ids = mock.MagicMock(name="parse_storage_ids")
    vm.parse_storage_kinds = real_vm.parse_storage_kinds
    vm.is_file_storage = real_vm.is_file_storage
    vm.VmError = real_vm.VmError
    vm.VmDestroyedError = real_vm.VmDestroyedError
    vm.counts_as_template = real_vm.counts_as_template
    vm.is_linked_clone = real_vm.is_linked_clone
    vm.prepare_replace = mock.MagicMock(name="prepare_replace", return_value=1)
    vm.qemu_img_convert_argv = real_vm.qemu_img_convert_argv
    vm.stdout_is_tty = real_vm.stdout_is_tty
    vm.refuse_replace_target = real_vm.refuse_replace_target
    vm.parse_storage_content = real_vm.parse_storage_content
    vm.storage_lacks_images = real_vm.storage_lacks_images
    vm.vmid_config_missing = real_vm.vmid_config_missing
    vm.parse_vmlist = real_vm.parse_vmlist
    vm.config_has_os_disk = real_vm.config_has_os_disk
    vm.os_disk_storage = real_vm.os_disk_storage
    vm.os_disk_format = real_vm.os_disk_format
    vm.import_directories = real_vm.import_directories
    vm.insert_disk = mock.MagicMock(name="insert_disk")
    vm.backups_since = real_vm.backups_since


def _restore_siblings(saved: dict[str, types.ModuleType]) -> None:
    pkg = sys.modules["pve_prep"]
    for name, module in saved.items():
        sys.modules[name] = module
        setattr(pkg, name.split(".", 1)[1], module)


def _load_orchestrator():
    import pve_prep.catalog as catalog_mod
    import pve_prep.customize as customize_mod
    import pve_prep.download as download_mod
    import pve_prep.vm as vm_mod

    saved = {
        "pve_prep.catalog": catalog_mod,
        "pve_prep.download": download_mod,
        "pve_prep.customize": customize_mod,
        "pve_prep.vm": vm_mod,
    }
    try:
        _install_sibling_stubs(customize_mod, vm_mod, catalog_mod, download_mod)
        path = ROOT / "pve-template-prep.py"
        spec = importlib.util.spec_from_file_location("pve_template_prep", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["pve_template_prep"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        # Collection imports test_vm next. Leave it the real module, not the stub.
        _restore_siblings(saved)


MOD = _load_orchestrator()
ORIGINAL_RUN = MOD.default_run


def _specs(_distro: str):
    return [
        SimpleNamespace(release="12", label="Debian 12", eol=False, family="deb"),
        SimpleNamespace(release="13", label="Debian 13", eol=False, family="deb"),
    ]


def _image_job(*, releases=("12", "13"), dry_run=True, prep=True, disk_format="raw"):
    from pve_prep.job import DEFAULT_CACHE, Job

    return Job(
        distro="debian",
        releases=tuple(releases),
        mode="image",
        disk_format=disk_format,
        dest_dir="/tmp/images",
        storage="",
        cache_dir=DEFAULT_CACHE,
        collision="backup",
        vmids=(),
        prep=prep,
        bridge="vmbr0",
        memory_mb=2048,
        cores=2,
        dry_run=dry_run,
        destroy_vmids=frozenset(),
    )


def _template_job(*, destroy=frozenset({910}), dry_run=True):
    from pve_prep.job import DEFAULT_CACHE, Job

    return Job(
        distro="debian",
        releases=("12", "13"),
        mode="template",
        disk_format="qcow2",
        dest_dir="",
        storage="dir-templates",
        cache_dir=DEFAULT_CACHE,
        collision="backup",
        vmids=(910, 911),
        prep=True,
        bridge="vmbr0",
        memory_mb=2048,
        cores=2,
        dry_run=dry_run,
        destroy_vmids=destroy,
        backup_vmids=destroy,
        make_template=True,
    )


class OrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        MOD.fetch_verified = mock.MagicMock(return_value=Path("/tmp/src.qcow2"))
        MOD.convert = mock.MagicMock()
        MOD.publish = mock.MagicMock(return_value=PublishResult("written"))
        MOD.customize_apply = mock.MagicMock()
        MOD.create_template = mock.MagicMock()
        MOD.releases_for = mock.MagicMock(side_effect=_specs)
        MOD.parse_storage_ids = mock.MagicMock(return_value=[])
        MOD.default_run = ORIGINAL_RUN
        MOD.clear_cache = mock.MagicMock(name="clear_cache")
        MOD.storage_kind = lambda storage: "dir"

    def test_image_order_fetch_convert_apply_publish(self) -> None:
        order: list[str] = []

        def fetch(spec, cache, dry_run=False):
            order.append("fetch")
            self.assertEqual(spec.release, "12")
            self.assertEqual(spec.family, "deb")
            self.assertEqual(cache, Path("/var/tmp/pve-template-prep/cache"))
            self.assertFalse(dry_run)
            return Path("/tmp/src.qcow2")

        def convert(src, work, disk_format, dry_run=False):
            order.append("convert")
            self.assertEqual(src, Path("/tmp/src.qcow2"))
            self.assertEqual(work, Path("/var/tmp/pve-template-prep/cache/debian-12-pve.raw.work"))
            self.assertEqual(disk_format, "raw")
            self.assertFalse(dry_run)

        def apply(
            path,
            family,
            disk_format="",
            password_auth=False,
            permit_root=False,
            dry_run=False,
            verbose=False,
            label="",
        ):
            order.append("apply")
            self.assertEqual(disk_format, "raw")
            self.assertEqual(path, "/var/tmp/pve-template-prep/cache/debian-12-pve.raw.work")
            self.assertEqual(family, "deb")
            self.assertFalse(password_auth)
            self.assertFalse(permit_root)
            self.assertFalse(dry_run)
            self.assertFalse(verbose)
            self.assertEqual(label, "Debian 12")

        def publish(work, dest, collision, dry_run=False):
            order.append("publish")
            self.assertEqual(work, Path("/var/tmp/pve-template-prep/cache/debian-12-pve.raw.work"))
            self.assertEqual(dest, Path("/tmp/images/debian-12-pve.raw"))
            self.assertEqual(collision, "backup")
            self.assertFalse(dry_run)
            return PublishResult("skipped")

        MOD.fetch_verified = fetch
        MOD.convert = convert
        MOD.customize_apply = apply
        MOD.publish = publish
        job = _image_job(releases=("12",), dry_run=False, prep=True)
        MOD.run_one(job, "12", None)
        self.assertEqual(order, ["fetch", "convert", "apply", "publish"])

    def test_image_skips_customize_when_prep_off(self) -> None:
        job = _image_job(releases=("12",), prep=False)
        MOD.run_one(job, "12", None)
        MOD.customize_apply.assert_not_called()
        MOD.publish.assert_called_once()
        MOD.create_template.assert_not_called()

    def test_template_destroy_ok_only_for_retyped_vmid(self) -> None:
        recorded: list[dict] = []

        def create_template(**kwargs):
            recorded.append(kwargs)
            self.assertIs(kwargs["run"], MOD.default_run)

        MOD.create_template = create_template
        job = _template_job()
        first = io.StringIO()
        with redirect_stdout(first):
            MOD.run_one(job, "12", 910)
        second = io.StringIO()
        with redirect_stdout(second):
            MOD.run_one(job, "13", 911)
        self.assertIn("qemu-img convert -O qcow2 <os-disk>", first.getvalue())
        self.assertNotIn("qemu-img", second.getvalue())
        self.assertEqual([item["destroy_ok"] for item in recorded], [True, False])
        self.assertEqual([item["backup_disks"] for item in recorded], [False, False])
        self.assertEqual([item["vmid"] for item in recorded], [910, 911])
        self.assertEqual(
            [item["name"] for item in recorded],
            ["debian-12-cloud", "debian-13-cloud"],
        )
        self.assertEqual(recorded[0]["storage"], "dir-templates")
        self.assertEqual(recorded[0]["memory_mb"], 2048)
        self.assertEqual(recorded[0]["cores"], 2)
        self.assertEqual(recorded[0]["bridge"], "vmbr0")
        self.assertTrue(recorded[0]["dry_run"])
        self.assertTrue(recorded[0]["make_template"])
        self.assertTrue(recorded[0]["image_path"].endswith("debian-12-pve.qcow2.work"))
        MOD.run_one(replace(job, make_template=False), "12", 910)
        self.assertFalse(recorded[-1]["make_template"])
        MOD.publish.assert_not_called()
        MOD.fetch_verified.assert_called()

    def test_disk_copy_runs_before_the_download(self) -> None:
        order: list[tuple] = []

        def prepare_replace(**kwargs):
            order.append(("copy", kwargs["backup"], kwargs["vmid"]))
            return 1

        def fetch(*_args, **_kwargs):
            order.append(("fetch",))
            return Path("/tmp/src.qcow2")

        MOD.prepare_replace = prepare_replace
        MOD.fetch_verified = fetch
        MOD.convert = lambda *_args, **_kwargs: None
        MOD.customize_apply = lambda *_args, **_kwargs: None
        MOD.create_template = lambda **_kwargs: None
        job = replace(_template_job(dry_run=False), releases=("12",), vmids=(910,))
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(job, "12", 910)
        self.assertEqual(order, [("copy", True, 910), ("fetch",)])
        self.assertIn(
            "VM disk copy completed, building new template images now...",
            buf.getvalue(),
        )

    def test_refused_target_is_not_downloaded(self) -> None:
        order: list[str] = []

        def prepare_replace(**kwargs):
            order.append("check")
            self.assertFalse(kwargs["backup"])
            raise RuntimeError("VM 910 is not a template")

        MOD.prepare_replace = prepare_replace
        MOD.fetch_verified = lambda *_args, **_kwargs: order.append("fetch")
        job = replace(
            _template_job(dry_run=False),
            releases=("12",),
            vmids=(910,),
            backup_vmids=frozenset(),
        )
        with self.assertRaises(RuntimeError):
            MOD.run_one(job, "12", 910)
        self.assertEqual(order, ["check"])

    def test_image_insert_does_not_publish_or_destroy(self) -> None:
        job = replace(
            _image_job(releases=("12",), dry_run=True),
            vmids=(910,),
            storage="dir-templates",
            collision="overwrite",
        )
        MOD.insert_disk = mock.MagicMock(return_value="dir-templates:vm-910-disk-0")
        MOD.run_one(job, "12", 910)
        MOD.insert_disk.assert_called_once()
        kwargs = MOD.insert_disk.call_args.kwargs
        self.assertEqual(kwargs["vmid"], 910)
        self.assertEqual(kwargs["storage"], "dir-templates")
        self.assertEqual(kwargs["disk_policy"], "overwrite")
        self.assertTrue(kwargs["dry_run"])
        MOD.publish.assert_not_called()
        MOD.create_template.assert_not_called()

    def test_live_insert_on_block_storage_is_refused_before_the_download(self) -> None:
        job = replace(
            _image_job(releases=("12",), dry_run=False),
            vmids=(910,),
            storage="local",
            disk_format="qcow2",
            collision="overwrite",
            kept_storage=((910, "vm-data"),),
        )
        MOD.insert_disk = mock.MagicMock()
        MOD.prepare_replace = mock.MagicMock()
        MOD.storage_kind = lambda storage: "zfspool" if storage == "vm-data" else "dir"
        with self.assertRaisesRegex(RuntimeError, "vm-data is zfspool"):
            MOD.run_one(job, "12", 910)
        MOD.prepare_replace.assert_not_called()
        MOD.fetch_verified.assert_not_called()
        MOD.insert_disk.assert_not_called()

    def test_live_template_on_unreadable_storage_type_is_refused(self) -> None:
        job = replace(_template_job(dry_run=False), releases=("12",), vmids=(910,))
        MOD.storage_kind = lambda storage: ""
        with self.assertRaisesRegex(RuntimeError, "an unreadable type"):
            MOD.run_one(job, "12", 910)
        MOD.fetch_verified.assert_not_called()
        MOD.create_template.assert_not_called()

    def test_image_insert_keeps_the_disk_format_on_file_storage(self) -> None:
        job = replace(
            _image_job(releases=("12",), dry_run=True, disk_format="qcow2"),
            vmids=(910,),
            storage="nfs-templates",
            collision="overwrite",
            kept_storage=((910, "nfs-templates"),),
            kept_format=((910, "raw"),),
        )
        MOD.insert_disk = mock.MagicMock(return_value="nfs-templates:910/vm-910-disk-0.raw")
        MOD.storage_kind = lambda storage: "nfs" if storage == "nfs-templates" else "dir"
        MOD.run_one(job, "12", 910)
        kwargs = MOD.insert_disk.call_args.kwargs
        self.assertEqual(kwargs["storage"], "nfs-templates")
        self.assertEqual(kwargs["volume_format"], "raw")

    def test_image_missing_vmid_creates_a_template(self) -> None:
        job = replace(
            _image_job(releases=("13",), dry_run=True),
            vmids=(920,),
            storage="dir-templates",
            template_vmids=frozenset({920}),
            make_template=False,
        )
        recorded: list[dict] = []

        def create_template(**kwargs):
            recorded.append(kwargs)

        MOD.create_template = create_template
        MOD.insert_disk = mock.MagicMock()
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(job, "13", 920)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["vmid"], 920)
        self.assertTrue(recorded[0]["make_template"])
        self.assertFalse(recorded[0]["destroy_ok"])
        MOD.insert_disk.assert_not_called()
        MOD.publish.assert_not_called()
        text = buf.getvalue()
        self.assertIn("Creating Debian 13 VM template", text)
        self.assertIn("Debian 13 VM template created", text)

    def test_release_lines_name_the_outcome(self) -> None:
        from pve_prep.job import DEFAULT_BACKUPS

        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(_template_job(), "12", 910)
        text = buf.getvalue()
        self.assertIn("\nCreating Debian 12 VM template\n\n", text)
        self.assertIn(
            f"\n\nDebian 12 VM template created. Previous VM deleted. "
            f"Old disk saved in {DEFAULT_BACKUPS}.\n",
            text,
        )
        buf = io.StringIO()
        kept = replace(
            _image_job(releases=("12",), dry_run=True),
            vmids=(910,),
            storage="dir-templates",
            backup_vmids=frozenset({910}),
        )
        MOD.insert_disk = mock.MagicMock(return_value="dir-templates:vm-910-disk-0")
        with redirect_stdout(buf):
            MOD.run_one(kept, "12", 910)
        self.assertIn(
            "\n\nDebian 12 VM disk image created and imported. "
            "Old disk saved in the backup directory.\n",
            buf.getvalue(),
        )
        replaced = replace(kept, backup_vmids=frozenset())
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(replaced, "12", 910)
        self.assertIn(
            "\n\nDebian 12 VM disk image created and imported. Previous disk deleted.\n",
            buf.getvalue(),
        )
        kept_template = SimpleNamespace(
            volid="dir-templates:vm-910-disk-1",
            kept_template=True,
            backup_dir="",
        )
        MOD.insert_disk = mock.MagicMock(return_value=kept_template)
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(replaced, "12", 910)
        self.assertIn(
            "Template 910 kept. Previous disk deleted.",
            buf.getvalue(),
        )
        cleaned = SimpleNamespace(
            volid="dir-templates:vm-910-disk-1",
            kept_template=True,
            backup_dir="/var/backups/pve-template-prep",
            cleanup_error="volume is locked",
        )
        MOD.insert_disk = mock.MagicMock(return_value=cleaned)
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(replaced, "12", 910)
        self.assertIn(
            "Debian 12 VM disk image created and imported. Template 910 updated. "
            "Old disk saved in /var/backups/pve-template-prep. "
            "Old disk cleanup failed: volume is locked.",
            buf.getvalue(),
        )
        self.assertNotIn("Previous disk deleted.", buf.getvalue())
        self.assertNotIn("template flag was restored", buf.getvalue())

    def test_main_continues_after_second_release_fails(self) -> None:
        job = _image_job(dry_run=True, prep=True)
        calls = {"n": 0}

        def fetch(spec, cache, dry_run=False):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return Path("/tmp/src.qcow2")

        MOD.fetch_verified = fetch
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=False),
            redirect_stdout(buf),
        ):
            code = MOD.main([])
        text = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertEqual(calls["n"], 2)
        self.assertIn("\nCreating Debian 12 disk image\n\n", text)
        self.assertIn(
            "\n\nDebian 12 disk image created at /tmp/images/debian-12-pve.raw\n",
            text,
        )
        self.assertIn("\nCreating Debian 13 disk image\n\n", text)
        self.assertNotIn("OK 12", text)
        self.assertIn("FAIL 13: boom", text)
        self.assertIn("done", text)
        MOD.convert.assert_called_once()

    def test_version_flag_and_startup_line(self) -> None:
        for flag in ("--version", "-V"):
            buf = io.StringIO()
            with mock.patch.object(MOD, "interview", side_effect=AssertionError("asked")):
                with redirect_stdout(buf), self.assertRaises(SystemExit) as caught:
                    MOD.main([flag])
            self.assertEqual(caught.exception.code, 0)
            self.assertEqual(buf.getvalue(), f"pve-template-prep {pve_prep.__version__}\n")
        self.assertEqual(pve_prep.__version__, "1.3.5")
        import pve_prep.download as download

        self.assertEqual(download._HEADERS["User-Agent"], f"pve-prep/{pve_prep.__version__}")

    def test_verbose_flag_reaches_the_job(self) -> None:
        seen: dict[str, bool] = {}
        job = _image_job(releases=("12",), dry_run=True)

        def run_one(got, _release, _vmid) -> None:
            seen["verbose"] = got.verbose

        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "run_one", run_one),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=False),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MOD.main(["-v"]), 0)
        self.assertTrue(seen["verbose"])

    def test_main_abort(self) -> None:
        with mock.patch.object(MOD, "interview", side_effect=MOD.PromptAbort("no")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = MOD.main([])
        self.assertEqual(code, 1)
        text = buf.getvalue()
        self.assertTrue(text.startswith(f"pve-template-prep {pve_prep.__version__}\n"))
        self.assertIn("aborted", text)

    def test_vmids_in_use_reads_the_cluster_list_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vmlist = Path(tmp) / ".vmlist"
            vmlist.write_text(
                json.dumps(
                    {
                        "version": 7,
                        "ids": {
                            "9001": {"node": "pve1", "type": "qemu", "version": 1},
                            "9002": {"node": "pve2", "type": "lxc", "version": 2},
                        },
                    }
                )
            )
            with (
                mock.patch.object(MOD, "VMLIST", vmlist),
                mock.patch.object(MOD.subprocess, "run", side_effect=AssertionError("qm ran")),
            ):
                self.assertEqual(MOD.vmids_in_use(tuple(range(9001, 9033))), {9001, 9002})

    def test_vmids_in_use_falls_back_to_qm_and_pct(self) -> None:
        # 9001 is a VM, 9002 a container, 9003 free. qm alone calls 9002 free.
        def status(argv, **kwargs):
            tool, vmid = argv[0], int(argv[-1])
            if (tool, vmid) in {("qm", 9001), ("pct", 9002)}:
                return SimpleNamespace(returncode=0, stdout="status: stopped\n", stderr="")
            kind = "VM" if tool == "qm" else "CT"
            return SimpleNamespace(returncode=2, stdout="", stderr=f"Configuration file for {kind} {vmid} does not exist\n")

        with tempfile.TemporaryDirectory() as tmp:
            for content in (None, "not json", '{"version": 1}'):
                vmlist = Path(tmp) / ".vmlist"
                if content is None:
                    vmlist.unlink(missing_ok=True)
                else:
                    vmlist.write_text(content)
                with (
                    mock.patch.object(MOD, "VMLIST", vmlist),
                    mock.patch.object(MOD.subprocess, "run", side_effect=status) as run,
                ):
                    self.assertEqual(MOD.vmids_in_use((9001, 9002, 9003)), {9001, 9002})
                probes = [call.args[0][:2] for call in run.call_args_list]
                # A VM answers on qm; pct is only asked when qm says no config.
                self.assertEqual(
                    probes,
                    [["qm", "status"], ["qm", "status"], ["pct", "status"], ["qm", "status"], ["pct", "status"]],
                )

    def test_vmids_fallback_without_pct_cannot_say_free(self) -> None:
        def status(argv, **kwargs):
            if argv[0] == "pct":
                raise FileNotFoundError("pct")
            return SimpleNamespace(returncode=2, stdout="", stderr="Configuration file does not exist\n")

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(MOD, "VMLIST", Path(tmp) / "missing"),
                mock.patch.object(MOD.subprocess, "run", side_effect=status),
            ):
                self.assertIsNone(MOD.vmids_in_use((9002,)))

    def test_container_id_is_blocked_not_replaceable(self) -> None:
        missing = SimpleNamespace(returncode=2, stdout="", stderr="Configuration file 'nodes/pve1/qemu-server/9002.conf' does not exist\n")
        with mock.patch.object(MOD.subprocess, "run", return_value=missing):
            self.assertIsNone(MOD.guest_role(9002))

    def test_main_dry_run_flag_and_storage_failure(self) -> None:
        seen = {}

        def fake_interview(
            read_line,
            write,
            *,
            dry_run,
            list_storages,
            releases_for,
            normalize_release,
            vmids_in_use,
            vm_has_disks,
            storage_kinds=None,
            guest_role=None,
            disk_storage=None,
            disk_format_of=None,
            import_storages=None,
        ):
            seen["dry_run"] = dry_run
            seen["storages"] = list_storages()
            seen["disk_storage"] = disk_storage
            seen["disk_format_of"] = disk_format_of
            seen["import_storages"] = import_storages
            return _image_job(releases=("12",), dry_run=True)

        MOD.fetch_verified = lambda *args, **kwargs: Path("/tmp/src.qcow2")
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", fake_interview),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=False),
            mock.patch.object(MOD.subprocess, "run", side_effect=OSError("no pvesm")),
            redirect_stdout(buf),
        ):
            code = MOD.main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(seen["dry_run"])
        self.assertEqual(seen["storages"], [])
        self.assertIs(seen["disk_storage"], MOD.disk_storage)
        self.assertIs(seen["disk_format_of"], MOD.disk_format)
        self.assertIs(seen["import_storages"], MOD.import_storages)
        self.assertIn("Debian 12 disk image created at /tmp/images/debian-12-pve.raw", buf.getvalue())
        MOD.clear_cache.assert_not_called()

    def test_main_deletes_the_cache_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = str(Path(tmp) / "pve-template-prep" / "cache")
            job = replace(
                _image_job(releases=("12",), dry_run=False),
                cache_dir=cache,
            )
            MOD.fetch_verified = lambda *args, **kwargs: Path("/tmp/src.qcow2")
            buf = io.StringIO()
            with (
                mock.patch.object(MOD, "interview", return_value=job),
                mock.patch.object(MOD, "preflight"),
                mock.patch.object(MOD, "ask_cache_cleanup", return_value=True) as ask,
                redirect_stdout(buf),
            ):
                code = MOD.main([])
        self.assertEqual(code, 0)
        ask.assert_called_once()
        self.assertEqual(ask.call_args.kwargs["cache"], cache)
        self.assertFalse(ask.call_args.kwargs["failed"])
        MOD.clear_cache.assert_called_once_with(cache)
        self.assertIn(
            "Debian 12 disk image created at /tmp/images/debian-12-pve.raw",
            buf.getvalue(),
        )
        self.assertIn(f"deleted the working files in {cache}", buf.getvalue())
        self.assertNotIn("backups", buf.getvalue())

    def test_main_keeps_the_cache_when_a_release_fails(self) -> None:
        job = _image_job(releases=("12",), dry_run=False)

        def fetch(*args, **kwargs):
            raise RuntimeError("boom")

        MOD.fetch_verified = fetch
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "preflight"),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=False) as ask,
            redirect_stdout(buf),
        ):
            code = MOD.main([])
        self.assertEqual(code, 1)
        self.assertTrue(ask.call_args.kwargs["failed"])
        MOD.clear_cache.assert_not_called()
        self.assertIn("cache kept:", buf.getvalue())
        self.assertIn("a release failed", buf.getvalue())
        self.assertNotIn("backups", buf.getvalue())

    def test_main_failure_can_still_delete_working_files(self) -> None:
        job = _image_job(releases=("12",), dry_run=False)

        def fetch(*args, **kwargs):
            raise RuntimeError("boom")

        MOD.fetch_verified = fetch
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "preflight"),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=True),
            redirect_stdout(buf),
        ):
            code = MOD.main([])
        self.assertEqual(code, 1)
        MOD.clear_cache.assert_called_once_with(job.cache_dir)
        self.assertIn(f"deleted the working files in {job.cache_dir}", buf.getvalue())

    def test_main_dry_run_does_not_delete_the_cache(self) -> None:
        job = _image_job(releases=("12",), dry_run=True)
        MOD.fetch_verified = lambda *args, **kwargs: Path("/tmp/src.qcow2")
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=True),
            redirect_stdout(buf),
        ):
            code = MOD.main([])
        self.assertEqual(code, 0)
        MOD.clear_cache.assert_not_called()
        self.assertIn("dry-run: would delete the working files in", buf.getvalue())
        self.assertNotIn("backups", buf.getvalue())

    def test_backup_line_only_when_this_run_wrote_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = replace(
                _image_job(releases=("12",), dry_run=False), backup_dir=tmp, backup_vmids=frozenset({910})
            )
            old = Path(tmp) / "vm-910-scsi0.bak.20250101T000000Z.qcow2"
            old.write_bytes(b"x")
            os.utime(old, (1_000, 1_000))
            (Path(tmp) / "notes.txt").write_text("not a backup")
            buf = io.StringIO()
            with redirect_stdout(buf):
                MOD._report_backups(job, started=2_000)
            self.assertEqual(buf.getvalue(), "")
            (Path(tmp) / "vm-910-scsi0.bak.20260101T000000Z.qcow2").write_bytes(b"x")
            with redirect_stdout(buf):
                MOD._report_backups(job, started=2_000)
            self.assertEqual(buf.getvalue(), f"VM Disk image backups located at {tmp}\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                MOD._report_backups(replace(job, dry_run=True), started=2_000)
                MOD._report_backups(replace(job, backup_vmids=frozenset()), started=2_000)
            self.assertEqual(buf.getvalue(), "")

    def test_list_storages_parses_status(self) -> None:
        proc = SimpleNamespace(returncode=0, stdout="NAME STATUS\n", stderr="")
        MOD.parse_storage_ids = mock.MagicMock(return_value=["dir-templates"])
        with mock.patch.object(MOD.subprocess, "run", return_value=proc):
            self.assertEqual(MOD.list_storages(), ["dir-templates"])
        MOD.parse_storage_ids.assert_called_once_with("NAME STATUS\n")

    def test_preflight_requires_root(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("already root")
        with self.assertRaises(SystemExit) as caught:
            MOD.preflight(_image_job(dry_run=False))
        self.assertIn("run as root on the PVE host", str(caught.exception))

    def test_preflight_missing_tool_hint(self) -> None:
        with (
            mock.patch.object(MOD.os, "geteuid", return_value=0),
            mock.patch.object(MOD.shutil, "which", return_value=None),
            self.assertRaises(SystemExit) as caught,
        ):
            MOD.preflight(_image_job(dry_run=False, prep=True))
        self.assertIn("apt-get install --no-install-recommends guestfs-tools libguestfs-xfs", str(caught.exception))
        self.assertIn("qemu-img", str(caught.exception))

    def test_preflight_tool_set_by_mode(self) -> None:
        seen: list[str] = []

        def which(name: str):
            seen.append(name)
            return "/usr/bin/" + name

        with (
            mock.patch.object(MOD.os, "geteuid", return_value=0),
            mock.patch.object(MOD.shutil, "which", side_effect=which),
            mock.patch.object(MOD, "package_installed", return_value=True),
        ):
            MOD.preflight(_image_job(dry_run=False, prep=True))
            image_tools = list(seen)
            seen.clear()
            MOD.preflight(_template_job(dry_run=False))
            template_tools = list(seen)
        self.assertEqual(image_tools, ["qemu-img", "virt-customize"])
        self.assertEqual(template_tools, ["qemu-img", "virt-customize", "qm", "pvesm"])

    def test_preflight_requires_xfs_support_even_with_virt_customize(self) -> None:
        installed = SimpleNamespace(returncode=0, stdout="install ok installed", stderr="")
        absent = SimpleNamespace(returncode=1, stdout="", stderr="dpkg-query: no packages found matching libguestfs-xfs\n")
        with (
            mock.patch.object(MOD.os, "geteuid", return_value=0),
            mock.patch.object(MOD.shutil, "which", side_effect=lambda name: "/usr/bin/" + name),
        ):
            with mock.patch.object(MOD.subprocess, "run", return_value=absent), self.assertRaises(SystemExit) as caught:
                MOD.preflight(_image_job(dry_run=False, prep=True))
            self.assertIn("missing libguestfs-xfs", str(caught.exception))
            self.assertIn("--no-install-recommends", str(caught.exception))
            with mock.patch.object(MOD.subprocess, "run", return_value=installed):
                MOD.preflight(_image_job(dry_run=False, prep=True))
            # No dpkg-query to ask: do not block on a guess.
            with mock.patch.object(MOD.subprocess, "run", side_effect=FileNotFoundError("dpkg-query")):
                MOD.preflight(_image_job(dry_run=False, prep=True))

    def test_main_live_calls_preflight(self) -> None:
        job = _image_job(dry_run=False, releases=("12",))
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "preflight", side_effect=SystemExit("run as root on the PVE host")),
            self.assertRaises(SystemExit),
        ):
            MOD.main([])

if __name__ == "__main__":
    unittest.main()
