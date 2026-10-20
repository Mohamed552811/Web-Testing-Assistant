"""Everything about how Claude Code is started lives here, so it is easy to change later.

Claude Code is run as the `claude` CLI on the user's own subscription login. This module
never sets or reads ANTHROPIC_API_KEY and never touches Claude credentials.
"""

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wta import cdp

CLAUDE_EXECUTABLE = "claude"
# Pinned so a new Playwright MCP release cannot change the tools under us.
PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp@0.0.83"
MCP_SERVER_NAME = "playwright"
# All tools of the MCP server above (Claude Code's mcp__<server> permission syntax).
PLAYWRIGHT_TOOLS = f"mcp__{MCP_SERVER_NAME}"
# Browser tools a test run may use. Left out on purpose: browser_evaluate and
# browser_run_code_unsafe (arbitrary JavaScript), browser_close, and browser_file_upload
# (allowed only when the session has a fixtures folder, see run_args).
PLAYWRIGHT_RUN_TOOLS = tuple(
    f"mcp__{MCP_SERVER_NAME}__browser_{name}"
    for name in (
        "snapshot", "click", "type", "fill_form", "select_option", "hover", "press_key", "drag", "drop",
        "navigate", "navigate_back", "wait_for", "take_screenshot", "tabs", "handle_dialog", "find",
        "console_messages", "network_requests", "network_request",
    )
)
# Denied outright as well, so they are not offered to Claude at all where Claude Code allows that.
PLAYWRIGHT_DENIED_TOOLS = tuple(
    f"mcp__{MCP_SERVER_NAME}__browser_{name}"
    for name in ("evaluate", "run_code_unsafe", "file_upload", "close", "install")
)
UPLOAD_TOOL = f"mcp__{MCP_SERVER_NAME}__browser_file_upload"
# Built-in tools for writing results.json and screenshots; --restricted confines them to the run folder.
FILE_TOOLS = ("Read", "Write", "Edit")
# Never passed to Claude Code, so it always uses the user's subscription login.
REMOVED_ENV_VARS = ("ANTHROPIC_API_KEY",)


def find_claude() -> list[str] | None:
    """Command prefix that starts the Claude Code CLI, or None if it is not installed."""
    path = shutil.which(CLAUDE_EXECUTABLE)
    if path is None:
        return None
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", path]  # npm shims cannot be started directly
    return [path]


def child_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for Claude Code processes, without variables that would bypass the subscription login."""
    env = dict(os.environ if base is None else base)
    for name in REMOVED_ENV_VARS:
        env.pop(name, None)
    return env


@dataclass
class SignInStatus:
    ok: bool
    message: str
    subscription: str = ""


def parse_auth_status(returncode: int, stdout: str) -> SignInStatus:
    """Interpret `claude auth status --json`. Only loggedIn, authMethod and subscriptionType are used."""
    try:
        data = json.loads(stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return SignInStatus(False, f"Could not read Claude Code sign-in status (exit code {returncode}).")
    if not data.get("loggedIn"):
        return SignInStatus(False, "Claude Code is not signed in. Run `claude auth login` in a terminal, then check again.")
    method = data.get("authMethod")
    if method != "claude.ai":
        return SignInStatus(
            False,
            f"Claude Code is signed in with {method!r}, not a claude.ai subscription. "
            "Sign in with your Claude subscription (`claude auth login`).",
        )
    subscription = str(data.get("subscriptionType") or "")
    return SignInStatus(True, f"Signed in to Claude Code ({subscription or 'subscription'})", subscription)


# Node.js runs Playwright MCP (started with npx). Playwright MCP needs Node.js 18 or later.
MIN_NODE_MAJOR = 18
NODE_DOWNLOAD_URL = "https://nodejs.org/en/download"
CLAUDE_SETUP_URL = "https://code.claude.com/docs/en/setup"
CLAUDE_INSTALL_POWERSHELL = "irm https://claude.ai/install.ps1 | iex"


@dataclass
class NodeStatus:
    ok: bool
    message: str
    version: str = ""


def parse_node_version(text: str) -> tuple[int, ...] | None:
    """(major, minor, patch) from `node --version` output such as "v22.11.0"."""
    parts = text.strip().lstrip("v").split(".")
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None


def check_node(timeout: float = 20) -> NodeStatus:
    """Is a recent enough Node.js (with npx) installed? Blocking; call it off the UI thread."""
    node, npx = shutil.which("node"), shutil.which("npx")
    if node is None or npx is None:
        return NodeStatus(False, "Node.js is not installed (no `node`/`npx` on PATH). It runs the browser tools.")
    try:
        done = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return NodeStatus(False, f"Could not run Node.js: {exc}")
    version = parse_node_version(done.stdout)
    if version is None:
        return NodeStatus(False, f"Could not read the Node.js version from {done.stdout.strip()!r}.")
    text = ".".join(map(str, version))
    if version[0] < MIN_NODE_MAJOR:
        return NodeStatus(False, f"Node.js {text} is too old; version {MIN_NODE_MAJOR} or later is needed.", text)
    return NodeStatus(True, f"Node.js {text}", text)


def check_sign_in(timeout: float = 60) -> SignInStatus:
    """Run `claude auth status --json`. Blocking; call it off the UI thread."""
    command = find_claude()
    if command is None:
        return SignInStatus(False, "Claude Code is not installed (no `claude` on PATH). Install it, sign in, then check again.")
    try:
        done = subprocess.run(
            command + ["auth", "status", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=child_environment(), stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SignInStatus(False, f"Could not run Claude Code: {exc}")
    return parse_auth_status(done.returncode, done.stdout)


def resume_command(run_dir: Path, session_id: str) -> str:
    """A command you can paste in a terminal to reopen a run's Claude session and ask about it.

    It starts in the run's folder (Claude Code keeps sessions per folder) and gives Claude no
    tools and none of your own settings, so it can only answer questions about the run."""
    command = (find_claude() or [CLAUDE_EXECUTABLE])[-1:]
    return format_for_shell(command + ["--resume", session_id, "--restricted", "--strict-mcp-config", "--tools="],
                            cwd=run_dir)


def run_args(mcp_config: Path, resume_session: str | None = None, model: str = "",
             allow_upload: bool = False) -> list[str]:
    """Arguments (after the executable) for a test run. The prompt is written to stdin,
    so the test account never appears on a command line. `resume_session` continues an
    earlier run (used after the user answers a confirmation request). `model` is an alias
    such as "sonnet" or a full model name; empty means Claude Code's own default.
    `allow_upload` permits the file upload tool; Playwright MCP limits it to files in the run folder."""
    resume = ["--resume", resume_session] if resume_session else []
    allowed = FILE_TOOLS + PLAYWRIGHT_RUN_TOOLS + ((UPLOAD_TOOL,) if allow_upload else ())
    denied = tuple(t for t in PLAYWRIGHT_DENIED_TOOLS if not (allow_upload and t == UPLOAD_TOOL))
    return resume + (["--model", model] if model else []) + [
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--mcp-config", str(mcp_config),
        "--strict-mcp-config",
        # Ignores the user's settings and hooks, removes command-running tools,
        # and confines file tools to the working directory (the run folder).
        "--restricted",
        "--tools=" + ",".join(FILE_TOOLS),
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(denied),
        "--permission-mode", "dontAsk",
    ]


def suggest_args(model: str = "") -> list[str]:
    """Arguments for asking Claude to suggest scenarios: text in, text out. No tools at all, no MCP
    servers, none of the user's settings, and the session is not saved (the prompt may hold page text)."""
    return (["--model", model] if model else []) + [
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--restricted",
        "--strict-mcp-config",
        "--tools=",
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
    ]


