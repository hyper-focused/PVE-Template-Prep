"""Orchestrator tests. Sibling modules are stubbed only while the script loads."""

from __future__ import annotations

import importlib.util
import io
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


def _stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__package__ = "pve_prep"
    module.__file__ = f"<stub {name}>"
    sys.modules[name] = module
    return module


def _install_sibling_stubs(real_customize, real_vm, real_catalog) -> None:
    catalog = _stub("pve_prep.catalog")
    # prompts.py imports this name while the orchestrator is loading.
    catalog.DISTROS = real_catalog.DISTROS
    catalog.releases_for = mock.MagicMock(name="releases_for")
    catalog.normalize_release = mock.MagicMock(name="normalize_release")

    download = _stub("pve_prep.download")
    download.fetch_verified = mock.MagicMock(name="fetch_verified")
    download.convert = mock.MagicMock(name="convert")
    download.publish = mock.MagicMock(name="publish", return_value="written")

    customize = _stub("pve_prep.customize")
    customize.apply = mock.MagicMock(name="apply")
    # Dry-run prints this. The orchestrator must bind the real builder, not a mock.
    customize.argv = real_customize.argv

    vm = _stub("pve_prep.vm")
    vm.create_template = mock.MagicMock(name="create_template")
    vm.parse_qm_status = mock.MagicMock(name="parse_qm_status")
    vm.existing_prep_commands = mock.MagicMock(name="existing_prep_commands")
    vm.parse_storage_ids = mock.MagicMock(name="parse_storage_ids")
    vm.VmError = real_vm.VmError
    vm.VmDestroyedError = real_vm.VmDestroyedError
    vm.is_template = real_vm.is_template
    vm.parse_storage_content = real_vm.parse_storage_content
    vm.storage_lacks_images = real_vm.storage_lacks_images
    vm.vmid_config_missing = real_vm.vmid_config_missing
    vm.existing_hw_commands = real_vm.existing_hw_commands
    vm.config_has_os_disk = real_vm.config_has_os_disk
    vm.insert_disk = mock.MagicMock(name="insert_disk")


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
        _install_sibling_stubs(customize_mod, vm_mod, catalog_mod)
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
        SimpleNamespace(release="12", label="bookworm", eol=False, family="deb"),
        SimpleNamespace(release="13", label="trixie", eol=False, family="deb"),
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
    )


def _existing_job(*, dry_run=False, prep=True, vmid=910):
    from pve_prep.job import DEFAULT_CACHE, Job

    return Job(
        distro="debian",
        releases=("12",),
        mode="existing",
        disk_format="raw",
        dest_dir="",
        storage="dir-templates",
        cache_dir=DEFAULT_CACHE,
        collision="backup",
        vmids=(vmid,),
        prep=prep,
        bridge="vmbr0",
        memory_mb=2048,
        cores=2,
        dry_run=dry_run,
        destroy_vmids=frozenset(),
    )


class OrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        MOD.fetch_verified = mock.MagicMock(return_value=Path("/tmp/src.qcow2"))
        MOD.convert = mock.MagicMock()
        MOD.publish = mock.MagicMock(return_value="written")
        MOD.customize_apply = mock.MagicMock()
        MOD.create_template = mock.MagicMock()
        MOD.releases_for = mock.MagicMock(side_effect=_specs)
        MOD.parse_qm_status = mock.MagicMock(return_value="stopped")
        MOD.existing_prep_commands = mock.MagicMock()
        MOD.parse_storage_ids = mock.MagicMock(return_value=[])
        MOD.default_run = ORIGINAL_RUN

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
            self.assertEqual(work, Path("/var/tmp/pve-template-prep/cache/debian-12-pve.img.work"))
            self.assertEqual(disk_format, "raw")
            self.assertFalse(dry_run)

        def apply(path, family, dry_run=False):
            order.append("apply")
            self.assertEqual(path, "/var/tmp/pve-template-prep/cache/debian-12-pve.img.work")
            self.assertEqual(family, "deb")
            self.assertFalse(dry_run)

        def publish(work, dest, collision, dry_run=False):
            order.append("publish")
            self.assertEqual(work, Path("/var/tmp/pve-template-prep/cache/debian-12-pve.img.work"))
            self.assertEqual(dest, Path("/tmp/images/debian-12-pve.img"))
            self.assertEqual(collision, "backup")
            self.assertFalse(dry_run)
            return "skipped"

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
        MOD.run_one(job, "12", 910)
        MOD.run_one(job, "13", 911)
        self.assertEqual([item["destroy_ok"] for item in recorded], [True, False])
        self.assertEqual([item["backup_disks"] for item in recorded], [True, False])
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
        self.assertTrue(recorded[0]["image_path"].endswith("debian-12-pve.qcow2.work"))
        MOD.publish.assert_not_called()
        MOD.fetch_verified.assert_called()

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
        with mock.patch.object(MOD, "interview", return_value=job), redirect_stdout(buf):
            code = MOD.main([])
        text = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertEqual(calls["n"], 2)
        self.assertIn("OK 12", text)
        self.assertIn("FAIL 13: boom", text)
        self.assertIn("done", text)
        MOD.convert.assert_called_once()

    def test_main_abort(self) -> None:
        with mock.patch.object(MOD, "interview", side_effect=MOD.PromptAbort("no")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = MOD.main([])
        self.assertEqual(code, 1)
        self.assertIn("aborted", buf.getvalue())

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
        ):
            seen["dry_run"] = dry_run
            seen["storages"] = list_storages()
            return _image_job(releases=("12",), dry_run=True)

        MOD.fetch_verified = lambda *args, **kwargs: Path("/tmp/src.qcow2")
        buf = io.StringIO()
        with (
            mock.patch.object(MOD, "interview", fake_interview),
            mock.patch.object(MOD.subprocess, "run", side_effect=OSError("no pvesm")),
            redirect_stdout(buf),
        ):
            code = MOD.main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(seen["dry_run"])
        self.assertEqual(seen["storages"], [])
        self.assertIn("OK 12", buf.getvalue())

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
        self.assertIn("apt-get install libguestfs-tools qemu-utils", str(caught.exception))
        self.assertIn("qemu-img", str(caught.exception))

    def test_preflight_tool_set_by_mode(self) -> None:
        seen: list[str] = []

        def which(name: str):
            seen.append(name)
            return "/usr/bin/" + name

        with mock.patch.object(MOD.os, "geteuid", return_value=0), mock.patch.object(MOD.shutil, "which", side_effect=which):
            MOD.preflight(_image_job(dry_run=False, prep=True))
            image_tools = list(seen)
            seen.clear()
            MOD.preflight(_existing_job(dry_run=False, prep=False))
            existing_tools = list(seen)
            seen.clear()
            MOD.preflight(_template_job(dry_run=False))
            template_tools = list(seen)
        self.assertEqual(image_tools, ["qemu-img", "virt-customize"])
        self.assertEqual(existing_tools, ["qm", "pvesm"])
        self.assertEqual(template_tools, ["qemu-img", "virt-customize", "qm", "pvesm"])

    def test_main_live_calls_preflight(self) -> None:
        job = _image_job(dry_run=False, releases=("12",))
        with (
            mock.patch.object(MOD, "interview", return_value=job),
            mock.patch.object(MOD, "preflight", side_effect=SystemExit("run as root on the PVE host")),
            self.assertRaises(SystemExit),
        ):
            MOD.main([])

    def test_existing_dry_run_does_not_touch_host(self) -> None:
        called = mock.Mock(side_effect=AssertionError("dry-run called the host"))
        MOD.default_run = called
        buf = io.StringIO()
        with redirect_stdout(buf):
            MOD.run_one(_existing_job(dry_run=True), "12", 910)
        text = buf.getvalue()
        self.assertIn("existing VMID 910 storage dir-templates guest-prep=yes", text)
        self.assertIn("virt-customize", text)
        self.assertIn("--agent", text)
        called.assert_not_called()
        MOD.fetch_verified.assert_not_called()

    def test_existing_live_apply_then_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disk = Path(tmp) / "vm-910-disk-0.raw"
            disk.write_bytes(b"not a real disk")
            calls: list[list[str]] = []

            def run(argv):
                calls.append(list(argv))
                if argv[:2] == ["qm", "status"]:
                    return SimpleNamespace(returncode=0, stdout="status: stopped\n", stderr="")
                if argv[:2] == ["qm", "config"]:
                    return SimpleNamespace(returncode=0, stdout="scsi0: dir-templates:910/vm-910-disk-0.raw\n", stderr="")
                if argv[:2] == ["pvesm", "path"]:
                    return SimpleNamespace(returncode=0, stdout=f"{disk}\n", stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            MOD.default_run = run
            MOD.parse_qm_status = lambda _text: "stopped"

            def prep_commands(config, *, vmid, storage):
                self.assertEqual(storage, "dir-templates")
                self.assertIn("scsi0:", config)
                return (
                    [["qm", "set", str(vmid), "--agent", "enabled=1"]],
                    "dir-templates:910/vm-910-disk-0.raw",
                )

            MOD.existing_prep_commands = prep_commands
            MOD.run_one(_existing_job(dry_run=False, prep=True), "12", 910)
            self.assertEqual(calls[0][:2], ["qm", "status"])
            self.assertEqual(calls[1][:2], ["qm", "config"])
            self.assertEqual(calls[2][:3], ["pvesm", "path", "dir-templates:910/vm-910-disk-0.raw"])
            self.assertEqual(calls[3], ["qm", "set", "910", "--agent", "enabled=1"])
            MOD.customize_apply.assert_called_once_with(str(disk), "deb", dry_run=False)
            MOD.fetch_verified.assert_not_called()

    def test_existing_running_fails(self) -> None:
        def run(argv):
            return SimpleNamespace(returncode=0, stdout="status: running\n", stderr="")

        MOD.default_run = run
        MOD.parse_qm_status = lambda _text: "running"
        with self.assertRaises(RuntimeError) as caught:
            MOD.run_one(_existing_job(dry_run=False), "12", 910)
        self.assertIn("SKIP 910 status=running (stop it first)", str(caught.exception))
        MOD.customize_apply.assert_not_called()

    def test_existing_nonzero_includes_stderr_tail(self) -> None:
        def run(argv):
            return SimpleNamespace(returncode=2, stdout="", stderr="noise\nno such VMID\n")

        MOD.default_run = run
        with self.assertRaises(RuntimeError) as caught:
            MOD.run_one(_existing_job(dry_run=False, prep=False), "12", 910)
        self.assertIn("no such VMID", str(caught.exception))

    def test_existing_rejects_non_regular_disk(self) -> None:
        def run(argv):
            if argv[:2] == ["qm", "status"]:
                return SimpleNamespace(returncode=0, stdout="status: stopped\n", stderr="")
            if argv[:2] == ["qm", "config"]:
                return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="/no/such/vm-disk.raw\n", stderr="")

        MOD.default_run = run
        MOD.parse_qm_status = lambda _text: "stopped"
        MOD.existing_prep_commands = lambda config, *, vmid, storage: (
            [],
            "dir-templates:910/vm-910-disk-0.raw",
        )
        with self.assertRaises(RuntimeError) as caught:
            MOD.run_one(_existing_job(dry_run=False, prep=True), "12", 910)
        self.assertIn("disk is not a regular file", str(caught.exception))
        MOD.customize_apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
