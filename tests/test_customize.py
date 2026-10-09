"""Guest virt-customize argv. Stdlib unittest only."""

import io
import os
import unittest
from contextlib import contextmanager, redirect_stdout

from pve_prep.customize import apply, argv


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
        self.assertEqual(lines[2], "")
        self.assertTrue(body.endswith("\n"))

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


if __name__ == "__main__":
    unittest.main()
