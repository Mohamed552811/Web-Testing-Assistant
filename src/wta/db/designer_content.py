"""What the user adds to and changes in a backlog: notes, supplementary context, answers to open questions,
and edits to the generated test cases (spec 8.1 (3), (7), (10))."""

import copy
from dataclasses import dataclass

from sqlalchemy import func, select

from wta.db import Database
from wta.db.designer_models import (
    BacklogContext,
    BacklogItem,
    DesignerTestCase,
    ExportLog,
    GenerationRun,
    OpenQuestion,
    Sheet,
    Workbook,
)
from wta.db.models import utcnow
from wta.designer import prompt_builder as pb
from wta.designer.settings import PER_STEP_EXPECTED, DesignerSettings
from wta.designer.validation import expected_id

DOMAIN_NOTE = "domain_note"
SUPPLEMENTARY = "supplementary"
CURRENT_STATUSES = ("ok", "insufficient_information")


# --- the same backlog in earlier versions of the workbook ------------------------------------------


def linked_ids(orm, backlog_id: int) -> list[int]:
    """This backlog and the same backlog (sheet name + number + occurrence) in earlier versions of its workbook,
    newest first. Notes, runs and test cases follow a backlog to a new version this way (spec 8.1 (1))."""
    item = orm.get(BacklogItem, backlog_id)
    if item is None:
        return [backlog_id]
    sheet, workbook = item.sheet, item.sheet.workbook
    rows = orm.execute(
        select(BacklogItem.id, BacklogItem.number)
        .join(Sheet, BacklogItem.sheet_id == Sheet.id).join(Workbook, Sheet.workbook_id == Workbook.id)
        .where(Workbook.family == workbook.family, Workbook.id <= workbook.id, Sheet.name == sheet.name,
               BacklogItem.occurrence == item.occurrence)
        .order_by(Workbook.id.desc())
    )
    linked = [row_id for row_id, number in rows if number.lower() == item.number.lower()]
    return linked or [backlog_id]


# --- notes and supplementary context --------------------------------------------------------------


@dataclass
class ContextItem:
    id: int
    kind: str
    text: str
    source: str


def list_context(db: Database, backlog_id: int, kind: str | None = None) -> list[ContextItem]:
    with db.Session() as orm:
        query = select(BacklogContext).where(BacklogContext.backlog_item_id.in_(linked_ids(orm, backlog_id)))
        if kind:
            query = query.where(BacklogContext.kind == kind)
        rows = orm.scalars(query.order_by(BacklogContext.kind, BacklogContext.position, BacklogContext.id))
        return [ContextItem(r.id, r.kind, r.text, r.source) for r in rows]


def texts(db: Database, backlog_id: int, kind: str) -> list[str]:
    return [item.text for item in list_context(db, backlog_id, kind)]


def add_context(db: Database, backlog_id: int, kind: str, text: str, source: str = "") -> int:
    text = text.strip()
    if kind not in (DOMAIN_NOTE, SUPPLEMENTARY):
        raise ValueError(f"Unknown context kind {kind!r}")
    if not text:
        raise ValueError("The text is empty.")
    with db.Session() as orm, orm.begin():
        position = orm.scalar(select(func.count()).select_from(BacklogContext).where(
            BacklogContext.backlog_item_id == backlog_id, BacklogContext.kind == kind))
        row = BacklogContext(backlog_item_id=backlog_id, kind=kind, text=text, source=source, position=position)
        orm.add(row)
        orm.flush()
        return row.id


def update_context(db: Database, item_id: int, text: str) -> None:
    text = text.strip()
    if not text:
        raise ValueError("The text is empty.")
    with db.Session() as orm, orm.begin():
        row = orm.get(BacklogContext, item_id)
        if row is None:
            raise LookupError(f"Note {item_id} not found")
        row.text = text


def delete_context(db: Database, item_id: int) -> None:
    with db.Session() as orm, orm.begin():
        row = orm.get(BacklogContext, item_id)
        if row is not None:
            orm.delete(row)


