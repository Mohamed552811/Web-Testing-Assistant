"""Phase 19: installer build script, installer settings, first-start setup check and the self-check."""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402

from PySide6.QtGui import QImage  # noqa: E402

from wta import __version__, claude_launcher, self_check  # noqa: E402
from wta.app import APP_USER_MODEL_ID, MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.setup_check import SetupDialog, setup_html  # noqa: E402
from wta.usage_bar import TopBar  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_installer", ROOT / "tools" / "build_installer.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

SIGNED_IN = claude_launcher.SignInStatus(True, "Signed in to Claude Code (pro)", "pro")
NOT_SIGNED_IN = claude_launcher.SignInStatus(False, "Claude Code is not installed (no `claude` on PATH).")
NODE_OK = claude_launcher.NodeStatus(True, "Node.js 22.11.0", "22.11.0")
NO_NODE = claude_launcher.NodeStatus(False, "Node.js is not installed (no `node`/`npx` on PATH).")


class BuildScriptTests(unittest.TestCase):
    def test_version_and_commands(self):
        self.assertEqual(build.app_version(), __version__)
        self.assertIn("--noconfirm", build.pyinstaller_command())
        self.assertEqual(build.pyinstaller_command()[3], str(build.SPEC))
        images = {"large": [Path("a.bmp"), Path("b.bmp")], "small": [Path("c.bmp")]}
        command = build.iscc_command(Path("ISCC.exe"), "1.2.3", images)
        self.assertIn("/DAppVersion=1.2.3", command)
        self.assertIn("/DWizardImage=a.bmp,b.bmp", command)
        self.assertEqual(command[-1], str(build.ISS))

    def test_find_iscc(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "Programs" / "Inno Setup 6" / "ISCC.exe"
            fake.parent.mkdir(parents=True)
            fake.write_text("")
            with mock.patch.object(build.shutil, "which", return_value=None):
                self.assertEqual(build.find_iscc({"LOCALAPPDATA": tmp}), fake)
                self.assertEqual(build.find_iscc({"ISCC": str(fake)}), fake)
                self.assertIsNone(build.find_iscc({"LOCALAPPDATA": str(Path(tmp) / "none"),
                                                   "ProgramFiles(x86)": tmp, "ProgramFiles": tmp}))

    def test_wizard_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = build.wizard_images(Path(tmp))
            sizes = {path.name: (QImage(str(path)).width(), QImage(str(path)).height())
                     for kind in images.values() for path in kind}
        self.assertEqual(sizes, {"wizard-large-100.bmp": (164, 314), "wizard-large-200.bmp": (328, 628),
                                 "wizard-small-100.bmp": (55, 58), "wizard-small-200.bmp": (110, 116)})

    def test_spec_and_installer_script(self):
        spec_text = (ROOT / "tools" / "wta.spec").read_text(encoding="utf-8")
        self.assertIn('collect_data_files("wta", subdir="assets")', spec_text)
        self.assertIn("console=False", spec_text)
        self.assertTrue((ROOT / "tools" / "wta_entry.py").is_file())
        iss = (ROOT / "tools" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn(f'#define AppUserModelID "{APP_USER_MODEL_ID}"', iss)  # the same taskbar identity as the app
        self.assertIn("PrivilegesRequired=lowest", iss)  # no administrator rights needed
        self.assertIn(r"DefaultDirName={autopf}\{#AppName}", iss)
        self.assertRegex(iss, r"AppId=\{\{[0-9A-F-]{36}\}")
        self.assertIn("Flags: unchecked", iss)  # the desktop shortcut is optional
        self.assertNotRegex(iss, r"(?im)^\[UninstallDelete\]")  # data in %APPDATA% is never removed
        requirements = (ROOT / "tools" / "build-requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^pyinstaller==\d+\.\d+\.\d+$")


class NodeCheckTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(claude_launcher.parse_node_version("v22.11.0\n"), (22, 11, 0))
        self.assertIsNone(claude_launcher.parse_node_version("not node"))

    def test_missing_old_and_ok(self):
        with mock.patch.object(claude_launcher.shutil, "which", return_value=None):
            self.assertIn("not installed", claude_launcher.check_node().message)

        def run(version):
            return subprocess.CompletedProcess(["node"], 0, stdout=version, stderr="")
        with mock.patch.object(claude_launcher.shutil, "which", return_value="C:/node/node.exe"):
            with mock.patch.object(claude_launcher.subprocess, "run", return_value=run("v16.20.2")):
                old = claude_launcher.check_node()
            with mock.patch.object(claude_launcher.subprocess, "run", return_value=run("v22.11.0")):
                ok = claude_launcher.check_node()
        self.assertFalse(old.ok)
        self.assertIn("too old", old.message)
        self.assertEqual((ok.ok, ok.version), (True, "22.11.0"))


class SetupDialogTests(unittest.TestCase):
    def test_html_explains_what_is_missing(self):
        html = setup_html(NOT_SIGNED_IN, NO_NODE)
        self.assertIn(claude_launcher.CLAUDE_INSTALL_POWERSHELL.replace("|", "|"), html)
        self.assertIn(claude_launcher.CLAUDE_SETUP_URL, html)
        self.assertIn(claude_launcher.NODE_DOWNLOAD_URL, html)
        ready = setup_html(SIGNED_IN, NODE_OK)
        self.assertNotIn(claude_launcher.NODE_DOWNLOAD_URL, ready)  # no fix shown when it is fine
        self.assertIn("checking…", setup_html(None, None))

    def test_top_bar_and_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            bar = TopBar(Path(tmp) / "usage.json")
            checked = []
            bar.setup_checked.connect(lambda c, n: checked.append((c.ok, n.ok)))
            bar._on_sign_in(SIGNED_IN, NO_NODE)
            self.assertEqual(bar.claude_label.text(), "Runs blocked: Node.js missing")
            self.assertFalse(bar.node_ok)
            self.assertEqual(checked, [(True, False)])
            bar.deleteLater()

            db = Database(Path(tmp) / "wta.sqlite3")
            window = MainWindow(db, app_dir=Path(tmp), check_sign_in=False)
            try:
                window.open_session_by_id(store.create_session(
                    db, store.SessionDetails(name="S", target_url="https://shop.test")).id)
                with mock.patch.object(SetupDialog, "show") as show:
                    window.top_bar._on_sign_in(SIGNED_IN, NO_NODE)
                    window.top_bar._on_sign_in(SIGNED_IN, NO_NODE)  # shown on its own only once per start
                self.assertEqual(show.call_count, 1)
                self.assertIn("Node.js 18 or later", window._run_blocker())
                again = []
                window.setup_dialog.check_again.connect(lambda: again.append(True))
                window.setup_dialog.again_button.click()
                self.assertEqual(again, [True])
                self.assertIn("checking…", window.setup_dialog.text.text())
                window.top_bar._on_sign_in(SIGNED_IN, NODE_OK)
                self.assertNotIn("checking…", window.setup_dialog.text.text())
            finally:
                window.deleteLater()
                app.processEvents()
                db.close()


class SelfCheckTests(unittest.TestCase):
    def run_check(self, **patches):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            with mock.patch.object(claude_launcher, "check_node", return_value=patches.get("node", NODE_OK)), \
                    mock.patch.object(claude_launcher, "check_sign_in", return_value=patches.get("claude", SIGNED_IN)):
                code = self_check.run(report, app)
            return code, json.loads(report.read_text(encoding="utf-8"))

    def test_everything_passes_from_source(self):
        code, report = self.run_check()
        self.assertEqual(code, 0)
        self.assertTrue(report["bundle_ok"])
        self.assertEqual(report["version"], __version__)
        self.assertEqual(set(report["checks"]), {"web_engine", "icons", "charts", "exports", "node", "claude"})
        self.assertEqual(report["checks"]["exports"]["detail"], "exported xlsx, docx, pdf, md")

    def test_machine_problems_are_reported_but_do_not_fail_the_bundle(self):
        code, report = self.run_check(node=NO_NODE, claude=NOT_SIGNED_IN)
        self.assertEqual(code, 0)
        self.assertFalse(report["checks"]["node"]["ok"])
        self.assertIn("not installed", report["checks"]["claude"]["detail"])

    def test_a_missing_bundle_part_fails(self):
        with mock.patch.object(self_check, "CHECKS", (("icons", lambda: (_ for _ in ()).throw(
                RuntimeError("bundled icons are missing"))),)):
            code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertFalse(report["bundle_ok"])
        self.assertEqual(report["checks"]["icons"]["detail"], "bundled icons are missing")


if __name__ == "__main__":
    unittest.main()
