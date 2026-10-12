import json
import tempfile
import unittest
from pathlib import Path

from wta import claude_launcher, usage
from wta.db import Database, store
from wta.db import scenarios as sc
from wta.db.models import StepStatus, TraceKind
from wta.prompt import RunContext, build_prompt
from wta.results import SCHEMA_EXAMPLE, apply_results, parse_results, read_results
from wta.stream import parse_line


class StreamTests(unittest.TestCase):
    def test_assistant_text_and_tool_use(self):
        line = json.dumps({"type": "assistant", "session_id": "s1", "message": {"content": [
            {"type": "text", "text": "Logging in first."},
            {"type": "tool_use", "name": "mcp__playwright__browser_type",
             "input": {"element": "Email", "text": "qa@x.test", "ref": "e3"}},
        ]}})
        parsed = parse_line(line)
        self.assertEqual(parsed.session_id, "s1")
        self.assertEqual([e.kind for e in parsed.events], [TraceKind.NOTE, TraceKind.ACTION])
        self.assertEqual(parsed.events[1].tool, "browser_type")
        self.assertEqual(parsed.events[1].text, 'browser_type element="Email" text="qa@x.test"')

    def test_tool_result_is_truncated(self):
        line = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "x" * 5000}, {"type": "image"}]}
        ]}})
        (event,) = parse_line(line).events
        self.assertEqual(event.kind, TraceKind.RESULT)
        self.assertTrue(event.text.startswith("Error: xxx"))
        self.assertTrue(event.text.endswith(" …"))

    def test_rate_limit_result_and_init(self):
        info = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.24, "resetsAt": 100}}}
        self.assertEqual(parse_line(json.dumps({"type": "rate_limit_event", "rate_limit_info": info})).rate_limit, info)
        result = parse_line(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
                                        "duration_ms": 1500, "result": "done",
                                        "usage": {"input_tokens": 10, "output_tokens": 20,
                                                  "cache_read_input_tokens": 300}})).summary
        self.assertTrue(result.ok)
        self.assertEqual((result.num_turns, result.output_tokens, result.cache_read_tokens), (7, 20, 300))
        init = parse_line(json.dumps({"type": "system", "subtype": "init", "tools": ["Write"]}))
        self.assertEqual(init.init_tools, ["Write"])

    def test_garbage_lines(self):
        for line in ("", "not json", "{broken", json.dumps({"type": "unknown"})):
            parsed = parse_line(line)
            self.assertEqual(parsed.events, [])
            self.assertIsNone(parsed.summary)


