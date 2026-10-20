"""Generate test cases for one backlog through Claude Code (spec 7.3, decision 1: `claude -p`, no API key).

- Planner: pure logic. Builds each call and decides what happens after it: done, one retry with the
  validation errors appended (spec 7.3), or a continuation with the cases already received passed as
  locked_test_cases when the answer was cut off.
- ClaudeCall: one `claude -p` process (QProcess), images in on stdin as a stream-json user message.
- GenerationRun: drives the two and emits `finished(GenerationOutcome)`.
"""

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from wta import claude_launcher, usage
from wta.designer import prompt_builder as pb
from wta.designer import validation
from wta.designer.settings import PINNED, DesignerSettings, snapshot
from wta.stream import parse_line

MAX_CONTINUATIONS = 2
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
INSUFFICIENT = "insufficient_information"


@dataclass
class GenerationRequest:
    """Everything one generation needs; the app builds it from the stored backlog (M4 adds notes storage)."""

    backlog_key: str
    title: str
    sheet: str
    first_row: int
    last_row: int
    text_blocks: list[tuple[str, str]]
    images: list[pb.PromptImage]
    settings: DesignerSettings
    domain_notes: list[str] = field(default_factory=list)
    supplementary: list[str] = field(default_factory=list)
    user_guidance: str = ""
    locked_test_cases: list[dict] = field(default_factory=list)
    status_code: str | None = None


def preflight(request: GenerationRequest) -> str | None:
    """Spec 3.5: no description, no pictures and no notes means no model call."""
    if not request.text_blocks and not request.images and not any(n.strip() for n in request.domain_notes):
        return INSUFFICIENT
    return None


@dataclass
class CallSpec:
    kind: str  # first | retry | continuation
    model: str
    system_prompt: str
    json_schema: str
    content: list[dict]


@dataclass
class CallResult:
    """What one `claude -p` call produced."""

    structured: object = None
    model: str = ""
    subtype: str = ""
    stop_reason: str = ""
    is_error: bool = False
    result_text: str = ""
    errors: list[str] = field(default_factory=list)
    partial_text: str = ""  # the last answer text or StructuredOutput input seen, for salvage
    process_error: str = ""  # Claude Code could not run or crashed
    api_error_status: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int | None = None
    truncated_hint: bool = False  # an api_retry or error said max_output_tokens

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens" or self.truncated_hint


@dataclass
class GenerationOutcome:
    ok: bool
    status: str  # ok | invalid | error | insufficient_information
    response: dict | None
    message: str  # for the user
    validation_errors: list[str]
    model_id: str
    model_resolution: str
    prompt_version: str
    settings_snapshot: dict
    user_guidance: str
    stop_reason: str
    attempts: list[dict]


def describe_failure(result: CallResult) -> str:
    """A short user-facing reason for a call without a usable answer."""
    if result.process_error:
        return result.process_error
    text = " ".join([result.result_text, *result.errors]).strip()
    lowered = text.lower() if result.is_error else ""  # an answer's own text is never read as an error
    if result.api_error_status in (401, 403) or "not logged in" in lowered or "please run /login" in lowered:
        return "Claude Code is not signed in. Run `claude auth login`, then try again. " + text
    if result.api_error_status == 429 or "rate limit" in lowered or "usage limit" in lowered:
        return "Claude usage limit reached; try again later. " + text
    if result.subtype == "error_max_structured_output_retries":
        return "Claude could not produce output matching the schema. " + text
    return text or f"Claude Code ended without an answer ({result.subtype or 'no result'})."


