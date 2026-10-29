# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""install.sh names every module it must copy. No root, no network."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep import __version__  # noqa: E402
from pve_prep.update import parse_version, version_key  # noqa: E402
_MODULES = re.compile(r"^MODULES=\(([^)]*)\)", re.MULTILINE)


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
        self.assertIn('echo "Installing pve-template-prep ${version}"', text)
        self.assertIn('echo "Installed pve-template-prep ${version} in ${DEST}"', text)
        source = (ROOT / "pve_prep" / "__init__.py").read_text(encoding="utf-8")
        found = parse_version(source)
        self.assertEqual(found, "1.4.0")
        self.assertEqual(found, __version__)
        self.assertIsNotNone(version_key(found or ""))


if __name__ == "__main__":
    unittest.main()
