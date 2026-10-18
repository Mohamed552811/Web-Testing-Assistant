"""Opening and saving test sessions."""

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from wta.db.database import Database
from wta.db.models import Scenario, Step, TestAccount, TestSession
from wta.urls import normalize_url


MAIN_ACCOUNT = "Main account"
MAX_OTHER_ACCOUNTS = 10


@dataclass
class AccountDetails:
    """Another test account of a session; `id` is None until it is saved."""

    role: str
    email: str
    password: str = ""
    id: int | None = None


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
    application: str = ""
    test_scope: str = ""
    other_accounts: list[AccountDetails] = field(default_factory=list)
    fixtures_dir: str = ""


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
    accounts = _validate_accounts(details.other_accounts)
    fixtures = details.fixtures_dir.strip()
    if fixtures:
        if not Path(fixtures).is_dir():
            raise ValueError(f"The fixtures folder does not exist: {fixtures}")
        fixtures = str(Path(fixtures).resolve())
    return SessionDetails(
        name=name,
        target_url=url,
        allowed_domains=domains,
        viewport_width=details.viewport_width,
        viewport_height=details.viewport_height,
        test_account_email=details.test_account_email.strip(),
        test_account_password=details.test_account_password,
        application=details.application.strip(),
        test_scope="\n".join(line.strip() for line in details.test_scope.splitlines() if line.strip()),
        other_accounts=accounts,
        fixtures_dir=fixtures,
    )


def _validate_accounts(accounts: list[AccountDetails]) -> list[AccountDetails]:
    if len(accounts) > MAX_OTHER_ACCOUNTS:
        raise ValueError(f"At most {MAX_OTHER_ACCOUNTS} other accounts per session.")
    cleaned, roles = [], {MAIN_ACCOUNT.lower()}
    for account in accounts:
        role, email = account.role.strip(), account.email.strip()
        if not role or not email:
            raise ValueError("Each other account needs a role (e.g. Sales manager) and an email.")
        if role.lower() in roles:
            raise ValueError(f"The role “{role}” is used twice. Give each account its own role name.")
        roles.add(role.lower())
        cleaned.append(AccountDetails(role[:100], email[:320], account.password, account.id))
    return cleaned


def _apply_accounts(session: TestSession, accounts: list[AccountDetails]) -> None:
    """Update, add and remove other accounts. A removed account's scenarios run as the main account."""
    existing = {a.id: a for a in session.other_accounts}
    keep = []
    for details in accounts:
        row = existing.get(details.id) if details.id is not None else None
        if row is None:
            row = TestAccount()
        row.role, row.email, row.password = details.role, details.email, details.password
        keep.append(row)
    for row in session.other_accounts:
        if row not in keep:
            for scenario in session.scenarios:
                if scenario.account_id == row.id:
                    scenario.account_id = None
    session.other_accounts = keep


def _apply(session: TestSession, details: SessionDetails) -> None:
    session.name = details.name
    session.target_url = details.target_url
    session.allowed_domains = list(details.allowed_domains)
    session.viewport_width = details.viewport_width
    session.viewport_height = details.viewport_height
    session.test_account_email = details.test_account_email or None
    session.test_account_password = details.test_account_password or None
    session.application = details.application or None
    session.test_scope = details.test_scope or None
    session.fixtures_dir = details.fixtures_dir or None
    _apply_accounts(session, details.other_accounts)


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
                selectinload(TestSession.other_accounts),
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
        application=session.application or "",
        test_scope=session.test_scope or "",
        other_accounts=[AccountDetails(a.role, a.email, a.password, a.id) for a in session.other_accounts],
        fixtures_dir=session.fixtures_dir or "",
    )


@dataclass(frozen=True)
class Account:
    """The account a scenario runs as."""

    role: str
    email: str
    password: str


def account_for(session: TestSession, account_id: int | None) -> Account:
    """A scenario's account: one of the other accounts, or the main account."""
    for other in session.other_accounts:
        if other.id == account_id:
            return Account(other.role, other.email, other.password)
    return Account(MAIN_ACCOUNT, session.test_account_email or "", session.test_account_password or "")


def account_choices(session: TestSession) -> list[tuple[int | None, str]]:
    """(account id, label) for a "Run as" list; None is the main account."""
    main = f"{MAIN_ACCOUNT} ({session.test_account_email})" if session.test_account_email else MAIN_ACCOUNT
    return [(None, main)] + [(a.id, f"{a.role} ({a.email})") for a in session.other_accounts]


def account_choices_for(db: Database, session_id: int) -> list[tuple[int | None, str]]:
    with db.Session() as orm:
        session = orm.get(TestSession, session_id)
        if session is None:
            raise LookupError(f"Session {session_id} not found")
        return account_choices(session)


def session_passwords(session: TestSession) -> list[str]:
    """Every test password of the session, for redaction."""
    return [p for p in [session.test_account_password] + [a.password for a in session.other_accounts] if p]
