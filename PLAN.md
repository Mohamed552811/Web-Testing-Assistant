# Build phases

Tick a phase only after it has been reviewed and accepted.

- [x] 1. Project skeleton: repo, venv, PySide6 window with URL bar and embedded browser. Done when the app opens and loads any site.
- [x] 2. Data layer: SQLite schema, models, session open/save. Done when a session survives closing and reopening.
- [x] 3. Scenario input and review screen: scenario tab (batches of up to 5), step table, approve/override/comment controls. Done when I can enter a scenario and review results by hand.
- [x] 4. Playwright MCP over CDP: launch QtWebEngine with a remote debugging port and attach a Playwright MCP server. Done when Claude Code, run by hand from a terminal, can click, type and take a screenshot in the visible browser. This is the biggest risk, so prove it early. If it cannot attach to QtWebEngine, tell me and propose the fallback of a Playwright-launched Chromium window beside the app.
- [x] 5. Claude Code runner, live run view and usage bar: startup sign-in check, `claude -p` subprocess, streamed run panel, saved trace, results.json loading, and the usage bar fed by `rate_limit_event` from each run's stream (decided 2026-10-12 instead of the statusLine bridge; no edit to `~/.claude/settings.json`). Start with one small scenario and measure plan usage. Done when one scenario runs end to end including the login, shows every step live, fills each step's result and status, has screenshots only for Blocked/Bug steps, and the usage bar shows the 5-hour and weekly percentages after the run.
- [x] 6. Safety controls: Stop button, tool limits, allowed-domain list, page-content-is-data rule, Close all browsers button, optional confirmation pause using `--resume` (off by default). Done when each control is shown to work against a test page built to trigger it.
- [x] 7. Trace review and site notes: reopen past runs and read traces; notes box per site added to the next run's prompt. Done when a note written after one run visibly changes how the next run handles the same site.
- [x] 8. Smoke test tab. Done when one real page produces a full list of checks with statuses, bug reports, and screenshots for Blocked and Bug checks.
- [ ] 9. Report export and Settings screen: Excel first (openpyxl) with Summary, Results and Bugs sheets and embedded screenshots, then PDF, Word and Markdown from the same data. Add the Settings screen with the screenshot timing and export format options. Done when I can hand over or import the file, in each format, without fixing it by hand.

## Findings to carry forward

From Phase 4 (2026-10-12):
- Playwright MCP (`@playwright/mcp@0.0.83`, `--cdp-endpoint`) attaches to QtWebEngine 6.10 / Chromium 140 and drives the visible page, including pages in the dedicated `wta-browser` profile.
- If the debug port is taken, QtWebEngine only logs an error and runs without debugging, so the app picks a free port and checks `/json/version`.
- Screenshots Claude names explicitly are saved relative to the working directory, not `--output-dir`. Run `claude -p` with the session folder as its working directory.
- `--tools=` (one argument) disables all built-in tools. `--tools ""` breaks in Windows PowerShell 5.1.
- Playwright MCP exposes `browser_evaluate` and `browser_run_code_unsafe` (arbitrary JS), and Claude used `browser_evaluate` unprompted. Phase 6 should allow an explicit tool list rather than the whole `mcp__playwright` server.
- `claude -p` still loads the user's own settings and hooks (SessionStart hooks ran). Phase 6 should isolate runs from user settings.
- `claude -p --output-format stream-json` emits `rate_limit_event` with `five_hour` / `seven_day` utilization and `resetsAt`. This is a candidate source for the usage bar in Phase 5, alongside the statusLine bridge.
- On Windows the venv `python.exe` is a launcher with a child interpreter process. PID tracking (Phase 6) must follow child processes.

