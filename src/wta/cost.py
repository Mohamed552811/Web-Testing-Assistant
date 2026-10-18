"""Plan usage control: an estimate before a batch and the pause limit during it.

Only data Claude Code itself reports is used (rate_limit_event readings, stored per run).
Readings come in whole percents a few times per run, so the estimate is an average over
recent runs; other Claude Code use on the same plan at the same time also shows up in it.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from wta import usage
from wta.db.database import Database
from wta.db.models import Run, RunOutcomeKind, Scenario, ScenarioKind

ESTIMATE_RUNS = 10  # the most recent finished runs of a kind the estimate is based on


@dataclass(frozen=True)
class Estimate:
    percent: float  # of the 5-hour window, for the whole batch
    runs: int  # how many earlier runs it is based on

    def text(self) -> str:
        amount = "less than 1%" if self.percent < 1 else f"about {self.percent:.0f}%"
        return f"{amount} of the 5-hour window (from {self.runs} earlier run{'' if self.runs == 1 else 's'})"


def average_use(db: Database, kind: ScenarioKind, limit: int = ESTIMATE_RUNS) -> tuple[float, int] | None:
    """Average 5-hour usage of the latest finished runs of this kind, and how many runs that is."""
    with db.Session() as orm:
        used = list(orm.scalars(
            select(Run.five_hour_used)
            .join(Scenario)
            .where(Scenario.kind == kind, Run.outcome == RunOutcomeKind.FINISHED, Run.five_hour_used.is_not(None))
            .order_by(Run.started_at.desc(), Run.id.desc())
            .limit(limit)
        ))
    return (sum(used) / len(used), len(used)) if used else None


def estimate_batch(db: Database, kinds: list[ScenarioKind]) -> Estimate | None:
    """Estimated 5-hour usage for running these scenarios. A kind with no history borrows the other kind's."""
    if not kinds:
        return None
    averages = {kind: average_use(db, kind) for kind in set(kinds)}
    known = [a for a in averages.values() if a is not None]
    if not known:
        others = [average_use(db, k) for k in ScenarioKind if k not in averages]
        known = [a for a in others if a is not None]
        if not known:
            return None
    fallback = known[0]
    total = sum((averages.get(kind) or fallback)[0] for kind in kinds)
    runs = max(n for _avg, n in known)
    return Estimate(round(total, 1), runs)


def current_five_hour(usage_path: Path, now: float | None = None) -> usage.WindowUsage | None:
    """The latest 5-hour reading, or None when there is none or its window has already reset."""
    reading = usage.load(usage_path).get("five_hour")
    return reading if reading is not None and not reading.is_stale(now) else None


def reset_text(reading: usage.WindowUsage) -> str:
    return time.strftime("%H:%M", time.localtime(reading.resets_at))


def pause_reason(usage_path: Path, limit: int, now: float | None = None) -> str | None:
    """Why the batch should pause before the next scenario, or None. `limit` 0 means never."""
    reading = current_five_hour(usage_path, now)
    if not limit or reading is None or reading.percent < limit:
        return None
    return (f"Your 5-hour plan usage is at {reading.percent:.0f}%, which has reached your pause limit of "
            f"{limit}%. The window resets at {reset_text(reading)}.")


def before_batch_warning(usage_path: Path, limit: int, estimate: Estimate | None,
                         now: float | None = None) -> str | None:
    """A warning to show before a batch starts, or None when it should simply run."""
    if not limit:
        return None
    reading = current_five_hour(usage_path, now)
    if reading is None:
        return None
    if reading.percent >= limit:
        return pause_reason(usage_path, limit, now)
    if estimate is not None and reading.percent + estimate.percent > limit:
        return (f"Your 5-hour plan usage is at {reading.percent:.0f}%. This batch is estimated to use "
                f"{estimate.text()}, which would take it to about {reading.percent + estimate.percent:.0f}%, "
                f"past your pause limit of {limit}%. The batch will pause before the next scenario once "
                f"the limit is reached. The window resets at {reset_text(reading)}.")
    return None