class Planner:
    def __init__(self, request: GenerationRequest) -> None:
        self.request = request
        self.settings = request.settings
        self.numeric = pb.is_numeric_key(request.backlog_key)
        self.locked = [dict(c) for c in request.locked_test_cases]
        self.retried = False
        self.continuations = 0
        self.feedback = ""
        self.attempts: list[dict] = []
        self.model_id = ""
        preset = self.settings.preset()
        self.system_prompt = pb.system_prompt(self.settings, request.backlog_key)
        self.json_schema = json.dumps(pb.api_schema(preset, self.numeric), separators=(",", ":"))
        self.row_data = pb.backlog_row_data(request.backlog_key, request.title, request.sheet, request.first_row,
                                            request.last_row, request.text_blocks, request.images,
                                            request.domain_notes, request.status_code)

    def _spec(self, kind: str) -> CallSpec:
        text = pb.user_message_text(self.row_data, self.request.domain_notes, self.request.supplementary,
                                    self.request.user_guidance, self.locked)
        if self.feedback:
            text += "\n\n" + self.feedback
        return CallSpec(kind, self.settings.cli_model(), self.system_prompt, self.json_schema,
                        pb.user_content(self.request.images, text))

    def first_call(self) -> CallSpec:
        return self._spec("first")

    def _merge(self, response: dict) -> dict:
        if not self.locked:
            return response
        locked_ids = {c.get("id") for c in self.locked}
        merged = dict(response)
        merged["test_cases"] = self.locked + [c for c in response.get("test_cases") or []
                                              if not (isinstance(c, dict) and c.get("id") in locked_ids)]
        return merged

    def on_result(self, kind: str, result: CallResult) -> "CallSpec | GenerationOutcome":
        self.model_id = result.model or self.model_id
        record = {"kind": kind, "subtype": result.subtype, "stop_reason": result.stop_reason,
                  "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                  "duration_ms": result.duration_ms, "errors": list(result.errors)}
        self.attempts.append(record)
        if result.process_error:
            return self._outcome("error", None, describe_failure(result), [], result)
        if not isinstance(result.structured, dict):
            if result.truncated and self.continuations < MAX_CONTINUATIONS:
                salvaged = validation.salvage_test_cases(result.partial_text)
                known = {c.get("id") for c in self.locked}
                new = [c for c in salvaged if c.get("id") not in known]
                if new:
                    self.locked += new
                    self.continuations += 1
                    record["salvaged"] = len(new)
                    return self._spec("continuation")
            if result.truncated:
                return self._outcome("error", None, "Claude's answer was cut off before it was complete, and no "
                                     "complete test cases could be kept. Try again, or split the backlog notes.",
                                     [], result)
            if not result.is_error and not self.retried:
                self.retried = True
                self.feedback = validation.feedback(["No JSON object matching the schema was returned."])
                return self._spec("retry")
            return self._outcome("error", None, describe_failure(result), [], result)
        checked = validation.validate(self._merge(result.structured), self.settings, self.request.backlog_key)
        record["validation_errors"] = list(checked.errors)
        if checked.ok:
            return self._outcome("ok", checked.response, "", [], result)
        if not self.retried:
            self.retried = True
            self.feedback = validation.feedback(checked.errors)
            return self._spec("retry")
        return self._outcome("invalid", checked.response,
                             f"The answer still failed validation after one retry ({len(checked.errors)} problem(s)).",
                             checked.errors, result)

    def _outcome(self, status: str, response, message: str, errors: list[str], result: CallResult | None):
        settings = self.settings
        resolution = (f"pinned: {settings.pinned_model_id}" if settings.model_mode == PINNED
                      else "latest Opus via `claude --model opus`")
        return GenerationOutcome(
            ok=status == "ok", status=status, response=response, message=message, validation_errors=errors,
            model_id=self.model_id, model_resolution=resolution, prompt_version=pb.PROMPT_VERSION,
            settings_snapshot=snapshot(settings), user_guidance=self.request.user_guidance,
            stop_reason=result.stop_reason if result else "", attempts=self.attempts,
        )

    def insufficient(self) -> GenerationOutcome:
        response = {"backlog_number": pb.backlog_number_value(self.request.backlog_key),
                    "backlog_title": self.request.title or self.request.backlog_key, "status": INSUFFICIENT,
                    "test_cases": [],
                    "open_questions": ["The backlog has no description text and no pictures. "
                                       "Add notes describing it, then generate again."]}
        return self._outcome(INSUFFICIENT, response, "Nothing to generate from: the backlog has no text, no "
                             "pictures and no notes (no model call was made).", [], None)


class ClaudeCall(QObject):
    """One `claude -p` process. `finished(CallResult)`."""

    finished = Signal(object)

    def __init__(self, work_dir: Path, usage_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.work_dir, self.usage_path = work_dir, usage_path
        self.process: QProcess | None = None
        self._buffer = b""
        self._result = CallResult()
        self._done = False
        self._started = 0.0

    def start(self, spec: CallSpec) -> None:
        command = claude_launcher.find_claude()
        if command is None:
            self._finish(CallResult(process_error="Claude Code is not installed (no `claude` on PATH)."))
            return
        self.work_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = self.work_dir / "system-prompt.txt"
        prompt_file.write_text(spec.system_prompt, encoding="utf-8")
        process = self.process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in claude_launcher.designer_environment().items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(self.work_dir))
        process.setProgram(command[0])
        process.setArguments(command[1:] + claude_launcher.designer_args(spec.model, prompt_file, spec.json_schema))
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(lambda code, _status: self._on_exit(code))
        process.errorOccurred.connect(self._on_error)
        self._started = time.monotonic()
        process.start()
        process.write(claude_launcher.stream_json_user_message(spec.content).encode("utf-8"))
        process.closeWriteChannel()

    def cancel(self) -> None:
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self._done = True
            self.process.kill()

    def feed(self, line: str) -> None:
        """Handle one stream-json line (also used by tests)."""
        parsed = parse_line(line)
        if parsed.model:
            self._result.model = parsed.model
        if parsed.rate_limit is not None and self.usage_path is not None:
            usage.record_event(self.usage_path, parsed.rate_limit)
        if parsed.summary is not None:
            s = parsed.summary
            r = self._result
            r.structured, r.subtype, r.stop_reason = s.structured_output, s.subtype, s.stop_reason
            r.is_error, r.result_text, r.errors = not s.ok, s.text, list(s.errors)
            r.api_error_status, r.duration_ms = s.api_error_status, s.duration_ms
            r.input_tokens, r.output_tokens = s.input_tokens, s.output_tokens
            if any("max_output_tokens" in e or "output token" in e.lower() for e in s.errors):
                r.truncated_hint = True
            return
        try:
            message = json.loads(line)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        if message.get("type") == "system" and message.get("subtype") == "api_retry" \
                and message.get("error") == "max_output_tokens":
            self._result.truncated_hint = True
        if message.get("type") == "assistant":
            body = message.get("message") or {}
            if body.get("stop_reason") == "max_tokens":
                self._result.truncated_hint = True
            for block in body.get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    self._result.partial_text = block["text"]
                elif block.get("type") == "tool_use" and block.get("name") == STRUCTURED_OUTPUT_TOOL:
                    self._result.partial_text = json.dumps(block.get("input"), ensure_ascii=False)

    def _read(self) -> None:
        self._buffer += bytes(self.process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            self.feed(raw.decode("utf-8", errors="replace"))

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._finish(CallResult(process_error="Claude Code could not be started."))

    def _on_exit(self, code: int) -> None:
        if self._done:
            return
        if self.process is not None:
            self._buffer += bytes(self.process.readAllStandardOutput()) + b"\n"
            self._read()
        result = self._result
        if result.duration_ms is None:
            result.duration_ms = int((time.monotonic() - self._started) * 1000)
        if not result.subtype and result.structured is None:
            stderr = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            result.process_error = (f"Claude Code ended without a result (exit code {code})."
                                    + (f" {stderr[:500]}" if stderr else ""))
        self._finish(result)

    def _finish(self, result: CallResult) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(result)


class GenerationRun(QObject):
    """One generation, possibly several calls. `progress(str)` and `finished(GenerationOutcome)`."""

    progress = Signal(str)
    finished = Signal(object)

    def __init__(self, request: GenerationRequest, work_root: Path, usage_path: Path | None = None,
                 call_factory=None, parent=None) -> None:
        super().__init__(parent)
        self.request = request
        self.work_root, self.usage_path = Path(work_root), usage_path
        self.planner = Planner(request)
        self.call_factory = call_factory or (lambda work_dir: ClaudeCall(work_dir, self.usage_path, self))
        self.call = None
        self.work_dir: Path | None = None
        self.cancelled = False

    def start(self) -> None:
        if preflight(self.request) is not None:
            self.finished.emit(self.planner.insufficient())
            return
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.work_dir = Path(tempfile.mkdtemp(prefix="generation-", dir=self.work_root))
        self._run(self.planner.first_call())

    _MESSAGES = {"first": "Asking Claude for test cases…", "retry": "Answer failed validation; asking Claude to fix it…",
                 "continuation": "Answer was cut off; asking Claude for the remaining test cases…"}

    def _run(self, spec: CallSpec) -> None:
        self.progress.emit(self._MESSAGES[spec.kind])
        call = self.call = self.call_factory(self.work_dir / f"call-{len(self.planner.attempts) + 1}")
        call.finished.connect(lambda result, kind=spec.kind: self._on_call(kind, result))
        call.start(spec)

    def _on_call(self, kind: str, result: CallResult) -> None:
        if self.cancelled:
            return
        self.progress.emit("Checking the answer…")
        step = self.planner.on_result(kind, result)
        if isinstance(step, CallSpec):
            self._run(step)
            return
        self._cleanup()
        self.finished.emit(step)

    def cancel(self) -> None:
        self.cancelled = True
        if self.call is not None:
            self.call.cancel()
        self._cleanup()

    def _cleanup(self) -> None:
        if self.work_dir is not None:
            shutil.rmtree(self.work_dir, ignore_errors=True)  # only the prompt file lives here
