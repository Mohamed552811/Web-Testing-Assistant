"""Quality over time per site: pass rate of runs by day, and open bugs per session (for example per build)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from wta.db.database import Database
from wta.db.models import Run, RunOutcomeKind, Scenario, Step, StepStatus, TestSession
from wta.db.site_notes import site_for_url


def _local_day(value: datetime) -> date:
    return value.replace(tzinfo=UTC).astimezone().date()


@dataclass
class DayStat:
    day: date
    runs: int
    steps: int
    passed: int
    failed: int  # Claude's Bug / Fail steps that day

    @property
    def pass_rate(self) -> float:
        return 100.0 * self.passed / self.steps if self.steps else 0.0


@dataclass
class SessionBugs:
    session_id: int
    name: str
    created: date
    open_bugs: int  # steps with a bug whose status that counts is Fail (your review, else Claude's)
    reviewed: int
    steps: int


def sites(db: Database) -> list[str]:
    with db.Session() as orm:
        return sorted({site_for_url(url) for url in orm.scalars(select(TestSession.target_url))} - {""})


def _sessions(orm, site: str) -> list[TestSession]:
    return [s for s in orm.scalars(select(TestSession).order_by(TestSession.created_at))
            if site_for_url(s.target_url) == site]


def runs_by_day(db: Database, site: str) -> list[DayStat]:
    with db.Session() as orm:
        ids = [s.id for s in _sessions(orm, site)]
        runs = list(orm.scalars(
            select(Run).join(Scenario).where(Scenario.session_id.in_(ids), Run.outcome == RunOutcomeKind.FINISHED)
            .options(selectinload(Run.steps)).order_by(Run.started_at)))
        days: dict[date, list] = defaultdict(lambda: [0, 0, 0, 0])
        for run in runs:
            if not run.steps:
                continue
            stat = days[_local_day(run.started_at)]
            stat[0] += 1
            stat[1] += len(run.steps)
            stat[2] += sum(1 for s in run.steps if s.status == StepStatus.PASS)
            stat[3] += sum(1 for s in run.steps if s.status == StepStatus.FAIL)
    return [DayStat(day, *values) for day, values in sorted(days.items())]


def bugs_by_session(db: Database, site: str) -> list[SessionBugs]:
    result = []
    with db.Session() as orm:
        for session in _sessions(orm, site):
            steps = list(orm.scalars(select(Step).join(Scenario).where(Scenario.session_id == session.id)
                                     .options(selectinload(Step.reviews), selectinload(Step.bugs))))
            open_bugs = sum(1 for s in steps if s.bugs and (s.final_status or s.status) == StepStatus.FAIL)
            reviewed = sum(1 for s in steps if s.final_status is not None)
            result.append(SessionBugs(session.id, session.name, _local_day(session.created_at), open_bugs,
                                      reviewed, len(steps)))
    return result
