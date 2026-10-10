"""Compare this install with main and build the command that replaces it.

The remote file is parsed. It is never executed. Nothing here prompts or restarts.
"""

from __future__ import annotations

import ast
import re
import urllib.error
import urllib.request
from pathlib import Path

from pve_prep import __version__

VERSION_URL = (
    "https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/"
    "main/pve_prep/__init__.py"
)
INSTALL_URL = (
    "https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/main/install.sh"
)
_MAX_BODY = 65536
_TRIPLE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(source: str) -> str | None:
    """Return the last __version__ string assignment, or None."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    found: str | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        named = any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
        if not named:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found = value.value
    return found


def version_key(text: str) -> tuple[int, int, int] | None:
    """x.y.z as integers. Anything else, including a pre-release suffix, is None."""
    match = _TRIPLE.fullmatch(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(local: str, remote: str) -> bool:
    """True when remote is a newer x.y.z than local. Equal and older are false."""
    current = version_key(local)
    offered = version_key(remote)
    if current is None or offered is None:
        return False
    return offered > current


def is_checkout(script: Path) -> bool:
    """True when the script lives in a git checkout. Those are not updated in place."""
    return (script.resolve().parent / ".git").exists()


def wants_fetch(answer: str) -> bool:
    """Enter, no, and anything else keep the installed copy."""
    return answer.strip().lower() in {"y", "yes"}


def notice(local: str, remote: str) -> str:
    return f"{remote} is available (this is {local})."


def fetch_text(url: str, timeout: float) -> str | None:
    """GET url. None on timeout, HTTP error, or a body larger than the cap."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"pve-prep/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_BODY + 1)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    if len(raw) > _MAX_BODY:
        return None
    return raw.decode("utf-8", errors="replace")


def fetch_version(url: str = VERSION_URL, timeout: float = 3.0) -> str | None:
    """Version string on main, or None when the check cannot be trusted."""
    text = fetch_text(url, timeout)
    if text is None:
        return None
    found = parse_version(text)
    if found is None or version_key(found) is None:
        return None
    return found


def install_argv(saved_script: Path, dest: Path, *, url: str = INSTALL_URL) -> list[str]:
    """Download install.sh, then run it with DEST set to this install."""
    return [
        "bash",
        "-c",
        'curl -fsSL --max-time 60 "$1" -o "$2" && DEST="$3" bash "$2"',
        "pve-prep-update",
        url,
        str(saved_script),
        str(dest),
    ]
