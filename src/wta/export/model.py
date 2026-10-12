"""The report data every export format is built from.

It reads the database once. Each row carries both Claude's status and the user's reviewed
final status, so a report never hides what Claude found. The test account email may appear
(the user's reports name the account); the password never does.
"""

import re
from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path

from wta.db import Database, store
from wta.db import runs as run_store
from wta.db.models import ReviewDecision, ScenarioKind, Step, StepStatus
from wta.step_table import PENDING, RERUN, SMOKE_LABELS, STATUS_LABELS

FINAL_COLUMNS = ("Pass", "Fail / Bug", "Blocked", "Not run", PENDING, RERUN)
KIND_LABELS = {ScenarioKind.SCENARIO: "Scenario", ScenarioKind.SMOKE: "Smoke test"}


def _local(value: datetime | None) -> str:
    return value.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M") if value else ""


@dataclass
class StepRow:
    kind: str
    batch: int
    scenario: str
    order: int
    screen: str
    element: str
    action: str
    expected: str
    actual: str
    claude_status: str
    final_status: str  # the reviewed status, or Pending review / Re-run requested
    final_key: str  # one of FINAL_COLUMNS, for counting
    decision: str
    comment: str
    reviewed_at: str
    reasoning: str
    page_url: str
    console_errors: list[str]
    screenshots: list[Path]


@dataclass
class BugRow:
    title: str
    severity: str
    priority: int
    repro_steps: str
    expected: str
    actual: str
    report_text: str
    kind: str
    scenario: str
    step_order: int
    review_state: str
    page_url: str
    screenshots: list[Path]
    step_action: str = ""
    review_comment: str = ""

    @property
    def tfs_repro_steps(self) -> str:
        """Repro Steps with expected and actual folded in, so a work-item import keeps them."""
        parts = [self.repro_steps.strip()]
        if self.expected:
            parts.append(f"Expected: {self.expected}")
        if self.actual:
            parts.append(f"Actual: {self.actual}")
        if self.page_url:
            parts.append(f"Page: {self.page_url}")
        return "\n\n".join(p for p in parts if p)


@dataclass
class Report:
    session_name: str
    target_url: str
    allowed_domains: list[str]
    browser_version: str
    viewport: str
    created_at: str
    exported_at: str
    rows: list[StepRow]
    bugs: list[BugRow]
    counts: dict[str, Counter] = field(default_factory=dict)  # kind label -> final_key -> n
    application: str = ""
    test_scope: list[str] = field(default_factory=list)
    test_account_email: str = ""
    tester: str = ""
    last_run_at: str = ""  # local date and time of the latest run, "" if never run
    run_count: int = 0
    output_tokens: int = 0
    five_hour_latest: float | None = None
    seven_day_latest: float | None = None

    @property
    def title(self) -> str:
        return f"Test report — {self.session_name}"


def _final(step: Step, labels: dict) -> tuple[str, str, str, str, str]:
    """(final status text, count key, decision, comment, reviewed at)."""
    review = step.latest_review
    if review is None:
        return PENDING, PENDING, "", "", ""
    when = _local(review.reviewed_at)
    if review.decision == ReviewDecision.RERUN_REQUESTED:
        return RERUN, RERUN, "Re-run requested", review.comment, when
    key = {StepStatus.PASS: "Pass", StepStatus.FAIL: "Fail / Bug", StepStatus.BLOCKED: "Blocked",
           StepStatus.NOT_RUN: "Not run"}[review.final_status]
    decision = "Approved" if review.decision == ReviewDecision.APPROVED else "Overridden"
    return labels[review.final_status], key, decision, review.comment, when


PASSWORD_PLACEHOLDER = "the test password"


def _redact_rows(items: list, replacements: list[tuple[str, str]]) -> None:
    """Replace secrets (the test password) in every text field. Reports are handed over; the password stays local."""
    if not replacements:
        return
    pattern = re.compile("|".join(re.escape(old) for old, _ in replacements), re.IGNORECASE)
    lookup = {old.lower(): new for old, new in replacements}

    def clean(text: str) -> str:
        return pattern.sub(lambda m: lookup[m.group(0).lower()], text)

    for item in items:
        for f in fields(item):
            value = getattr(item, f.name)
            if isinstance(value, str):
                setattr(item, f.name, clean(value))
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                setattr(item, f.name, [clean(v) for v in value])


