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

from PySide6.QtCore import QByteArray, QIODevice, QObject, QProcess, QProcessEnvironment, QTimer, Signal

from wta import claude_launcher, processes, usage
from wta.db.database import Database
from wta.db import runs
from wta.db.models import RunOutcomeKind, TraceKind
from wta.prompt import RunContext, build_prompt
from wta.processes import ProcessRegistry
from wta.results import (
    AWAITING_CONFIRMATION,
    RESULTS_FILE,
    SCREENSHOT_DIR,
    ConfirmationRequest,
    Results,
    apply_results,
    read_results,
)
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
    stopped: bool = False


def make_redactor(*secrets: str):
    secrets = tuple(s for s in secrets if s)

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, REDACTED)
        return text

    return redact


def run_folder(
    base: Path, session_id: int, batch: int, position: int, now: datetime | None = None, smoke: bool = False
) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    batch_dir, item = (f"smoke-batch-{batch}", "page") if smoke else (f"batch-{batch}", "scenario")
    return base / f"session-{session_id}" / batch_dir / f"{item}-{position}-{stamp}"


class ScenarioRun(QObject):
    """`claude -p` for one scenario. Usually one process; after a confirmation request the
    run continues in a new process with `--resume`, in the same run folder."""

    event = Signal(object)  # TraceEvent
    results_changed = Signal(int)  # scenario id
    usage_changed = Signal(object)  # dict[str, WindowUsage]
    confirmation_needed = Signal(object)  # ConfirmationRequest
    finished = Signal(object)  # RunOutcome

    def __init__(
        self,
        db: Database,
        job: ScenarioJob,
        cdp_port: int,
        usage_path: Path,
        registry: ProcessRegistry | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.job = job
        self.cdp_port = cdp_port
        self.usage_path = usage_path
        self.registry = registry
        self.redact = make_redactor(job.context.test_account_password)
        self.process: QProcess | None = None
        self.session_id: str | None = None
        self.stopped = False
        self.awaiting: ConfirmationRequest | None = None
        self._mcp_config: Path | None = None
        self._buffer = b""
        self._trace_file = None
        self._results_mtime = 0.0
        self._mtime_at_resume = 0.0
        self._last_results: Results | None = None
        self._reported: set[str] = set()
        self._ticks = 0
        self._done = False
        self.outcome = RunOutcome(job.scenario_id, -1, None, usage_at_start=usage.load(usage_path))
        self.run_id: int | None = None
        self._poll = QTimer(self, interval=RESULTS_POLL_MS)
        self._poll.timeout.connect(self._tick)

    def start(self) -> None:
        (self.job.run_dir / SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
        # Long, final path (see wta.paths.data_dir): otherwise Claude's writes can be refused.
        run_dir = self.job.run_dir = self.job.run_dir.resolve()
        self._mcp_config = claude_launcher.write_mcp_config(
            run_dir / "mcp.json", self.cdp_port, run_dir / "playwright-output"
        )
        prompt = build_prompt(self.job.context)
        (run_dir / "prompt.md").write_text(self.redact(prompt), encoding="utf-8")
        self._trace_file = open(run_dir / "trace.jsonl", "w", encoding="utf-8")
        before = self.outcome.usage_at_start
        self.run_id = runs.start_run(
            self.db, self.job.scenario_id, run_dir,
            five_hour_before=before["five_hour"].percent if "five_hour" in before else None,
            seven_day_before=before["seven_day"].percent if "seven_day" in before else None,
            site_notes_used=len(self.job.context.site_notes),
        )
        self._emit(TraceKind.NOTE, f"Starting Claude Code in {run_dir}")
        if self.job.context.site_notes:
            self._emit(TraceKind.NOTE, f"Using {len(self.job.context.site_notes)} site note(s): "
                       + " | ".join(self.job.context.site_notes))
        self._launch(prompt)

    def _launch(self, stdin_text: str, resume: str | None = None) -> None:
        command = claude_launcher.find_claude()
        if command is None:
            self._emit(TraceKind.RESULT, "Claude Code is not installed (no `claude` on PATH).")
            self._finish(-1)
            return
        process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in claude_launcher.child_environment().items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(self.job.run_dir))
        process.setProgram(command[0])
        process.setArguments(command[1:] + claude_launcher.run_args(self._mcp_config, resume_session=resume))
        process.setStandardErrorFile(str(self.job.run_dir / "stderr.log"), QIODevice.OpenModeFlag.Append)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.finished.connect(lambda code, _status: self._on_process_finished(code))
        process.errorOccurred.connect(self._on_error)
        process.started.connect(lambda: self._on_started(stdin_text))
        self.process = process
        self._buffer = b""
        process.start()
        self._poll.start()

    def stop(self) -> None:
        """Stop the run now: kills Claude Code and everything it started (Playwright MCP)."""
        if self._done:
            return
        self.stopped = True
        self._emit(TraceKind.NOTE, "Stopped by the user.")
        if self.is_running():
            processes.kill_tree(self.process.processId())
            self.process.waitForFinished(5000)  # finished -> _on_process_finished -> _finish
        if not self._done:
            self._finish(-1)

    kill = stop

    def is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def answer_confirmation(self, approved: bool) -> None:
        """Continue after a confirmation request, with the user's decision."""
        request, self.awaiting = self.awaiting, None
        if request is None or self._done:
            return
        if not self.session_id:
            self.outcome.problems.append("Could not resume: no Claude session id was reported.")
            self._finish(-1)
            return
        if approved:
            self._emit(TraceKind.NOTE, f"User approved: {request.action}")
            message = (
                f"The user APPROVED this action: {request.action}\n"
                f"Carry it out now, then continue the scenario as before, keeping {RESULTS_FILE} updated "
                f"(set \"state\" back to \"running\" and remove \"confirmation_request\")."
            )
        else:
            self._emit(TraceKind.NOTE, f"User declined: {request.action}")
            message = (
                f"The user DECLINED this action: {request.action}\n"
                "Do not do it. Mark that step \"blocked\" with the reasoning \"Declined by the user\", "
                f"then continue with any remaining steps that do not depend on it, keeping {RESULTS_FILE} updated "
                f"(set \"state\" back to \"running\" and remove \"confirmation_request\")."
            )
        self._mtime_at_resume = self._results_mtime
        self._launch(message, resume=self.session_id)

    # --- process I/O -----------------------------------------------------------

    def _on_started(self, stdin_text: str) -> None:
        if self.registry is not None:
            self.registry.track(self.process.processId(), "claude run")
        self.process.write(QByteArray(stdin_text.encode("utf-8")))
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
        if parsed.session_id:
            self.session_id = parsed.session_id
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
        runs.add_trace(self.db, self.job.scenario_id, self.run_id, kind, text)
        self.event.emit(TraceEvent(kind, text, tool))

    def add_note(self, text: str) -> None:
        """Record something the app did during the run (e.g. a blocked navigation)."""
        if not self._done:
            self._emit(TraceKind.NOTE, text)

    # --- results.json ----------------------------------------------------------

    def _tick(self) -> None:
        self._ticks += 1
        self._check_results()
        if self.registry is not None and self._ticks % 3 == 0:
            self.registry.refresh()  # pick up Playwright MCP and other children

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
        self._last_results = results
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

    def _on_process_finished(self, exit_code: int) -> None:
        if self._done:
            return
        self._check_results()
        results = self._last_results
        written_since_resume = self._results_mtime > self._mtime_at_resume
        if (
            not self.stopped
            and exit_code == 0
            and results is not None
            and results.state == AWAITING_CONFIRMATION
            and results.confirmation is not None
            and written_since_resume
        ):
            self._poll.stop()
            self.awaiting = results.confirmation
            self._emit(TraceKind.NOTE, f"Waiting for your confirmation: {results.confirmation.action}")
            self.confirmation_needed.emit(results.confirmation)
            return
        self._finish(exit_code)

    def _finish(self, exit_code: int) -> None:
        if self._done:  # a start failure can report both errorOccurred and finished
            return
        self._done = True
        self.awaiting = None
        self._poll.stop()
        self._results_mtime = 0.0
        self._check_results()
        if self._trace_file:
            self._trace_file.close()
            self._trace_file = None
        if self.registry is not None:
            self.registry.kill_all()  # anything this run left behind (e.g. an orphaned MCP server)
        self.outcome.exit_code = exit_code
        self.outcome.stopped = self.stopped
        try:
            tail = (self.job.run_dir / "stderr.log").read_text("utf-8", errors="replace")[-2000:]
        except OSError:
            tail = ""
        self.outcome.stderr_tail = self.redact(tail)
        if not (self.job.run_dir / RESULTS_FILE).exists():
            self.outcome.problems.append("Claude did not write results.json")
        self._record_run_end()
        self.finished.emit(self.outcome)

    def _record_run_end(self) -> None:
        if self.run_id is None:
            return
        s = self.outcome.summary
        if self.stopped:
            outcome = RunOutcomeKind.STOPPED
        elif s is not None and s.ok and self.outcome.exit_code == 0:
            outcome = RunOutcomeKind.FINISHED
        else:
            outcome = RunOutcomeKind.FAILED
        seen = self.outcome.usage_seen
        runs.finish_run(self.db, self.run_id, runs.RunEnd(
            outcome=outcome,
            exit_code=self.outcome.exit_code,
            num_turns=s.num_turns if s else None,
            duration_ms=s.duration_ms if s else None,
            input_tokens=s.input_tokens if s else 0,
            output_tokens=s.output_tokens if s else 0,
            cache_read_tokens=s.cache_read_tokens if s else 0,
            cache_creation_tokens=s.cache_creation_tokens if s else 0,
            five_hour_seen=seen["five_hour"].percent if "five_hour" in seen else None,
            seven_day_seen=seen["seven_day"].percent if "seven_day" in seen else None,
            summary=self.redact(s.text) if s else "",
        ))


class BatchRun(QObject):
    """Runs scenario jobs one after another."""

    scenario_started = Signal(object, int, int)  # ScenarioJob, index (1-based), total
    event = Signal(object)
    results_changed = Signal(int)
    usage_changed = Signal(object)
    confirmation_needed = Signal(object, object)  # ScenarioRun, ConfirmationRequest
    scenario_finished = Signal(object)  # RunOutcome
    all_finished = Signal()

    def __init__(
        self,
        db: Database,
        jobs: list[ScenarioJob],
        cdp_port: int,
        usage_path: Path,
        registry: ProcessRegistry | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.db, self.jobs, self.cdp_port, self.usage_path = db, list(jobs), cdp_port, usage_path
        self.registry = registry
        self.current: ScenarioRun | None = None
        self.stopped = False
        self._index = 0

    def start(self) -> None:
        self._next()

    def is_running(self) -> bool:
        return self.current is not None and self.current.is_running()

    def stop(self) -> None:
        """Stop the current scenario and skip the rest of the batch."""
        self.stopped = True
        self.jobs = self.jobs[: self._index]
        if self.current:
            self.current.stop()

    kill = stop

    def _next(self) -> None:
        if self._index >= len(self.jobs):
            self.current = None
            self.all_finished.emit()
            return
        job = self.jobs[self._index]
        self._index += 1
        run = ScenarioRun(self.db, job, self.cdp_port, self.usage_path, self.registry, self)
        run.event.connect(self.event)
        run.results_changed.connect(self.results_changed)
        run.usage_changed.connect(self.usage_changed)
        run.confirmation_needed.connect(lambda request, r=run: self.confirmation_needed.emit(r, request))
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

