"""Turn `claude -p --output-format stream-json --verbose` lines into events for the run panel."""

import json
from dataclasses import dataclass, field

from wta.db.models import TraceKind

# How much of a tool result ("what Claude saw") to keep in the panel and trace.
RESULT_TEXT_LIMIT = 1500


@dataclass
class TraceEvent:
    kind: TraceKind
    text: str
    tool: str = ""


@dataclass
class RunSummary:
    ok: bool
    subtype: str
    num_turns: int | None = None
    duration_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    text: str = ""
    errors: list[str] = field(default_factory=list)
    structured_output: object = None  # with --json-schema: the validated JSON
    stop_reason: str = ""
    api_error_status: int | None = None


@dataclass
class ParsedLine:
    events: list[TraceEvent] = field(default_factory=list)
    rate_limit: dict | None = None  # the raw rate_limit_info object
    summary: RunSummary | None = None
    init_tools: list[str] | None = None
    model: str | None = None  # the model Claude Code actually uses, from the init message
    session_id: str | None = None


def _short_tool_name(name: str) -> str:
    return name.removeprefix("mcp__playwright__")


def _describe_tool_use(name: str, tool_input: dict) -> str:
    short = _short_tool_name(name)
    if short in ("Write", "Edit", "Read"):
        return f"{short} {tool_input.get('file_path', '')}"
    parts = []
    for key in ("url", "element", "text", "key", "values", "filename", "action", "time"):
        if key in tool_input and tool_input[key] not in (None, "", []):
            parts.append(f"{key}={json.dumps(tool_input[key], ensure_ascii=False)}")
    return f"{short} " + " ".join(parts) if parts else short


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    texts = []
    for item in content or []:
        if isinstance(item, dict):
            texts.append(item.get("text") if item.get("type") == "text" else f"<{item.get('type', 'data')}>")
    return "\n".join(t for t in texts if t)


def parse_line(line: str) -> ParsedLine:
    """Parse one stream-json line. Unknown or malformed lines give an empty result."""
    out = ParsedLine()
    line = line.strip()
    if not line.startswith("{"):
        return out
    try:
        message = json.loads(line)
    except ValueError:
        return out
    kind = message.get("type")
    out.session_id = message.get("session_id")

    if kind == "system" and message.get("subtype") == "init":
        out.init_tools = list(message.get("tools") or [])
        out.model = message.get("model") if isinstance(message.get("model"), str) else None
    elif kind == "assistant":
        for block in message.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.events.append(TraceEvent(TraceKind.NOTE, block["text"].strip()))
            elif block.get("type") == "tool_use":
                name = block.get("name", "")
                out.events.append(
                    TraceEvent(TraceKind.ACTION, _describe_tool_use(name, block.get("input") or {}), _short_tool_name(name))
                )
    elif kind == "user":
        content = message.get("message", {}).get("content", [])
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = _tool_result_text(block.get("content"))
                if len(text) > RESULT_TEXT_LIMIT:
                    text = text[:RESULT_TEXT_LIMIT] + " …"
                prefix = "Error: " if block.get("is_error") else ""
                out.events.append(TraceEvent(TraceKind.RESULT, prefix + text))
    elif kind == "rate_limit_event":
        info = message.get("rate_limit_info")
        if isinstance(info, dict):
            out.rate_limit = info
    elif kind == "result":
        usage = message.get("usage") or {}
        out.summary = RunSummary(
            ok=message.get("subtype") == "success" and not message.get("is_error"),
            subtype=message.get("subtype", ""),
            num_turns=message.get("num_turns"),
            duration_ms=message.get("duration_ms"),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
            text=message.get("result", "") or "",
            errors=list(message.get("errors") or []),
            structured_output=message.get("structured_output"),
            stop_reason=message.get("stop_reason") or "",
            api_error_status=message.get("api_error_status") if isinstance(message.get("api_error_status"), int)
            else None,
        )
    return out
