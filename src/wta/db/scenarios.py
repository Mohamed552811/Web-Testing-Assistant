"""Batches, scenarios, steps and reviews within a test session."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from wta.db.database import Database
from wta.urls import normalize_url
from wta.db.models import (
    BUG_PRIORITIES,
    BUG_SEVERITIES,
    MAX_SCENARIOS_PER_BATCH,
    Bug,
    Review,
    ReviewDecision,
    Scenario,
    ScenarioKind,
    Screenshot,
    SmokePage,
    Step,
    StepStatus,
    TestAccount,
    TestSession,
)


_STEP_NUMBER = re.compile(r"^\s*(?:step\s*)?\d+\s*[.):-]\s*", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-*•]\s+")


def step_lines(text: str | None) -> list[str]:
    """The steps as a plain list: one per non-empty line, any numbering or bullet removed."""
    lines = []
    for line in (text or "").splitlines():
        line = _BULLET.sub("", _STEP_NUMBER.sub("", line)).strip()
        if line:
            lines.append(line)
    return lines


def numbered_steps(text: str | None) -> str:
    """Steps renumbered 1., 2., … whatever numbering (or none) they were typed with."""
    return "\n".join(f"{i}. {line}" for i, line in enumerate(step_lines(text), start=1))


@dataclass
class ScenarioInput:
    name: str
    steps: str = ""
    expected_result: str = ""
    test_data: str = ""

    def cleaned(self) -> "ScenarioInput":
        name = self.name.strip()
        if not name:
            raise ValueError("Scenario name is required.")
        return ScenarioInput(name=name, steps=numbered_steps(self.steps),
                             expected_result=self.expected_result.strip(), test_data=self.test_data.strip())

    @classmethod
    def of(cls, scenario: Scenario) -> "ScenarioInput":
        return cls(scenario.name, scenario.test_steps or "", scenario.expected_result or "", scenario.test_data)


@dataclass
class StepInput:
    """Claude's side of a step. Entered by hand until the runner exists."""

    screen_name: str = ""
    element: str = ""
    action: str = ""
    expected_result: str = ""
    actual_result: str = ""
    status: StepStatus = StepStatus.NOT_RUN
    reasoning: str = ""

    def cleaned(self) -> "StepInput":
        values = {k: v.strip() if isinstance(v, str) else v for k, v in vars(self).items()}
        values["status"] = StepStatus(self.status)
        if not values["action"]:
            raise ValueError("Step action or input is required.")
        return StepInput(**values)


class BatchFullError(ValueError):
    pass


class BatchNotReviewedError(ValueError):
    pass


def _get(orm: OrmSession, cls, ident: int):
    obj = orm.get(cls, ident)
    if obj is None:
        raise LookupError(f"{cls.__name__} {ident} not found")
    return obj


# --- batches -----------------------------------------------------------------
# Scenario batches and smoke-test batches are numbered separately (kind is part of the slot).

_SCENARIO = ScenarioKind.SCENARIO


def batch_numbers(db: Database, session_id: int, kind: ScenarioKind = _SCENARIO) -> list[int]:
    """Batch numbers that have at least one scenario of this kind, ascending."""
    with db.Session() as orm:
        return list(
            orm.scalars(
                select(Scenario.batch_number)
                .where(Scenario.session_id == session_id, Scenario.kind == kind)
                .distinct()
                .order_by(Scenario.batch_number)
            )
        )


def batch_scenarios(db: Database, session_id: int, batch: int, kind: ScenarioKind = _SCENARIO) -> list[Scenario]:
    with db.Session() as orm:
        scenarios = _batch_scenarios(orm, session_id, batch, kind)
        for scenario in scenarios:
            scenario.smoke_page  # load before the ORM session closes
        return scenarios


def _batch_scenarios(orm: OrmSession, session_id: int, batch: int, kind: ScenarioKind) -> list[Scenario]:
    return list(
        orm.scalars(
            select(Scenario)
            .where(Scenario.session_id == session_id, Scenario.batch_number == batch, Scenario.kind == kind)
            .order_by(Scenario.position)
        )
    )


