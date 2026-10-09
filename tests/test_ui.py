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

from pve_prep.job import Job, DEFAULT_CACHE  # noqa: E402
from pve_prep.prompts import PromptAbort, interview  # noqa: E402
from pve_prep import ui  # noqa: E402


def _specs(distro: str):
    if distro != "debian":
        raise AssertionError(distro)
    return [
        SimpleNamespace(release="11", label="bullseye", eol=True),
        SimpleNamespace(release="12", label="bookworm", eol=False),
        SimpleNamespace(release="13", label="trixie", eol=False),
    ]


def _normalize(distro: str, raw: str) -> str:
    known = {"11": "11", "12": "12", "13": "13", "bookworm": "12"}
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
                [("ZFS raw", "raw"), ("QEMU qcow2", "qcow2")],
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

    def test_dark_background_keeps_yes_no_readable(self) -> None:
        self.assertTrue(ui.available())
        rules = dict(ui.widget_style().style_rules)
        self.assertIn("ansigreen", rules["separator"])
        self.assertEqual(rules["text"], "fg:ansicyan")
        self.assertEqual(rules["highlighted"], "bg:ansicyan fg:ansiblack")
        self.assertEqual(rules["selected"], rules["highlighted"])
        self.assertEqual(rules["pointer"], rules["highlighted"])
        self.assertIn("ansibrightblack", rules["question"])
        self.assertEqual(rules["instruction"], "fg:ansiwhite")
        self.assertEqual(rules["answer"], "fg:ansiwhite")
        self.assertNotIn("#", " ".join(rules.values()))
        self.assertIn("ansired", rules["danger"])
        self.assertTrue(ui.tone("explanation", "body").startswith("\033[37m"))
        self.assertIn("explanation", ui.tone("explanation", "body"))
        self.assertTrue(ui.tone("Releases.", "header").startswith("\033[32m"))
        self.assertIn("deleted", ui.tone("deleted", "danger"))
        title = ui.choice_title("11  bullseye EOL")
        self.assertEqual(title[1], ("class:danger", " EOL"))
        self.assertIn("bullseye", title[0][1])


class ArmTest(unittest.TestCase):
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

        def select(message, choices, default=None):
            if any(len(item) > 2 and item[2] for item in choices):
                self.assertTrue(choices[1][2])
            return selects.pop(0)

        def checkbox(message, choices, instruction=None, validate=None):
            self.assertIn("Space marks a release", instruction or "")
            return checks.pop(0)

        def text(message, default="", instruction=None):
            self.assertTrue(message)
            return texts.pop(0)

        confirms_out = list(confirms)
        seen_confirms: list[str] = []

        def confirm_capture(message, default=True):
            seen_confirms.append(message)
            return confirms_out.pop(0)

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
        return job, "".join(written), seen_confirms

    def test_checkbox_order_and_hardware_change(self) -> None:
        job, blob, confirms = self._run(
            selects=["debian", "template", "raw", "dir-templates"],
            checks=[["13", "12"]],
            texts=["", "vmbr1", "4096", "4", "YES"],
            confirms=[True, False, True, False],
            in_use=set(),
            disks=set(),
        )
        self.assertEqual(job.releases, ("12", "13"))
        self.assertEqual(job.vmids, (9001, 9002))
        self.assertEqual(job.bridge, "vmbr1")
        self.assertEqual(job.memory_mb, 4096)
        self.assertEqual(job.cores, 4)
        self.assertEqual(job.storage, "dir-templates")
        self.assertIn("Releases.", blob)
        self.assertNotIn("codename", blob)
        self.assertNotIn("  1) ", blob)
        self.assertIn("Complete PVE VM template", blob)
        self.assertIn("Create a VM, import the disk", blob)
        self.assertTrue(any(line.startswith("Guest prep") for line in confirms))
        self.assertTrue(any("vmbr0" in line for line in confirms))
        self.assertTrue(any("qm template" in line for line in confirms))
        self.assertTrue(any("pve-template-prep/cache" in line for line in confirms))
        self.assertFalse(any(line.endswith("[Y/n]") for line in confirms))
        self.assertTrue(job.make_template)
        self.assertFalse(job.clean_cache)

    def test_delete_x_still_aborts(self) -> None:
        with self.assertRaises(PromptAbort):
            self._run(
                selects=["debian", "template", "raw", "overwrite"],
                checks=[["12"]],
                texts=["910", "X"],
                confirms=[],
                in_use={910},
                disks={910},
            )

    def test_plain_reader_never_calls_a_widget(self) -> None:
        answers = ["1", "2, 3", "2", "1", "", "/tmp/images", "", "", "yes"]

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
            )
        self.assertEqual(job.mode, "image")
        self.assertEqual(job.dest_dir, "/tmp/images")
        self.assertEqual(job.vmids, ())
        self.assertIn("Type YES: ", "".join(written))
        self.assertIn("codename", "".join(written))
        self.assertIsInstance(job, Job)
        self.assertEqual(job.cache_dir, DEFAULT_CACHE)


if __name__ == "__main__":
    unittest.main()
