"""The app's own watch on the page under test during a run: console errors, uncaught exceptions,
failed requests and page load times, on every step (not only the ones Claude marks Bug or Blocked).

It connects to the embedded page's DevTools target over the debugging port the app already
opens for Playwright (a second, read-only client) using Qt's own WebSocket. It costs no plan usage.
"""

import json
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from wta import cdp
from wta.db.database import Database
from wta.db import runs
from wta.db.models import PageIssueKind

FLUSH_MS = 2000
# Requests the page itself gave up on (navigation away, cancelled fetch) are not failures.
IGNORED_ERRORS = {"net::ERR_ABORTED"}
BLOCKED_BY_APP = "net::ERR_BLOCKED_BY_CLIENT"  # the app's allowed-domain guard


def target_url(port: int, target_id: str) -> str:
    return f"{cdp.endpoint_url(port).replace('http://', 'ws://')}/devtools/page/{target_id}"


class PageMonitor(QObject):
    connected_changed = Signal(bool)

    def __init__(self, db: Database, run_id: Callable[[], int | None], parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.run_id = run_id  # the run events belong to, asked when each event arrives
        self.target_id = ""
        self.connected = False
        self.dropped = 0
        self._requests: dict[str, tuple[str, str]] = {}  # requestId -> (url, method)
        self._navigation: tuple[str, float] | None = None  # main-frame document: (url, start timestamp)
        self._pending: dict[int, tuple[list[runs.IssueRecord], list[tuple[str, int]]]] = {}
        self._next_id = 0
        self.socket = QWebSocket()
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.textMessageReceived.connect(self._on_message)
        self._timer = QTimer(self, interval=FLUSH_MS)
        self._timer.timeout.connect(self.flush)

    # --- connection --------------------------------------------------------------

    def attach(self, port: int, target_id: str) -> None:
        self.target_id = target_id
        self._timer.start()
        self.socket.open(QUrl(target_url(port, target_id)))

    def detach(self) -> None:
        self.flush()
        self._timer.stop()
        self.socket.close()

    def _on_connected(self) -> None:
        self.connected = True
        for method in ("Runtime.enable", "Network.enable", "Page.enable"):
            self._send(method)
        self.connected_changed.emit(True)

    def _on_disconnected(self) -> None:
        self.connected = False
        self.connected_changed.emit(False)

    def _send(self, method: str, params: dict | None = None) -> None:
        self._next_id += 1
        self.socket.sendTextMessage(json.dumps({"id": self._next_id, "method": method, "params": params or {}}))

    def _on_message(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        if isinstance(message, dict) and "method" in message:
            self.handle_event(message["method"], message.get("params") or {})

    # --- events ------------------------------------------------------------------

    def handle_event(self, method: str, params: dict) -> None:
        if method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "warning", "assert"):
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args") or [])
            frames = (params.get("stackTrace") or {}).get("callFrames") or [{}]
            level = "warning" if params["type"] == "warning" else "error"
            self._issue(runs.IssueRecord(PageIssueKind.CONSOLE, text, frames[0].get("url", ""), level))
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = (details.get("exception") or {}).get("description") or details.get("text", "")
            self._issue(runs.IssueRecord(PageIssueKind.EXCEPTION, text, details.get("url", "")))
        elif method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            url = request.get("url", "")
            self._requests[params.get("requestId", "")] = (url, request.get("method", "GET"))
            if params.get("type") == "Document" and params.get("frameId") == self.target_id:
                self._navigation = (url, float(params.get("timestamp", 0)))
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            status = int(response.get("status") or 0)
            if status >= 400:
                url = response.get("url", "")
                verb = self._requests.get(params.get("requestId", ""), ("", "GET"))[1]
                text = f"{status} {response.get('statusText', '')}".strip() + f" — {verb} {url}"
                self._issue(runs.IssueRecord(PageIssueKind.REQUEST, text, url, status=status))
        elif method == "Network.loadingFailed":
            error = params.get("errorText", "")
            if params.get("canceled") or error in IGNORED_ERRORS:
                return
            url, verb = self._requests.get(params.get("requestId", ""), ("", "GET"))
            if url.startswith(("data:", "blob:")):
                return
            reason = "blocked: outside the allowed domains" if error == BLOCKED_BY_APP else error
            if params.get("blockedReason"):
                reason = f"blocked: {params['blockedReason']}"
            self._issue(runs.IssueRecord(PageIssueKind.REQUEST, f"Failed ({reason}) — {verb} {url}", url))
        elif method == "Page.loadEventFired" and self._navigation is not None:
            url, started = self._navigation
            self._navigation = None
            load_ms = round((float(params.get("timestamp", started)) - started) * 1000)
            if load_ms >= 0:
                self._add(loads=[(url, load_ms)])

    def add_issues(self, issues: list[runs.IssueRecord], run_id: int | None = None) -> None:
        """Findings from elsewhere (the app's accessibility check) for the current or a given run."""
        self._add(issues=issues, run_id=run_id)

    def _issue(self, issue: runs.IssueRecord) -> None:
        self._add(issues=[issue])

    def _add(self, issues=(), loads=(), run_id: int | None = None) -> None:
        run_id = run_id if run_id is not None else self.run_id()
        if run_id is None:
            return  # between runs: nobody to report it to
        pending_issues, pending_loads = self._pending.setdefault(run_id, ([], []))
        pending_issues.extend(issues)
        pending_loads.extend(loads)

    def flush(self) -> None:
        pending, self._pending = self._pending, {}
        for run_id, (issues, loads) in pending.items():
            if issues or loads:
                self.dropped += runs.add_page_findings(self.db, run_id, issues, loads)
