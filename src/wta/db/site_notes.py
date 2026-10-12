"""The user's notes about a site, added to the prompt of the next run on that site.

A site is the host name only (no scheme or port), so 127.0.0.1:8765 and 127.0.0.1:3000
share notes, as do http and https.
"""

from urllib.parse import urlsplit

from sqlalchemy import select

from wta.db.database import Database
from wta.db.models import SiteNote
from wta.urls import normalize_url


def site_for_url(url: str) -> str:
    normalized = normalize_url(url) or ""
    return (urlsplit(normalized).hostname or "").lower()


def list_notes(db: Database, site: str) -> list[SiteNote]:
    with db.Session() as orm:
        return list(
            orm.scalars(select(SiteNote).where(SiteNote.site == site.lower()).order_by(SiteNote.created_at, SiteNote.id))
        )


def add_note(db: Database, site: str, note: str) -> SiteNote:
    site, note = site.strip().lower(), note.strip()
    if not site:
        raise ValueError("A site is required.")
    if not note:
        raise ValueError("The note is empty.")
    with db.Session.begin() as orm:
        row = SiteNote(site=site, note=note)
        orm.add(row)
    return row


def delete_note(db: Database, note_id: int) -> None:
    with db.Session.begin() as orm:
        row = orm.get(SiteNote, note_id)
        if row is None:
            raise LookupError(f"Site note {note_id} not found")
        orm.delete(row)
