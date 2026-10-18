"""Test data variables: {{random_email}}, {{today}} and others, filled in fresh for each run.

The same variable gets the same value everywhere in one run (steps, expected result, test data),
so "Sign up with {{random_email}}" and "The welcome mail goes to {{random_email}}" agree.
"""

import re
import secrets
import string
import uuid
from datetime import datetime, timedelta

PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def _random_text(length: int = 8) -> str:
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(length))


GENERATORS = {
    "random_email": lambda now: f"wta.{_random_text(6)}{secrets.randbelow(1000):03d}@example.com",
    "random_number": lambda now: str(100000 + secrets.randbelow(900000)),
    "random_text": lambda now: _random_text(),
    "random_name": lambda now: f"Test {_random_text(5).capitalize()}",
    "uuid": lambda now: str(uuid.uuid4()),
    "today": lambda now: now.strftime("%Y-%m-%d"),
    "tomorrow": lambda now: (now + timedelta(days=1)).strftime("%Y-%m-%d"),
    "yesterday": lambda now: (now - timedelta(days=1)).strftime("%Y-%m-%d"),
    "now": lambda now: now.strftime("%Y-%m-%d %H:%M"),
    "timestamp": lambda now: now.strftime("%Y%m%d%H%M%S"),
}
HELP = ("Variables filled in fresh for each run: " + ", ".join("{{" + name + "}}" for name in GENERATORS) + ".")


class Filler:
    """Fills variables for one run, remembering each value so it is the same everywhere in that run."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime.now()
        self.values: dict[str, str] = {}
        self.unknown: set[str] = set()

    def fill(self, text: str) -> str:
        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in GENERATORS:
                self.unknown.add(name)
                return match.group(0)
            if name not in self.values:
                self.values[name] = GENERATORS[name](self.now)
            return self.values[name]
        return PATTERN.sub(replace, text or "")


def used_variables(*texts: str) -> set[str]:
    return {m.group(1) for text in texts for m in PATTERN.finditer(text or "")}