From Phase 5 (2026-10-12):
- Runs use `--restricted` (ignores user settings and hooks, removes command tools, confines file tools to the run folder), `--tools=Read,Write,Edit`, an explicit Playwright allowlist without `browser_evaluate` / `browser_run_code_unsafe` / `browser_file_upload`, and `--permission-mode dontAsk`. Denied tools return errors and Claude carries on.
- The tool list Claude sees still includes the denied Playwright tools. Phase 6 can check whether hiding them is possible.
- The QtWebEngine DevTools server answers through the Qt main thread of our own process, so `/json/version` must be checked from a worker thread. A synchronous check on the UI thread always times out.
- A Windows 8.3 short working-directory path (e.g. `MOHAME~1`) makes Claude Code refuse writes as "outside the working directory". Data and run folders are resolved to their long form.
- The prompt goes in on stdin, so the test password never appears on a command line. It is redacted in prompt.md, trace.jsonl, run traces and loaded results.
- First real run (one scenario, 8 steps, login included): 66 s, 20 turns, 5-hour usage 40% → 43%, weekly 5% → 6% (Pro plan).
- Claude did not always rewrite results.json after every step: it wrote steps 5 to 8 in one update to save turns. The live step table then jumps ahead in one go. The live action panel is unaffected.
- Playwright tool results ("Saw") include the Playwright code that ran. Useful but wordy in the live panel.

From Phase 6 (2026-10-12):
- `--disallowedTools` removes the denied Playwright tools from Claude's tool list entirely; Claude reported "no JavaScript evaluation tool is available".
- `--restricted` refuses writes outside the working directory even when explicitly asked ("--restricted confines the file tools to the working directory").
- The domain guard (a QWebEngineUrlRequestInterceptor on the profile, active only during runs) blocked a JavaScript redirect to another host. Playwright saw `net::ERR_ACCESS_DENIED`, and the app logged the block in the trace.
- Prompt injection on a page (visible and hidden) was ignored and reported as notices in all runs.
- A run's process tree on Windows is claude.exe → cmd/conhost → node (npx) → cmd/conhost → node (Playwright MCP): 7 processes, all tracked and killed by Stop or Close all browsers in about 0.3 s. Other browser-like processes were untouched (36 before, 36 after).
- After a `--resume`, Claude's earlier snapshot element refs are stale; it re-snapshots and retries by itself.
- Plan usage over Phase 6's six real runs: 5-hour went from 52% to 55%, weekly from 7% to 8%.

From Phase 7 (2026-10-12):
- Added a `run` table (one row per scenario run, including `--resume` continuations) and `run_trace.run_id`, as agreed. On startup the database gains any missing *nullable* columns automatically; anything bigger needs a real migration.
- Site notes are keyed by host only. The prompt marks them as the user's own notes ("not page content") and Claude follows them.
- Proof run: with the note "after saving, reload and check the name persisted", run 2 added three steps (reload, sign in again, check). That surfaced a new finding: the change is not saved at all. Run 2 took 38 turns against 20 for run 1. Notes that add steps cost plan usage.
- Claude noticed that navigating to the same `#hash` URL is not a reload and did a full reload instead.
- The step table shows only each scenario's latest results; older runs stay readable from their own results.json in the Runs tab.

From Phase 8 (2026-10-12):
- Smoke batches are numbered separately from scenario batches: the slot uniqueness rule now includes `kind`. There was no real database yet, so no migration was needed.
- The domain guard now checks top-level page navigations only. Checking frames too blocked a Google reCAPTCHA iframe on practicetestautomation.com's Contact page, and would break captchas, payment fields and maps.
- Claude took extra "visual check" screenshots on passing checks until the prompt said screenshots are evidence only. After that the folder held only Bug/Blocked screenshots.
- Smoke cost depends mostly on how many links Claude opens. First practice-page run: 52 turns, about 11% of the 5-hour window. After "check link targets from the snapshot, open at most two": 37 turns, about 3%.
- practicetestautomation.com login page: 14 checks, all Pass. Valid published credentials deliberately not used to sign in (third-party site). The local Profile page smoke run produced 5 Bugs, each with a bug report and one screenshot.
