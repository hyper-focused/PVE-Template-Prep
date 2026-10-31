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
    download.file_digest = real_download.file_digest
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
    vm.is_template = real_vm.is_template
    vm.unfinished_swap = real_vm.unfinished_swap
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
    vm.boot_disk_location = real_vm.boot_disk_location
    vm.import_directories = real_vm.import_directories
    vm.insert_disk = mock.MagicMock(name="insert_disk")
    vm.set_guest_options = mock.MagicMock(name="set_guest_options")
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
ORIGINAL_CLAIM = MOD._claim_host
ORIGINAL_LOCK = MOD.acquire_run_lock


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
        MOD.insert_disk = mock.MagicMock(name="insert_disk")
        MOD.set_guest_options = mock.MagicMock(name="set_guest_options")
        MOD.prepare_replace = mock.MagicMock(name="prepare_replace", return_value=1)
        MOD.releases_for = mock.MagicMock(side_effect=_specs)
        MOD.parse_storage_ids = mock.MagicMock(return_value=[])
        MOD.default_run = ORIGINAL_RUN
        MOD.clear_cache = mock.MagicMock(name="clear_cache")
        MOD.storage_kind = lambda storage: "dir"
        MOD._claim_host = mock.MagicMock(name="_claim_host", return_value=0)
        MOD.acquire_run_lock = mock.MagicMock(name="acquire_run_lock", return_value=-1)

    def tearDown(self) -> None:
        MOD._claim_host = ORIGINAL_CLAIM
        MOD.acquire_run_lock = ORIGINAL_LOCK

    def test_image_order_fetch_convert_apply_publish(self) -> None:
        order: list[str] = []

        def fetch(spec, cache, dry_run=False, quiet=False):
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
            quiet=False,
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

    def test_retyped_vmid_swaps_the_disk_and_a_new_vmid_is_created(self) -> None:
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

        MOD.insert_disk.assert_called_once()
        swap = MOD.insert_disk.call_args.kwargs
        self.assertEqual(swap["vmid"], 910)
        self.assertEqual(swap["storage"], "dir-templates")
        self.assertEqual(swap["disk_policy"], "backup")
        self.assertTrue(swap["dry_run"])
        self.assertTrue(swap["image_path"].endswith("debian-12-pve.qcow2.work"))
        options = MOD.set_guest_options.call_args.kwargs
        self.assertEqual(
            {key: options[key] for key in ("vmid", "name", "memory_mb", "cores", "bridge")},
            {"vmid": 910, "name": "debian-12-cloud", "memory_mb": 2048, "cores": 2, "bridge": "vmbr0"},
        )

        self.assertEqual([item["vmid"] for item in recorded], [911])
        created = recorded[0]
        self.assertNotIn("destroy_ok", created)
        self.assertEqual(created["name"], "debian-13-cloud")
        self.assertEqual(created["storage"], "dir-templates")
        self.assertEqual(created["memory_mb"], 2048)
        self.assertEqual(created["cores"], 2)
        self.assertEqual(created["bridge"], "vmbr0")
        self.assertTrue(created["dry_run"])
        self.assertTrue(created["make_template"])
        self.assertTrue(created["image_path"].endswith("debian-13-pve.qcow2.work"))
        MOD.run_one(replace(job, make_template=False), "13", 911)
        self.assertFalse(recorded[-1]["make_template"])
        MOD.publish.assert_not_called()
        MOD.fetch_verified.assert_called()

    def test_failed_swap_never_creates_or_changes_settings(self) -> None:
        MOD.prepare_replace = mock.MagicMock(return_value=1)
        MOD.insert_disk = mock.MagicMock(side_effect=MOD.VmError("import failed"))
        job = replace(_template_job(dry_run=False), releases=("12",), vmids=(910,))
        with self.assertRaises(MOD.VmError):
            MOD.run_one(job, "12", 910)
        MOD.create_template.assert_not_called()
        MOD.set_guest_options.assert_not_called()

    def test_settings_failure_after_swap_is_reported_not_raised(self) -> None:
        MOD.insert_disk = mock.MagicMock(
            return_value=SimpleNamespace(volid="dir-templates:vm-910-disk-1", backup_dir="", cleanup_error="")
        )
        MOD.set_guest_options = mock.MagicMock(side_effect=MOD.VmError("VM 910 failed at qm set"))
        job = replace(_template_job(destroy=frozenset({910})), backup_vmids=frozenset())
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(job, "12", 910)
        self.assertIn(
            "Debian 12 VM template 910 replaced with the new disk. Previous disk deleted. "
            "Name, memory, cores, and bridge were not updated: VM 910 failed at qm set.",
            buf.getvalue(),
        )

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
        self.assertNotIn("destroy_ok", recorded[0])
        MOD.insert_disk.assert_not_called()
        MOD.publish.assert_not_called()
        text = buf.getvalue()
        self.assertIn("Creating Debian 13 VM template", text)
        self.assertIn("Debian 13 VM template created", text)

    def test_release_lines_name_the_outcome(self) -> None:
        from pve_prep.job import DEFAULT_BACKUPS

        MOD.insert_disk = mock.MagicMock(
            return_value=SimpleNamespace(
                volid="dir-templates:vm-910-disk-1",
                backup_dir=DEFAULT_BACKUPS,
                cleanup_error="",
            )
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(_template_job(), "12", 910)
        text = buf.getvalue()
        self.assertIn("\nCreating Debian 12 VM template\n\n", text)
        self.assertIn(
            f"\n\nDebian 12 VM template 910 replaced with the new disk. "
            f"Old disk saved in {DEFAULT_BACKUPS}.\n",
            text,
        )
        self.assertNotIn("Previous VM deleted", text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(_template_job(), "13", 911)
        self.assertIn("\n\nDebian 13 VM template created\n", buf.getvalue())
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

        def fetch(spec, cache, dry_run=False, quiet=False):
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
        self.assertEqual(pve_prep.__version__, "1.4.0")
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

    def test_swap_marker_alone_is_not_a_template_role(self) -> None:
        def config(text: str) -> SimpleNamespace:
            return SimpleNamespace(returncode=0, stdout=text, stderr="")

        marked = config("tags: prod;pve-prep-swap-910\nscsi0: NFS-SATA-SSD1:base-910-disk-0\n")
        with mock.patch.object(MOD.subprocess, "run", return_value=marked):
            self.assertEqual(MOD.guest_role(910), "unfinished")
        other = config("tags: pve-prep-swap-9001\nscsi0: NFS-SATA-SSD1:vm-910-disk-0\n")
        with mock.patch.object(MOD.subprocess, "run", return_value=other):
            self.assertEqual(MOD.guest_role(910), "vm")

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
            # No answer is not "installed". Stop before the download.
            with (
                mock.patch.object(MOD.subprocess, "run", side_effect=FileNotFoundError("dpkg-query")),
                self.assertRaises(SystemExit) as caught,
            ):
                MOD.preflight(_image_job(dry_run=False, prep=True))
            self.assertIn("could not check libguestfs-xfs with dpkg-query", str(caught.exception))
            self.assertIn("--no-install-recommends", str(caught.exception))

    def test_main_live_calls_preflight(self) -> None:
        job = _image_job(dry_run=False, releases=("12",))
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "preflight", side_effect=SystemExit("run as root on the PVE host")),
            self.assertRaises(SystemExit),
        ):
            MOD.main([])

    def test_unattended_skips_the_interview(self) -> None:
        buf = io.StringIO()
        interview = mock.Mock(side_effect=AssertionError("asked"))
        with (
            mock.patch.object(MOD, "interview", interview),
            mock.patch.object(MOD, "_consider_update", side_effect=AssertionError("update")),
            mock.patch("pve_prep.unattended.run_unattended", return_value=0) as refresh,
            mock.patch.object(MOD, "clear_cache") as clear_cache,
            mock.patch.object(MOD, "preflight") as preflight,
            redirect_stdout(buf),
        ):
            code = MOD.main(["--unattended", "weekly"])
        self.assertEqual(code, 0)
        interview.assert_not_called()
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0], "weekly")
        kwargs = refresh.call_args.kwargs
        self.assertIs(kwargs["digest_for"], MOD._digest_for)
        self.assertIs(kwargs["perform"], MOD.run_one)
        self.assertIs(kwargs["storage_kind"], MOD.storage_kind)
        self.assertIs(kwargs["run"], MOD.default_run)
        self.assertEqual(kwargs["nodes_root"], Path("/etc/pve/nodes"))
        self.assertEqual(kwargs["directory"], MOD.ENROLL_DIR)
        job = preflight.call_args.args[0]
        self.assertEqual(job.mode, "image")
        self.assertTrue(job.prep)
        self.assertEqual(job.vmids, (100,))
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.distro, "debian")
        self.assertEqual(job.releases, ("12",))
        self.assertEqual(job.storage, "local")
        self.assertEqual(job.cache_dir, MOD.DEFAULT_CACHE)
        self.assertEqual(job.collision, "overwrite")
        self.assertEqual(job.bridge, "vmbr0")
        self.assertEqual(job.memory_mb, 2048)
        self.assertEqual(job.cores, 2)
        self.assertFalse(job.dry_run)
        self.assertEqual(job.destroy_vmids, frozenset())
        clear_cache.assert_called_once_with(MOD.DEFAULT_CACHE)
        text = buf.getvalue()
        self.assertTrue(text.startswith(f"pve-template-prep {pve_prep.__version__}\n"))
        self.assertNotIn("deleted the working files", text)

    def test_unattended_failure_keeps_the_cache(self) -> None:
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", side_effect=AssertionError("asked")),
            mock.patch("pve_prep.unattended.run_unattended", return_value=1),
            mock.patch.object(MOD, "clear_cache") as clear_cache,
            mock.patch.object(MOD, "preflight"),
            redirect_stdout(buf),
        ):
            code = MOD.main(["--unattended", "monthly"])
        self.assertEqual(code, 1)
        clear_cache.assert_not_called()
        self.assertNotIn("cache kept:", buf.getvalue())

    def test_unattended_cache_delete_failure(self) -> None:
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", side_effect=AssertionError("asked")),
            mock.patch("pve_prep.unattended.run_unattended", return_value=0),
            mock.patch.object(MOD, "clear_cache", side_effect=MOD.DownloadError("nope")),
            mock.patch.object(MOD, "preflight"),
            redirect_stdout(buf),
        ):
            code = MOD.main(["--unattended", "weekly"])
        self.assertEqual(code, 1)
        self.assertIn(f"Working files in {MOD.DEFAULT_CACHE} were kept: nope", buf.getvalue())

    def test_unattended_dry_run_skips_preflight_and_cache(self) -> None:
        with (
            mock.patch.object(MOD, "interview", side_effect=AssertionError("asked")),
            mock.patch("pve_prep.unattended.run_unattended", return_value=0) as refresh,
            mock.patch.object(MOD, "clear_cache") as clear_cache,
            mock.patch.object(MOD, "preflight") as preflight,
            redirect_stdout(io.StringIO()),
        ):
            code = MOD.main(["--dry-run", "--unattended", "weekly"])
        self.assertEqual(code, 0)
        preflight.assert_not_called()
        clear_cache.assert_not_called()
        self.assertIs(refresh.call_args.kwargs["dry_run"], True)

    def test_successful_template_offers_created_vmids(self) -> None:
        job = _template_job(dry_run=True)
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "run_one"),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=False),
            mock.patch.object(MOD, "offer_created") as offer,
            redirect_stdout(io.StringIO()),
        ):
            code = MOD.main([])
        self.assertEqual(code, 0)
        offer.assert_called_once()
        kwargs = offer.call_args.kwargs
        self.assertEqual(kwargs["created"], [("12", 910), ("13", 911)])
        self.assertEqual(kwargs["distro"], job.distro)
        self.assertEqual(kwargs["password_auth"], job.password_auth)
        self.assertEqual(kwargs["permit_root"], job.permit_root)
        self.assertIs(kwargs["digest_for"], MOD._digest_for)
        self.assertIs(kwargs["pin_for"], MOD._pin_for)
        self.assertEqual(kwargs["program"], str(Path(MOD.__file__).resolve()))
        self.assertEqual(kwargs["directory"], MOD.ENROLL_DIR)
        self.assertIsNone(kwargs["cron_directory"])
        self.assertEqual(kwargs["dry_run"], job.dry_run)
        self.assertIs(offer.call_args.args[0], MOD._read_line)
        self.assertIs(offer.call_args.args[1], MOD._write)

    def test_offer_abort_still_asks_about_the_cache(self) -> None:
        job = _template_job(dry_run=True)
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "run_one"),
            mock.patch.object(MOD, "offer_created", side_effect=MOD.PromptAbort("eof")),
            mock.patch.object(MOD, "ask_cache_cleanup", return_value=False) as ask,
            redirect_stdout(io.StringIO()),
        ):
            code = MOD.main([])
        self.assertEqual(code, 0)
        ask.assert_called_once()
        self.assertFalse(ask.call_args.kwargs["failed"])

    def test_image_insert_is_offered_like_a_created_template(self) -> None:
        job = replace(
            _image_job(releases=("12",), dry_run=False),
            mode="image",
            vmids=(910,),
            storage="dir-templates",
            make_template=False,
            template_vmids=frozenset(),
        )
        self.assertFalse(MOD.converts_to_template(job, 910))
        self.assertEqual(MOD._created_templates(job, [("12", 910)]), [("12", 910)])
        self.assertEqual(MOD._created_templates(job, [("12", None)]), [])

    def test_replaced_template_is_offered(self) -> None:
        job = replace(_template_job(), make_template=False)
        self.assertEqual(
            MOD._created_templates(job, [("12", 910), ("13", 911)]),
            [("12", 910)],
        )

    def test_run_lock_is_exclusive_and_refuses_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "run.lock"
            first = ORIGINAL_LOCK(lock)
            try:
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
                # flock is per open file description, so a second open in this process conflicts.
                with self.assertRaises(MOD.RunLocked):
                    ORIGINAL_LOCK(lock)
            finally:
                os.close(first)
            os.close(ORIGINAL_LOCK(lock))
            link = Path(tmp) / "link.lock"
            link.symlink_to(lock)
            with self.assertRaises(OSError) as caught:
                ORIGINAL_LOCK(link)
            self.assertNotIsInstance(caught.exception, MOD.RunLocked)

    def test_claim_host_stops_a_second_run_before_the_prune(self) -> None:
        prune = mock.MagicMock()
        buf = io.StringIO()
        with (
            mock.patch.object(MOD.os, "geteuid", return_value=0),
            mock.patch.object(MOD, "_prune_enrollments", prune),
            mock.patch.object(MOD, "acquire_run_lock", side_effect=MOD.RunLocked("lock")),
            redirect_stdout(buf),
        ):
            self.assertEqual(ORIGINAL_CLAIM(), 1)
        prune.assert_not_called()
        self.assertIn("Another pve-template-prep run is in progress", buf.getvalue())
        self.assertIn("Nothing was changed", buf.getvalue())
        with (
            mock.patch.object(MOD.os, "geteuid", return_value=0),
            mock.patch.object(MOD, "_prune_enrollments", prune),
            mock.patch.object(MOD, "acquire_run_lock", return_value=-1),
        ):
            self.assertEqual(ORIGINAL_CLAIM(), 0)
        prune.assert_called_once_with(dry_run=False)
        with (
            mock.patch.object(MOD.os, "geteuid", return_value=1000),
            self.assertRaises(SystemExit),
        ):
            ORIGINAL_CLAIM()

    def test_main_takes_the_host_before_the_interview(self) -> None:
        order: list[str] = []
        MOD._claim_host = mock.MagicMock(side_effect=lambda: order.append("claim") or 1)
        interview = mock.Mock(side_effect=lambda *a, **k: order.append("interview"))
        with (
            mock.patch.object(MOD, "interview", interview),
            mock.patch.object(MOD, "_consider_update", return_value=0),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MOD.main([]), 1)
        self.assertEqual(order, ["claim"])

    def test_locked_unattended_run_changes_nothing(self) -> None:
        MOD.acquire_run_lock = mock.MagicMock(side_effect=MOD.RunLocked("lock"))
        buf = io.StringIO()
        with (
            mock.patch("pve_prep.unattended.run_unattended") as refresh,
            mock.patch.object(MOD, "preflight"),
            redirect_stdout(buf),
        ):
            code = MOD.main(["--unattended", "weekly"])
        self.assertEqual(code, 0)
        refresh.assert_not_called()
        MOD.clear_cache.assert_not_called()
        self.assertIn("Automated update skipped", buf.getvalue())

    def test_digest_for_finalizes_an_empty_filename(self) -> None:
        spec = SimpleNamespace(
            filename="",
            url="https://example.test/index",
            checksum_alg="256",
        )
        finalized = SimpleNamespace(
            filename="Fedora.qcow2",
            url="https://example.test/Fedora.qcow2",
            checksum_alg="256",
        )
        with (
            mock.patch.object(MOD, "_spec_for", return_value=spec),
            mock.patch("pve_prep.catalog.finalize_index", return_value=finalized) as finalize,
            mock.patch("pve_prep.download._read_text", return_value="<html>") as read_text,
            mock.patch("pve_prep.download._expected_digest", return_value=("sum", "ab" * 32)) as expected,
        ):
            digest, alg = MOD._digest_for("fedora", "42")
        finalize.assert_called_once_with(spec, "<html>")
        read_text.assert_called_once_with("https://example.test/index")
        expected.assert_called_once_with(finalized)
        self.assertEqual(digest, "ab" * 32)
        self.assertEqual(alg, "256")

    def test_digest_for_uses_a_named_file(self) -> None:
        spec = SimpleNamespace(
            filename="debian-12.qcow2",
            url="https://example.test/debian-12.qcow2",
            checksum_alg="512",
        )
        with (
            mock.patch.object(MOD, "_spec_for", return_value=spec),
            mock.patch("pve_prep.catalog.finalize_index") as finalize,
            mock.patch("pve_prep.download._expected_digest", return_value=(None, "cd" * 64)),
        ):
            digest, alg = MOD._digest_for("debian", "12")
        finalize.assert_not_called()
        self.assertEqual((digest, alg), ("cd" * 64, "512"))


if __name__ == "__main__":
    unittest.main()
