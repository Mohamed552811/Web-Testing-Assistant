"""Runs `claude -p` for scenarios, one at a time, and feeds the app as it goes.

Each scenario gets its own run folder (the working directory of the Claude process):
  prompt.md      the prompt, with the test password redacted
  mcp.json       Playwright MCP config attached to the embedded browser
  trace.jsonl    the raw stream-json output, with the test password redacted
  stderr.log     anything Claude Code printed to stderr
  results.json   written by Claude after every step
  screenshots/   written by Claude, only for Blocked / Bug steps
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QProcess, QProcessEnvironment, QTimer, Signal

from wta import claude_launcher, usage
from wta.db.database import Database
from wta.db.models import RunTrace, TraceKind
from wta.prompt import RunContext, build_prompt
from wta.results import RESULTS_FILE, SCREENSHOT_DIR, apply_results, read_results
from wta.stream import RunSummary, TraceEvent, parse_line

REDACTED = "[test password]"
RESULTS_POLL_MS = 1000


@dataclass
class ScenarioJob:
    scenario_id: int
    title: str
    context: RunContext
    run_dir: Path


@dataclass
class RunOutcome:
    scenario_id: int
    exit_code: int
    summary: RunSummary | None
    notices: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    usage_at_start: dict[str, usage.WindowUsage] = field(default_factory=dict)
    usage_seen: dict[str, usage.WindowUsage] = field(default_factory=dict)
    stderr_tail: str = ""


def make_redactor(*secrets: str):
    secrets = tuple(s for s in secrets if s)

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, REDACTED)
        return text

    return redact


def run_folder(base: Path, session_id: int, batch: int, position: int, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return base / f"session-{session_id}" / f"batch-{batch}" / f"scenario-{position}-{stamp}"


class ScenarioRun(QObject):
    """One `claude -p` process for one scenario."""

    event = Signal(object)  # TraceEvent
    results_changed = Signal(int)  # scenario id
    usage_changed = Signal(object)  # dict[str, WindowUsage]
    finished = Signal(object)  # RunOutcome

    def __init__(self, db: Database, job: ScenarioJob, cdp_port: int, usage_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.job = job
        self.cdp_port = cdp_port
        self.usage_path = usage_path
        self.redact = make_redactor(job.context.test_account_password)
        self.process = QProcess(self)
        self._buffer = b""
        self._trace_file = None
        self._results_mtime = 0.0
        self._reported: set[str] = set()
        self._done = False
        self.outcome = RunOutcome(job.scenario_id, -1, None, usage_at_start=usage.load(usage_path))
        self._poll = QTimer(self, interval=RESULTS_POLL_MS)
        self._poll.timeout.connect(self._check_results)

    def start(self) -> None:
        (self.job.run_dir / SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
        # Long, final path (see wta.paths.data_dir): otherwise Claude's writes can be refused.
        run_dir = self.job.run_dir = self.job.run_dir.resolve()
        mcp_config = claude_launcher.write_mcp_config(run_dir / "mcp.json", self.cdp_port, run_dir / "playwright-output")
        prompt = build_prompt(self.job.context)
        (run_dir / "prompt.md").write_text(self.redact(prompt), encoding="utf-8")
        self._trace_file = open(run_dir / "trace.jsonl", "w", encoding="utf-8")

        command = claude_launcher.find_claude()
        if command is None:
            self._emit(TraceKind.RESULT, "Claude Code is not installed (no `claude` on PATH).")
            self._finish(-1)
            return
        env = QProcessEnvironment()
        for key, value in claude_launcher.child_environment().items():
            env.insert(key, value)
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(run_dir))
        self.process.setProgram(command[0])
        self.process.setArguments(command[1:] + claude_launcher.run_args(mcp_config))
        self.process.setStandardErrorFile(str(run_dir / "stderr.log"))
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.finished.connect(lambda code, _status: self._finish(code))
        self.process.errorOccurred.connect(self._on_error)
        self.process.started.connect(lambda: self._send_prompt(prompt))
        self._emit(TraceKind.NOTE, f"Starting Claude Code in {run_dir}")
        self.process.start()
        self._poll.start()

    def kill(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)

    def is_running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    # --- process I/O -----------------------------------------------------------

    def _send_prompt(self, prompt: str) -> None:
        self.process.write(QByteArray(prompt.encode("utf-8")))
        self.process.closeWriteChannel()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._emit(TraceKind.RESULT, f"Claude Code failed to start: {self.process.errorString()}")
            self._finish(-1)

    def _read_stdout(self) -> None:
        self._buffer += bytes(self.process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            self._handle_line(raw.decode("utf-8", errors="replace"))

    def _handle_line(self, line: str) -> None:
        if not line.strip():
            return
        if self._trace_file:
            self._trace_file.write(self.redact(line.rstrip("\r")) + "\n")
            self._trace_file.flush()
        parsed = parse_line(line)
        for event in parsed.events:
            self._emit(event.kind, event.text, event.tool)
        if parsed.rate_limit is not None:
            state = usage.record_event(self.usage_path, parsed.rate_limit)
            self.outcome.usage_seen = state
            self.usage_changed.emit(state)
        if parsed.summary is not None:
            self.outcome.summary = parsed.summary

    def _emit(self, kind: TraceKind, text: str, tool: str = "") -> None:
        text = self.redact(text)
        with self.db.Session.begin() as orm:
            orm.add(RunTrace(scenario_id=self.job.scenario_id, kind=kind, text=text))
        self.event.emit(TraceEvent(kind, text, tool))

    # --- results.json ----------------------------------------------------------

    def _check_results(self) -> None:
        path = self.job.run_dir / RESULTS_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == self._results_mtime:
            return
        results = read_results(self.job.run_dir)
        if results is None:
            return  # half-written; try again on the next tick
        self._results_mtime = mtime
        problems = results.problems + apply_results(
            self.db, self.job.scenario_id, results, self.job.run_dir, redact=self.redact
        )
        self.outcome.notices = [self.redact(n) for n in results.notices]
        self.outcome.problems = problems
        for notice in self.outcome.notices:
            self._report_once(f"Notice from Claude: {notice}")
        self.results_changed.emit(self.job.scenario_id)

    def _report_once(self, text: str) -> None:
        if text not in self._reported:
            self._reported.add(text)
            self._emit(TraceKind.NOTE, text)

    def _finish(self, exit_code: int) -> None:
        if self._done:  # a start failure can report both errorOccurred and finished
            return
        self._done = True
        self._poll.stop()
        self._results_mtime = 0.0
        self._check_results()
        if self._trace_file:
            self._trace_file.close()
            self._trace_file = None
        self.outcome.exit_code = exit_code
        try:
            tail = (self.job.run_dir / "stderr.log").read_text("utf-8", errors="replace")[-2000:]
        except OSError:
            tail = ""
        self.outcome.stderr_tail = self.redact(tail)
        if not (self.job.run_dir / RESULTS_FILE).exists():
            self.outcome.problems.append("Claude did not write results.json")
        self.finished.emit(self.outcome)


class BatchRun(QObject):
    """Runs scenario jobs one after another."""

    scenario_started = Signal(object, int, int)  # ScenarioJob, index (1-based), total
    event = Signal(object)
    results_changed = Signal(int)
    usage_changed = Signal(object)
    scenario_finished = Signal(object)  # RunOutcome
    all_finished = Signal()

    def __init__(self, db: Database, jobs: list[ScenarioJob], cdp_port: int, usage_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.db, self.jobs, self.cdp_port, self.usage_path = db, list(jobs), cdp_port, usage_path
        self.current: ScenarioRun | None = None
        self._index = 0

    def start(self) -> None:
        self._next()

    def is_running(self) -> bool:
        return self.current is not None and self.current.is_running()

    def kill(self) -> None:
        self.jobs = self.jobs[: self._index]  # nothing after the current one
        if self.current:
            self.current.kill()

    def _next(self) -> None:
        if self._index >= len(self.jobs):
            self.current = None
            self.all_finished.emit()
            return
        job = self.jobs[self._index]
        self._index += 1
        run = ScenarioRun(self.db, job, self.cdp_port, self.usage_path, self)
        run.event.connect(self.event)
        run.results_changed.connect(self.results_changed)
        run.usage_changed.connect(self.usage_changed)
        run.finished.connect(self._on_finished)
        self.current = run
        self.scenario_started.emit(job, self._index, len(self.jobs))
        run.start()

    def _on_finished(self, outcome: RunOutcome) -> None:
        self.scenario_finished.emit(outcome)
        self._next()


def default_runs_dir(data_dir: Path) -> Path:
    path = data_dir / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path

