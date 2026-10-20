"""Generation runs of the Test Case Designer: build a request from a stored backlog, store the outcome."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from wta import claude_launcher
from wta.db import Database
from wta.db import workbooks as wb
from wta.db.designer_content import CURRENT_STATUSES, linked_ids
from wta.db.designer_models import BacklogItem, DesignerTestCase, GenerationRun, OpenQuestion
from wta.db.models import utcnow
from wta.designer import prompt_builder as pb
from wta.designer.generation import GenerationOutcome, GenerationRequest
from wta.designer.settings import DesignerSettings


def request_for_backlog(db: Database, backlog_id: int, settings: DesignerSettings, domain_notes: list[str] = (),
                        supplementary: list[str] = (), user_guidance: str = "",
                        locked_test_cases: list[dict] = (), transcriptions: dict[str, str] | None = None
                        ) -> GenerationRequest:
    """A request with the backlog's text and its attached pictures (real image bytes, in attachment order)."""
    backlog = wb.load_backlog(db, backlog_id)
    with db.Session() as orm:
        item = orm.get(BacklogItem, backlog_id)
        sheet_name = item.sheet.name
    transcriptions = transcriptions or {}
    images = [pb.PromptImage(i.label, i.anchor_cell, i.mime, wb.image_data(db, i.md5), transcriptions.get(i.label, ""))
              for i in backlog.images]
    return GenerationRequest(backlog.number, backlog.title, sheet_name, backlog.first_row, backlog.last_row,
                             list(backlog.text_blocks), images, settings, list(domain_notes), list(supplementary),
                             user_guidance, list(locked_test_cases), backlog.status_raw)


def save_outcome(db: Database, backlog_id: int, outcome: GenerationOutcome) -> int:
    """Store a run (every outcome, including failures); test cases and questions only for a valid answer."""
    with db.Session() as orm, orm.begin():
        run = GenerationRun(
            backlog_item_id=backlog_id, model_id=outcome.model_id, model_resolution=outcome.model_resolution,
            max_output_tokens=claude_launcher.DESIGNER_MAX_OUTPUT_TOKENS, prompt_version=outcome.prompt_version,
            settings_snapshot=outcome.settings_snapshot, user_guidance=outcome.user_guidance, status=outcome.status,
            message=outcome.message, stop_reason=outcome.stop_reason, raw_response=outcome.response,
            schema_valid=outcome.ok, validation_errors=outcome.validation_errors, attempts=outcome.attempts,
        )
        orm.add(run)
        orm.flush()
        response = outcome.response or {}
        if outcome.ok or outcome.status == "insufficient_information":
            for seq, case in enumerate(response.get("test_cases") or [], 1):
                orm.add(DesignerTestCase(run_id=run.id, backlog_item_id=backlog_id, case_id=case["id"], seq=seq,
                                         data=case, locked=case["id"] in outcome.locked_ids))
            for text in response.get("open_questions") or []:
                orm.add(OpenQuestion(backlog_item_id=backlog_id, run_id=run.id, text=text))
        return run.id


@dataclass
class RunView:
    id: int
    backlog_id: int
    created_at: datetime
    status: str
    message: str
    model_id: str
    model_resolution: str
    prompt_version: str
    schema_valid: bool
    validation_errors: list[str]
    test_cases: list[dict]
    open_questions: list[str]
    attempts: list[dict]
    settings_snapshot: dict
    user_guidance: str = ""


def _view(run: GenerationRun) -> RunView:
    return RunView(run.id, run.backlog_item_id, run.created_at, run.status, run.message, run.model_id,
                   run.model_resolution, run.prompt_version, run.schema_valid, list(run.validation_errors or []),
                   [c.data for c in run.test_cases if c.deleted_at is None], [q.text for q in run.open_questions],
                   list(run.attempts or []), dict(run.settings_snapshot or {}), run.user_guidance or "")


def load_run(db: Database, run_id: int) -> RunView:
    with db.Session() as orm:
        run = orm.get(GenerationRun, run_id)
        if run is None:
            raise LookupError(f"Generation run {run_id} not found")
        return _view(run)


def list_runs(db: Database, backlog_id: int) -> list[RunView]:
    """All runs of the backlog, including those of the same backlog in earlier workbook versions; newest first."""
    with db.Session() as orm:
        runs = orm.scalars(select(GenerationRun).where(GenerationRun.backlog_item_id.in_(linked_ids(orm, backlog_id)))
                           .order_by(GenerationRun.id.desc()))
        return [_view(run) for run in runs]


def restore_run(db: Database, run_id: int) -> None:
    """Make an earlier valid run the current one again (spec 8.1 (6)); later runs stay in the history."""
    with db.Session() as orm, orm.begin():
        run = orm.get(GenerationRun, run_id)
        if run is None:
            raise LookupError(f"Generation run {run_id} not found")
        if run.status not in CURRENT_STATUSES:
            raise ValueError("Only a run with valid test cases can be restored.")
        run.restored_at = utcnow()