def batch_is_reviewed(db: Database, session_id: int, batch: int, kind: ScenarioKind = _SCENARIO) -> bool:
    """True when every scenario in the batch has steps and every step has a final review."""
    with db.Session() as orm:
        scenarios = _batch_scenarios(orm, session_id, batch, kind)
        if not scenarios:
            return False
        for scenario in scenarios:
            if not scenario.steps or any(step.final_status is None for step in scenario.steps):
                return False
        return True


def next_batch_number(db: Database, session_id: int, kind: ScenarioKind = _SCENARIO) -> int:
    """The batch a new batch of this kind would get. Raises if the latest one is not fully reviewed.

    The first batch is always allowed. Later batches use more plan usage, so the UI
    asks for confirmation before calling this.
    """
    with db.Session() as orm:
        latest = orm.scalar(
            select(func.max(Scenario.batch_number)).where(Scenario.session_id == session_id, Scenario.kind == kind)
        )
    if latest is None:
        return 1
    if not batch_is_reviewed(db, session_id, latest, kind):
        raise BatchNotReviewedError(f"Batch {latest} must be fully reviewed before starting another batch.")
    return latest + 1


# --- scenarios ---------------------------------------------------------------


def _next_position(orm: OrmSession, session_id: int, batch: int, kind: ScenarioKind) -> int:
    used = _batch_scenarios(orm, session_id, batch, kind)
    if len(used) >= MAX_SCENARIOS_PER_BATCH:
        what = "smoke-test pages" if kind == ScenarioKind.SMOKE else "scenarios"
        raise BatchFullError(f"Batch {batch} already has {MAX_SCENARIOS_PER_BATCH} {what}.")
    return len(used) + 1


def add_scenario(
    db: Database, session_id: int, batch: int, data: ScenarioInput, kind: ScenarioKind = _SCENARIO
) -> Scenario:
    data = data.cleaned()
    with db.Session.begin() as orm:
        _get(orm, TestSession, session_id)
        scenario = Scenario(
            session_id=session_id, batch_number=batch, position=_next_position(orm, session_id, batch, kind),
            kind=kind, name=data.name, test_steps=data.steps or None, expected_result=data.expected_result or None,
            test_data=data.test_data,
        )
        orm.add(scenario)
    return scenario


def add_highlight_screenshot(db: Database, scenario_id: int, order: int, path: Path) -> bool:
    """Attach the app's marked screenshot to a step (first in its list). False if the step is gone."""
    with db.Session.begin() as orm:
        step = orm.scalars(select(Step).where(Step.scenario_id == scenario_id, Step.order == order)).first()
        if step is None:
            return False
        step.screenshots[:] = [Screenshot(file_path=str(path))] + [s for s in step.screenshots
                                                                  if s.file_path != str(path)]
    return True


def scenario_batch(db: Database, scenario_id: int) -> int | None:
    with db.Session() as orm:
        scenario = orm.get(Scenario, scenario_id)
        return scenario.batch_number if scenario else None


def scenario_kind(db: Database, scenario_id: int) -> ScenarioKind | None:
    with db.Session() as orm:
        scenario = orm.get(Scenario, scenario_id)
        return scenario.kind if scenario else None


def set_account(db: Database, scenario_id: int, account_id: int | None) -> None:
    """Which of the session's accounts a scenario (or smoke page) runs as; None = the main account."""
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        if account_id is not None:
            account = orm.get(TestAccount, account_id)
            if account is None or account.session_id != scenario.session_id:
                raise ValueError("That account does not belong to this session.")
        scenario.account_id = account_id


def update_scenario(db: Database, scenario_id: int, data: ScenarioInput) -> Scenario:
    data = data.cleaned()
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        scenario.name, scenario.test_data = data.name, data.test_data
        scenario.test_steps, scenario.expected_result = data.steps or None, data.expected_result or None
    return scenario


