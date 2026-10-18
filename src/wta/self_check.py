"""`WebTestingAssistant.exe --self-check report.json`: check an installed build without opening the window.

Confirms the bundle has what the app needs (icons, the browser engine, charts, the export
libraries and their templates) and reports whether Node.js and Claude Code are ready. Nothing is
sent anywhere and no test is run; a throwaway database in a temporary folder is used for the exports.
"""

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QApplication

from wta import __version__, claude_launcher


def _web_engine(app: QApplication) -> str:
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView()
    loaded: list[bool] = []
    view.loadFinished.connect(loaded.append)
    view.setHtml("<html><body><p id='x'>ok</p></body></html>", QUrl("https://self-check.test/"))
    end = time.monotonic() + 30
    while not loaded and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    view.deleteLater()
    _flush_deletions(app)  # gone before the application, or Qt WebEngine crashes on exit
    if not loaded or not loaded[0]:
        raise RuntimeError("the test page did not load")
    return "a page loads"


def _flush_deletions(app: QApplication) -> None:
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def _icons() -> str:
    from wta import icons

    if icons.logo_icon().isNull() or icons.icon("play", "#000000").isNull():
        raise RuntimeError("bundled icons are missing")
    return "logo and icons load"


def _charts() -> str:
    from PySide6.QtCharts import QChart, QLineSeries

    chart = QChart()
    chart.addSeries(QLineSeries())
    return "Qt Charts available"


def _exports() -> str:
    from wta.db import Database, store
    from wta.db import scenarios as sc
    from wta.db.models import Bug, Step, StepStatus
    from wta.export import export_report

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        db = Database(base / "check.sqlite3")
        try:
            session = store.create_session(db, store.SessionDetails(name="Self-check", target_url="https://a.test"))
            scenario = sc.add_scenario(db, session.id, 1, sc.ScenarioInput(name="Check", steps="Open"))
            with db.Session.begin() as orm:
                orm.add(Step(scenario_id=scenario.id, order=1, action="Open", status=StepStatus.FAIL,
                             bugs=[Bug(title="Example bug")]))
            made = []
            for suffix in ("xlsx", "docx", "pdf", "md"):
                export_report(db, session.id, base / f"report.{suffix}")
                if not any(base.glob(f"report*{'' if suffix == 'md' else '.' + suffix}")):
                    raise RuntimeError(f"no {suffix} file was written")
                made.append(suffix)
        finally:
            db.close()
    return "exported " + ", ".join(made)


def _node() -> str:
    status = claude_launcher.check_node()
    if not status.ok:
        raise RuntimeError(status.message)
    return status.message


def _claude() -> str:
    status = claude_launcher.check_sign_in()
    if not status.ok:
        raise RuntimeError(status.message)
    return status.message


CHECKS = (("icons", _icons), ("charts", _charts), ("exports", _exports), ("node", _node), ("claude", _claude))
# Node.js and Claude Code belong to the machine, not the bundle: reported, but they do not fail the check.
MACHINE_CHECKS = {"node", "claude"}


def run(report_path: Path, app: QApplication) -> int:
    results: dict[str, dict] = {}
    for name, check in (("web_engine", lambda: _web_engine(app)), *CHECKS):
        try:
            results[name] = {"ok": True, "detail": check()}
        except Exception as exc:  # report every failure, keep checking the rest
            results[name] = {"ok": False, "detail": str(exc) or type(exc).__name__,
                             "trace": traceback.format_exc(limit=3)}
    bundle_ok = all(r["ok"] for name, r in results.items() if name not in MACHINE_CHECKS)
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "bundle_ok": bundle_ok,
              "checks": results}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _flush_deletions(app)
    return 0 if bundle_ok else 1
