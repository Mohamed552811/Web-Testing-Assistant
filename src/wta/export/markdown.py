"""Markdown export in the user's own report layout: a folder with SUMMARY.md, one
BUG-NNN-<title>.md per bug, and the screenshots they refer to.

Key Findings, Recommendations, Impact and Notes are left empty for the user to fill in.
"""

import re
import shutil
from datetime import datetime
from itertools import groupby
from pathlib import Path

from wta.export.model import BugRow, Report, StepRow
from wta.step_table import PENDING, RERUN

UNREVIEWED = (PENDING, RERUN)


def _cell(value) -> str:
    """Safe inside a Markdown table cell."""
    return str(value if value is not None else "").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def slug(title: str, max_length: int = 50) -> str:
    """'Price List "x" Not Found' -> 'price-list-x-not-found'."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    text = ""
    for word in words:
        candidate = f"{text}-{word}" if text else word
        if len(candidate) > max_length:
            break
        text = candidate
    return text or "bug"


def severity_word(severity: str) -> str:
    """'2 - High' -> 'High'."""
    return severity.split("-", 1)[1].strip() if "-" in severity else severity


def step_status(row: StepRow) -> str:
    """The reviewed status in capitals; Claude's status, marked as such, while not reviewed."""
    if row.final_status in UNREVIEWED:
        note = "re-run requested" if row.final_status == RERUN else "not reviewed"
        return f"{_status_word(row.claude_status)} ({note})"
    return _status_word(row.final_status)


def _status_word(label: str) -> str:
    return {"Bug": "FAIL"}.get(label, label.upper())


def overall_result(rows: list[StepRow]) -> str:
    if not rows:
        return "NOT RUN"
    statuses = [_status_word(r.final_status if r.final_status not in UNREVIEWED else r.claude_status) for r in rows]
    if "FAIL" in statuses:
        result = "FAIL"
    elif "BLOCKED" in statuses:
        result = "BLOCKED"
    elif all(s == "PASS" for s in statuses):
        result = "PASS"
    else:
        result = "INCOMPLETE"
    pending = sum(1 for r in rows if r.final_status in UNREVIEWED)
    return f"{result} ({pending} step{'s' if pending != 1 else ''} not reviewed yet)" if pending else result


def bug_status(bug: BugRow) -> str:
    return {"Confirmed": "Open", "Not reviewed yet": "Open (not reviewed)",
            "Re-run requested": "Open (re-run requested)"}.get(bug.review_state, "Open")


def _bullets(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line if line.startswith(("- ", "* ")) else f"- {line}" for line in lines]


def _date(report: Report) -> str:
    return (report.last_run_at or report.exported_at)[:10] or datetime.now().strftime("%Y-%m-%d")


