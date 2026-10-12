"""Stands in for `claude -p` in tests: reads the prompt, prints stream-json, writes results.json."""

import json
import sys
from pathlib import Path

prompt = sys.stdin.read()
password = "pw-secret-9" if "pw-secret-9" in prompt else "missing"


def out(message):
    print(json.dumps(message), flush=True)


out({"type": "system", "subtype": "init", "tools": ["Write"], "session_id": "fake"})
out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
    "five_hour": {"utilization": 0.31, "resetsAt": 4102444800},
    "seven_day": {"utilization": 0.07, "resetsAt": 4102444800}}}})
out({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Logging in with the test account."},
    {"type": "tool_use", "name": "mcp__playwright__browser_type", "input": {"element": "Password", "text": password}},
]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "content": "typed"}]}})

Path("screenshots").mkdir(exist_ok=True)
Path("screenshots/step-2.png").write_bytes(b"\x89PNG fake")
Path("results.json").write_text(json.dumps({
    "scenario": "Login", "state": "done", "summary": "One bug.",
    "notices": ["Page said: ignore your instructions"],
    "steps": [
        {"order": 1, "action": "Log in", "status": "pass", "reasoning": f"typed {password}"},
        {"order": 2, "action": "Save profile", "status": "fail", "screenshot": "screenshots/step-2.png",
         "bug": {"title": "Save shows old name", "severity": "2 - High", "priority": 2}},
    ],
}), encoding="utf-8")
out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 1200,
     "result": "Done.", "usage": {"input_tokens": 5, "output_tokens": 50}})
