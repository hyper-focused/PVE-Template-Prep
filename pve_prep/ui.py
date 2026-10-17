"""Terminal widgets for the interview.

prompts.py still owns the questions, the free-VMID rule, and the DELETE
gate. This module only draws them. No TTY, or a vendor tree that will
not import, and the caller keeps the numbered prompts.

One palette. The terminal profile picks 16, 256, or 24-bit. A pipe,
NO_COLOR, or TERM=dumb stays plain. prompt_toolkit downsamples the hex
style to the profile it is given.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
_ready: bool | None = None
# Tests call tone() before arm(). A real run sets this from the terminal.
_depth = "16"

_SELECT_KEYS = "\n  ↑↓ move · Space or Enter selects"
_CHECK_KEYS = "\n  ↑↓ move · Space marks · Enter accepts"

# Dark background. Danger stays red. The current row stays inverted cyan.
_RGB = {
    "header": (95, 191, 122),
    "body": (230, 230, 230),
    "cyan": (95, 215, 255),
    "danger": (255, 85, 85),
    "muted": (138, 138, 138),
    "ink": (18, 18, 18),
}
_ANSI16 = {
    "header": "32",
    "body": "37",
    "cyan": "36",
    "danger": "31",
    "muted": "90",
}
_CUBE = (0, 95, 135, 175, 215, 255)
_RESET = "\033[0m"


def _ensure_vendor() -> None:
    entry = str(_VENDOR)
    if _VENDOR.is_dir() and entry not in sys.path:
        sys.path.insert(0, entry)


def available() -> bool:
    """True when the vendored widgets import."""
    global _ready
    if _ready is not None:
        return _ready
    _ensure_vendor()
    try:
        import prompt_toolkit  # noqa: F401
        import questionary  # noqa: F401
        import wcwidth  # noqa: F401
    except Exception:
        _ready = False
    else:
        _ready = True
    return _ready


def enabled(read_line) -> bool:
    """The production reader sets this. Test readers do not."""
    return bool(getattr(read_line, "use_questionary", False)) and available()


def color_depth(
    *,
    environ: Mapping[str, str] | None = None,
    is_tty: bool | None = None,
) -> str:
    """none, 16, 256, or 24. Env and isatty only. No cursor query."""
    env = os.environ if environ is None else environ
    if is_tty is None:
        try:
            is_tty = bool(sys.stdout.isatty())
        except Exception:
            is_tty = False
    if not is_tty or env.get("NO_COLOR"):
        return "none"
    forced = {
        "DEPTH_1_BIT": "none",
        "DEPTH_4_BIT": "16",
        "DEPTH_8_BIT": "256",
        "DEPTH_24_BIT": "24",
    }
    chosen = forced.get(env.get("PROMPT_TOOLKIT_COLOR_DEPTH", ""))
    if chosen is not None:
        return chosen
    term = env.get("TERM", "")
    if term == "dumb":
        return "none"
    if term in {"linux", "eterm-color"}:
        return "16"
    colorterm = env.get("COLORTERM", "").casefold()
    if colorterm in {"truecolor", "24bit"}:
        return "24"
    return "256"


def arm(read_line):
    """Use the widgets only when stdin and stdout are both terminals."""
    global _depth
    use = False
    try:
        use = sys.stdin.isatty() and sys.stdout.isatty() and available()
    except Exception:
        use = False
    try:
        tty = sys.stdout.isatty()
    except Exception:
        tty = False
    _depth = color_depth(is_tty=tty)
    try:
        read_line.use_questionary = use
    except (AttributeError, TypeError):
        pass
    return read_line


def _hex(role: str) -> str:
    red, green, blue = _RGB[role]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _xterm256(rgb: tuple[int, int, int]) -> int:
    red, green, blue = rgb
    if abs(red - green) < 10 and abs(green - blue) < 10:
        index = int(round((red - 8) / 10))
        return 232 + max(0, min(23, index))

    def cube(value: int) -> int:
        return min(range(6), key=lambda index: abs(_CUBE[index] - value))

    return 16 + 36 * cube(red) + 6 * cube(green) + cube(blue)


def _fg(role: str, depth: str) -> str:
    if depth == "none":
        return ""
    if depth == "16":
        return f"\033[{_ANSI16[role]}m"
    red, green, blue = _RGB[role]
    if depth == "24":
        return f"\033[38;2;{red};{green};{blue}m"
    return f"\033[38;5;{_xterm256((red, green, blue))}m"


def widget_style():
    """prompt_toolkit style for the menus. Built for a dark background."""
    from prompt_toolkit.styles import Style

    ink = _hex("ink")
    cyan = _hex("cyan")
    return Style(
        [
            ("qmark", f"fg:{_hex('muted')}"),
            ("question", f"fg:{_hex('muted')}"),
            ("answer", f"fg:{_hex('body')}"),
            ("pointer", f"bg:{cyan} fg:{ink}"),
            ("highlighted", f"bg:{cyan} fg:{ink}"),
            ("selected", f"bg:{cyan} fg:{ink}"),
            ("instruction", f"fg:{_hex('body')}"),
            ("danger", f"fg:{_hex('danger')} bold"),
            ("text", f"fg:{cyan}"),
            ("disabled", f"fg:{cyan}"),
            ("separator", f"fg:{_hex('header')}"),
        ]
    )


def tone(text: str, role: str = "body", *, depth: str | None = None) -> str:
    """Wrap text for a dark terminal. The words themselves stay intact."""
    chosen = _depth if depth is None else depth
    name = role if role in _RGB else "body"
    prefix = _fg(name, chosen)
    if not prefix:
        return text
    return f"{prefix}{text}{_RESET}"


def rule() -> str:
    """Header color, between the explanation and the menu."""
    return tone("  ------------------------------------------", "header")


def choice_title(label: str):
    """EOL stays on the row and is drawn as a warning."""
    if label.endswith(" EOL"):
        return [
            ("class:text", label[: -len(" EOL")]),
            ("class:danger", " EOL"),
        ]
    return str(label)


def _app_kwargs() -> dict[str, Any]:
    from prompt_toolkit.output.color_depth import ColorDepth

    depth = {
        "none": ColorDepth.DEPTH_1_BIT,
        "16": ColorDepth.DEPTH_4_BIT,
        "256": ColorDepth.DEPTH_8_BIT,
        "24": ColorDepth.DEPTH_24_BIT,
    }.get(_depth, ColorDepth.DEPTH_8_BIT)
    return {"color_depth": depth}


def _run(question) -> Any:
    try:
        return question.unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        return None


def _bind_space_accept(question) -> None:
    """Space accepts the highlighted row. Checkbox keeps Space for marking."""
    application = getattr(question, "application", None)
    if application is None:
        return
    from questionary.prompts.common import InquirerControl

    @application.key_bindings.add(" ", eager=True)
    def _space(event):
        found = None
        for window in event.app.layout.find_all_windows():
            content = getattr(window, "content", None)
            if isinstance(content, InquirerControl):
                found = content
                break
        if found is None:
            return
        found.is_answered = True
        event.app.exit(result=found.get_pointed_at().value)


def _choice(index: int, item):
    import questionary

    label = item[0]
    value = item[1]
    danger = len(item) > 2 and bool(item[2])
    description = item[3] if len(item) > 3 else None
    if danger:
        title = [
            ("class:text", f"{index}) "),
            ("class:danger", str(label)),
        ]
    else:
        title = str(label)
    return questionary.Choice(
        title=title,
        value=value,
        shortcut_key=str(index),
        description=description,
    )


def select(message: str, choices, *, default=None, instruction: str | None = None, **kwargs):
    """choices are (label, value), plus optional danger and description.

    A number highlights that row. Space or Enter accepts the row the
    pointer is on. None means cancelled.

    default is accepted and ignored. questionary paints that value with
    class:selected for the whole menu, so the first row stays marked after
    the pointer moves. The first choice is the one Enter accepts.
    """
    del default
    import questionary

    built = [_choice(index, item) for index, item in enumerate(choices, start=1)]
    if instruction is None:
        instruction = _SELECT_KEYS
    question = questionary.select(
        message,
        choices=built,
        style=widget_style(),
        use_shortcuts=True,
        use_jk_keys=False,
        use_emacs_keys=False,
        instruction=instruction,
        **_app_kwargs(),
        **kwargs,
    )
    _bind_space_accept(question)
    return _run(question)


def checkbox(message: str, choices, *, instruction: str | None = None, validate=None, **kwargs):
    """choices are (label, value). Space marks. Enter accepts. None cancels."""
    import questionary

    built = [
        questionary.Choice(title=choice_title(str(label)), value=value) for label, value in choices
    ]
    if instruction is None:
        instruction = _CHECK_KEYS
    extra = {}
    if validate is not None:
        extra["validate"] = validate
    question = questionary.checkbox(
        message,
        choices=built,
        style=widget_style(),
        use_jk_keys=False,
        use_emacs_keys=False,
        instruction=instruction,
        **_app_kwargs(),
        **extra,
        **kwargs,
    )
    return _run(question)


def text(message: str, *, default: str = "", instruction: str | None = None, **kwargs):
    """Empty string is an answer. None means cancelled."""
    import questionary

    question = questionary.text(
        message,
        default=default,
        style=widget_style(),
        instruction=instruction,
        **_app_kwargs(),
        **kwargs,
    )
    return _run(question)


def confirm(message: str, *, default: bool = True, **kwargs) -> bool | None:
    """Enter accepts the default. n and y answer immediately. None cancels."""
    import questionary

    question = questionary.confirm(
        message,
        default=default,
        style=widget_style(),
        auto_enter=True,
        **_app_kwargs(),
        **kwargs,
    )
    result = _run(question)
    if result is None:
        return None
    return bool(result)