def backlog_as_context(db: Database, backlog_id: int) -> tuple[str, str]:
    """(text, source) describing another backlog, to add as supplementary context. Pictures are named, not sent."""
    with db.Session() as orm:
        item = orm.get(BacklogItem, backlog_id)
        if item is None:
            raise LookupError(f"Backlog {backlog_id} not found")
        sheet = item.sheet
        workbook = sheet.workbook
        lines = [f"Backlog {item.number} \"{item.title}\" (workbook {workbook.file_name}, sheet \"{sheet.name}\"):"]
        lines += [f"{block.cell}: {block.text}" for block in item.text_blocks]
        pictures = [link.image.label for link in item.images]
        if pictures:
            lines.append("Its pictures (not attached here): " + ", ".join(pictures))
        return "\n".join(lines), f"{workbook.file_name} · {sheet.name} · #{item.number}"


# --- current run, open questions --------------------------------------------------------------------


def current_run_id(db: Database, backlog_id: int) -> int | None:
    """The valid run shown for a backlog: the newest one, or the one last restored from history."""
    with db.Session() as orm:
        return orm.scalar(
            select(GenerationRun.id)
            .where(GenerationRun.backlog_item_id.in_(linked_ids(orm, backlog_id)),
                   GenerationRun.status.in_(CURRENT_STATUSES))
            .order_by(func.coalesce(GenerationRun.restored_at, GenerationRun.created_at).desc(),
                      GenerationRun.id.desc())
            .limit(1))


@dataclass
class QuestionView:
    id: int
    text: str
    answered: bool
    answer: str


def list_questions(db: Database, backlog_id: int) -> list[QuestionView]:
    run_id = current_run_id(db, backlog_id)
    if run_id is None:
        return []
    with db.Session() as orm:
        rows = orm.scalars(select(OpenQuestion).where(OpenQuestion.run_id == run_id).order_by(OpenQuestion.id))
        return [QuestionView(q.id, q.text, q.answered, q.answer_note or "") for q in rows]


def answer_question(db: Database, question_id: int, answer: str) -> int:
    """Store the answer and add it to the backlog's notes for the next generation (spec 8.1 (10))."""
    answer = answer.strip()
    if not answer:
        raise ValueError("The answer is empty.")
    with db.Session() as orm, orm.begin():
        question = orm.get(OpenQuestion, question_id)
        if question is None:
            raise LookupError(f"Question {question_id} not found")
        question.answered, question.answer_note = True, answer
        backlog_id, text = question.backlog_item_id, question.text
    return add_context(db, backlog_id, DOMAIN_NOTE, f"{text} Answer: {answer}", source="open question")


# --- test case edits --------------------------------------------------------------------------------


@dataclass
class CaseView:
    row_id: int
    seq: int
    data: dict
    locked: bool
    edited: bool


def current_cases(db: Database, backlog_id: int) -> list[CaseView]:
    run_id = current_run_id(db, backlog_id)
    if run_id is None:
        return []
    with db.Session() as orm:
        rows = orm.scalars(select(DesignerTestCase).where(DesignerTestCase.run_id == run_id,
                                                          DesignerTestCase.deleted_at.is_(None))
                           .order_by(DesignerTestCase.seq))
        return [CaseView(r.id, r.seq, copy.deepcopy(r.data), r.locked, r.edited) for r in rows]


def _row(orm, row_id: int) -> DesignerTestCase:
    row = orm.get(DesignerTestCase, row_id)
    if row is None or row.deleted_at is not None:
        raise LookupError(f"Test case {row_id} not found")
    return row


def _live_rows(orm, run_id: int) -> list[DesignerTestCase]:
    return list(orm.scalars(select(DesignerTestCase).where(DesignerTestCase.run_id == run_id,
                                                           DesignerTestCase.deleted_at.is_(None))
                            .order_by(DesignerTestCase.seq)))


def _resequence(rows: list[DesignerTestCase]) -> None:
    for seq, row in enumerate(rows, 1):
        row.seq = seq


def update_case(db: Database, row_id: int, data: dict) -> None:
    """Save an edited test case (marks it edited). The ID stays as it is until Renumber."""
    with db.Session() as orm, orm.begin():
        row = _row(orm, row_id)
        row.data = copy.deepcopy(data)
        row.case_id = str(data.get("id", row.case_id))
        row.edited, row.updated_at = True, utcnow()


def set_locked(db: Database, row_id: int, locked: bool) -> None:
    with db.Session() as orm, orm.begin():
        _row(orm, row_id).locked = locked


