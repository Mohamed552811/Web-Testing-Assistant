"""HTML for the report, used by the PDF writer (Qt renders it)."""

import hashlib
from html import escape
from pathlib import Path

from wta.export.model import FINAL_COLUMNS, Report, summary_pairs

STATUS_COLORS = {"Pass": "#c6efce", "Fail": "#ffc7ce", "Bug": "#ffc7ce", "Blocked": "#ffeb9c", "Not run": "#e7e6e6"}


def _status(text: str) -> str:
    color = STATUS_COLORS.get(text.split(" ")[0], "#ffffff")
    return f'<td style="background:{color}">{escape(text)}</td>'


def _text(value) -> str:
    return escape(str(value or "")).replace("\n", "<br>")


def image_name(path: Path) -> str:
    """Resource name the PDF writer registers each screenshot under."""
    return f"shot://{hashlib.sha1(str(path).encode()).hexdigest()[:16]}.png"


def report_html(report: Report, image_width: int = 360) -> str:
    out = [
        "<html><head><style>",
        "body { font-family: sans-serif; font-size: 9pt; }",
        "h1 { font-size: 16pt; } h2 { font-size: 13pt; margin-top: 14pt; } h3 { font-size: 10pt; }",
        "table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 3px; vertical-align: top; }",
        "th { background: #203040; color: white; }",
        "</style></head><body>",
        f"<h1>{escape(report.title)}</h1>",
        '<table cellspacing="0">',
    ]
    out += [f"<tr><td><b>{escape(k)}</b></td><td>{_text(v)}</td></tr>" for k, v in summary_pairs(report)]
    out.append("</table>")

    out.append("<h2>Final status by type</h2><table cellspacing='0'><tr><th>Type</th>")
    out += [f"<th>{escape(c)}</th>" for c in FINAL_COLUMNS] + ["<th>Total</th></tr>"]
    for kind, counter in report.counts.items():
        out.append(f"<tr><td>{escape(kind)}</td>")
        out += [f"<td>{counter.get(c, 0)}</td>" for c in FINAL_COLUMNS]
        out.append(f"<td>{sum(counter.values())}</td></tr>")
    out.append("</table>")

    if report.scenarios:
        out.append("<h2>Scenarios</h2><table cellspacing='0' width='100%'><tr><th>Batch</th><th>Scenario</th>"
                   "<th>Steps</th><th>Expected result</th><th>Test data</th></tr>")
        for info in report.scenarios:
            out.append(f"<tr><td>{info.batch}</td><td>{_text(info.name)}</td><td>{_text(info.numbered_steps())}</td>"
                       f"<td>{_text(info.expected_result)}</td><td>{_text(info.test_data)}</td></tr>")
        out.append("</table>")

    out.append("<h2>Results</h2><table cellspacing='0' width='100%'><tr>")
    for name in ("Type", "Scenario / page", "#", "Action / input", "Expected", "Actual", "Claude", "Final",
                 "Review comment"):
        out.append(f"<th>{name}</th>")
    out.append("</tr>")
    for step in report.rows:
        out.append(
            f"<tr><td>{_text(step.kind)} {step.batch}</td><td>{_text(step.scenario)}</td><td>{step.order}</td>"
            f"<td>{_text(step.action)}</td><td>{_text(step.expected)}</td><td>{_text(step.actual)}</td>"
            f"{_status(step.claude_status)}{_status(step.final_status)}<td>{_text(step.comment)}</td></tr>"
        )
    out.append("</table>")

    out.append(f"<h2>Bugs ({len(report.bugs)})</h2>")
    if not report.bugs:
        out.append("<p>No bugs in this report.</p>")
    for i, bug in enumerate(report.bugs, start=1):
        out.append(f"<h3>{i}. {escape(bug.title)}</h3>")
        out.append("<table cellspacing='0'>")
        for key, value in (("Severity", bug.severity), ("Priority", bug.priority), ("Review state", bug.review_state),
                           ("Where", f"{bug.kind}: {bug.scenario}, step {bug.step_order}"),
                           ("Repro steps", bug.tfs_repro_steps), ("Bug report", bug.report_text)):
            out.append(f"<tr><td><b>{key}</b></td><td>{_text(value)}</td></tr>")
        out.append("</table>")
        for shot in bug.screenshots:
            out.append(f'<p><img src="{image_name(shot)}" width="{image_width}"></p>')

    others = other_screenshot_rows(report)
    if others:
        out.append("<h2>Other screenshots</h2>")
        for step in others:
            out.append(f"<h3>{escape(step.scenario)}, step {step.order}: {escape(step.final_status)}</h3>")
            out += [f'<p><img src="{image_name(s)}" width="{image_width}"></p>' for s in step.screenshots]
    out.append("</body></html>")
    return "\n".join(out)


def other_screenshot_rows(report: Report):
    """Steps with screenshots that are not already shown with a bug (e.g. Blocked steps)."""
    bug_steps = {(b.scenario, b.step_order) for b in report.bugs}
    return [s for s in report.rows if s.screenshots and (s.scenario, s.order) not in bug_steps]


def all_screenshots(report: Report) -> list[Path]:
    seen: list[Path] = []
    for path in [p for s in report.rows for p in s.screenshots] + [p for b in report.bugs for p in b.screenshots]:
        if path not in seen:
            seen.append(path)
    return seen
