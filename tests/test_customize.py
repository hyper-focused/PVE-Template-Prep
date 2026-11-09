# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Guest virt-customize argv. Stdlib unittest only."""

import io
import os
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest import mock

from pve_prep.customize import CustomizeError, _PrepMeter, _format_bar, _phase_of, apply, argv


def _commands(args: list[str]) -> list[str]:
    return [args[i + 1] for i, part in enumerate(args) if part == "--run-command"]


@contextmanager
def _backend(value: str | None):
    saved = os.environ.pop("LIBGUESTFS_BACKEND", None)
    if value is not None:
        os.environ["LIBGUESTFS_BACKEND"] = value
    try:
        yield
    finally:
        os.environ.pop("LIBGUESTFS_BACKEND", None)
        if saved is not None:
            os.environ["LIBGUESTFS_BACKEND"] = saved


class ArgvTests(unittest.TestCase):
    def test_deb_shape_and_family_commands(self) -> None:
        args = argv("disk.img", "deb")
        self.assertEqual(args[:3], ["virt-customize", "-a", "disk.img"])
        self.assertEqual(args[3], "--install")
        install = args[4]
        self.assertIn("cloud-guest-utils", install)
        self.assertNotIn("cloud-utils-growpart", install)
        text = "\n".join(_commands(args))
        self.assertIn("99-serial.cfg", text)
        self.assertIn("apparmor", text)
        self.assertIn("snapd", text)
        self.assertNotIn("SELINUX", text)
        self.assertNotIn("firewalld", text)

    def test_format_is_passed_before_the_disk(self) -> None:
        args = argv("disk.raw", "deb", disk_format="raw")
        self.assertEqual(args[:5], ["virt-customize", "--format", "raw", "-a", "disk.raw"])
        with self.assertRaises(ValueError):
            argv("disk.img", "deb", disk_format="vmdk")

    def test_el_family_commands(self) -> None:
        args = argv("disk.img", "el")
        install = args[args.index("--install") + 1]
        self.assertIn("cloud-utils-growpart", install)
        text = "\n".join(_commands(args))
        self.assertIn("SELINUX=permissive", text)
        self.assertIn("firewalld", text)
        self.assertIn("grubby", text)
        self.assertIn("/etc/chrony.conf", text)
        self.assertNotIn("/etc/chrony/chrony.conf", text)
        self.assertNotIn("apparmor", text)
        self.assertNotIn("snapd", text)
        grub = next(cmd for cmd in _commands(args) if "GRUB_CMDLINE_LINUX" in cmd)
        self.assertIn(r"\(GRUB_CMDLINE_LINUX", grub)

    def test_shared_commands(self) -> None:
        for family in ("deb", "el"):
            text = "\n".join(_commands(argv("disk.img", family)))
            self.assertIn(
                "multi-user.target.wants/qemu-guest-agent.service",
                text,
            )
            self.assertIn("99-grow.cfg", text)
            self.assertIn("ssh_host_", text)
            self.assertIn("machine-id", text)
            self.assertIn(".bash_history", text)
            self.assertIn("/var/log", text)
            self.assertIn("random-seed", text)
            self.assertIn("70-persistent-net.rules", text)
            self.assertNotIn("00-enableroot.conf", text)
            commands = _commands(argv("disk.img", family))
            clean = next(cmd for cmd in commands if ".bash_history" in cmd)
            self.assertGreater(commands.index(clean), commands.index(next(cmd for cmd in commands if "ssh_host_" in cmd)))
            self.assertNotIn("PermitRootLogin", text)

    def test_ssh_drop_in_is_one_command_on_both_families(self) -> None:
        for family in ("deb", "el"):
            commands = _commands(argv("disk.img", family, password_auth=True, permit_root=True))
            root = [cmd for cmd in commands if "00-enableroot.conf" in cmd]
            self.assertEqual(len(root), 1)
            text = root[0]
            self.assertIn("PasswordAuthentication yes", text)
            self.assertIn("PermitRootLogin yes", text)
            self.assertIn("chmod 644 /etc/ssh/sshd_config.d/00-enableroot.conf", text)
            self.assertIn("chcon --reference=/etc/ssh/sshd_config", text)
            self.assertIn("restorecon -F /etc/ssh/sshd_config.d/00-enableroot.conf", text)
            self.assertIn("|| true", text)
            commands = _commands(argv("disk.img", family, password_auth=True, permit_root=True))
            clean = next(i for i, cmd in enumerate(commands) if ".bash_history" in cmd)
            root_at = next(i for i, cmd in enumerate(commands) if "00-enableroot.conf" in cmd)
            self.assertLess(clean, root_at)

    def test_each_ssh_answer_writes_only_its_line(self) -> None:
        for family in ("deb", "el"):
            only_password = _commands(argv("disk.img", family, password_auth=True))
            text = next(cmd for cmd in only_password if "00-enableroot.conf" in cmd)
            self.assertIn("'PasswordAuthentication yes'", text)
            self.assertNotIn("PermitRootLogin", text)
            only_root = _commands(argv("disk.img", family, permit_root=True))
            text = next(cmd for cmd in only_root if "00-enableroot.conf" in cmd)
            self.assertIn("'PermitRootLogin yes'", text)
            self.assertNotIn("PasswordAuthentication", text)

    def test_unknown_family(self) -> None:
        with self.assertRaises(ValueError):
            argv("disk.img", "suse")