def blank_case(settings: DesignerSettings, backlog_key: str, sequence: int) -> dict:
    """A new test case in the selected preset's shape."""
    preset = settings.preset()
    single = preset.steps_style != PER_STEP_EXPECTED
    data = {
        "id": expected_id(preset.id_prefix, preset.backlog_pad, preset.seq_pad, pb.key_digits(backlog_key), sequence),
        "backlog_number": pb.backlog_number_value(backlog_key),
        "title": f"{preset.title_prefix} …",
        "type": "corner_case",
        "priority": preset.priority_labels[1],
        "tags": ["regression"] if settings.tag_suggestions else [],
        "preconditions": [],
        "steps": ["", "Assert that …"] if single else [{"action": "", "expected": "Assert that …"}],
        "expected_result": "",
        "basis": "inferred",
        "source_reference": "manual",
    }
    return {k: v for k, v in data.items() if preset.includes(k)}


def add_case(db: Database, backlog_id: int, data: dict, after_row_id: int | None = None) -> int:
    run_id = current_run_id(db, backlog_id)
    if run_id is None:
        raise LookupError("Generate test cases first; there is nothing to add to yet.")
    with db.Session() as orm, orm.begin():
        rows = _live_rows(orm, run_id)
        index = next((i + 1 for i, r in enumerate(rows) if r.id == after_row_id), len(rows))
        owner = orm.get(GenerationRun, run_id).backlog_item_id  # may be the same backlog in an earlier version
        row = DesignerTestCase(run_id=run_id, backlog_item_id=owner, case_id=str(data.get("id", "")),
                               seq=0, data=copy.deepcopy(data), edited=True)
        orm.add(row)
        rows.insert(index, row)
        _resequence(rows)
        orm.flush()
        return row.id


def duplicate_case(db: Database, row_id: int) -> int:
    with db.Session() as orm:
        row = _row(orm, row_id)
        data, backlog_id = copy.deepcopy(row.data), row.backlog_item_id
    data["title"] = f"{data.get('title', '')} (copy)"
    return add_case(db, backlog_id, data, after_row_id=row_id)


def delete_case(db: Database, row_id: int) -> None:
    """Soft delete (spec 8.2 deleted_at); positions close up."""
    with db.Session() as orm, orm.begin():
        row = _row(orm, row_id)
        row.deleted_at = utcnow()
        orm.flush()
        _resequence(_live_rows(orm, row.run_id))


def move_case(db: Database, row_id: int, offset: int) -> None:
    with db.Session() as orm, orm.begin():
        row = _row(orm, row_id)
        rows = _live_rows(orm, row.run_id)
        index = rows.index(row)
        target = max(0, min(len(rows) - 1, index + offset))
        rows.insert(target, rows.pop(index))
        _resequence(rows)


def renumber(db: Database, backlog_id: int, settings: DesignerSettings, backlog_key: str) -> None:
    """IDs follow the current order again, in the selected preset's pattern."""
    run_id = current_run_id(db, backlog_id)
    if run_id is None:
        return
    preset = settings.preset()
    number = pb.key_digits(backlog_key)
    with db.Session() as orm, orm.begin():
        for row in _live_rows(orm, run_id):
            new_id = expected_id(preset.id_prefix, preset.backlog_pad, preset.seq_pad, number, row.seq)
            if row.data.get("id") != new_id:
                data = dict(row.data)
                data["id"] = new_id
                row.data, row.case_id = data, new_id


# --- export -----------------------------------------------------------------------------------------


def export_entries(db: Database, backlog_ids: list[int]):
    """The current test cases and open questions of each backlog that has any, in the given order."""
    from wta.designer.export import ExportEntry  # the writers need no database

    entries = []
    for backlog_id in backlog_ids:
        cases = current_cases(db, backlog_id)
        if not cases:
            continue
        with db.Session() as orm:
            item = orm.get(BacklogItem, backlog_id)
            sheet, key, title = item.sheet.name, item.number, item.title
        entries.append(ExportEntry(sheet, key, title, [c.data for c in cases],
                                   [q.text for q in list_questions(db, backlog_id)]))
    return entries


def log_export(db: Database, user: str, fmt: str, selection: dict) -> None:
    with db.Session() as orm, orm.begin():
        orm.add(ExportLog(user=user, format=fmt, selection=selection))
