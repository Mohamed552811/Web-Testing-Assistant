"""Run history: one row per scenario run, with its trace."""

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from wta.db.database import Database
from wta.db.models import Run, RunOutcomeKind, RunTrace, Scenario, TraceKind, utcnow


def start_run(
    db: Database,
    scenario_id: int,
    run_dir: Path,
    five_hour_before: float | None = None,
    seven_day_before: float | None = None,
    site_notes_used: int = 0,
) -> int:
    with db.Session.begin() as orm:
        run = Run(
            scenario_id=scenario_id, run_dir=str(run_dir), five_hour_before=five_hour_before,
            seven_day_before=seven_day_before, site_notes_used=site_notes_used,
        )
        orm.add(run)
        orm.flush()
        return run.id


@dataclass
class RunEnd:
    outcome: RunOutcomeKind
    exit_code: int | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    five_hour_seen: float | None = None
    seven_day_seen: float | None = None
    summary: str = ""


def finish_run(db: Database, run_id: int, end: RunEnd) -> None:
    with db.Session.begin() as orm:
        run = orm.get(Run, run_id)
        if run is None:
            raise LookupError(f"Run {run_id} not found")
        run.ended_at = utcnow()
        for key, value in vars(end).items():
            setattr(run, key, value)


def add_trace(db: Database, scenario_id: int, run_id: int | None, kind: TraceKind, text: str) -> None:
    with db.Session.begin() as orm:
        orm.add(RunTrace(scenario_id=scenario_id, run_id=run_id, kind=kind, text=text))


def list_runs(db: Database, session_id: int) -> list[Run]:
    """Runs of a session, newest first, with their scenario loaded."""
    with db.Session() as orm:
        return list(
            orm.scalars(
                select(Run)
                .join(Scenario)
                .where(Scenario.session_id == session_id)
                .options(selectinload(Run.scenario))
                .order_by(Run.started_at.desc(), Run.id.desc())
            )
        )


def run_traces(db: Database, run_id: int, kind: TraceKind | None = None, search: str = "") -> list[RunTrace]:
    with db.Session() as orm:
        query = select(RunTrace).where(RunTrace.run_id == run_id)
        if kind is not None:
            query = query.where(RunTrace.kind == kind)
        if search:
            query = query.where(RunTrace.text.icontains(search))
        return list(orm.scalars(query.order_by(RunTrace.timestamp, RunTrace.id)))


def mark_interrupted_runs(db: Database) -> int:
    """Runs still marked running when the app starts were cut off (crash or kill). Mark them failed."""
    with db.Session.begin() as orm:
        runs = list(orm.scalars(select(Run).where(Run.outcome == RunOutcomeKind.RUNNING)))
        for run in runs:
            run.outcome = RunOutcomeKind.FAILED
            run.summary = (run.summary + "\n" if run.summary else "") + "The app closed before this run finished."
            run.ended_at = run.ended_at or utcnow()
        return len(runs)


def run_steps_from_folder(run_dir: str | Path) -> list[dict]:
    """The steps exactly as this run reported them (results.json in its folder).

    The step table only shows each scenario's latest results; this keeps older runs readable.
    """
    try:
        data = json.loads((Path(run_dir) / "results.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