def build_report(db: Database, session_id: int, tester: str = "") -> Report:
    session = store.load_session(db, session_id)
    rows: list[StepRow] = []
    bugs: list[BugRow] = []
    counts: dict[str, Counter] = {}
    ordered = sorted(session.scenarios, key=lambda s: (s.kind != ScenarioKind.SCENARIO, s.batch_number, s.position))
    for scenario in ordered:
        kind = KIND_LABELS[scenario.kind]
        labels = SMOKE_LABELS if scenario.kind == ScenarioKind.SMOKE else STATUS_LABELS
        counter = counts.setdefault(kind, Counter())
        for step in scenario.steps:
            final_text, final_key, decision, comment, reviewed_at = _final(step, labels)
            counter[final_key] += 1
            capture = step.capture_logs[0] if step.capture_logs else None
            shots = [Path(s.file_path) for s in step.screenshots if Path(s.file_path).is_file()]
            rows.append(StepRow(
                kind=kind, batch=scenario.batch_number, scenario=scenario.name, order=step.order,
                screen=step.screen_name, element=step.element, action=step.action,
                expected=step.expected_result, actual=step.actual_result,
                claude_status=labels[step.status], final_status=final_text, final_key=final_key,
                decision=decision, comment=comment, reviewed_at=reviewed_at, reasoning=step.reasoning,
                page_url=capture.page_url if capture else "",
                console_errors=list(capture.console_errors) if capture else [],
                screenshots=shots,
            ))
            # A bug is reported unless the user reviewed the step as something other than Fail.
            if step.final_status is not None and step.final_status != StepStatus.FAIL:
                continue
            state = "Confirmed" if step.final_status == StepStatus.FAIL else (
                "Re-run requested" if step.rerun_requested else "Not reviewed yet")
            for bug in step.bugs:
                bugs.append(BugRow(
                    title=bug.title, severity=bug.severity, priority=bug.priority, repro_steps=bug.repro_steps,
                    expected=bug.expected, actual=bug.actual, report_text=bug.report_text, kind=kind,
                    scenario=scenario.name, step_order=step.order, review_state=state,
                    page_url=capture.page_url if capture else "", screenshots=shots,
                    step_action=step.action, review_comment=comment,
                ))

    replacements = [(session.test_account_password, PASSWORD_PLACEHOLDER)] if session.test_account_password else []
    _redact_rows(rows + bugs, replacements)

    runs = run_store.list_runs(db, session_id)
    latest = next((r for r in runs if r.five_hour_seen is not None), None)
    return Report(
        session_name=session.name,
        target_url=session.target_url,
        allowed_domains=list(session.allowed_domains),
        browser_version=session.browser_version or "",
        viewport=f"{session.viewport_width} × {session.viewport_height}",
        created_at=_local(session.created_at),
        exported_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows=rows,
        bugs=bugs,
        counts=counts,
        run_count=len(runs),
        output_tokens=sum(r.output_tokens or 0 for r in runs),
        five_hour_latest=latest.five_hour_seen if latest else None,
        seven_day_latest=latest.seven_day_seen if latest else None,
        application=session.application or "",
        test_scope=[line for line in (session.test_scope or "").splitlines() if line.strip()],
        test_account_email=session.test_account_email or "",
        tester=tester,
        last_run_at=_local(runs[0].started_at) if runs else "",
    )


def summary_pairs(report: Report) -> list[tuple[str, str]]:
    """Key facts shown at the top of every format."""
    usage = []
    if report.five_hour_latest is not None:
        usage.append(f"5-hour {report.five_hour_latest:.0f}%")
    if report.seven_day_latest is not None:
        usage.append(f"weekly {report.seven_day_latest:.0f}%")
    return [
        ("Session", report.session_name),
        ("Target URL", report.target_url),
        ("Allowed domains", ", ".join(report.allowed_domains)),
        ("Browser", report.browser_version or "not recorded"),
        ("Viewport", report.viewport),
        ("Session created", report.created_at),
        ("Report exported", report.exported_at),
        ("Claude Code runs", str(report.run_count)),
        ("Output tokens (all runs)", str(report.output_tokens)),
        ("Plan usage at last run", ", ".join(usage) or "not reported"),
        ("Bugs in this report", str(len(report.bugs))),
    ]
