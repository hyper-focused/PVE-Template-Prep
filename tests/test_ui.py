"""Widget renderer. Plain interviews stay in test_prompts.py."""

from __future__ import annotations

import signal
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pve_prep.job import DEFAULT_BACKUPS, DEFAULT_CACHE, Job  # noqa: E402
from pve_prep.prompts import PromptAbort, _split_hint, interview  # noqa: E402
from pve_prep import ui  # noqa: E402


def _specs(distro: str):
    if distro != "debian":
        raise AssertionError(distro)
    return [
        SimpleNamespace(release="12", label="bookworm", eol=False),
        SimpleNamespace(release="13", label="trixie", eol=False),
    ]


def _normalize(distro: str, raw: str) -> str:
    known = {"12": "12", "13": "13", "bookworm": "12"}
    if distro != "debian" or raw not in known:
        raise RuntimeError(f"unknown release {raw}")
    return known[raw]


def _alarm(seconds: int) -> None:
    def _fire(signum, frame):
        raise TimeoutError("widget hung")

    signal.signal(signal.SIGALRM, _fire)
    signal.alarm(seconds)


class VendorTest(unittest.TestCase):
    def test_packages_import_without_a_c_extension(self) -> None:
        self.assertTrue(ui.available())
        import wcwidth

        self.assertEqual(wcwidth.__version__, "0.9.2")
        self.assertFalse(wcwidth.HAS_C_EXTENSION)
        self.assertFalse(list((ROOT / "vendor" / "wcwidth").glob("_wcwidth_c.*")))
        import prompt_toolkit
        import questionary

        self.assertEqual(prompt_toolkit.__version__, "3.0.52")
        self.assertEqual(questionary.__version__, "2.1.1")

    def test_headless_shortcut_then_enter(self) -> None:
        import questionary
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        _alarm(8)
        try:
            with create_pipe_input() as pipe:
                pipe.send_text("2\r")
                result = questionary.select(
                    "Build",
                    choices=["template", "image"],
                    default="template",
                    use_shortcuts=True,
                    input=pipe,
                    output=DummyOutput(),
                ).unsafe_ask()
        finally:
            signal.alarm(0)
        self.assertEqual(result, "image")

    def test_select_styles_the_danger_row_and_cancel_is_none(self) -> None:
        import questionary

        seen = {}

        class _Question:
            def unsafe_ask(self):
                return "overwrite"

            def __init__(self, choices):
                seen["titles"] = [choice.title for choice in choices]

        def fake_select(message, *, choices, **kwargs):
            self.assertEqual(message, "Backup")
            self.assertTrue(kwargs["use_shortcuts"])
            self.assertTrue(str(kwargs["instruction"]).startswith("\n"))
            seen["passed_default"] = "default" in kwargs
            return _Question(choices)

        with mock.patch.object(questionary, "select", fake_select):
            picked = ui.select(
                "Backup",
                [("keep", "backup"), ("drop", "overwrite", True)],
            )
        self.assertEqual(picked, "overwrite")
        self.assertEqual(seen["titles"][0], "keep")
        self.assertEqual(seen["titles"][1][1], ("class:danger", "drop"))
        self.assertFalse(seen["passed_default"])

    def test_select_does_not_paint_the_enter_default(self) -> None:
        import questionary

        seen = {}

        class _Question:
            def unsafe_ask(self):
                return "qcow2"

        def fake_select(message, *, choices, **kwargs):
            seen["message"] = message
            seen["kwargs"] = kwargs
            seen["values"] = [choice.value for choice in choices]
            return _Question()

        with mock.patch.object(questionary, "select", fake_select):
            picked = ui.select(
                "Format",
                [("raw (.img, full size)", "raw"), ("QEMU qcow2 (sparse)", "qcow2")],
                default="raw",
            )
        self.assertEqual(picked, "qcow2")
        self.assertEqual(seen["message"], "Format")
        self.assertEqual(seen["values"], ["raw", "qcow2"])
        self.assertNotIn("default", seen["kwargs"])

        class _Cancel:
            def unsafe_ask(self):
                raise KeyboardInterrupt

        with mock.patch.object(questionary, "select", lambda *args, **kwargs: _Cancel()):
            self.assertIsNone(ui.select("Distro", [("debian", "debian")]))

    def test_palette_roles_drive_the_widget_style(self) -> None:
        self.assertTrue(ui.available())
        palette = ui.load_palette()
        self.assertEqual(set(palette), {"dark", "light"})
        with mock.patch.object(ui, "_theme", "dark"), mock.patch.object(ui, "_depth", "256"):
            rules = dict(ui.widget_style().style_rules)
        self.assertEqual(rules["question"], "fg:#00d7ff bold")
        self.assertEqual(rules["qmark"], rules["question"])
        self.assertEqual(rules["text"], "fg:#d0d0d0")
        self.assertEqual(rules["pointer"], "fg:#00afff bold")
        self.assertEqual(rules["highlighted"], "fg:#00afff bold noreverse")
        self.assertEqual(rules["selected"], "fg:#00afaf noreverse")
        self.assertEqual(rules["separator"], "fg:#949494")
        self.assertEqual(rules["danger"], "fg:#ff0000 bold")
        self.assertNotIn("bg:", "".join(rules.values()))
        with mock.patch.object(ui, "_theme", "light"), mock.patch.object(ui, "_depth", "16"):
            rules = dict(ui.widget_style().style_rules)
        self.assertEqual(rules["question"], "fg:ansiblue bold")
        self.assertEqual(rules["text"], "fg:ansiblack")
        self.assertEqual(rules["danger"], "fg:ansired bold")

    def test_tone_follows_the_theme_and_depth(self) -> None:
        with mock.patch.object(ui, "_theme", "dark"):
            self.assertEqual(ui.tone("Releases", "header", depth="16"), "\033[1;36mReleases\033[0m")
            self.assertTrue(ui.tone("explanation", "body", depth="16").startswith("\033[37m"))
            self.assertTrue(ui.tone("Creating", "accent", depth="16").startswith("\033[96m"))
            self.assertTrue(ui.tone("status", "accent", depth="256").startswith("\033[38;5;51m"))
            self.assertIn("1;38;2;255;0;0", ui.tone("deleted", "danger", depth="24"))
            self.assertEqual(ui.tone("plain", "body", depth="none"), "plain")
            self.assertTrue(ui.tone("odd", "no-such-role", depth="16").startswith("\033[37m"))
        with mock.patch.object(ui, "_theme", "light"):
            self.assertTrue(ui.tone("Releases", "header", depth="256").startswith("\033[1;38;5;27m"))
            self.assertTrue(ui.tone("note", "note", depth="256").startswith("\033[38;5;242m"))
        self.assertEqual(ui.rule(color=False), "    " + "-" * 42)
        title = ui.choice_title("42  Fedora 42 EOL")
        self.assertEqual(title[1], ("class:danger", " EOL"))
        self.assertIn("Fedora 42", title[0][1])

    def test_xterm_index_maps_to_its_fixed_rgb(self) -> None:
        self.assertEqual(ui.xterm_rgb(16), (0, 0, 0))
        self.assertEqual(ui.xterm_rgb(45), (0, 215, 255))
        self.assertEqual(ui.xterm_rgb(196), (255, 0, 0))
        self.assertEqual(ui.xterm_rgb(231), (255, 255, 255))
        self.assertEqual(ui.xterm_rgb(232), (8, 8, 8))
        self.assertEqual(ui.xterm_rgb(255), (238, 238, 238))
        with self.assertRaises(ValueError):
            ui.xterm_rgb(15)

    def test_bad_palette_is_refused_and_turns_color_off(self) -> None:
        import json
        import tempfile

        good = ui.load_palette()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "palette.json"
            broken = json.loads(json.dumps(good))
            del broken["light"]["danger"]
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "light.danger is missing"):
                ui.load_palette(path)
            broken = json.loads(json.dumps(good))
            broken["dark"]["note"]["xterm"] = 7
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dark.note.xterm"):
                ui.load_palette(path)
            broken = json.loads(json.dumps(good))
            broken["dark"]["note"]["ansi"] = "38"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dark.note.ansi"):
                ui.load_palette(path)
            path.write_text("{", encoding="utf-8")
            with (
                mock.patch.object(ui, "_PALETTE_FILE", path),
                mock.patch.object(ui, "_palette", None),
                mock.patch.object(sys, "stderr") as err,
            ):
                self.assertEqual(ui.tone("plain", "header", depth="256"), "plain")
                self.assertIn("Colors are off", "".join(call.args[0] for call in err.write.call_args_list))

    def test_color_profile_follows_the_terminal(self) -> None:
        self.assertEqual(ui.color_depth(environ={"TERM": "xterm-256color"}, is_tty=False), "none")
        self.assertEqual(
            ui.color_depth(environ={"TERM": "xterm-256color", "NO_COLOR": "1"}, is_tty=True),
            "none",
        )
        self.assertEqual(ui.color_depth(environ={"TERM": "dumb"}, is_tty=True), "none")
        self.assertEqual(ui.color_depth(environ={"TERM": "linux"}, is_tty=True), "16")
        self.assertEqual(
            ui.color_depth(
                environ={"TERM": "linux", "COLORTERM": "truecolor"},
                is_tty=True,
            ),
            "16",
        )
        self.assertEqual(
            ui.color_depth(environ={"TERM": "xterm-256color", "COLORTERM": "truecolor"}, is_tty=True),
            "24",
        )
        self.assertEqual(ui.color_depth(environ={"TERM": "xterm-256color"}, is_tty=True), "256")
        self.assertEqual(
            ui.color_depth(
                environ={
                    "TERM": "dumb",
                    "PROMPT_TOOLKIT_COLOR_DEPTH": "DEPTH_24_BIT",
                },
                is_tty=True,
            ),
            "24",
        )

    def test_hint_splits_off_the_last_line(self) -> None:
        self.assertEqual(
            _split_hint("Keep this hardware? (y/n) [Yes]:"),
            ("Keep this hardware?", "(y/n) [Yes]"),
        )
        self.assertEqual(
            _split_hint("Enter bookworm template VMID [9001]:"),
            ("Enter bookworm template VMID", "[9001]"),
        )
        self.assertEqual(
            _split_hint("A VMID is required.\nEnter bookworm template VMID [9001]:"),
            ("A VMID is required.\nEnter bookworm template VMID", "[9001]"),
        )
        self.assertEqual(
            _split_hint("Enter bookworm template VMID:"), ("Enter bookworm template VMID:", None)
        )
        self.assertEqual(_split_hint("Bridge:"), ("Bridge:", None))
        gate = "Type 'DELETE' to continue or 'X' to exit now without changes:"
        self.assertEqual(_split_hint(gate), (gate, None))

    def test_text_prompt_paints_question_hint_and_delete(self) -> None:
        tokens = ui.prompt_tokens("Keep this hardware?", hint="(y/n) [Yes]")
        self.assertEqual(tokens[0], ("class:qmark", " "))
        self.assertIn(("class:question", "Keep this hardware?"), tokens)
        self.assertEqual(tokens[-1], ("class:instruction", " (y/n) [Yes] "))
        self.assertFalse([token for token in tokens if token[0] == "class:danger"])

        gate = "Type 'DELETE' to continue or 'X' to exit now without changes:"
        tokens = ui.prompt_tokens(gate, danger_word="DELETE")
        self.assertIn(("class:danger", "DELETE"), tokens)
        self.assertIn(("class:question", "Type '"), tokens)
        self.assertEqual("".join(text for _style, text in tokens).strip(), gate)
        self.assertFalse([token for token in tokens if token[0] == "class:instruction"])

        tokens = ui.prompt_tokens("Enter bookworm template VMID", hint="[9001]")
        self.assertEqual(tokens[-1], ("class:instruction", " [9001] "))

        rules = dict(ui.widget_style(instruction="highlight").style_rules)
        self.assertEqual(rules["instruction"], rules["pointer"])
        self.assertNotEqual(rules["instruction"], rules["question"])
        self.assertNotEqual(rules["answer"], rules["instruction"])
        self.assertEqual(dict(ui.widget_style().style_rules)["instruction"], ui._style("note"))

    def test_text_field_takes_delete_and_the_bracket_default(self) -> None:
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.lexers import SimpleLexer
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.shortcuts import PromptSession

        seen = {}
        real_init = PromptSession.__init__

        def spy_init(session, *args, **kwargs):
            seen["lexer"] = kwargs.get("lexer")
            real_init(session, *args, **kwargs)

        _alarm(8)
        try:
            with mock.patch.object(PromptSession, "__init__", spy_init):
                with create_pipe_input() as pipe:
                    pipe.send_text("DELETE\r")
                    typed = ui.text(
                        "Type 'DELETE' to continue or 'X' to exit now without changes:",
                        danger_word="DELETE",
                        input=pipe,
                        output=DummyOutput(),
                    )
                self.assertEqual(typed, "DELETE")
                self.assertIsInstance(seen["lexer"], SimpleLexer)
                self.assertEqual(seen["lexer"].style, "class:danger")
                with create_pipe_input() as pipe:
                    pipe.send_text("\r")
                    typed = ui.text(
                        "Keep this hardware?", hint="(y/n) [Yes]", input=pipe, output=DummyOutput()
                    )
                self.assertEqual(typed, "")
                self.assertEqual(seen["lexer"].style, "class:answer")
        finally:
            signal.alarm(0)

    def test_space_accepts_the_highlighted_row(self) -> None:
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        _alarm(8)
        try:
            with create_pipe_input() as pipe:
                pipe.send_text(" ")
                picked = ui.select(
                    "Build",
                    [("template", "template"), ("image", "image")],
                    input=pipe,
                    output=DummyOutput(),
                )
        finally:
            signal.alarm(0)
        self.assertEqual(picked, "template")


