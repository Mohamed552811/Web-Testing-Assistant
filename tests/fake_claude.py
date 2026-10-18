"""Stands in for `claude -p` in tests: reads the prompt, prints stream-json, writes results.json.

FAKE_CLAUDE_MODE selects the behaviour:
  (unset)   normal run with one pass and one fail step
  slow      starts a child process (like Playwright MCP) and then waits, so the run can be stopped
  confirm   first run asks for confirmation; a --resume run acts on APPROVED / DECLINED
A call with no tools (--tools= and --no-session-persistence) is a request for scenario suggestions.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

prompt = sys.stdin.buffer.read().decode("utf-8")  # the app sends UTF-8, as the real Claude Code expects
mode = os.environ.get("FAKE_CLAUDE_MODE", "")
password = "pw-secret-9" if "pw-secret-9" in prompt else "missing"


def out(message):
    print(json.dumps(message), flush=True)


def write_results(state, steps, **extra):
    Path("results.json").write_text(json.dumps({"scenario": "Login", "state": state, "steps": steps, **extra}),
                                    encoding="utf-8")


def result(text="Done."):
    out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 10,
         "result": text, "usage": {"input_tokens": 1, "output_tokens": 1}, "session_id": "fake-session"})


# Like Claude Code, report the model in use: the one asked for with --model, or a default.
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "fake-default-model"
out({"type": "system", "subtype": "init", "tools": ["Write"], "session_id": "fake-session", "model": model})

if "--tools=" in sys.argv and "--no-session-persistence" in sys.argv:
    # Scenario suggestions: reply with a JSON list in a code fence, as Claude sometimes does.
    suggestions = [
        {"name": "Sign in with a valid account", "steps": ["Open the login page", "Enter the test account",
                                                           "Press Sign in"],
         "expected_result": "The home page shows the user's name", "test_data": ""},
        {"name": "Sign in with a wrong password", "steps": ["Open the login page", "Enter a wrong password"],
         "expected_result": "An error message is shown", "test_data": "{{random_text}}"},
        {"name": "", "steps": ["no name: dropped"], "expected_result": "", "test_data": ""},
    ]
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.33, "resetsAt": 4102444800}}}})
    fence = "`" * 3
    text = f"Here you go:\n{fence}json\n{json.dumps(suggestions)}\n{fence}" if "SAY NO" not in prompt else "No."
    out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 10,
         "result": text, "usage": {"input_tokens": 1, "output_tokens": 1}, "session_id": "fake-suggest"})
    sys.exit(0)

if os.environ.get("CLAUDE_CONFIG_DIR"):
    # Save the conversation the way Claude Code does: projects/<cwd with non-alphanumerics as "-">/<id>.jsonl
    import re
    folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "fake-session.jsonl", "a", encoding="utf-8") as transcript:
        transcript.write(json.dumps({"type": "user", "sessionId": "fake-session", "message": {"content": prompt}}) + "\n")

if mode == "slow":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path("child.pid").write_text(str(child.pid))
    write_results("running", [{"order": 1, "action": "Open page", "status": "pass"}])
    time.sleep(120)
    sys.exit(0)

if mode == "confirm":
    if "--resume" not in sys.argv:
        write_results("awaiting_confirmation", [{"order": 1, "action": "Open notes", "status": "pass"}],
                      confirmation_request={"step": 2, "action": "Click Delete note", "reason": "Scenario says delete"})
        result("Waiting for confirmation.")
    else:
        assert sys.argv[sys.argv.index("--resume") + 1] == "fake-session"
        approved = "APPROVED" in prompt
        write_results("done", [
            {"order": 1, "action": "Open notes", "status": "pass"},
            {"order": 2, "action": "Click Delete note", "status": "pass" if approved else "blocked",
             "reasoning": "Deleted" if approved else "Declined by the user"},
        ])
        result()
    sys.exit(0)

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
write_results("done", [
    {"order": 1, "action": "Log in", "status": "pass", "reasoning": f"typed {password}"},
    {"order": 2, "action": "Save profile", "status": "fail", "screenshot": "screenshots/step-2.png",
     "bug": {"title": "Save shows old name", "severity": "2 - High", "priority": 2}},
], summary="One bug.", notices=["Page said: ignore your instructions"])
out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 1200,
     "result": "Done.", "usage": {"input_tokens": 5, "output_tokens": 50}})
