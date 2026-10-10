"""Version check. No network, no installer."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep import __version__  # noqa: E402
from pve_prep.update import (  # noqa: E402
    fetch_version,
    install_argv,
    is_checkout,
    is_newer,
    notice,
    parse_version,
    version_key,
    wants_fetch,
)


class ParseTests(unittest.TestCase):
    def test_real_init_is_a_triple(self) -> None:
        source = (ROOT / "pve_prep" / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(parse_version(source), __version__)
        self.assertEqual(version_key(__version__), tuple(int(part) for part in __version__.split(".")))

    def test_last_assignment_wins_and_junk_does_not(self) -> None:
        source = '\n'.join(
            [
                '"""doc"""',
                '__version__ = "1.0.0"',
                "other = 3",
                '__version__ = "1.1.2"',
            ]
        )
        self.assertEqual(parse_version(source), "1.1.2")
        self.assertIsNone(parse_version("this is not python"))
        self.assertIsNone(parse_version("__version__ = 1"))
        self.assertIsNone(parse_version("__version__ = ('1', '1', '0')"))

    def test_patch_bugfix_and_release_rows_are_newer(self) -> None:
        self.assertTrue(is_newer("1.1.0", "1.1.1"))
        self.assertTrue(is_newer("1.1.9", "1.2.0"))
        self.assertTrue(is_newer("1.9.0", "1.10.0"))
        self.assertFalse(is_newer("1.1.1", "1.1.1"))
        self.assertFalse(is_newer("1.1.1", "1.1.0"))
        self.assertFalse(is_newer("1.1.0", "1.1.0-rc1"))
        self.assertFalse(is_newer("nope", "1.1.1"))

    def test_answer_and_notice(self) -> None:
        self.assertTrue(wants_fetch("y"))
        self.assertTrue(wants_fetch(" YES "))
        self.assertFalse(wants_fetch(""))
        self.assertFalse(wants_fetch("n"))
        self.assertFalse(wants_fetch("yep"))
        self.assertEqual(notice("1.1.0", "1.1.1"), "1.1.1 is available (this is 1.1.0).")

    def test_checkout_is_the_git_dir_beside_the_script(self) -> None:
        self.assertTrue(is_checkout(ROOT / "pve-template-prep.py"))
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "pve-template-prep.py"
            self.assertFalse(is_checkout(script))
            (Path(tmp) / ".git").mkdir()
            self.assertTrue(is_checkout(script))

    def test_install_command_pins_dest_and_the_script(self) -> None:
        argv = install_argv(Path("/tmp/install.sh"), Path("/opt/pve-template-prep"))
        self.assertEqual(argv[0], "bash")
        self.assertEqual(argv[3], "pve-prep-update")
        self.assertEqual(argv[4], "https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/main/install.sh")
        self.assertEqual(argv[5], "/tmp/install.sh")
        self.assertEqual(argv[6], "/opt/pve-template-prep")
        self.assertIn('DEST="$3"', argv[2])


class FetchTests(unittest.TestCase):
    def test_body_over_the_cap_or_a_network_error_is_no_offer(self) -> None:
        class _Big:
            def read(self, _n):
                return b"x" * 70000

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with mock.patch("pve_prep.update.urllib.request.urlopen", return_value=_Big()):
            self.assertIsNone(fetch_version("https://example.invalid/v"))

        with mock.patch(
            "pve_prep.update.urllib.request.urlopen",
            side_effect=TimeoutError("slow"),
        ):
            self.assertIsNone(fetch_version("https://example.invalid/v"))

    def test_remote_assignment_is_the_offer(self) -> None:
        body = b'"""x"""\n__version__ = "1.1.4"\n'

        class _Body:
            def read(self, _n):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with mock.patch("pve_prep.update.urllib.request.urlopen", return_value=_Body()):
            self.assertEqual(fetch_version("https://example.invalid/v"), "1.1.4")


class StartupTests(unittest.TestCase):
    def _main_module(self):
        from tests.test_orchestrator import MOD

        return MOD

    def test_enter_keeps_this_copy(self) -> None:
        mod = self._main_module()
        script = Path("/opt/pve-template-prep/pve-template-prep.py")
        buf = io.StringIO()
        with (
            mock.patch.object(mod.tool_update, "is_checkout", return_value=False),
            mock.patch.object(mod.tool_update, "fetch_version", return_value="1.1.1"),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
            mock.patch("builtins.input", return_value=""),
            mock.patch.object(sys.stdout, "write", buf.write),
        ):
            self.assertIsNone(mod._consider_update(script))
        self.assertIn(f"1.1.1 is available (this is {__version__}).", buf.getvalue())
        self.assertIn("Fetch it now? [y/N]", buf.getvalue())

    def test_unreachable_main_does_not_ask(self) -> None:
        mod = self._main_module()
        script = Path("/opt/pve-template-prep/pve-template-prep.py")
        with (
            mock.patch.object(mod.tool_update, "is_checkout", return_value=False),
            mock.patch.object(mod.tool_update, "fetch_version", return_value=None),
            mock.patch("builtins.input", side_effect=AssertionError("asked")),
        ):
            self.assertIsNone(mod._consider_update(script))

    def test_yes_without_root_continues(self) -> None:
        mod = self._main_module()
        script = Path("/opt/pve-template-prep/pve-template-prep.py")
        buf = io.StringIO()
        with (
            mock.patch.object(mod.tool_update, "is_checkout", return_value=False),
            mock.patch.object(mod.tool_update, "fetch_version", return_value="1.2.0"),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="y"),
            mock.patch.object(mod.os, "geteuid", return_value=1000),
            mock.patch.object(mod.subprocess, "run", side_effect=AssertionError("ran")),
            mock.patch.object(sys.stdout, "write", buf.write),
        ):
            self.assertIsNone(mod._consider_update(script))
        self.assertIn("Run as root to update.", buf.getvalue())

    def test_yes_as_root_reexecs_and_does_not_ask_twice(self) -> None:
        mod = self._main_module()
        script = Path("/opt/pve-template-prep/pve-template-prep.py")
        seen = {}

        def run(argv):
            seen["argv"] = argv
            return mock.Mock(returncode=0)

        def execv(executable, argv):
            seen["exec"] = (executable, argv)
            raise SystemExit(0)

        with (
            mock.patch.object(mod.tool_update, "is_checkout", return_value=False),
            mock.patch.object(mod.tool_update, "fetch_version", return_value="1.1.1"),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="yes"),
            mock.patch.object(mod.os, "geteuid", return_value=0),
            mock.patch.object(mod.subprocess, "run", side_effect=run),
            mock.patch.object(mod.os, "execv", side_effect=execv),
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("PVE_PREP_SKIP_UPDATE", None)
            with self.assertRaises(SystemExit):
                mod._consider_update(script)
            self.assertEqual(seen["argv"][6], "/opt/pve-template-prep")
            self.assertEqual(seen["exec"][1][1], str(script))
            self.assertEqual(os.environ.get("PVE_PREP_SKIP_UPDATE"), "1")

    def test_pipe_prints_the_notice_and_does_not_wait(self) -> None:
        mod = self._main_module()
        script = Path("/opt/pve-template-prep/pve-template-prep.py")
        buf = io.StringIO()
        with (
            mock.patch.object(mod.tool_update, "is_checkout", return_value=False),
            mock.patch.object(mod.tool_update, "fetch_version", return_value="1.1.3"),
            mock.patch.object(sys.stdin, "isatty", return_value=False),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=AssertionError("asked")),
            mock.patch.object(sys.stdout, "write", buf.write),
        ):
            self.assertIsNone(mod._consider_update(script))
        self.assertIn("1.1.3 is available", buf.getvalue())
        self.assertNotIn("Fetch it now", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