def delete_scenario(db: Database, scenario_id: int) -> None:
    """Delete a scenario (or smoke page) and close the gap in its batch's positions."""
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        session_id, batch, kind = scenario.session_id, scenario.batch_number, scenario.kind
        orm.delete(scenario)
        orm.flush()
        for position, other in enumerate(_batch_scenarios(orm, session_id, batch, kind), start=1):
            if other.position != position:
                other.position = position
                orm.flush()


# --- smoke-test pages ----------------------------------------------------------


@dataclass
class SmokePageInput:
    page_name: str
    url: str
    how_to_reach: str = ""
    login_needed: bool = False
    page_information: str = ""
    expected_elements: str = ""

    def cleaned(self) -> "SmokePageInput":
        name = self.page_name.strip()
        if not name:
            raise ValueError("Page name is required.")
        url = normalize_url(self.url)
        if url is None:
            raise ValueError("Page URL must be a valid http(s) URL.")
        return SmokePageInput(
            page_name=name, url=url, how_to_reach=self.how_to_reach.strip(), login_needed=bool(self.login_needed),
            page_information=self.page_information.strip(), expected_elements=self.expected_elements.strip(),
        )

    @classmethod
    def of(cls, page: SmokePage) -> "SmokePageInput":
        return cls(page.page_name, page.url, page.how_to_reach, page.login_needed, page.page_information,
                   page.expected_elements)


def add_smoke_page(db: Database, session_id: int, batch: int, data: SmokePageInput) -> Scenario:
    """A smoke test is stored as a scenario of kind smoke with its SmokePage; its checks are steps."""
    data = data.cleaned()
    with db.Session.begin() as orm:
        _get(orm, TestSession, session_id)
        scenario = Scenario(
            session_id=session_id, batch_number=batch,
            position=_next_position(orm, session_id, batch, ScenarioKind.SMOKE), kind=ScenarioKind.SMOKE,
            name=data.page_name, goal=f"Smoke test of {data.url}",
        )
        scenario.smoke_page = SmokePage(**vars(data))
        orm.add(scenario)
    return scenario


def update_smoke_page(db: Database, scenario_id: int, data: SmokePageInput) -> Scenario:
    data = data.cleaned()
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        if scenario.smoke_page is None:
            raise LookupError(f"Scenario {scenario_id} is not a smoke test")
        for key, value in vars(data).items():
            setattr(scenario.smoke_page, key, value)
        scenario.name, scenario.goal = data.page_name, f"Smoke test of {data.url}"
    return scenario


# --- steps -------------------------------------------------------------------


def scenario_steps(db: Database, scenario_id: int) -> list[Step]:
    """Steps in order, with their reviews, bugs and screenshots loaded."""
    with db.Session() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        steps = list(scenario.steps)
        for step in steps:
            step.reviews, step.bugs, step.screenshots, step.capture_logs  # load before the ORM session closes
        return steps


# --- bugs ----------------------------------------------------------------------

BUG_FIELDS = ("title", "severity", "priority", "repro_steps", "expected", "actual", "report_text")


@dataclass
class BugEdit:
    title: str
    severity: str
    priority: int
    repro_steps: str = ""
    expected: str = ""
    actual: str = ""
    report_text: str = ""

    def cleaned(self) -> "BugEdit":
        title = self.title.strip()
        if not title:
            raise ValueError("The bug needs a title.")
        if self.severity not in BUG_SEVERITIES:
            raise ValueError(f"Severity must be one of: {', '.join(BUG_SEVERITIES)}.")
        if self.priority not in BUG_PRIORITIES:
            raise ValueError("Priority must be 1, 2, 3 or 4.")
        return BugEdit(title[:255], self.severity, self.priority, self.repro_steps.strip(), self.expected.strip(),
                       self.actual.strip(), self.report_text.strip())