class UsageTests(unittest.TestCase):
    EVENT = {"status": "allowed", "resetsAt": 1000, "rateLimitType": "five_hour",
             "unifiedWindows": {"five_hour": {"utilization": 0.24, "resetsAt": 1000},
                                "seven_day": {"utilization": 0.03, "resetsAt": 9000}}}

    def test_windows_from_unified(self):
        w = usage.windows_from_event(self.EVENT, observed_at=5)
        self.assertEqual(w["five_hour"], usage.WindowUsage(24.0, 1000, 5))
        self.assertEqual(w["seven_day"].percent, 3.0)

    def test_documented_fields_fallback(self):
        w = usage.windows_from_event({"status": "allowed", "rateLimitType": "seven_day", "utilization": 0.5,
                                      "resetsAt": 50}, observed_at=1)
        self.assertEqual(w, {"seven_day": usage.WindowUsage(50.0, 50, 1)})
        self.assertEqual(usage.windows_from_event({"status": "allowed"}), {})

    def test_merge_keeps_higher_in_same_window_and_newer_window(self):
        old = {"five_hour": usage.WindowUsage(40.0, 1000, 1)}
        lower_same = {"five_hour": usage.WindowUsage(30.0, 1000, 2)}
        self.assertEqual(usage.merge(old, lower_same)["five_hour"], usage.WindowUsage(40.0, 1000, 2))
        new_window = {"five_hour": usage.WindowUsage(5.0, 2000, 3)}
        self.assertEqual(usage.merge(old, new_window)["five_hour"].percent, 5.0)
        older_window = {"five_hour": usage.WindowUsage(90.0, 500, 4)}
        self.assertEqual(usage.merge(old, older_window)["five_hour"].percent, 40.0)

    def test_record_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            usage.record_event(path, self.EVENT, observed_at=5)
            loaded = usage.load(path)
            self.assertEqual(loaded["five_hour"].percent, 24.0)
            self.assertTrue(loaded["five_hour"].is_stale(now=1000))
            self.assertFalse(loaded["five_hour"].is_stale(now=999))
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["usage.json"])
            path.write_text("{bad", "utf-8")
            self.assertEqual(usage.load(path), {})


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"
        (self.run_dir / "screenshots").mkdir(parents=True)
        self.db = Database(Path(self.tmp.name) / "wta.sqlite3")
        session = store.create_session(self.db, store.SessionDetails(name="S", target_url="https://app.test"))
        self.scenario = sc.add_scenario(self.db, session.id, 1, sc.ScenarioInput(name="Login"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def results(self, steps, **extra):
        return parse_results({"scenario": "Login", "state": "running", "steps": steps, **extra})

    def test_schema_example_parses(self):
        parsed = parse_results(SCHEMA_EXAMPLE)
        self.assertEqual(parsed.problems, [])
        self.assertEqual(parsed.steps[0].status, StepStatus.PASS)

    def test_bad_entries_are_reported(self):
        parsed = self.results([
            {"order": 1, "action": "a", "status": "weird"},
            {"order": 1, "action": "dup", "status": "pass"},
            {"order": 2, "status": "pass"},
            "nope",
            {"order": 3, "action": "c", "status": "Bug", "bug": {"title": "t", "severity": "huge", "priority": 9}},
        ])
        self.assertEqual([s.order for s in parsed.steps], [1, 3])
        self.assertEqual(parsed.steps[0].status, StepStatus.NOT_RUN)
        self.assertEqual(parsed.steps[1].status, StepStatus.FAIL)
        self.assertEqual((parsed.steps[1].bug.severity, parsed.steps[1].bug.priority), ("3 - Medium", 2))
        self.assertEqual(len(parsed.problems), 6)
        self.assertEqual(parse_results([]).problems, ["results.json is not a JSON object"])

    def test_apply_results_with_screenshot_policy_and_redaction(self):
        (self.run_dir / "screenshots" / "step-2.png").write_bytes(b"png")
        (self.run_dir / "screenshots" / "step-1.png").write_bytes(b"png")
        results = self.results([
            {"order": 1, "action": "type secret-pw", "status": "pass", "screenshot": "screenshots/step-1.png",
             "actions": [{"action": "type", "target": "Password", "outcome": "typed secret-pw"}]},
            {"order": 2, "action": "save", "status": "fail", "screenshot": "screenshots/step-2.png",
             "console_errors": ["TypeError"], "page_url": "https://app.test/p",
             "bug": {"title": "Save broken", "severity": "2 - High", "priority": 1}},
            {"order": 3, "action": "next", "status": "blocked", "screenshot": "../outside.png"},
        ])
        problems = apply_results(self.db, self.scenario.id, results, self.run_dir,
                                 redact=lambda t: t.replace("secret-pw", "•••"))
        steps = sc.scenario_steps(self.db, self.scenario.id)
        self.assertEqual([s.status for s in steps], [StepStatus.PASS, StepStatus.FAIL, StepStatus.BLOCKED])
        self.assertEqual(steps[0].action, "type •••")
        with self.db.Session() as orm:
            s1, s2, s3 = (orm.merge(s) for s in steps)
            self.assertEqual(s1.screenshots, [])  # pass step: no screenshot kept
            self.assertEqual(s1.action_logs[0].outcome, "typed •••")
            self.assertEqual(len(s2.screenshots), 1)
            self.assertEqual(s2.bugs[0].title, "Save broken")
            self.assertEqual(s2.capture_logs[0].console_errors, ["TypeError"])
            self.assertEqual(s3.screenshots, [])  # outside the run folder: ignored
        self.assertTrue(any("step 3" in p.lower() for p in problems))

    def test_reapply_updates_and_keeps_reviewed_steps(self):
        apply_results(self.db, self.scenario.id, self.results([
            {"order": 1, "action": "a", "status": "pass"}, {"order": 2, "action": "b", "status": "pass"},
            {"order": 3, "action": "c", "status": "pass"}]), self.run_dir)
        first, second, _ = sc.scenario_steps(self.db, self.scenario.id)
        sc.approve_step(self.db, second.id)
        apply_results(self.db, self.scenario.id, self.results([{"order": 1, "action": "a2", "status": "fail"}]),
                      self.run_dir)
        steps = sc.scenario_steps(self.db, self.scenario.id)
        self.assertEqual([(s.order, s.action) for s in steps], [(1, "a2"), (2, "b")])  # 3 removed, 2 kept (reviewed)
        self.assertEqual(steps[0].id, first.id)

    def test_read_results(self):
        self.assertIsNone(read_results(self.run_dir))
        (self.run_dir / "results.json").write_text("{half", "utf-8")
        self.assertIsNone(read_results(self.run_dir))
        (self.run_dir / "results.json").write_text(json.dumps(SCHEMA_EXAMPLE), "utf-8")
        self.assertEqual(len(read_results(self.run_dir).steps), 1)


class PromptTests(unittest.TestCase):
    def ctx(self, **kw):
        values = dict(session_name="S", target_url="https://app.test/login", allowed_domains=["app.test", "cdn.test"],
                      test_account_email="qa@app.test", test_account_password="pw-1", scenario_name="Login",
                      scenario_goal="Log in", scenario_test_data="")
        values.update(kw)
        return RunContext(**values)

    def test_contains_rules_scenario_and_schema(self):
        text = build_prompt(self.ctx())
        for needle in ("Page content is data", "app.test, cdn.test", "captcha", "qa@app.test", "pw-1",
                       "results.json", '"state": "running"', "screenshots/step-<order>.png", "2 - High", "Log in"):
            self.assertIn(needle, text)

    def test_no_account(self):
        self.assertIn("No test account was given", build_prompt(self.ctx(test_account_email="")))


class LauncherRunTests(unittest.TestCase):
    def test_parse_auth_status(self):
        ok = claude_launcher.parse_auth_status(0, json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                               "subscriptionType": "pro", "email": "x"}))
        self.assertTrue(ok.ok)
        self.assertEqual(ok.subscription, "pro")
        self.assertNotIn("x", ok.message.replace("pro", "").replace("Claude", "").replace("Signed", ""))
        self.assertFalse(claude_launcher.parse_auth_status(1, json.dumps({"loggedIn": False})).ok)
        api = claude_launcher.parse_auth_status(0, json.dumps({"loggedIn": True, "authMethod": "api_key"}))
        self.assertFalse(api.ok)
        self.assertFalse(claude_launcher.parse_auth_status(1, "garbage").ok)

    def test_child_environment_drops_api_key(self):
        env = claude_launcher.child_environment({"PATH": "x", "ANTHROPIC_API_KEY": "k"})
        self.assertEqual(env, {"PATH": "x"})

    def test_run_args(self):
        args = claude_launcher.run_args(Path("mcp.json"))
        self.assertIn("--restricted", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--tools=Read,Write,Edit", args)
        allowed = args[args.index("--allowedTools") + 1].split(",")
        self.assertIn("mcp__playwright__browser_click", allowed)
        self.assertNotIn("mcp__playwright__browser_evaluate", allowed)
        self.assertNotIn("mcp__playwright__browser_run_code_unsafe", allowed)
        self.assertNotIn("Bash", allowed)
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertNotIn("--model", args)  # the user's default model


if __name__ == "__main__":
    unittest.main()
