# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""install.sh names every module it must copy. No root, no network."""

from __future__ import annotations

import os
import pty
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep import __version__  # noqa: E402
from pve_prep.update import parse_version, version_key  # noqa: E402
_MODULES = re.compile(r"^MODULES=\(([^)]*)\)", re.MULTILINE)


def _run_report(script: str, env: dict[str, str]) -> str:
    """Run the closing-note function on a terminal, so color follows the tty check."""
    master, slave = pty.openpty()
    child_env = os.environ.copy()
    child_env.pop("NO_COLOR", None)
    child_env.update(env)
    proc = subprocess.Popen(
        ["bash", "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=slave,
        stderr=slave,
        env=child_env,
        close_fds=True,
    )
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(master)
    code = proc.wait()
    if code != 0:
        raise AssertionError(code)
    return b"".join(chunks).decode().replace("\r\n", "\n")


def shipped_modules(text: str) -> list[str]:
    """Names inside the MODULES array, in file order."""
    match = _MODULES.search(text)
    if match is None:
        raise AssertionError("install.sh has no MODULES array")
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


class InstallManifestTests(unittest.TestCase):
    def test_modules_are_every_package_file(self) -> None:
        names = shipped_modules((ROOT / "install.sh").read_text(encoding="utf-8"))
        on_disk = sorted(path.name for path in (ROOT / "pve_prep").glob("*.py"))
        self.assertEqual(names, on_disk)

    def test_install_check_imports_the_enrollment_modules(self) -> None:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        for name in ("cronscript", "enroll", "offer", "unattended"):
            self.assertIn(f"import pve_prep.{name}", text)

    def test_install_reports_the_archive_version(self) -> None:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('echo "Downloading pve-template-prep....."', text)
        self.assertIn('echo "Installing pve-template-prep ${version} at ${DEST}....."', text)
        self.assertIn('report_installed "$version"', text)
        source = (ROOT / "pve_prep" / "__init__.py").read_text(encoding="utf-8")
        found = parse_version(source)
        self.assertEqual(found, "1.4.0")
        self.assertEqual(found, __version__)
        self.assertIsNotNone(version_key(found or ""))

    def test_closing_notes_break_into_sections(self) -> None:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        match = re.search(r"^report_installed\(\) \{.*?\n\}", text, re.M | re.S)
        self.assertIsNotNone(match)
        script = match.group(0) + '\nreport_installed "1.4.0"\n'
        plain = _run_report(script, {"NO_COLOR": "1", "TERM": "xterm-256color"})
        self.assertEqual(
            plain,
            "\n"
            "Installation of pve-template-prep 1.4.0 complete!\n"
            "\n"
            "To run: (as root or sudo): pve-template-prep\n"
            "To do a dry run: pve-template-prep --dry-run\n",
        )
        colored = _run_report(script, {"TERM": "xterm-256color"})
        self.assertIn("\033[38;5;78mInstallation of pve-template-prep 1.4.0 complete!\033[0m", colored)
        self.assertIn("\033[38;5;45mTo run: (as root or sudo):\033[0m \033[1mpve-template-prep\033[0m", colored)
        self.assertIn("\033[38;5;45mTo do a dry run:\033[0m \033[1mpve-template-prep --dry-run\033[0m", colored)
        dumb = _run_report(script, {"TERM": "dumb"})
        self.assertNotIn("\033", dumb)


if __name__ == "__main__":
    unittest.main()
