"""The scenario library: saved scenarios and smoke pages to add to any batch, in any session.

A scenario added from the library (or saved to it) keeps a link to its item (`template_id`),
so runs of the same test can be compared across sessions, for example one session per build.
"""

from dataclasses import dataclass

from sqlalchemy import select

from wta.db.database import Database
from wta.db.models import LibraryItem, Scenario, ScenarioKind, SmokePage, utcnow
from wta.db.scenarios import ScenarioInput, SmokePageInput, _get, _next_position


@dataclass
class ItemInput:
    """A new library item: a scenario (`scenario`) or a smoke page (`page`)."""

    scenario: ScenarioInput | None = None
    page: SmokePageInput | None = None
    source: str = ""

    def cleaned(self) -> "ItemInput":
        if (self.scenario is None) == (self.page is None):
            raise ValueError("A library item is either a scenario or a smoke page.")
        return ItemInput(self.scenario.cleaned() if self.scenario else None,
                         self.page.cleaned() if self.page else None, self.source.strip()[:300])


def _fill(item: LibraryItem, data: ItemInput) -> None:
    if data.scenario is not None:
        item.kind, item.name = ScenarioKind.SCENARIO, data.scenario.name
        item.test_steps, item.expected_result = data.scenario.steps, data.scenario.expected_result
        item.test_data = data.scenario.test_data
    else:
        page = data.page
        item.kind, item.name, item.url = ScenarioKind.SMOKE, page.page_name, page.url
        item.how_to_reach, item.login_needed = page.how_to_reach, page.login_needed
        item.page_information, item.expected_elements = page.page_information, page.expected_elements
    item.updated_at = utcnow()


def add_items(db: Database, items: list[ItemInput]) -> list[LibraryItem]:
    cleaned = [item.cleaned() for item in items]  # all or nothing
    rows = []
    with db.Session.begin() as orm:
        for data in cleaned:
            row = LibraryItem(source=data.source)
            _fill(row, data)
            orm.add(row)
            rows.append(row)
    return rows


def update_item(db: Database, item_id: int, data: ItemInput) -> LibraryItem:
    data = data.cleaned()
    with db.Session.begin() as orm:
        row = _get(orm, LibraryItem, item_id)
        if (data.page is not None) != (row.kind == ScenarioKind.SMOKE):
            raise ValueError("A scenario stays a scenario and a smoke page stays a smoke page.")
        _fill(row, data)
    return row


def list_items(db: Database, kind: ScenarioKind | None = None, search: str = "") -> list[LibraryItem]:
    with db.Session() as orm:
        query = select(LibraryItem).order_by(LibraryItem.name, LibraryItem.id)
        if kind is not None:
            query = query.where(LibraryItem.kind == kind)
        if search.strip():
            like = f"%{search.strip()}%"
            query = query.where(LibraryItem.name.ilike(like) | LibraryItem.test_steps.ilike(like)
                                | LibraryItem.url.ilike(like))
        return list(orm.scalars(query))


def delete_item(db: Database, item_id: int) -> None:
    """Remove an item. Scenarios added from it stay; they just lose the link."""
    with db.Session.begin() as orm:
        orm.delete(_get(orm, LibraryItem, item_id))


def item_input(item: LibraryItem) -> ItemInput:
    if item.kind == ScenarioKind.SMOKE:
        return ItemInput(page=SmokePageInput(item.name, item.url, item.how_to_reach, item.login_needed,
                                             item.page_information, item.expected_elements), source=item.source)
    return ItemInput(scenario=ScenarioInput(item.name, item.test_steps, item.expected_result, item.test_data),
                     source=item.source)


def save_scenario(db: Database, scenario_id: int) -> tuple[LibraryItem, bool]:
    """Save a scenario or smoke page to the library: updates its linked item, or creates one.
    Returns the item and True when it was new."""
    with db.Session.begin() as orm:
        scenario = _get(orm, Scenario, scenario_id)
        page = scenario.smoke_page
        data = ItemInput(page=SmokePageInput(page.page_name, page.url, page.how_to_reach, page.login_needed,
                                             page.page_information, page.expected_elements)) if page else \
            ItemInput(scenario=ScenarioInput(scenario.name, scenario.test_steps or "", scenario.expected_result or "",
                                             scenario.test_data or ""))
        data = data.cleaned()
        row = orm.get(LibraryItem, scenario.template_id) if scenario.template_id is not None else None
        created = row is None
        if created:
            row = LibraryItem()
            orm.add(row)
        _fill(row, data)
        orm.flush()
        scenario.template_id = row.id
    return row, created


def add_to_batch(db: Database, session_id: int, batch: int, item_ids: list[int],
                 kind: ScenarioKind) -> list[Scenario]:
    """Add library items to a batch as new scenarios (or smoke pages) linked to their items."""
    with db.Session.begin() as orm:
        items = [_get(orm, LibraryItem, item_id) for item_id in item_ids]
        if any(item.kind != kind for item in items):
            raise ValueError("Add scenarios on the Scenarios tab and smoke pages on the Smoke test tab.")
        added = []
        for item in items:
            position = _next_position(orm, session_id, batch, kind)  # raises BatchFullError when full
            scenario = Scenario(session_id=session_id, batch_number=batch, position=position, kind=kind,
                                name=item.name, template_id=item.id)
            if kind == ScenarioKind.SMOKE:
                scenario.goal = f"Smoke test of {item.url}"
                scenario.smoke_page = SmokePage(page_name=item.name, url=item.url, how_to_reach=item.how_to_reach,
                                                login_needed=item.login_needed, page_information=item.page_information,
                                                expected_elements=item.expected_elements)
            else:
                scenario.test_steps = item.test_steps or None
                scenario.expected_result = item.expected_result or None
                scenario.test_data = item.test_data
            orm.add(scenario)
            orm.flush()  # the next position counts this one
            added.append(scenario)
    return added