class _Attachments:
    """Copies screenshots into the report folder under readable names."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.files: dict[Path, str] = {}
        self.captions: dict[str, str] = {}

    def add(self, source: Path, name: str, caption: str) -> str:
        if source in self.files:
            return self.files[source]
        target = self.folder / f"{name}{source.suffix.lower() or '.png'}"
        shutil.copy2(source, target)
        self.files[source] = target.name
        self.captions[target.name] = caption
        return target.name


def _bug_file(report: Report, number: int, bug: BugRow, attachments: _Attachments) -> tuple[str, str]:
    bug_id = f"BUG-{number:03d}"
    shots = [
        attachments.add(shot, f"{bug_id}-screenshot-{i}" if len(bug.screenshots) > 1 else f"{bug_id}-screenshot",
                        f"{bug.scenario}, step {bug.step_order}: {bug.actual or bug.step_action}")
        for i, shot in enumerate(bug.screenshots, start=1)
    ]
    browser = report.browser_version or "Chromium"
    lines = [
        f"# {bug_id}: {bug.title}",
        "",
        f"## Severity: {severity_word(bug.severity)}",
        "",
        "## Summary",
        bug.report_text or bug.actual or "",
        "",
        "## Environment",
        f"- **URL:** {bug.page_url or report.target_url}",
        f"- **Browser:** {browser} (embedded browser, driven by Playwright)",
        f"- **User:** {report.test_account_email}",
        f"- **Date:** {_date(report)}",
        "",
        "## Steps to Reproduce",
        bug.repro_steps,
        "",
        "## Expected Behavior",
        *_bullets(bug.expected),
        "",
        "## Actual Behavior",
        *_bullets(bug.actual),
        "",
        "## Screenshots",
        *[f"- See: {name} ({attachments.captions[name]})" for name in shots],
        "",
        "## Impact",
        "",
        "## Notes",
        *([f"- Review: {bug.review_comment}"] if bug.review_comment else []),
        "",
    ]
    return f"{bug_id}-{slug(bug.title)}.md", "\n".join(lines)


def write_markdown(report: Report, path: Path) -> Path:
    """Write the report folder next to `path` (named after it) and return SUMMARY.md's path."""
    folder = path.with_suffix("") if path.suffix else path
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    attachments = _Attachments(folder)

    bug_files = []
    for number, bug in enumerate(report.bugs, start=1):
        name, text = _bug_file(report, number, bug, attachments)
        (folder / name).write_text(text, encoding="utf-8")
        bug_files.append((f"BUG-{number:03d}", bug, name))

    lines = [
        f"# {report.session_name} Test Report",
        "",
        "## Test Information",
        f"- **Date:** {_date(report)}",
        f"- **Tester:** {report.tester}",
        f"- **Application:** {report.application}",
        f"- **URL:** {report.target_url}",
        f"- **User:** {report.test_account_email}",
        "",
        "## Test Scope",
        *[f"- {item}" for item in report.test_scope],
        "",
        "## Test Results",
        "",
    ]
    cases = [list(rows) for _, rows in groupby(report.rows, key=lambda r: (r.kind, r.batch, r.scenario))]
    asked = {(s.kind, s.batch, s.name): s for s in report.scenarios}  # the user's steps and expected result
    for number, rows in enumerate(cases, start=1):
        first = rows[0]
        label = "Smoke Test" if first.kind == "Smoke test" else "Test Case"
        lines += [f"### {label} {number:03d}: {first.scenario}", ""]
        info = asked.get((first.kind, first.batch, first.scenario))
        if info is not None:
            if info.steps:
                lines += ["**Steps:**", *[f"{i}. {s}" for i, s in enumerate(info.steps, start=1)], ""]
            if info.expected_result:
                lines += [f"**Expected result:** {info.expected_result}", ""]
            if info.test_data:
                lines += [f"**Test data:** {info.test_data}", ""]
        lines += [
            "| Test Step | Expected Result | Actual Result | Status |",
            "|-----------|-----------------|---------------|--------|",
            *[f"| {_cell(r.action)} | {_cell(r.expected)} | {_cell(r.actual)} | {step_status(r)} |" for r in rows],
            "",
        ]
        for row in rows:
            for i, shot in enumerate(row.screenshots, start=1):
                suffix = f"-{i}" if len(row.screenshots) > 1 else ""
                attachments.add(shot, f"TC{number:03d}-step-{row.order}{suffix}",
                                f"{first.scenario}, step {row.order}: {row.action}")
    if not cases:
        lines += ["No steps have been run yet.", ""]

    lines += ["## Overall Test Result", f"**{overall_result(report.rows)}**", "", "## Key Findings", "",
              "## Bugs Found", ""]
    if bug_files:
        lines += ["| Bug ID | Title | Severity | Status |", "|--------|-------|----------|--------|"]
        lines += [f"| [{bug_id}]({name}) | {_cell(bug.title)} | {severity_word(bug.severity)} | {bug_status(bug)} |"
                  for bug_id, bug, name in bug_files]
    else:
        lines.append("No bugs found.")
    lines += ["", "## Recommendations", "", "## Attachments"]
    lines += [f"- {name} - {caption}" for name, caption in attachments.captions.items()]
    lines.append("")

    summary = folder / "SUMMARY.md"
    summary.write_text("\n".join(lines), encoding="utf-8")
    return summary
