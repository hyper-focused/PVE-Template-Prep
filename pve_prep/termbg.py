# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Light or dark terminal background. Dark when it cannot tell.

Order: PVE_PREP_THEME, then an OSC 11 query, then COLORFGBG, then dark.

The query is followed by a DA1 request. Every terminal answers DA1, so
a DA1 reply without an OSC 11 reply before it means no support, and the
probe returns at once instead of waiting out the timeout. The timeout
only runs when the terminal answers nothing at all.
"""

from __future__ import annotations

import os
import re
import select
import time
from typing import Mapping

THEMES = ("dark", "light")
# Long enough for one SSH round trip across an ocean.
_TIMEOUT = 1.0
_QUERY = b"\033]11;?\033\\\033[c"
_DA1 = re.compile(rb"\033\[\?[0-9;]*c")
_OSC11 = re.compile(
    rb"\033\]11;rgba?:([0-9A-Fa-f]{1,4})/([0-9A-Fa-f]{1,4})/([0-9A-Fa-f]{1,4})"
)


def detect(*, environ: Mapping[str, str] | None = None, probe: bool = True) -> str:
    """dark or light. probe=False skips the terminal query."""
    env = os.environ if environ is None else environ
    forced = env.get("PVE_PREP_THEME", "").strip().casefold()
    if forced in THEMES:
        return forced
    if probe:
        rgb = query_background()
        if rgb is not None:
            return theme_for(rgb)
    return from_colorfgbg(env.get("COLORFGBG", "")) or "dark"


def theme_for(rgb: tuple[float, float, float]) -> str:
    """Channels are 0 to 1. Rec. 709 weights, no gamma. Mid grey and up is light."""
    red, green, blue = rgb
    return "light" if 0.2126 * red + 0.7152 * green + 0.0722 * blue >= 0.5 else "dark"


def from_colorfgbg(value: str) -> str | None:
    """fg;bg or fg;default;bg. The last field is the background color index."""
    last = value.strip().rpartition(";")[2]
    if not last.isdigit():
        return None
    index = int(last)
    if index in (7, *range(9, 16)):
        return "light"
    if 0 <= index <= 15:
        return "dark"
    return None


def parse_reply(reply: bytes) -> tuple[float, float, float] | None:
    """rgb:RRRR/GGGG/BBBB from an OSC 11 reply. Each channel may be 1 to 4 digits."""
    found = _OSC11.search(reply)
    if found is None:
        return None
    channels = []
    for digits in found.groups():
        channels.append(int(digits, 16) / (16 ** len(digits) - 1))
    return channels[0], channels[1], channels[2]


def query_background(
    timeout: float = _TIMEOUT, *, device: str = "/dev/tty"
) -> tuple[float, float, float] | None:
    """Ask the controlling terminal. None when there is no answer or no terminal."""
    try:
        import termios
    except ImportError:
        return None
    try:
        fd = os.open(device, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return None
    try:
        quiet = termios.tcgetattr(fd)
        quiet[3] &= ~(termios.ECHO | termios.ICANON)
        quiet[6][termios.VMIN] = 0
        quiet[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, quiet)
        os.write(fd, _QUERY)
        reply = _read_reply(fd, timeout)
    except (OSError, termios.error):
        reply = b""
    finally:
        # TCSAFLUSH drops anything unread, so a reply never reaches the first prompt.
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, saved)
        except termios.error:
            pass
        os.close(fd)
    return parse_reply(reply)


def _read_reply(fd: int, timeout: float) -> bytes:
    """Read until the DA1 reply arrives or the time runs out."""
    deadline = time.monotonic() + timeout
    buffer = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return buffer
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            return buffer
        chunk = os.read(fd, 256)
        if not chunk:
            return buffer
        buffer += chunk
        if _DA1.search(buffer):
            return buffer
