"""Phase 16: the app's page monitor (errors, failed requests, load times), the contrast check and their views."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wta import accessibility, settings  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.db import runs  # noqa: E402
from wta.db import scenarios as sc  # noqa: E402
from wta.db.models import CaptureLog, PageIssueKind, Step, StepStatus  # noqa: E402
from wta.page_monitor import PageMonitor, target_url  # noqa: E402
from wta.prompt import ACCESSIBILITY_CHECKS, RunContext, SmokeContext, build_prompt  # noqa: E402
from wta.runs_tab import RunsTab, findings_html  # noqa: E402
from wta.scenario_tab import ScenarioTab  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402
TARGET = "TARGET1"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        self.session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://shop.test"))
        self.scenario = sc.add_scenario(self.db, self.session.id, 1, sc.ScenarioInput(name="Checkout"))
        self.run_id = runs.start_run(self.db, self.scenario.id, Path(self.tmp.name) / "run")
        self.current = self.run_id
        self.monitor = PageMonitor(self.db, lambda: self.current)
        self.monitor.target_id = TARGET

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def events(self, *events):
        for method, params in events:
            self.monitor.handle_event(method, params)
        self.monitor.flush()
        return runs.page_findings(self.db, self.run_id)


class EventTests(Fixture):
    def test_console_and_exceptions(self):
        found = self.events(
            ("Runtime.consoleAPICalled", {"type": "error", "args": [{"value": "Save failed"}, {"value": 500}],
                                          "stackTrace": {"callFrames": [{"url": "https://shop.test/app.js"}]}}),
            ("Runtime.consoleAPICalled", {"type": "warning", "args": [{"description": "Deprecated API"}]}),
            ("Runtime.consoleAPICalled", {"type": "log", "args": [{"value": "hello"}]}),  # not a problem
            ("Runtime.exceptionThrown", {"exceptionDetails": {"text": "Uncaught", "url": "https://shop.test/a.js",
                                                              "exception": {"description": "TypeError: x is null"}}}),
        )
        self.assertEqual([(i.kind, i.level, i.message) for i in found.issues], [
            (PageIssueKind.CONSOLE, "error", "Save failed 500"),
            (PageIssueKind.CONSOLE, "warning", "Deprecated API"),
            (PageIssueKind.EXCEPTION, "error", "TypeError: x is null"),
        ])
        self.assertEqual(found.issues[0].url, "https://shop.test/app.js")

    def test_failed_requests(self):
        found = self.events(
            ("Network.requestWillBeSent", {"requestId": "1", "request": {"url": "https://shop.test/api/save",
                                                                         "method": "POST"}}),
            ("Network.responseReceived", {"requestId": "1", "response": {"url": "https://shop.test/api/save",
                                                                         "status": 500, "statusText": "Server Error"}}),
            ("Network.requestWillBeSent", {"requestId": "2", "request": {"url": "https://shop.test/ok"}}),
            ("Network.responseReceived", {"requestId": "2", "response": {"url": "https://shop.test/ok", "status": 200}}),
            ("Network.requestWillBeSent", {"requestId": "3", "request": {"url": "https://ads.example/x.js"}}),
            ("Network.loadingFailed", {"requestId": "3", "errorText": "net::ERR_BLOCKED_BY_CLIENT"}),
            ("Network.requestWillBeSent", {"requestId": "4", "request": {"url": "https://shop.test/slow"}}),
            ("Network.loadingFailed", {"requestId": "4", "errorText": "net::ERR_ABORTED"}),  # page moved on
            ("Network.loadingFailed", {"requestId": "5", "errorText": "net::ERR_FAILED", "canceled": True}),
            ("Network.responseReceived", {"requestId": "6", "response": {"url": "https://shop.test/favicon.ico",
                                                                         "status": 404}}),  # the browser's own
        )
        self.assertEqual([i.message for i in found.issues], [
            "500 Server Error — POST https://shop.test/api/save",
            "Failed (blocked: outside the allowed domains) — GET https://ads.example/x.js",
        ])
        self.assertEqual(found.issues[0].status, 500)

    def test_page_load_time_of_the_main_frame(self):
        found = self.events(
            ("Network.requestWillBeSent", {"requestId": "9", "type": "Document", "frameId": "IFRAME",
                                           "request": {"url": "https://shop.test/frame"}, "timestamp": 1.0}),
            ("Network.requestWillBeSent", {"requestId": "1", "type": "Document", "frameId": TARGET,
                                           "request": {"url": "https://shop.test/orders"}, "timestamp": 10.0}),
            ("Page.loadEventFired", {"timestamp": 13.25}),
            ("Page.loadEventFired", {"timestamp": 20.0}),  # no navigation started: ignored
        )
        self.assertEqual([(l.url, l.load_ms) for l in found.loads], [("https://shop.test/orders", 3250)])
        self.assertEqual(len(found.slow_loads), 1)
        self.assertEqual(runs.latest_page_loads(self.db, self.scenario.id), {"https://shop.test/orders": 3250})

    def test_nothing_is_kept_between_runs(self):
        self.current = None
        found = self.events(("Runtime.exceptionThrown", {"exceptionDetails": {"text": "x"}}))
        self.assertEqual(found.issues, [])

    def test_a_flood_is_capped(self):
        with mock.patch.object(runs, "MAX_PAGE_ISSUES_PER_RUN", 3):
            found = self.events(*[("Runtime.consoleAPICalled", {"type": "error", "args": [{"value": i}]})
                                  for i in range(5)])
        self.assertEqual(len(found.issues), 3)
        self.assertEqual(self.monitor.dropped, 2)

    def test_target_url(self):
        self.assertEqual(target_url(9222, "ABC"), "ws://127.0.0.1:9222/devtools/page/ABC")


class ViewTests(Fixture):
    def test_findings_html_and_runs_tab(self):
        self.assertIn("No console errors", findings_html(runs.page_findings(self.db, self.run_id)))
        self.events(
            ("Runtime.exceptionThrown", {"exceptionDetails": {"text": "Boom <script>"}}),
            ("Network.requestWillBeSent", {"requestId": "1", "type": "Document", "frameId": TARGET,
                                           "request": {"url": "https://shop.test/"}, "timestamp": 1.0}),
            ("Page.loadEventFired", {"timestamp": 5.0}),
        )
        html = findings_html(runs.page_findings(self.db, self.run_id))
        self.assertIn("Uncaught JavaScript errors (1)", html)
        self.assertIn("Boom &lt;script&gt;", html)  # page text is escaped
        self.assertIn("4.00 s <b style='color:#C77700'>slow</b>", html)
        tab = RunsTab(self.db)
        tab.set_session(self.session.id, "shop.test")
        self.assertEqual(tab.detail_tabs.tabText(1), "Errors and speed (2)")  # 1 error + 1 slow load
        tab.deleteLater()

    def test_step_shows_its_page_load(self):
        self.events(
            ("Network.requestWillBeSent", {"requestId": "1", "type": "Document", "frameId": TARGET,
                                           "request": {"url": "https://shop.test/cart"}, "timestamp": 1.0}),
            ("Page.loadEventFired", {"timestamp": 1.8}),
        )
        with self.db.Session.begin() as orm:
            orm.add(Step(scenario_id=self.scenario.id, order=1, action="Open cart", status=StepStatus.PASS,
                         capture_logs=[CaptureLog(page_url="https://shop.test/cart#top")]))
        tab = ScenarioTab(self.db)
        tab.set_session(self.session.id)
        self.assertIn("Page load 0.80 s", tab.step_details.toPlainText().replace("\n", " ").replace("\t", " "))
        tab.deleteLater()


class AccessibilityTests(unittest.TestCase):
    def test_contrast_check_on_a_real_page(self):
        view = QWebEngineView()
        view.resize(800, 600)
        view.show()  # as in the app: an unshown view lays the page out at zero size
        loaded = []
        view.loadFinished.connect(loaded.append)
        view.setHtml("<html><body style='background:#fff'>"
                     "<p id='faint' style='color:#bbb'>Hard to read</p>"
                     "<p style='color:#222'>Easy to read</p>"
                     "<h1 style='color:#888;font-size:32px'>Big grey heading</h1></body></html>",
                     QUrl("https://shop.test/"))
        end = time.monotonic() + 15
        while not loaded and time.monotonic() < end:
            app.processEvents()
        result = []
        view.page().runJavaScript(accessibility.CONTRAST_JS, QWebEngineScript.ScriptWorldId.ApplicationWorld,
                                  result.append)
        end = time.monotonic() + 15
        while not result and time.monotonic() < end:
            app.processEvents()
        issues = accessibility.contrast_issues(result[0])
        self.assertEqual(len(issues), 1)  # #888 at 32px passes the 3:1 rule for large text
        self.assertIn("“Hard to read” in p#faint", issues[0].message)
        self.assertIn("needs 4.5:1", issues[0].message)
        self.assertEqual(issues[0].url, "https://shop.test/")
        self.assertEqual(accessibility.contrast_issues(None), [])
        view.deleteLater()

    def test_setting_and_prompt(self):
        self.assertTrue(settings.Settings().smoke_accessibility)
        ctx = RunContext("S", "https://shop.test", ["shop.test"], "", "", "Home", "", "",
                         smoke=SmokeContext("Home", "https://shop.test"), accessibility_checks=True)
        self.assertIn(ACCESSIBILITY_CHECKS, build_prompt(ctx))
        ctx.accessibility_checks = False
        self.assertNotIn(ACCESSIBILITY_CHECKS, build_prompt(ctx))


if __name__ == "__main__":
    unittest.main()
