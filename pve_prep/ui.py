# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Terminal widgets for the interview.

prompts.py still owns the questions, the free-VMID rule, and the DELETE
gate. This module only draws them. No TTY, or a vendor tree that will
not import, and the caller keeps the numbered prompts.

Colors come from palette.json: one entry per theme and role, as an xterm
256 index plus an ANSI 16 fallback. termbg picks the theme. The terminal
profile picks 16, 256, or 24-bit. A pipe, NO_COLOR, or TERM=dumb stays
plain.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from pve_prep import termbg

_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
_PALETTE_FILE = Path(__file__).resolve().parent / "palette.json"
_ready: bool | None = None
# Tests call tone() before arm(). A real run sets these from the terminal.
_depth = "16"
_theme = "dark"
_palette: dict[str, dict[str, dict[str, Any]]] | None = None

MARGIN = "  "
_RULE = "-" * 42
_SELECT_KEYS = "\n  ↑↓ move · Space or Enter selects"
_CHECK_KEYS = "\n  ↑↓ move · Space marks · Enter accepts"
# A newline after the question leaves a blank line above the rows.
MENU_GAP = "\n"

ROLES = ("header", "body", "option", "highlight", "success", "note", "warning", "danger", "accent")
_ANSI_NAMES = {
    "30": "ansiblack",
    "31": "ansired",
    "32": "ansigreen",
    "33": "ansiyellow",
    "34": "ansiblue",
    "35": "ansimagenta",
    "36": "ansicyan",
    "37": "ansigray",
    "90": "ansibrightblack",
    "91": "ansibrightred",
    "92": "ansibrightgreen",
    "93": "ansibrightyellow",
    "94": "ansibrightblue",
    "95": "ansibrightmagenta",
    "96": "ansibrightcyan",
    "97": "ansiwhite",
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
    """Use the widgets only when stdin and stdout are both terminals.

    Also sets the color profile and the theme. The background query runs
    only when both ends are terminals; otherwise only the environment is read.
    """
    global _depth, _theme
    try:
        both = sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        both = False
    use = both and available()
    try:
        tty = sys.stdout.isatty()
    except Exception:
        tty = False
    _depth = color_depth(is_tty=tty)
    if _depth != "none":
        _theme = termbg.detect(probe=both)
    try:
        read_line.use_questionary = use
    except (AttributeError, TypeError):
        pass
    return read_line


@contextmanager
def quiet_echo(read_line):
    """Terminal echo off while the widgets run the interview.

    A host lookup between two fields can take a second. Keys typed in that
    gap would be echoed at column 0 by the terminal and then drawn again in
    the next field. With echo off they are drawn once, by the field. The
    plain path keeps echo, because input() relies on it.
    """
    try:
        import termios
    except ImportError:
        termios = None
    saved = None
    fd = -1
    if termios is not None and enabled(read_line):
        try:
            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            quiet = termios.tcgetattr(fd)
            quiet[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, quiet)
        except (OSError, ValueError, termios.error):
            saved = None
    try:
        yield
    finally:
        if saved is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, saved)
            except termios.error:
                pass


