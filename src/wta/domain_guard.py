"""Blocks the embedded browser from leaving the allowed domains while Claude is running.

This is enforced by the app, not just asked of Claude in the prompt. Only top-level page
navigations are checked, so a page on an allowed domain can still load its scripts, fonts,
images and embedded frames (captchas, payment fields, maps) from elsewhere; blocking those
frames broke normal pages. Non-web schemes (file:, data:, javascript: …) are blocked for
navigations too, so the browser cannot be used to read local files.
"""

import ipaddress

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor

_NAVIGATION_TYPES = (QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame,)
_ALWAYS_ALLOWED = ("about:blank",)


def host_allowed(host: str, allowed: list[str]) -> bool:
    """True if `host` is an allowed domain or a subdomain of one. IP addresses must match exactly."""
    host = host.lower().strip("[]").rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    for domain in allowed:
        domain = domain.lower().strip("[]").rstrip(".")
        if host == domain or (not is_ip and host.endswith("." + domain)):
            return True
    return False


def navigation_allowed(url: QUrl, allowed: list[str]) -> bool:
    if url.toString() in _ALWAYS_ALLOWED:
        return True
    if url.scheme().lower() not in ("http", "https"):
        return False
    return host_allowed(url.host(), allowed)


class NavigationGuard(QWebEngineUrlRequestInterceptor):
    """Install on the browser profile. Inactive until `activate()` is called."""

    blocked = Signal(str)  # the URL that was blocked

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.allowed: list[str] = []
        self.active = False

    def activate(self, allowed: list[str]) -> None:
        self.allowed = list(allowed)
        self.active = True

    def deactivate(self) -> None:
        self.active = False

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802 (Qt name)
        if not self.active or info.resourceType() not in _NAVIGATION_TYPES:
            return
        url = info.requestUrl()
        if not navigation_allowed(url, self.allowed):
            info.block(True)
            self.blocked.emit(url.toString())
