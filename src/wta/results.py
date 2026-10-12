"""results.json: what Claude writes in the run folder, and loading it into the database.

Claude rewrites the whole file after every step, so the app can show steps live.
Loading replaces Claude's side of the scenario's steps; reviews are never touched,
and a step that already has a review is never deleted.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from wta.db.database import Database
from wta.db.models import (
    BUG_PRIORITIES,
    BUG_SEVERITIES,
    ActionLog,
    Bug,
    CaptureLog,
    Screenshot,
    Step,
    StepStatus,
    utcnow,
)

RESULTS_FILE = "results.json"
SCREENSHOT_DIR = "screenshots"

# Shown to Claude in the prompt. Keep in sync with parse_results().
SCHEMA_EXAMPLE = {
    "scenario": "Name of the scenario",
    "state": "running",
    "summary": "One or two sentences on the outcome (fill in when done)",
    "notices": ["Anything the user must know, e.g. instructions found on a page, captcha seen"],
    "steps": [
        {
            "order": 1,
            "screen": "Login page",
            "element": "Email field",
            "action": "Type the test account email",
            "expected": "Email is accepted",
            "actual": "Email entered",
            "status": "pass",
            "reasoning": "Short explanation of why this status was chosen",
            "page_url": "https://app.example.test/login",
            "console_errors": [],
            "failed_requests": [],
            "actions": [{"action": "type", "target": "Email textbox", "outcome": "ok"}],
            "screenshot": None,
            "bug": None,
        }
    ],
}
BUG_EXAMPLE = {
    "title": "Short bug title",
    "severity": "2 - High",
    "priority": 2,
    "repro_steps": "1. ...\n2. ...",
    "expected": "What should happen",
    "actual": "What happened",
    "report_text": "Full bug report a developer can act on",
}

_STATUS_ALIASES = {"pass": "pass", "passed": "pass", "fail": "fail", "failed": "fail", "bug": "fail",
                   "blocked": "blocked", "not_run": "not_run", "not run": "not_run", "skipped": "not_run"}


@dataclass
class BugResult:
    title: str
    severity: str
    priority: int
    repro_steps: str
    expected: str
    actual: str
    report_text: str


@dataclass
class StepResult:
    order: int
    screen: str
    element: str
    action: str
    expected: str
    actual: str
    status: StepStatus
    reasoning: str
    page_url: str
    console_errors: list[str]
    failed_requests: list[str]
    actions: list[dict]
    screenshot: str | None
    bug: BugResult | None


@dataclass
class ConfirmationRequest:
    step: int | None
    action: str
    reason: str


AWAITING_CONFIRMATION = "awaiting_confirmation"


@dataclass
class Results:
    scenario: str
    state: str
    summary: str
    notices: list[str]
    steps: list[StepResult]
    problems: list[str] = field(default_factory=list)  # what was wrong in the file, for the run panel
    confirmation: ConfirmationRequest | None = None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value))


def _strings(value) -> list[str]:
    return [_text(v) for v in value if _text(v)] if isinstance(value, list) else []


def _bug(raw, problems: list[str], order: int) -> BugResult | None:
    if not isinstance(raw, dict):
        return None
    severity = _text(raw.get("severity"))
    if severity not in BUG_SEVERITIES:
        problems.append(f"Step {order}: unknown bug severity {severity!r}, using 3 - Medium")
        severity = "3 - Medium"
    priority = raw.get("priority")
    if priority not in BUG_PRIORITIES:
        problems.append(f"Step {order}: unknown bug priority {priority!r}, using 2")
        priority = 2
    return BugResult(
        title=_text(raw.get("title"))[:255] or "Untitled bug",
        severity=severity,
        priority=priority,
        repro_steps=_text(raw.get("repro_steps")),
        expected=_text(raw.get("expected")),
        actual=_text(raw.get("actual")),
        report_text=_text(raw.get("report_text")),
    )


def parse_results(data) -> Results:
    """Validate Claude's results.json content. Bad steps are skipped and reported in `problems`."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return Results("", "invalid", "", [], [], ["results.json is not a JSON object"])
    steps: list[StepResult] = []
    seen: set[int] = set()
    for index, raw in enumerate(data.get("steps") or [], start=1):
        if not isinstance(raw, dict):
            problems.append(f"Step entry {index} is not an object")
            continue
        order = raw.get("order", index)
        if not isinstance(order, int) or order < 1 or order in seen:
            problems.append(f"Step entry {index}: bad or duplicate order {order!r}")
            continue
        status_text = _text(raw.get("status")).lower()
        if status_text not in _STATUS_ALIASES:
            problems.append(f"Step {order}: unknown status {status_text!r}, using not_run")
        status = StepStatus(_STATUS_ALIASES.get(status_text, "not_run"))
        action = _text(raw.get("action"))
        if not action:
            problems.append(f"Step {order}: missing action")
            continue
        seen.add(order)
        actions = [
            {"action": _text(a.get("action"))[:100], "target": _text(a.get("target")), "outcome": _text(a.get("outcome"))}
            for a in raw.get("actions") or []
            if isinstance(a, dict) and _text(a.get("action"))
        ]
        steps.append(
            StepResult(
                order=order,
                screen=_text(raw.get("screen"))[:200],
                element=_text(raw.get("element"))[:300],
                action=action,
                expected=_text(raw.get("expected")),
                actual=_text(raw.get("actual")),
                status=status,
                reasoning=_text(raw.get("reasoning")),
                page_url=_text(raw.get("page_url")),
                console_errors=_strings(raw.get("console_errors")),
                failed_requests=_strings(raw.get("failed_requests")),
                actions=actions,
                screenshot=_text(raw.get("screenshot")) or None,
                bug=_bug(raw.get("bug"), problems, order) if status == StepStatus.FAIL else None,
            )
        )
    steps.sort(key=lambda s: s.order)
    state = _text(data.get("state")) or "running"
    confirmation = None
    raw_request = data.get("confirmation_request")
    if state == AWAITING_CONFIRMATION:
        if isinstance(raw_request, dict) and _text(raw_request.get("action")):
            step = raw_request.get("step")
            confirmation = ConfirmationRequest(
                step=step if isinstance(step, int) else None,
                action=_text(raw_request.get("action")),
                reason=_text(raw_request.get("reason")),
            )
        else:
            problems.append("state is awaiting_confirmation but confirmation_request is missing")
    return Results(
        scenario=_text(data.get("scenario")),
        state=state,
        summary=_text(data.get("summary")),
        notices=_strings(data.get("notices")),
        steps=steps,
        problems=problems,
        confirmation=confirmation,
    )