def load_palette(path: Path = _PALETTE_FILE) -> dict[str, dict[str, dict[str, Any]]]:
    """Every theme needs every role. ValueError names the first bad entry."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("palette is not an object")
    for theme in termbg.THEMES:
        roles = payload.get(theme)
        if not isinstance(roles, dict):
            raise ValueError(f"palette has no {theme} theme")
        for role in ROLES:
            spec = roles.get(role)
            if not isinstance(spec, dict):
                raise ValueError(f"{theme}.{role} is missing")
            index = spec.get("xterm")
            if not isinstance(index, int) or isinstance(index, bool) or not 16 <= index <= 255:
                raise ValueError(f"{theme}.{role}.xterm must be 16 to 255")
            if spec.get("ansi") not in _ANSI_NAMES:
                raise ValueError(f"{theme}.{role}.ansi must be 30-37 or 90-97")
            if not isinstance(spec.get("bold", False), bool):
                raise ValueError(f"{theme}.{role}.bold must be true or false")
    return payload


def _roles() -> dict[str, dict[str, Any]]:
    """The current theme. A broken palette file turns color off and says so once."""
    global _palette
    if _palette is None:
        try:
            _palette = load_palette(_PALETTE_FILE)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"{_PALETTE_FILE.name}: {exc}. Colors are off.\n")
            _palette = {}
    return _palette.get(_theme, {})


def xterm_rgb(index: int) -> tuple[int, int, int]:
    """The xterm RGB for a 256-color index from 16 to 255."""
    if 16 <= index <= 231:
        offset = index - 16
        return _CUBE[offset // 36], _CUBE[(offset // 6) % 6], _CUBE[offset % 6]
    if 232 <= index <= 255:
        level = 8 + 10 * (index - 232)
        return level, level, level
    raise ValueError(f"no fixed RGB for xterm color {index}")


def _spec(role: str) -> dict[str, Any] | None:
    roles = _roles()
    return roles.get(role) or roles.get("body")


def _fg(role: str, depth: str) -> str:
    spec = _spec(role)
    if depth == "none" or spec is None:
        return ""
    bold = "1;" if spec.get("bold") else ""
    if depth == "16":
        color = spec["ansi"]
    elif depth == "24":
        red, green, blue = xterm_rgb(spec["xterm"])
        color = f"38;2;{red};{green};{blue}"
    else:
        color = f"38;5;{spec['xterm']}"
    return f"\033[{bold}{color}m"


def _style(role: str) -> str:
    """prompt_toolkit style for one role. 16 colors use ANSI names, not the nearest hex."""
    spec = _spec(role)
    if spec is None:
        return ""
    if _depth == "16":
        color = _ANSI_NAMES[spec["ansi"]]
    else:
        red, green, blue = xterm_rgb(spec["xterm"])
        color = f"#{red:02x}{green:02x}{blue:02x}"
    bold = " bold" if spec.get("bold") else ""
    return f"fg:{color}{bold}"


def widget_style(*, instruction: str = "note"):
    """prompt_toolkit style for the menus, in the current theme.

    instruction is the role for the hint after the question: key help on a
    menu stays note grey; a text field's (y/n) [Yes] or [9001] is highlight.
    """
    from prompt_toolkit.styles import Style

    return Style(
        [
            ("qmark", _style("header")),
            ("question", _style("header")),
            ("answer", _style("success") + " nobold"),
            ("pointer", _style("highlight")),
            # prompt_toolkit's own style reverses selected rows. The palette colors them instead.
            ("highlighted", _style("highlight") + " noreverse"),
            ("selected", _style("success") + " noreverse"),
            ("instruction", _style(instruction)),
            ("danger", _style("danger")),
            ("success", _style("success")),
            ("text", _style("option")),
            ("disabled", _style("note")),
            ("separator", _style("note")),
        ]
    )


def tone(text: str, role: str = "body", *, depth: str | None = None) -> str:
    """Wrap text in the role color. The words themselves stay intact."""
    chosen = _depth if depth is None else depth
    prefix = _fg(role, chosen)
    if not prefix:
        return text
    return f"{prefix}{text}{_RESET}"


def heading(text: str) -> str:
    """Section title, at the margin."""
    return tone(MARGIN + text, "header")


def rule(*, color: bool = True) -> str:
    """Under a heading, indented past it. Note grey, so it recedes."""
    line = MARGIN * 2 + _RULE
    return tone(line, "note") if color else line


def indent(text: str) -> str:
    """Every line after the first, at the margin. The widget puts the first there."""
    return text.replace("\n", "\n" + MARGIN)


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
        indent(message),
        choices=built,
        style=widget_style(),
        qmark=MARGIN + "?",
        pointer=MARGIN + "»",
        erase_when_done=True,
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
        indent(message),
        choices=built,
        style=widget_style(),
        qmark=MARGIN + "?",
        pointer=MARGIN + "»",
        erase_when_done=True,
        use_jk_keys=False,
        use_emacs_keys=False,
        instruction=instruction,
        **_app_kwargs(),
        **extra,
        **kwargs,
    )
    return _run(question)


def prompt_tokens(
    message: str,
    *,
    hint: str | None = None,
    danger_word: str | None = None,
    success_word: str | None = None,
):
    """The text prompt line, laid out the way questionary lays out qmark, question, instruction.

    hint is the (y/n) [Yes] or [9001] part, drawn as highlight. danger_word
    is drawn red wherever it appears in the question. success_word is drawn
    in the success color. The quotes around either word stay with the question.
    """
    tokens = [("class:qmark", " "), ("class:question", " ")]
    body = indent(message)
    marked = ""
    mark_class = ""
    if danger_word and danger_word in body:
        marked, mark_class = danger_word, "class:danger"
    elif success_word and success_word in body:
        marked, mark_class = success_word, "class:success"
    if marked:
        parts = body.split(marked)
        for index, part in enumerate(parts):
            if index:
                tokens.append((mark_class, marked))
            if part:
                tokens.append(("class:question", part))
    else:
        tokens.append(("class:question", body))
    if hint:
        tokens.append(("class:question", " "))
        tokens.append(("class:instruction", hint))
        tokens.append(("class:question", ": "))
    else:
        tokens.append(("class:question", " "))
    return tokens


def _input_lexer(*, danger_word: str | None, yes_green: bool):
    """How typed characters are colored.

    A delete gate is red. A yes/no field paints y or yes in the success
    color and leaves n in the plain text color. Every other field paints
    the whole value in that same success color.
    """
    _ensure_vendor()
    from prompt_toolkit.document import Document
    from prompt_toolkit.lexers import Lexer, SimpleLexer

    if danger_word:
        return SimpleLexer("class:danger")
    if not yes_green:
        return SimpleLexer("class:success")

    class _YesLexer(Lexer):
        def lex_document(self, document: Document):
            def get_line(lineno: int):
                line = document.lines[lineno]
                if line.lower() in {"y", "ye", "yes"}:
                    return [("class:success", line)]
                if line:
                    return [("class:text", line)]
                return []

            return get_line

    return _YesLexer()


def text(
    message: str,
    *,
    default: str = "",
    hint: str | None = None,
    danger_word: str | None = None,
    success_word: str | None = None,
    yes_green: bool = False,
    **kwargs,
):
    """Empty string is an answer. None means cancelled.

    A prompt_toolkit session, not questionary.text, because the question
    is formatted text: danger_word is red in the question and in the field.
    success_word is the success color in the question. On a yes/no field,
    a typed y or yes is that same color.
    """
    from prompt_toolkit.shortcuts import PromptSession
    from prompt_toolkit.styles import merge_styles
    from questionary.constants import DEFAULT_STYLE

    session = PromptSession(
        prompt_tokens(message, hint=hint, danger_word=danger_word, success_word=success_word),
        style=merge_styles([DEFAULT_STYLE, widget_style(instruction="highlight")]),
        lexer=_input_lexer(danger_word=danger_word, yes_green=yes_green),
        **_app_kwargs(),
        **kwargs,
    )
    try:
        return session.prompt(default=default)
    except (KeyboardInterrupt, EOFError):
        return None


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
