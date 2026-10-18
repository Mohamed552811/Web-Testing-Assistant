"""Search across all sessions: scenarios, steps, bugs, site notes and the library."""

from dataclasses import dataclass

from sqlalchemy import or_, select

from wta.db.database import Database
from wta.db.models import Bug, LibraryItem, Scenario, ScenarioKind, SiteNote, Step, TestSession

MAX_HITS = 200


@dataclass
class Hit:
    kind: str  # "Scenario", "Smoke page", "Step", "Check", "Bug", "Site note", "Library"
    title: str
    snippet: str
    session_id: int | None = None
    session_name: str = ""
    scenario_id: int | None = None
    step_id: int | None = None
    site: str = ""


def snippet(text: str, query: str, width: int = 70) -> str:
    """The part of the text around the first match, on one line."""
    flat = " ".join((text or "").split())
    at = flat.lower().find(query.lower())
    if at < 0:
        return flat[: width * 2]
    start, end = max(0, at - width), min(len(flat), at + len(query) + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _first_match(query: str, *texts: str) -> str:
    return next((t for t in texts if t and query.lower() in t.lower()), texts[0] if texts else "")


def search(db: Database, query: str, limit: int = MAX_HITS) -> list[Hit]:
    query = query.strip()
    if len(query) < 2:
        return []
    like = f"%{query}%"
    hits: list[Hit] = []
    with db.Session() as orm:
        for scenario, session in orm.execute(
            select(Scenario, TestSession).join(TestSession)
            .where(or_(Scenario.name.ilike(like), Scenario.test_steps.ilike(like),
                       Scenario.expected_result.ilike(like), Scenario.goal.ilike(like)))
            .order_by(TestSession.created_at.desc(), Scenario.batch_number, Scenario.position).limit(limit)
        ):
            smoke = scenario.kind == ScenarioKind.SMOKE
            text = _first_match(query, scenario.name, scenario.test_steps or "", scenario.expected_result or "",
                                scenario.goal)
            hits.append(Hit("Smoke page" if smoke else "Scenario", scenario.name, snippet(text, query),
                            session.id, session.name, scenario.id))
        for step, scenario, session in orm.execute(
            select(Step, Scenario, TestSession).join(Scenario, Step.scenario_id == Scenario.id).join(TestSession)
            .where(or_(Step.action.ilike(like), Step.actual_result.ilike(like), Step.expected_result.ilike(like),
                       Step.reasoning.ilike(like), Step.screen_name.ilike(like), Step.element.ilike(like)))
            .order_by(TestSession.created_at.desc(), Scenario.id, Step.order).limit(limit)
        ):
            smoke = scenario.kind == ScenarioKind.SMOKE
            text = _first_match(query, step.action, step.actual_result, step.expected_result, step.reasoning,
                                step.screen_name, step.element)
            hits.append(Hit("Check" if smoke else "Step", f"{scenario.name} — {'check' if smoke else 'step'} "
                            f"{step.order}", snippet(text, query), session.id, session.name, scenario.id, step.id))
        for bug, step, scenario, session in orm.execute(
            select(Bug, Step, Scenario, TestSession).join(Step, Bug.step_id == Step.id)
            .join(Scenario, Step.scenario_id == Scenario.id).join(TestSession)
            .where(or_(Bug.title.ilike(like), Bug.repro_steps.ilike(like), Bug.actual.ilike(like),
                       Bug.report_text.ilike(like)))
            .order_by(TestSession.created_at.desc(), Bug.id).limit(limit)
        ):
            text = _first_match(query, bug.title, bug.actual, bug.repro_steps, bug.report_text)
            hits.append(Hit("Bug", bug.title, snippet(text, query), session.id, session.name, scenario.id, step.id))
        for note in orm.scalars(select(SiteNote).where(SiteNote.note.ilike(like)).limit(limit)):
            hits.append(Hit("Site note", note.site, snippet(note.note, query), site=note.site))
        for item in orm.scalars(select(LibraryItem).where(or_(
                LibraryItem.name.ilike(like), LibraryItem.test_steps.ilike(like), LibraryItem.url.ilike(like),
                LibraryItem.page_information.ilike(like))).limit(limit)):
            text = _first_match(query, item.name, item.test_steps, item.url, item.page_information)
            hits.append(Hit("Library", item.name, snippet(text, query)))
    return hits[:limit]
