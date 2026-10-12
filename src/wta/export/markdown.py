"""Markdown report. Screenshots are copied next to the file so the folder can be handed over as is."""

import shutil
from pathlib import Path

from wta.export.html import other_screenshot_rows
from wta.export.model import FINAL_COLUMNS, Report, summary_pairs


def _cell(value) -> str:
    """Safe inside a Markdown table cell."""
    return str(value if value is not None else "").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _table(headers, rows) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def write_markdown(report: Report, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    files_dir = path.with_name(path.stem + "_files")
    copied: dict[Path, str] = {}

    def link(shot: Path) -> str:
        if shot not in copied:
            files_dir.mkdir(exist_ok=True)
            target = files_dir / f"{len(copied) + 1:03d}-{shot.name}"
            shutil.copy2(shot, target)
            copied[shot] = f"{files_dir.name}/{target.name}"
        return copied[shot]

    lines = [f"# {report.title}", ""]
    lines += _table(("Item", "Value"), summary_pairs(report)) + [""]

    lines += ["## Final status by type", ""]
    lines += _table(("Type",) + FINAL_COLUMNS + ("Total",), [
        (kind, *(counter.get(c, 0) for c in FINAL_COLUMNS), sum(counter.values()))
        for kind, counter in report.counts.items()
    ]) + [""]

    lines += ["## Results", ""]
    lines += _table(
        ("Type", "Scenario / page", "#", "Action / input", "Expected", "Actual", "Claude", "Final", "Review comment"),
        [(f"{s.kind} {s.batch}", s.scenario, s.order, s.action, s.expected, s.actual, s.claude_status,
          s.final_status, s.comment) for s in report.rows],
    ) + [""]

    lines += [f"## Bugs ({len(report.bugs)})", ""]
    if not report.bugs:
        lines += ["No bugs in this report.", ""]
    for i, bug in enumerate(report.bugs, start=1):
        lines += [f"### {i}. {bug.title}", ""]
        lines += _table(("Field", "Value"), [
            ("Severity", bug.severity), ("Priority", bug.priority), ("Review state", bug.review_state),
            ("Where", f"{bug.kind}: {bug.scenario}, step {bug.step_order}"),
        ]) + [""]
        lines += ["**Repro steps**", "", bug.tfs_repro_steps, ""]
        if bug.report_text:
            lines += ["**Bug report**", "", bug.report_text, ""]
        lines += [f"![Screenshot for bug {i}]({link(shot)})" for shot in bug.screenshots] + [""]

    others = other_screenshot_rows(report)
    if others:
        lines += ["## Other screenshots", ""]
        for step in others:
            lines += [f"### {step.scenario}, step {step.order}: {step.final_status}", ""]
            lines += [f"![{step.scenario} step {step.order}]({link(shot)})" for shot in step.screenshots] + [""]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
