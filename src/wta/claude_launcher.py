"""Everything about how Claude Code is started lives here, so it is easy to change later.

Claude Code is run as the `claude` CLI on the user's own subscription login. This module
never sets or reads ANTHROPIC_API_KEY and never touches Claude credentials.
"""

import json
import os
import shlex
from pathlib import Path

from wta import cdp

CLAUDE_EXECUTABLE = "claude"
# Pinned so a new Playwright MCP release cannot change the tools under us.
PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp@0.0.83"
MCP_SERVER_NAME = "playwright"
# All tools of the MCP server above (Claude Code's mcp__<server> permission syntax).
PLAYWRIGHT_TOOLS = f"mcp__{MCP_SERVER_NAME}"


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
