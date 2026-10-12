"""Opening and saving test sessions."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from wta.db.database import Database
from wta.db.models import Scenario, Step, TestSession
from wta.urls import normalize_url


@dataclass
class SessionDetails:
    """What the user enters for a session."""

    name: str
    target_url: str
    allowed_domains: list[str] = field(default_factory=list)
    viewport_width: int = 1280
    viewport_height: int = 800
    test_account_email: str = ""
    test_account_password: str = ""


def parse_domains(text: str) -> list[str]:
    """Split comma/space/newline separated domains into unique lower-case host names.

    Full URLs are reduced to their host; ports and paths are dropped.
    """
    domains: list[str] = []
    for item in text.replace(",", " ").split():
        url = normalize_url(item)
        host = urlsplit(url).hostname if url else None
        if host and host not in domains:
            domains.append(host)
    return domains


def validate(details: SessionDetails) -> SessionDetails:
    """Return cleaned details, or raise ValueError with a message for the user."""
    name = details.name.strip()
    if not name:
        raise ValueError("Session name is required.")
    url = normalize_url(details.target_url)
    if url is None:
        raise ValueError("Target URL must be a valid http(s) URL.")
    if details.viewport_width <= 0 or details.viewport_height <= 0:
        raise ValueError("Viewport width and height must be positive.")
    domains = parse_domains(" ".join(details.allowed_domains))
    target_host = urlsplit(url).hostname
    if target_host not in domains:
        domains.insert(0, target_host)
    return SessionDetails(
        name=name,
        target_url=url,
        allowed_domains=domains,
        viewport_width=details.viewport_width,
        viewport_height=details.viewport_height,
        test_account_email=details.test_account_email.strip(),
        test_account_password=details.test_account_password,
    )


def _apply(session: TestSession, details: SessionDetails) -> None:
    session.name = details.name
    session.target_url = details.target_url
    session.allowed_domains = list(details.allowed_domains)
    session.viewport_width = details.viewport_width
    session.viewport_height = details.viewport_height
    session.test_account_email = details.test_account_email or None
    session.test_account_password = details.test_account_password or None


def create_session(db: Database, details: SessionDetails, browser_version: str | None = None) -> TestSession:
    details = validate(details)
    session = TestSession(browser_version=browser_version)
    _apply(session, details)
    with db.Session.begin() as orm:
        orm.add(session)
    return load_session(db, session.id)


def update_session(db: Database, session_id: int, details: SessionDetails) -> TestSession:
    details = validate(details)
    with db.Session.begin() as orm:
        session = orm.get(TestSession, session_id)
        if session is None:
            raise LookupError(f"Session {session_id} not found")
        _apply(session, details)
    return load_session(db, session_id)


def list_sessions(db: Database) -> list[TestSession]:
    """All sessions, newest first, without their scenarios."""
    with db.Session() as orm:
        return list(orm.scalars(select(TestSession).order_by(TestSession.created_at.desc(), TestSession.id.desc())))


def load_session(db: Database, session_id: int) -> TestSession:
    """A session with its scenarios, smoke pages, steps and everything under the steps loaded."""
    with db.Session() as orm:
        session = orm.scalars(
            select(TestSession)
            .where(TestSession.id == session_id)
            .options(
                selectinload(TestSession.scenarios).selectinload(Scenario.smoke_page),
                selectinload(TestSession.scenarios).selectinload(Scenario.traces),
                selectinload(TestSession.scenarios)
                .selectinload(Scenario.steps)
                .selectinload(Step.reviews),
                selectinload(TestSession.scenarios).selectinload(Scenario.steps).selectinload(Step.bugs),
                selectinload(TestSession.scenarios).selectinload(Scenario.steps).selectinload(Step.screenshots),
                selectinload(TestSession.scenarios).selectinload(Scenario.steps).selectinload(Step.capture_logs),
                selectinload(TestSession.scenarios).selectinload(Scenario.steps).selectinload(Step.action_logs),
            )
        ).one_or_none()
        if session is None:
            raise LookupError(f"Session {session_id} not found")
        return session


def details_of(session: TestSession) -> SessionDetails:
    return SessionDetails(
        name=session.name,
        target_url=session.target_url,
        allowed_domains=list(session.allowed_domains),
        viewport_width=session.viewport_width,
        viewport_height=session.viewport_height,
        test_account_email=session.test_account_email or "",
        test_account_password=session.test_account_password or "",
    )