def update_bug(db: Database, bug_id: int, edit: BugEdit) -> Bug:
    """Save the user's version of a bug. Claude's original is kept the first time it is edited."""
    edit = edit.cleaned()
    with db.Session.begin() as orm:
        bug = _get(orm, Bug, bug_id)
        if bug.claude_original is None:
            bug.claude_original = json.dumps({f: getattr(bug, f) for f in BUG_FIELDS})
        for field_name in BUG_FIELDS:
            setattr(bug, field_name, getattr(edit, field_name))
    return bug


def claude_original(bug: Bug) -> dict | None:
    """Claude's version of an edited bug, or None if the user has not edited it."""
    return json.loads(bug.claude_original) if bug.claude_original else None


def add_step(db: Database, scenario_id: int, data: StepInput) -> Step:
    data = data.cleaned()
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        order = (max((s.order for s in scenario.steps), default=0)) + 1
        step = Step(scenario_id=scenario_id, order=order, **vars(data))
        orm.add(step)
    return step


def update_step(db: Database, step_id: int, data: StepInput) -> Step:
    """Change Claude's side of a step. Earlier reviews are kept as history."""
    data = data.cleaned()
    with db.Session.begin() as orm:
        step = _get(orm, Step, step_id)
        for key, value in vars(data).items():
            setattr(step, key, value)
    return step


def delete_step(db: Database, step_id: int) -> None:
    with db.Session.begin() as orm:
        step = _get(orm, Step, step_id)
        scenario_id = step.scenario_id
        orm.delete(step)
        orm.flush()
        remaining = orm.scalars(select(Step).where(Step.scenario_id == scenario_id).order_by(Step.order))
        for order, other in enumerate(remaining, start=1):
            if other.order != order:
                other.order = order
                orm.flush()


# --- reviews -----------------------------------------------------------------


def _add_review(
    db: Database, step_id: int, decision: ReviewDecision, final_status: StepStatus | None, comment: str
) -> Review:
    with db.Session.begin() as orm:
        _get(orm, Step, step_id)
        review = Review(step_id=step_id, decision=decision, final_status=final_status, comment=comment.strip())
        orm.add(review)
    return review


def approve_step(db: Database, step_id: int, comment: str = "") -> Review:
    """Accept Claude's status as final."""
    with db.Session() as orm:
        status = _get(orm, Step, step_id).status
    return _add_review(db, step_id, ReviewDecision.APPROVED, status, comment)


def override_step(db: Database, step_id: int, final_status: StepStatus, comment: str = "") -> Review:
    """Set a different final status. Claude's own status on the step is left as it was."""
    final_status = StepStatus(final_status)
    with db.Session() as orm:
        status = _get(orm, Step, step_id).status
    if final_status == status:
        raise ValueError("The override status is the same as Claude's; use Approve instead.")
    return _add_review(db, step_id, ReviewDecision.OVERRIDDEN, final_status, comment)


def request_rerun(db: Database, step_id: int, comment: str = "") -> Review:
    """Mark the step for another run. It is not final until reviewed again."""
    return _add_review(db, step_id, ReviewDecision.RERUN_REQUESTED, None, comment)


# --- running -----------------------------------------------------------------


def scenarios_to_run(
    db: Database, session_id: int, batch: int, kind: ScenarioKind = ScenarioKind.SCENARIO
) -> list[tuple[Scenario, bool]]:
    """Scenarios in the batch that need a run, with True when it is a re-run.

    A scenario needs a run when it has no steps yet, or when any step's latest review
    asks for a re-run. Fully reviewed or pending-review scenarios are left alone.
    """
    with db.Session() as orm:
        selected = []
        for scenario in _batch_scenarios(orm, session_id, batch, kind):
            scenario.smoke_page  # load before the ORM session closes
            if not scenario.steps:
                selected.append((scenario, False))
            elif any(step.rerun_requested for step in scenario.steps):
                selected.append((scenario, True))
        return selected