class ArmTest(unittest.TestCase):
    def setUp(self) -> None:
        # arm() sets the module's depth and theme. Never query the real terminal from a test.
        patches = (
            mock.patch.object(ui, "_depth", ui._depth),
            mock.patch.object(ui, "_theme", ui._theme),
            mock.patch.object(ui.termbg, "query_background", return_value=None),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_flag_follows_the_tty(self) -> None:
        def read_line() -> str:
            return ""

        with (
            mock.patch.object(sys.stdin, "isatty", return_value=False),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
        ):
            ui.arm(read_line)
        self.assertFalse(getattr(read_line, "use_questionary", True))

        def again() -> str:
            return ""

        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
        ):
            ui.arm(again)
        self.assertTrue(again.use_questionary)

    def test_background_probe_needs_both_ends_on_a_terminal(self) -> None:
        seen: list[bool] = []

        def detect(*, probe: bool) -> str:
            seen.append(probe)
            return "light"

        def read_line() -> str:
            return ""

        with (
            mock.patch.object(ui, "_theme", "dark"),
            mock.patch.object(ui, "color_depth", return_value="256"),
            mock.patch.object(ui.termbg, "detect", detect),
        ):
            with (
                mock.patch.object(sys.stdin, "isatty", return_value=False),
                mock.patch.object(sys.stdout, "isatty", return_value=True),
            ):
                ui.arm(read_line)
            with (
                mock.patch.object(sys.stdin, "isatty", return_value=True),
                mock.patch.object(sys.stdout, "isatty", return_value=True),
            ):
                ui.arm(read_line)
            self.assertEqual(ui._theme, "light")
        self.assertEqual(seen, [False, True])

    def test_no_color_skips_the_probe(self) -> None:
        def read_line() -> str:
            return ""

        with (
            mock.patch.object(ui, "_depth", "16"),
            mock.patch.object(ui, "color_depth", return_value="none"),
            mock.patch.object(ui.termbg, "detect", side_effect=AssertionError("probed")),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
        ):
            ui.arm(read_line)

    def test_missing_library_disables_the_flag(self) -> None:
        def read_line() -> str:
            return ""

        read_line.use_questionary = True
        with mock.patch.object(ui, "available", return_value=False):
            self.assertFalse(ui.enabled(read_line))


