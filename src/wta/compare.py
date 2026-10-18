"""Compare two runs of the same test: what changed since the previous run.

The previous run is the latest earlier finished run of the same scenario, or else of any scenario
added from the same library item (for example the same test in the previous build's session).
Steps are matched by their action text, then by position. Statuses are Claude's, as each run
reported them; your reviews apply to the latest results only.
"""

import re
from dataclasses import dataclass, field
from html import escape

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from wta.db import runs as run_store
from wta.db.database import Database
from wta.db.models import Run, RunOutcomeKind, RunStep, Scenario, StepStatus

REGRESSION, FIXED, CHANGED, NEW, GONE = "regression", "fixed", "changed", "new", "gone"
LABELS = {REGRESSION: "Regression", FIXED: "Fixed", CHANGED: "Changed", NEW: "New step", GONE: "No longer run"}
BAD = (StepStatus.FAIL, StepStatus.BLOCKED)


@dataclass
class StepChange:
    kind: str
    action: str
    before: StepStatus | None
    after: StepStatus | None
    order: int | None  # in the newer run, or the older one for steps no longer run
    bug: str = ""  # the newer bug for regressions, the older one for fixes


@dataclass
class Comparison:
    changes: list[StepChange] = field(default_factory=list)
    unchanged: int = 0

    def count(self, kind: str) -> int:
        return sum(1 for c in self.changes if c.kind == kind)

    def summary(self) -> str:
        words = {REGRESSION: ("regression", "regressions"), FIXED: ("fixed", "fixed"), CHANGED: ("changed", "changed"),
                 NEW: ("new step", "new steps"), GONE: ("no longer run", "no longer run")}
        parts = [f"{self.count(k)} {words[k][self.count(k) != 1]}" for k in words if self.count(k)]
        if not parts:
            return f"No changes ({self.unchanged} steps the same)"
        return ", ".join(parts) + f"; {self.unchanged} the same"


def _key(action: str) -> str:
    text = re.sub(r"^\s*(?:step\s*)?\d+\s*[.):-]\s*", "", action.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def compare_steps(before: list[RunStep], after: list[RunStep]) -> Comparison:
    result = Comparison()
    unmatched = list(before)
    pairs: list[tuple[RunStep | None, RunStep]] = []
    for step in after:  # same action text first
        match = next((b for b in unmatched if _key(b.action) and _key(b.action) == _key(step.action)), None)
        if match is not None:
            unmatched.remove(match)
        pairs.append((match, step))
    for index, (match, step) in enumerate(pairs):  # then the same position
        if match is None:
            match = next((b for b in unmatched if b.order == step.order), None)
            if match is not None:
                unmatched.remove(match)
                pairs[index] = (match, step)
    for old, new in pairs:
        if old is None:
            result.changes.append(StepChange(NEW, new.action, None, new.status, new.order, new.bug_title or ""))
        elif old.status == new.status:
            result.unchanged += 1
        elif old.status == StepStatus.PASS and new.status in BAD:
            result.changes.append(StepChange(REGRESSION, new.action, old.status, new.status, new.order,
                                             new.bug_title or ""))
        elif old.status in BAD and new.status == StepStatus.PASS:
            result.changes.append(StepChange(FIXED, new.action, old.status, new.status, new.order,
                                             old.bug_title or ""))
        else:
            result.changes.append(StepChange(CHANGED, new.action, old.status, new.status, new.order))
    for old in unmatched:
        result.changes.append(StepChange(GONE, old.action, old.status, None, old.order))
    order = {REGRESSION: 0, FIXED: 1, CHANGED: 2, NEW: 3, GONE: 4}
    result.changes.sort(key=lambda c: (order[c.kind], c.order or 0))
    return result


def previous_run(db: Database, run_id: int) -> Run | None:
    """The run to compare a run with (see the module docstring), with its scenario loaded."""
    with db.Session() as orm:
        run = orm.get(Run, run_id)
        if run is None:
            return None
        scenario = run.scenario
        same_test = Scenario.id == scenario.id
        if scenario.template_id is not None:
            same_test = or_(same_test, Scenario.template_id == scenario.template_id)
        return orm.scalars(
            select(Run).join(Scenario).options(selectinload(Run.scenario).selectinload(Scenario.session))
            .where(same_test, Run.id != run.id, Run.outcome == RunOutcomeKind.FINISHED,
                   Run.started_at < run.started_at, Run.steps.any())
            .order_by(Run.started_at.desc(), Run.id.desc()).limit(1)
        ).first()


def compare_with_previous(db: Database, run_id: int) -> tuple[Run | None, Comparison | None]:
    previous = previous_run(db, run_id)
    if previous is None:
        return None, None
    return previous, compare_steps(run_store.run_steps(db, previous.id), run_store.run_steps(db, run_id))


STATUS_TEXT = {StepStatus.PASS: "Pass", StepStatus.FAIL: "Fail", StepStatus.BLOCKED: "Blocked",
               StepStatus.NOT_RUN: "Not run", None: "—"}
COLORS = {REGRESSION: "#E5484D", FIXED: "#2E8B57", CHANGED: "#C77700", NEW: "#6B7280", GONE: "#6B7280"}


def comparison_html(previous: Run, comparison: Comparison, local_time) -> str:
    where = f" in session “{escape(previous.scenario.session.name)}”" if previous.scenario.session else ""
    parts = [f"<p>Compared with the run of {local_time(previous.started_at)}{where}: "
             f"<b>{escape(comparison.summary())}</b></p>",
             "<p style='color:gray'>Statuses are Claude's, as each run reported them.</p>"]
    if comparison.changes:
        parts.append("<table cellspacing='0' cellpadding='4'>"
                     "<tr><th align='left'>Change</th><th align='left'>Step</th><th align='left'>Before</th>"
                     "<th align='left'>Now</th><th align='left'>Bug</th></tr>")
        for change in comparison.changes:
            parts.append(
                f"<tr><td style='color:{COLORS[change.kind]}'><b>{LABELS[change.kind]}</b></td>"
                f"<td>{change.order or ''}. {escape(change.action)}</td><td>{STATUS_TEXT[change.before]}</td>"
                f"<td>{STATUS_TEXT[change.after]}</td><td>{escape(change.bug)}</td></tr>")
        parts.append("</table>")
    return "".join(parts)
