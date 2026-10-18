"""SQLAlchemy models.

Claude's findings (Step, Bug, Screenshot, logs, traces) and the user's Review are
separate tables, so an override never changes what Claude recorded.
Timestamps are naive UTC.
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MAX_SCENARIOS_PER_BATCH = 5


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ScenarioKind(enum.StrEnum):
    SCENARIO = "scenario"
    SMOKE = "smoke"


class StepStatus(enum.StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"

    @property
    def smoke_label(self) -> str:
        """Label used on the smoke test tab."""
        return {"pass": "Pass", "fail": "Bug", "blocked": "Blocked", "not_run": "Not run"}[self.value]


class ReviewDecision(enum.StrEnum):
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    # Sends the step back for another run; it has no final status until reviewed again.
    RERUN_REQUESTED = "rerun_requested"


class TraceKind(enum.StrEnum):
    NOTE = "note"
    ACTION = "action"
    RESULT = "result"


# TFS / Azure DevOps Bug work item values (Microsoft.VSTS.Common.Severity / Priority).
BUG_SEVERITIES = ("1 - Critical", "2 - High", "3 - Medium", "4 - Low")
BUG_PRIORITIES = (1, 2, 3, 4)


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    """Store enum values (not names) as text with a CHECK constraint."""
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
        length=20,
    )


class Base(DeclarativeBase):
    pass


class TestSession(Base):
    __tablename__ = "test_session"
    __test__ = False  # not a unittest/pytest class

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    target_url: Mapped[str] = mapped_column(Text)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    browser_version: Mapped[str | None] = mapped_column(String(100))
    viewport_width: Mapped[int] = mapped_column(Integer, default=1280)
    viewport_height: Mapped[int] = mapped_column(Integer, default=800)
    # Test-environment account only; stays in this local database.
    test_account_email: Mapped[str | None] = mapped_column(String(320))
    test_account_password: Mapped[str | None] = mapped_column(Text)
    # For the exported summary report.
    application: Mapped[str | None] = mapped_column(String(200))
    test_scope: Mapped[str | None] = mapped_column(Text)  # one item per line

    scenarios: Mapped[list["Scenario"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="(Scenario.batch_number, Scenario.position)",
    )

    __table_args__ = (
        CheckConstraint("viewport_width > 0 AND viewport_height > 0", name="ck_session_viewport"),
    )


class Scenario(Base):
    __tablename__ = "scenario"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("test_session.id", ondelete="CASCADE"), index=True)
    batch_number: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[ScenarioKind] = mapped_column(_enum(ScenarioKind, "scenario_kind"), default=ScenarioKind.SCENARIO)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")  # smoke tests: page description; scenarios: legacy
    # The steps the user wrote, one per line ("1. ...\n2. ..."). Not `steps`: that is Claude's step results.
    test_steps: Mapped[str | None] = mapped_column(Text)
    expected_result: Mapped[str | None] = mapped_column(Text)  # what must be true after the steps
    test_data: Mapped[str] = mapped_column(Text, default="")

    session: Mapped[TestSession] = relationship(back_populates="scenarios")
    smoke_page: Mapped["SmokePage | None"] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", uselist=False
    )
    steps: Mapped[list["Step"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="Step.order"
    )
    traces: Mapped[list["RunTrace"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="(RunTrace.timestamp, RunTrace.id)"
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="Run.started_at"
    )

    __table_args__ = (
        # Scenario and smoke-test batches are numbered separately.
        UniqueConstraint("session_id", "kind", "batch_number", "position", name="uq_scenario_slot"),
        CheckConstraint("batch_number >= 1", name="ck_scenario_batch"),
        CheckConstraint(f"position BETWEEN 1 AND {MAX_SCENARIOS_PER_BATCH}", name="ck_scenario_position"),
    )


class SmokePage(Base):
    __tablename__ = "smoke_page"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id", ondelete="CASCADE"), unique=True)
    page_name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    how_to_reach: Mapped[str] = mapped_column(Text, default="")
    login_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    page_information: Mapped[str] = mapped_column(Text, default="")
    expected_elements: Mapped[str] = mapped_column(Text, default="")

    scenario: Mapped[Scenario] = relationship(back_populates="smoke_page")


class Step(Base):
    __tablename__ = "step"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column("step_order", Integer)
    screen_name: Mapped[str] = mapped_column(String(200), default="")
    element: Mapped[str] = mapped_column(String(300), default="")
    action: Mapped[str] = mapped_column(Text, default="")
    expected_result: Mapped[str] = mapped_column(Text, default="")
    actual_result: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"), default=StepStatus.NOT_RUN)
    reasoning: Mapped[str] = mapped_column(Text, default="")

    scenario: Mapped[Scenario] = relationship(back_populates="steps")
    screenshots: Mapped[list["Screenshot"]] = relationship(back_populates="step", cascade="all, delete-orphan")
    capture_logs: Mapped[list["CaptureLog"]] = relationship(back_populates="step", cascade="all, delete-orphan")
    action_logs: Mapped[list["ActionLog"]] = relationship(
        back_populates="step", cascade="all, delete-orphan", order_by="(ActionLog.timestamp, ActionLog.id)"
    )
    bugs: Mapped[list["Bug"]] = relationship(back_populates="step", cascade="all, delete-orphan")
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="step", cascade="all, delete-orphan", order_by="(Review.reviewed_at, Review.id)"
    )

    __table_args__ = (
        UniqueConstraint("scenario_id", "step_order", name="uq_step_order"),
        CheckConstraint("step_order >= 1", name="ck_step_order"),
    )

    @property
    def latest_review(self) -> "Review | None":
        return self.reviews[-1] if self.reviews else None

    @property
    def final_status(self) -> StepStatus | None:
        """The reviewed status, or None while the step is not final (unreviewed or re-run requested)."""
        review = self.latest_review
        return review.final_status if review else None

    @property
    def rerun_requested(self) -> bool:
        review = self.latest_review
        return review is not None and review.decision == ReviewDecision.RERUN_REQUESTED


class Screenshot(Base):
    __tablename__ = "screenshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("step.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    step: Mapped[Step] = relationship(back_populates="screenshots")


class CaptureLog(Base):
    __tablename__ = "capture_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("step.id", ondelete="CASCADE"), index=True)
    console_errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    failed_requests: Mapped[list[str]] = mapped_column(JSON, default=list)
    page_url: Mapped[str] = mapped_column(Text, default="")

    step: Mapped[Step] = relationship(back_populates="capture_logs")


class ActionLog(Base):
    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("step.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    step: Mapped[Step] = relationship(back_populates="action_logs")


class RunOutcomeKind(enum.StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "stopped"
    FAILED = "failed"


class Run(Base):
    """One `claude -p` run of one scenario (including any --resume continuations)."""

    __tablename__ = "run"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id", ondelete="CASCADE"), index=True)
    run_dir: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    outcome: Mapped[RunOutcomeKind] = mapped_column(_enum(RunOutcomeKind, "run_outcome"), default=RunOutcomeKind.RUNNING)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    num_turns: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Plan usage before the run and as reported during it (percent), from rate_limit_event.
    five_hour_before: Mapped[float | None] = mapped_column()
    five_hour_seen: Mapped[float | None] = mapped_column()
    seven_day_before: Mapped[float | None] = mapped_column()
    seven_day_seen: Mapped[float | None] = mapped_column()
    # How much of the 5-hour window this run used (percentage points), for the estimate before a batch.
    five_hour_used: Mapped[float | None] = mapped_column()
    model: Mapped[str | None] = mapped_column(String(100))  # as Claude Code reported it
    summary: Mapped[str] = mapped_column(Text, default="")
    site_notes_used: Mapped[int] = mapped_column(Integer, default=0)
    # Claude Code's own session for this run, and when the test password was removed from it.
    claude_session_id: Mapped[str | None] = mapped_column(String(64))
    transcript_cleaned_at: Mapped[datetime | None] = mapped_column(DateTime)

    scenario: Mapped[Scenario] = relationship(back_populates="runs")
    traces: Mapped[list["RunTrace"]] = relationship(
        back_populates="run", order_by="(RunTrace.timestamp, RunTrace.id)"
    )


class RunTrace(Base):
    __tablename__ = "run_trace"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("step.id", ondelete="SET NULL"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    kind: Mapped[TraceKind] = mapped_column(_enum(TraceKind, "trace_kind"))
    text: Mapped[str] = mapped_column(Text)

    scenario: Mapped[Scenario] = relationship(back_populates="traces")
    run: Mapped[Run | None] = relationship(back_populates="traces")


class Bug(Base):
    """Field names follow the TFS / Azure DevOps Bug work item for later import."""

    __tablename__ = "bug"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("step.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))  # System.Title (max 255)
    severity: Mapped[str] = mapped_column(String(20), default="3 - Medium")  # Microsoft.VSTS.Common.Severity
    priority: Mapped[int] = mapped_column(Integer, default=2)  # Microsoft.VSTS.Common.Priority
    repro_steps: Mapped[str] = mapped_column(Text, default="")  # Microsoft.VSTS.TCM.ReproSteps
    expected: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    report_text: Mapped[str] = mapped_column(Text, default="")
    # Claude's own version (JSON), kept the first time the user edits the bug, so an edit never erases it.
    claude_original: Mapped[str | None] = mapped_column(Text)

    step: Mapped[Step] = relationship(back_populates="bugs")

    __table_args__ = (
        CheckConstraint(
            "severity IN (" + ", ".join(f"'{s}'" for s in BUG_SEVERITIES) + ")", name="ck_bug_severity"
        ),
        CheckConstraint(
            "priority IN (" + ", ".join(str(p) for p in BUG_PRIORITIES) + ")", name="ck_bug_priority"
        ),
    )


class Review(Base):
    """The user's decision on a step. Several rows keep the history; the latest one counts."""

    __tablename__ = "review"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("step.id", ondelete="CASCADE"), index=True)
    decision: Mapped[ReviewDecision] = mapped_column(_enum(ReviewDecision, "review_decision"))
    final_status: Mapped[StepStatus | None] = mapped_column(_enum(StepStatus, "review_final_status"))
    comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    step: Mapped[Step] = relationship(back_populates="reviews")

    __table_args__ = (
        CheckConstraint(
            "(decision = 'rerun_requested') = (final_status IS NULL)", name="ck_review_final_status"
        ),
    )


class SiteNote(Base):
    __tablename__ = "site_note"

    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(String(255), index=True)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
