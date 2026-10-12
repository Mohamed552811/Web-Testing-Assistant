"""Remote debugging (CDP) port for the embedded QtWebEngine browser.

QtWebEngine reads QTWEBENGINE_REMOTE_DEBUGGING when it starts, so `enable()` must run
before the QApplication is created. The port is bound to 127.0.0.1 only. If the port
is taken, QtWebEngine just logs an error and runs without debugging, so callers check
the endpoint with `endpoint_info()` after startup.
"""

import json
import os
import socket
import urllib.error
import urllib.request

HOST = "127.0.0.1"
ENV_VAR = "QTWEBENGINE_REMOTE_DEBUGGING"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def endpoint_url(port: int) -> str:
    return f"http://{HOST}:{port}"


def enable(port: int) -> list[str]:
    """Turn on remote debugging for this process. Returns extra QApplication arguments.

    Qt adds --remote-allow-origins=* by default; we narrow it so web pages opened in
    other browsers cannot open a DevTools WebSocket. Playwright connects without an
    Origin header and is unaffected.
    """
    os.environ[ENV_VAR] = f"{HOST}:{port}"
    return ["--webEngineArgs", f"--remote-allow-origins={endpoint_url(port)}"]


def endpoint_info(port: int, timeout: float = 1.0) -> dict | None:
    """The /json/version answer from the debugging endpoint, or None if it is not reachable."""
    try:
        with urllib.request.urlopen(f"{endpoint_url(port)}/json/version", timeout=timeout) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, ValueError):
        return None