# Test Case Designer: ask for the model's maximum output; Claude Code lowers it to each model's cap.
DESIGNER_MAX_OUTPUT_TOKENS = 128000


def designer_args(model: str, system_prompt_file: Path, json_schema: str) -> list[str]:
    """Arguments for one Test Case Designer generation: the backlog and its screenshots go in on stdin as one
    stream-json user message; the answer comes back as `structured_output` checked against `json_schema`.
    No tools, no MCP servers, none of the user's settings, and the session is not saved."""
    return ["--model", model] + [
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--restricted",
        "--strict-mcp-config",
        "--tools=",
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        "--system-prompt-file", str(system_prompt_file),
        "--json-schema", json_schema,
    ]


def designer_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = child_environment(base)
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(DESIGNER_MAX_OUTPUT_TOKENS)
    return env


def stream_json_user_message(content: list[dict]) -> str:
    """One line for `--input-format stream-json` (the Agent SDK's user message shape)."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": content},
                       "parent_tool_use_id": None}) + "\n"


def playwright_mcp_config(cdp_port: int, output_dir: Path) -> dict:
    """MCP config that attaches Playwright MCP to the embedded browser's CDP endpoint."""
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": "npx",
                "args": [
                    "-y",
                    PLAYWRIGHT_MCP_PACKAGE,
                    "--cdp-endpoint",
                    cdp.endpoint_url(cdp_port),
                    "--output-dir",
                    Path(output_dir).as_posix(),
                ],
            }
        }
    }


def write_mcp_config(path: Path, cdp_port: int, output_dir: Path) -> Path:
    """Write the MCP config atomically and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(playwright_mcp_config(cdp_port, output_dir), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def manual_command(mcp_config: Path, prompt: str) -> list[str]:
    """`claude -p` arguments for a hand-run check: Playwright tools only, no built-in tools."""
    return [
        CLAUDE_EXECUTABLE,
        "-p",
        prompt,
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        # One argument, not `--tools ""`: Windows PowerShell 5.1 drops empty arguments.
        "--tools=",
        "--allowedTools",
        PLAYWRIGHT_TOOLS,
        "--permission-mode",
        "dontAsk",
    ]


def format_for_shell(args: list[str], cwd: Path | None = None, windows: bool = os.name == "nt") -> str:
    """Render arguments as one line for PowerShell (Windows) or a POSIX shell.

    With `cwd`, the line first changes to that folder. Playwright MCP saves screenshots
    that Claude names explicitly relative to the working directory, not to --output-dir.
    """
    def quote(arg: str) -> str:
        if not windows:
            return shlex.quote(arg)
        if arg and not any(c in arg for c in " '\"$`;&|()<>{}@#,"):
            return arg
        return "'" + arg.replace("'", "''") + "'"

    line = " ".join(quote(a) for a in args)
    if cwd is None:
        return line
    return f"Set-Location {quote(str(cwd))}; {line}" if windows else f"cd {quote(str(cwd))} && {line}"
