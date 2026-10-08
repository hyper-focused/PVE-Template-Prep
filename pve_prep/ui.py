"""Terminal widgets for the interview.

prompts.py still owns the questions, the free-VMID rule, and the DELETE
gate. This module only draws them. No TTY, or a vendor tree that will
not import, and the caller keeps the numbered prompts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
_ready: bool | None = None


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


def arm(read_line):
    """Use the widgets only when stdin and stdout are both terminals."""
    use = False
    try:
        use = sys.stdin.isatty() and sys.stdout.isatty() and available()
    except Exception:
        use = False
    try:
        read_line.use_questionary = use
    except (AttributeError, TypeError):
        pass
    return read_line


def _style():
    from prompt_toolkit.styles import Style

    return Style(
        [
            ("qmark", "fg:ansicyan bold"),
            ("question", "bold"),
            ("answer", "fg:ansicyan"),
            ("pointer", "fg:ansicyan bold"),
            ("highlighted", "fg:ansicyan bold"),
            ("selected", "fg:ansigreen"),
            ("instruction", "fg:ansibrightblack"),
            ("danger", "fg:ansired bold"),
            ("text", ""),
            ("disabled", "fg:ansibrightblack italic"),
        ]
    )


def _run(question) -> Any:
    try:
        return question.unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        return None


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


def select(message: str, choices, *, default=None, instruction: str | None = None):
    """choices are (label, value), plus optional danger and description.

    A number highlights that row. Enter accepts the row the pointer is on.
    None means cancelled.

    default is accepted and ignored. questionary paints that value with
    class:selected for the whole menu, so the first row stays marked after
    the pointer moves. The first choice is the one Enter accepts.
    """
    del default
    import questionary

    built = [_choice(index, item) for index, item in enumerate(choices, start=1)]
    if instruction is None:
        instruction = "\n  Enter accepts."
    question = questionary.select(
        message,
        choices=built,
        style=_style(),
        use_shortcuts=True,
        instruction=instruction,
    )
    return _run(question)


def checkbox(message: str, choices, *, instruction: str | None = None, validate=None):
    """choices are (label, value). None means cancelled."""
    import questionary

    built = [questionary.Choice(title=str(label), value=value) for label, value in choices]
    kwargs = {}
    if validate is not None:
        kwargs["validate"] = validate
    question = questionary.checkbox(
        message,
        choices=built,
        style=_style(),
        instruction=instruction,
        **kwargs,
    )
    return _run(question)


def text(message: str, *, default: str = "", instruction: str | None = None):
    """Empty string is an answer. None means cancelled."""
    import questionary

    question = questionary.text(
        message,
        default=default,
        style=_style(),
        instruction=instruction,
    )
    return _run(question)


def confirm(message: str, *, default: bool = True) -> bool | None:
    """Enter accepts the default. n and y answer immediately. None cancels."""
    import questionary

    question = questionary.confirm(
        message,
        default=default,
        style=_style(),
        auto_enter=True,
    )
    result = _run(question)
    if result is None:
        return None
    return bool(result)
