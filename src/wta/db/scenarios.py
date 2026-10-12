"""Batches, scenarios, steps and reviews within a test session."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from wta.db.database import Database
from wta.db.models import (
    MAX_SCENARIOS_PER_BATCH,
    Review,
    ReviewDecision,
    Scenario,
    ScenarioKind,
    Step,
    StepStatus,
    TestSession,
)


@dataclass
class ScenarioInput:
    name: str
    goal: str = ""
    test_data: str = ""

    def cleaned(self) -> "ScenarioInput":
        name = self.name.strip()
        if not name:
            raise ValueError("Scenario name is required.")
        return ScenarioInput(name=name, goal=self.goal.strip(), test_data=self.test_data.strip())


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


def batch_numbers(db: Database, session_id: int, kind: ScenarioKind = ScenarioKind.SCENARIO) -> list[int]:
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


def batch_scenarios(db: Database, session_id: int, batch: int) -> list[Scenario]:
    with db.Session() as orm:
        return _batch_scenarios(orm, session_id, batch)


def _batch_scenarios(orm: OrmSession, session_id: int, batch: int) -> list[Scenario]:
    return list(
        orm.scalars(
            select(Scenario)
            .where(Scenario.session_id == session_id, Scenario.batch_number == batch)
            .order_by(Scenario.position)
        )
    )


def batch_is_reviewed(db: Database, session_id: int, batch: int) -> bool:
    """True when every scenario in the batch has steps and every step has a final review."""
    with db.Session() as orm:
        scenarios = _batch_scenarios(orm, session_id, batch)
        if not scenarios:
            return False
        for scenario in scenarios:
            if not scenario.steps or any(step.final_status is None for step in scenario.steps):
                return False
        return True


def next_batch_number(db: Database, session_id: int) -> int:
    """The batch a new scenario batch would get. Raises if the latest batch is not fully reviewed.

    The first batch is always allowed. Later batches use more plan usage, so the UI
    asks for confirmation before calling this.
    """
    with db.Session() as orm:
        latest = orm.scalar(select(func.max(Scenario.batch_number)).where(Scenario.session_id == session_id))
    if latest is None:
        return 1
    if not batch_is_reviewed(db, session_id, latest):
        raise BatchNotReviewedError(f"Batch {latest} must be fully reviewed before starting another batch.")
    return latest + 1


# --- scenarios ---------------------------------------------------------------


def add_scenario(
    db: Database, session_id: int, batch: int, data: ScenarioInput, kind: ScenarioKind = ScenarioKind.SCENARIO
) -> Scenario:
    data = data.cleaned()
    with db.Session.begin() as orm:
        _get(orm, TestSession, session_id)
        used = _batch_scenarios(orm, session_id, batch)
        if len(used) >= MAX_SCENARIOS_PER_BATCH:
            raise BatchFullError(f"Batch {batch} already has {MAX_SCENARIOS_PER_BATCH} scenarios.")
        scenario = Scenario(
            session_id=session_id, batch_number=batch, position=len(used) + 1, kind=kind,
            name=data.name, goal=data.goal, test_data=data.test_data,
        )
        orm.add(scenario)
    return scenario


def update_scenario(db: Database, scenario_id: int, data: ScenarioInput) -> Scenario:
    data = data.cleaned()
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        scenario.name, scenario.goal, scenario.test_data = data.name, data.goal, data.test_data
    return scenario


def delete_scenario(db: Database, scenario_id: int) -> None:
    """Delete a scenario and close the gap in its batch's positions."""
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        session_id, batch = scenario.session_id, scenario.batch_number
        orm.delete(scenario)
        orm.flush()
        for position, other in enumerate(_batch_scenarios(orm, session_id, batch), start=1):
            if other.position != position:
                other.position = position
                orm.flush()


# --- steps -------------------------------------------------------------------


def scenario_steps(db: Database, scenario_id: int) -> list[Step]:
    """Steps in order, with their reviews loaded."""
    with db.Session() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        steps = list(scenario.steps)
        for step in steps:
            step.reviews  # load before the ORM session closes
        return steps


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


def scenarios_to_run(db: Database, session_id: int, batch: int) -> list[tuple[Scenario, bool]]:
    """Scenarios in the batch that need a run, with True when it is a re-run.

    A scenario needs a run when it has no steps yet, or when any step's latest review
    asks for a re-run. Fully reviewed or pending-review scenarios are left alone.
    """
    with db.Session() as orm:
        selected = []
        for scenario in _batch_scenarios(orm, session_id, batch):
            if scenario.kind != ScenarioKind.SCENARIO:
                continue
            if not scenario.steps:
                selected.append((scenario, False))
            elif any(step.rerun_requested for step in scenario.steps):
                selected.append((scenario, True))
        return selected