class ApplyTests(unittest.TestCase):
    def test_dry_run_does_not_call_run(self) -> None:
        def fake_run(command: list[str], env: dict[str, str]) -> object:
            raise AssertionError("dry_run must not call run")

        buf = io.StringIO()
        with redirect_stdout(buf):
            apply("disk.img", "deb", dry_run=True, run=fake_run)
        body = buf.getvalue()
        lines = body.splitlines()
        self.assertEqual(lines[0], "")
        self.assertIn("virt-customize", lines[1])
        self.assertIn("disk.img", lines[1])
        self.assertNotIn("00-enableroot.conf", lines[1])
        self.assertEqual(lines[2], "")
        self.assertTrue(body.endswith("\n"))

        buf = io.StringIO()
        with redirect_stdout(buf):
            apply("disk.img", "el", permit_root=True, dry_run=True, run=fake_run)
        self.assertIn("00-enableroot.conf", buf.getvalue())
        self.assertIn("PermitRootLogin yes", buf.getvalue())

    def test_success_uses_direct_when_unset(self) -> None:
        calls: list[tuple[list[str], dict[str, str]]] = []

        class Result:
            returncode = 0

        def fake_run(command: list[str], env: dict[str, str]) -> Result:
            calls.append((command, env))
            return Result()

        with _backend(None):
            apply("disk.img", "el", run=fake_run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["LIBGUESTFS_BACKEND"], "direct")
        self.assertEqual(calls[0][0][:3], ["virt-customize", "-a", "disk.img"])

    def test_backend_passthrough(self) -> None:
        seen: dict[str, str] = {}

        class Result:
            returncode = 0

        def fake_run(command: list[str], env: dict[str, str]) -> Result:
            seen.update(env)
            return Result()

        with _backend("appliance"):
            apply("disk.img", "deb", run=fake_run)
        self.assertEqual(seen["LIBGUESTFS_BACKEND"], "appliance")

    def test_nonzero_raises(self) -> None:
        class Result:
            returncode = 7

        def fake_run(command: list[str], env: dict[str, str]) -> Result:
            return Result()

        with self.assertRaises(RuntimeError) as caught:
            apply("disk.img", "deb", run=fake_run)
        self.assertIn("7", str(caught.exception))

    def test_bar_names_the_phase_and_hides_the_shell(self) -> None:
        shell = "[  36.4] Running: find /root -name .bash_history -delete"
        self.assertEqual(_phase_of(shell), "command")
        self.assertEqual(
            _phase_of("[  15.6] Installing packages: qemu-guest-agent cloud-init"),
            "install",
        )
        self.assertEqual(_phase_of("[   0.0] Examining the guest ..."), "examine")
        meter = _PrepMeter(18)
        start = 1000.0
        meter.begin(start)
        percent, phase = meter.snapshot(start + 10)
        self.assertGreater(percent, 10)
        self.assertLess(percent, 30)
        self.assertEqual(phase, "Examining the guest")
        meter.note("install", start + 15)
        percent, phase = meter.snapshot(start + 35)
        self.assertEqual(phase, "Installing packages")
        self.assertGreater(percent, 60)
        self.assertLess(percent, 88)
        meter.note(_phase_of(shell), start + 40)
        shown = _format_bar(*meter.snapshot(start + 40))
        self.assertIn("Configuring the guest", shown)
        self.assertNotIn("bash_history", shown)
        self.assertNotIn("Running", shown)

    def test_verbose_keeps_the_runner(self) -> None:
        calls: list[list[str]] = []

        class Result:
            returncode = 0

        def fake_run(command: list[str], env: dict[str, str]) -> Result:
            calls.append(command)
            return Result()

        buf = io.StringIO()
        with redirect_stdout(buf):
            apply("disk.img", "el", verbose=True, progress=True, run=fake_run)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("Preparing", buf.getvalue())
        self.assertNotIn("/100%", buf.getvalue())

    def test_quiet_failure_keeps_the_log_off_stdout(self) -> None:
        with mock.patch(
            "pve_prep.customize._run_with_bar", return_value=(1, "secret log\n")
        ):
            with redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(CustomizeError) as caught:
                    apply("disk.img", "deb", quiet=True)
        self.assertEqual(caught.exception.log, "secret log\n")
        self.assertEqual(out.getvalue(), "")

    def test_quiet_success_discards_the_captured_log(self) -> None:
        with mock.patch(
            "pve_prep.customize._run_with_bar", return_value=(0, "secret log\n")
        ):
            with redirect_stdout(io.StringIO()) as out:
                apply("disk.img", "deb", quiet=True)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
