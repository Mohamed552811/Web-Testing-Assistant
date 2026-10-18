"""Phase 18: the failing element is marked with a box in the app's own screenshot."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import highlight  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import Screenshot, Step, StepStatus  # noqa: E402
from wta.page_monitor import PageMonitor  # noqa: E402
from wta.prompt import RunContext, build_prompt  # noqa: E402
from wta.results import HIGHLIGHT_SUFFIX, read_results, apply_results  # noqa: E402
from wta.review_widgets import ScreenshotCard  # noqa: E402
from wta.runner import ScenarioJob, ScenarioRun  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402
PAGE = """<html><body style="margin:0;font:16px sans-serif">
<h1 style="margin:20px">Profile</h1>
<input aria-label="Display name" value="Sam Tester" style="margin:20px;width:300px">
<p id="msg" style="margin:20px">Profile saved. Display name: Sam Tester</p></body></html>"""


def wait(condition, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not condition():
        app.processEvents()
        time.sleep(0.01)
    return condition()


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.view = QWebEngineView()
        self.view.resize(800, 600)
        self.view.show()
        loaded = []
        self.view.loadFinished.connect(loaded.append)
        self.view.setHtml(PAGE, QUrl("https://shop.test/profile"))
        self.assertTrue(wait(lambda: loaded))

    def tearDown(self):
        self.view.deleteLater()
        app.processEvents()
        self.tmp.cleanup()

    def find(self, text):
        result = []
        self.view.page().runJavaScript(highlight.find_js(text), QWebEngineScript.ScriptWorldId.ApplicationWorld,
                                       result.append)
        self.assertTrue(wait(lambda: result))
        return highlight.parse_box(result[0])

    def test_find_by_text_label_and_part(self):
        message = self.find("Profile saved. Display name: Sam Tester")
        self.assertEqual(message["viewport_width"], 800)
        self.assertGreater(message["y"], 60)
        field = self.find("display name")  # the field's label: exact match beats the message containing it
        self.assertLess(field["y"], message["y"])
        part = self.find("saved. display")  # only part of the message
        self.assertEqual(part["y"], message["y"])
        self.assertIsNone(self.find("Nothing like this"))
        self.assertIsNone(self.find(""))
        self.assertIn('"it\\"s"', highlight.find_js('it"s'))  # text goes in as a JSON string

    def test_highlighter_saves_a_marked_copy(self):
        done = []
        target = highlight.highlight_path(Path(self.tmp.name), 3)
        highlight.Highlighter(self.view, None).highlight("Profile saved. Display name: Sam Tester", target,
                                                         done.append)
        self.assertTrue(wait(lambda: done))
        self.assertEqual(done[0], target)
        self.assertTrue(target.name.endswith(HIGHLIGHT_SUFFIX))
        self.assertFalse(QImage(str(target)).isNull())
        missing = []
        highlight.Highlighter(self.view, None).highlight("Not there", target, missing.append)
        self.assertTrue(wait(lambda: missing))
        self.assertIsNone(missing[0])


class DrawTests(unittest.TestCase):
    def test_box_is_drawn_scaled_to_the_image(self):
        image = QImage(1600, 1200, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        marked = highlight.draw_box(image, {"x": 100, "y": 100, "width": 200, "height": 50,
                                            "viewport_width": 800, "dpr": 2})
        # 2x image: the box's top edge runs at about y = (100 - 4) * 2
        self.assertEqual(marked.pixelColor(400, 192).name(), highlight.BOX_COLOR.lower())
        self.assertEqual(marked.pixelColor(400, 260).name(), "#ffffff")  # inside stays untouched
        self.assertEqual(image.pixelColor(400, 192).name(), "#ffffff")  # the original is not changed


class StoreAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://shop.test"))
        self.scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Profile"))
        self.run_dir = self.base / "run"
        (self.run_dir / "screenshots").mkdir(parents=True)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def write_results(self, screenshot=None):
        (self.run_dir / "results.json").write_text(json.dumps({"scenario": "Profile", "state": "running", "steps": [
            {"order": 1, "action": "Save", "status": "fail", "highlight": "Profile saved. Display name: Sam Tester",
             "screenshot": screenshot, "bug": {"title": "Old name shown", "severity": "2 - High", "priority": 2}},
        ]}), "utf-8")

    def test_runner_asks_once_per_step(self):
        job = ScenarioJob(self.scenario.id, "Profile", RunContext("S", "https://shop.test", [], "", "", "Profile",
                                                                  "", ""), self.run_dir)
        run = ScenarioRun(self.db, job, 1, self.base / "usage.json")
        asked = []
        run.highlight_requested.connect(lambda *args: asked.append(args))
        self.write_results()
        run._check_results()
        (self.run_dir / "results.json").touch()
        os.utime(self.run_dir / "results.json", (time.time() + 5, time.time() + 5))
        run._check_results()  # results written again: not asked twice
        self.assertEqual(asked, [(self.scenario.id, 1, "Profile saved. Display name: Sam Tester", self.run_dir)])

    def test_marked_copy_is_kept_when_results_change(self):
        self.write_results()
        apply_results(self.db, self.scenario.id, read_results(self.run_dir), self.run_dir)
        marked = highlight.highlight_path(self.run_dir, 1)
        QImage(10, 10, QImage.Format.Format_ARGB32).save(str(marked))
        self.assertTrue(sc.add_highlight_screenshot(self.db, self.scenario.id, 1, marked))
        self.assertFalse(sc.add_highlight_screenshot(self.db, self.scenario.id, 9, marked))  # no such step

        claude_shot = self.run_dir / "screenshots" / "step-1.png"
        QImage(10, 10, QImage.Format.Format_ARGB32).save(str(claude_shot))
        self.write_results("screenshots/step-1.png")
        apply_results(self.db, self.scenario.id, read_results(self.run_dir), self.run_dir)
        step, = sc.scenario_steps(self.db, self.scenario.id)
        self.assertEqual([Path(s.file_path).name for s in step.screenshots], [marked.name, "step-1.png"])

        card = ScreenshotCard()
        card.set_paths([claude_shot, marked])
        self.assertEqual(card.paths[0], marked)  # the marked copy is shown first
        self.assertIn("Failing element marked by the app", card.more.text())
        card.deleteLater()


class OtherTests(unittest.TestCase):
    def test_prompt_asks_for_the_element(self):
        prompt = build_prompt(RunContext("S", "https://shop.test", ["shop.test"], "", "", "N", "Open", ""))
        self.assertIn('set "highlight" to the exact visible text', prompt)

    def test_monitor_request_reply(self):
        monitor = PageMonitor(None, lambda: None)
        sent = []
        monitor.socket.sendTextMessage = sent.append
        replies = []
        monitor.request("Page.captureScreenshot", {"format": "png"}, replies.append)
        message_id = json.loads(sent[0])["id"]
        monitor._on_message(json.dumps({"id": message_id, "result": {"data": "abc"}}))
        monitor._on_message(json.dumps({"id": message_id, "result": {"data": "again"}}))  # answered once only
        self.assertEqual(replies, [{"data": "abc"}])


if __name__ == "__main__":
    unittest.main()