class WidgetInterviewTest(unittest.TestCase):
    def _run(self, *, selects, checks, texts, confirms, in_use=None, disks=None):
        written: list[str] = []

        def read_line() -> str:
            raise AssertionError("plain prompt used")

        read_line.use_questionary = True

        def select(message, choices, default=None, instruction=None):
            if any(len(item) > 2 and item[2] for item in choices):
                self.assertTrue(choices[1][2])
            self.assertTrue(message.endswith("press enter"), message)
            self.assertEqual(instruction, ui.MENU_GAP)
            return selects.pop(0)

        def checkbox(message, choices, instruction=None, validate=None):
            self.assertTrue(message.endswith("press enter"), message)
            self.assertEqual(instruction, ui.MENU_GAP)
            return checks.pop(0)

        seen_texts: list[str] = []

        def text(message, default="", hint=None, danger_word=None):
            self.assertTrue(message)
            self.assertNotIn("[", message)
            seen_texts.append(f"{message} {hint}:" if hint else message)
            if hint:
                self.assertEqual(default, "")
            if danger_word:
                self.assertEqual(danger_word, "DELETE")
                self.assertIn("'DELETE'", message)
            return texts.pop(0)

        def confirm_capture(message, default=True):
            raise AssertionError(message)

        def list_storages():
            return ["dir-templates"]

        def vmids_in_use(vmids):
            if in_use is None:
                return None
            return {vmid for vmid in vmids if vmid in in_use}

        def vm_has_disks(vmid):
            if disks is None:
                return None
            return vmid in disks

        with (
            mock.patch.object(ui, "select", select),
            mock.patch.object(ui, "checkbox", checkbox),
            mock.patch.object(ui, "text", text),
            mock.patch.object(ui, "confirm", confirm_capture),
        ):
            job = interview(
                read_line,
                written.append,
                dry_run=True,
                list_storages=list_storages,
                releases_for=_specs,
                normalize_release=_normalize,
                vmids_in_use=vmids_in_use,
                vm_has_disks=vm_has_disks,
            )
        return job, "".join(written), seen_texts

    def test_checkbox_order_and_hardware_change(self) -> None:
        job, blob, asked = self._run(
            selects=["template", "debian", "dir-templates", "qcow2"],
            checks=[["13", "12"]],
            texts=["", "", "n", "", "n", "vmbr1", "4096", "4", "", "YES"],
            confirms=[],
            in_use=set(),
            disks=set(),
        )
        self.assertEqual(job.releases, ("12", "13"))
        self.assertEqual(job.vmids, (9001, 9002))
        self.assertEqual(job.bridge, "vmbr1")
        self.assertEqual(job.memory_mb, 4096)
        self.assertEqual(job.cores, 4)
        self.assertEqual(job.storage, "dir-templates")
        self.assertEqual(job.disk_format, "qcow2")
        self.assertIn("Releases", blob)
        self.assertIn("debian 12, 13 selected.", blob)
        self.assertNotIn("codename", blob)
        self.assertNotIn("  1) ", blob)
        self.assertIn("Space marks", ui._CHECK_KEYS)
        self.assertIn("Space or Enter", ui._SELECT_KEYS)
        self.assertNotIn("Guest prep is applied", blob)
        self.assertTrue(job.prep)
        self.assertFalse(any("00-enableroot.conf" in line for line in asked))
        self.assertTrue(any("Enable SSH login for 'root' with a password? (y/n) [Yes]:" in line for line in asked))
        self.assertTrue(job.permit_root)
        self.assertTrue(any("Keep this hardware? (y/n) [Yes]:" in line for line in asked))
        self.assertTrue(any("qm template? (y/n) [Yes]:" in line for line in asked))
        self.assertFalse(any("pve-template-prep/cache" in line for line in asked))
        self.assertTrue(any("Accept these defaults? (y/n) [Yes]:" in line for line in asked))
        self.assertFalse(any(line.endswith("[Y/n]") for line in asked))
        self.assertTrue(job.make_template)
        self.assertFalse(job.clean_cache)

    def test_delete_x_still_aborts(self) -> None:
        with self.assertRaises(PromptAbort):
            self._run(
                selects=["template", "debian", "dir-templates", "qcow2"],
                checks=[["12"]],
                texts=["910", "", "n", "X"],
                confirms=[],
                in_use={910},
                disks={910},
            )

    def test_plain_reader_never_calls_a_widget(self) -> None:
        answers = ["3", "1", "1, 2", "1", "", "", "yes"]

        def read_line() -> str:
            if not answers:
                raise EOFError
            return answers.pop(0)

        def boom(*args, **kwargs):
            raise AssertionError("widget")

        written: list[str] = []
        with (
            mock.patch.object(ui, "select", boom),
            mock.patch.object(ui, "checkbox", boom),
            mock.patch.object(ui, "text", boom),
            mock.patch.object(ui, "confirm", boom),
        ):
            job = interview(
                read_line,
                written.append,
                dry_run=False,
                list_storages=lambda: ["unused"],
                releases_for=_specs,
                normalize_release=_normalize,
                vmids_in_use=lambda vmids: set(),
                vm_has_disks=lambda vmid: False,
                import_storages=lambda: [("local", "/var/lib/vz/import")],
            )
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.disk_format, "qcow2")
        self.assertEqual(job.dest_dir, "/var/lib/vz/import")
        self.assertEqual(job.vmids, ())
        self.assertTrue(job.permit_root)
        self.assertIn("Type 'YES' to continue or 'X' to exit now: ", "".join(written))
        self.assertIn("codename", "".join(written))
        self.assertIsInstance(job, Job)
        self.assertEqual(job.cache_dir, DEFAULT_CACHE)
        self.assertEqual(job.backup_dir, DEFAULT_BACKUPS)


if __name__ == "__main__":
    unittest.main()
