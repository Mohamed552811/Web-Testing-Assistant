"""Turn what the user types in the URL bar into a loadable URL."""

from urllib.parse import urlsplit

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def normalize_url(text: str) -> str | None:
    """Return a full http(s) URL for the URL bar text, or None if it is not usable.

    Bare hosts get https://, except local development hosts, which get http://.
    Schemes other than http and https are rejected so the bar cannot open
    file:, javascript: or similar URLs.
    """
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        return None

    if "://" not in text:
        host = text.split("/", 1)[0].split(":", 1)[0].lower()
        if text.startswith("[::1]"):
            host = "[::1]"
        local = host in _LOCAL_HOSTS or host.endswith((".localhost", ".test"))
        text = ("http://" if local else "https://") + text

    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return text