def read_results(run_dir: Path) -> Results | None:
    """None when there is no file yet or it is mid-write / not valid JSON."""
    try:
        return parse_results(json.loads((run_dir / RESULTS_FILE).read_text("utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None


def screenshot_path(run_dir: Path, relative: str | None) -> Path | None:
    """The screenshot file if it exists inside the run folder; anything outside is ignored."""
    if not relative:
        return None
    path = (run_dir / relative).resolve()
    root = run_dir.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def apply_results(
    db: Database,
    scenario_id: int,
    results: Results,
    run_dir: Path,
    screenshot_statuses: frozenset[StepStatus] = frozenset({StepStatus.BLOCKED, StepStatus.FAIL}),
    redact=lambda text: text,
) -> list[str]:
    """Write Claude's results into the scenario's steps. Returns extra problems found."""
    problems: list[str] = []
    with db.Session.begin() as orm:
        existing = {s.order: s for s in orm.scalars(select(Step).where(Step.scenario_id == scenario_id))}
        wanted = {r.order for r in results.steps}
        for order, step in existing.items():
            if order not in wanted and not step.reviews:
                orm.delete(step)
        orm.flush()

        for r in results.steps:
            step = existing.get(r.order)
            if step is None:
                step = Step(scenario_id=scenario_id, order=r.order)
                orm.add(step)
            step.screen_name = redact(r.screen)
            step.element = redact(r.element)
            step.action = redact(r.action)
            step.expected_result = redact(r.expected)
            step.actual_result = redact(r.actual)
            step.status = r.status
            step.reasoning = redact(r.reasoning)

            step.capture_logs[:] = [
                CaptureLog(console_errors=[redact(e) for e in r.console_errors],
                           failed_requests=[redact(e) for e in r.failed_requests], page_url=r.page_url)
            ]
            now = utcnow()
            step.action_logs[:] = [
                ActionLog(action=a["action"], target=redact(a["target"]), outcome=redact(a["outcome"]), timestamp=now)
                for a in r.actions
            ]
            step.bugs[:] = (
                [Bug(title=redact(r.bug.title), severity=r.bug.severity, priority=r.bug.priority,
                     repro_steps=redact(r.bug.repro_steps), expected=redact(r.bug.expected),
                     actual=redact(r.bug.actual), report_text=redact(r.bug.report_text))]
                if r.bug else []
            )
            if r.status == StepStatus.FAIL and r.bug is None:
                problems.append(f"Step {r.order} is a bug but has no bug report yet")

            shot = screenshot_path(run_dir, r.screenshot) if r.status in screenshot_statuses else None
            if r.status in screenshot_statuses and r.screenshot and shot is None:
                problems.append(f"Step {r.order}: screenshot {r.screenshot!r} not found in the run folder")
            if r.status in screenshot_statuses and not r.screenshot:
                problems.append(f"Step {r.order} is {r.status.value} but has no screenshot")
            current = [s for s in step.screenshots if shot is not None and Path(s.file_path) == shot]
            step.screenshots[:] = current or (
                [Screenshot(file_path=str(shot), captured_at=datetime.fromtimestamp(shot.stat().st_mtime, UTC).replace(tzinfo=None))]
                if shot else []
            )
    return problems
