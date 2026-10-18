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
- [x] 9. Report export and Settings screen: Excel first (openpyxl) with Summary, Results and Bugs sheets and embedded screenshots, then PDF, Word and Markdown from the same data. Add the Settings screen with the screenshot timing and export format options. Done when I can hand over or import the file, in each format, without fixing it by hand.

### Enhancement phases (approved 2026-10-18; full detail in Enhancement-Plan-v4.pdf)

- [ ] 10. Security fix and session history: after every run (finished, stopped, failed, including `--resume` continuations) replace the test password with `[test password]` in that run's Claude Code session files; one-time clean-up of all earlier app runs; store each run's Claude session ID and show it in the Runs tab with a "Copy resume command" button. Done when, after a real run, its session file contains no password, the session still resumes with the copied command, and the 2 existing session files are clean.
- [ ] 11. Scenario steps and expected result: the scenario form gets a numbered Steps field (replacing Goal) and a new Expected result field; Claude follows the steps and judges Pass/Fail against the expected result; reports show both; existing goals move into Steps.
- [ ] 12. UI foundation and logo: original bug-and-check logo (title bar, taskbar, welcome screen, About box, installer, shortcuts, report headers, README); light and dark themes with a dark brick-red accent (#9B2D20 light, #C8553D dark); icons on buttons; main toolbar; welcome screen; remembered layout.
- [ ] 13. Review experience: screenshots in the review panel; status badges and summary line; review cards; bug details shown and editable; "Save as site note" on review comments; review keyboard shortcuts; reopen the last session on startup.
- [ ] 14. Live run experience: "Claude is controlling this browser" banner; live run timeline with collapsed output, timer and progress; neater top bar; Windows notification when a run finishes or needs confirmation.
- [ ] 15. Cost control: usage estimate and automatic pause at 80% of the 5-hour window (changeable); model per run type (Sonnet for smoke tests, default model for scenarios); log in once per batch.
- [ ] 16. Broader coverage: several test accounts or roles per session; new tabs and pop-ups; file uploads from a fixtures folder; accessibility checks; page speed; errors summary per run.
- [ ] 17. Regression testing: reusable scenario library; compare runs; keep every run's steps; Claude suggests scenarios; import test cases (Excel/CSV, Azure DevOps); test data variables.
- [ ] 18. Housekeeping and insight: highlight the failing element in bug screenshots; search across everything; clean up old runs and back up; trends dashboard.
- [ ] 19. Windows installer: PyInstaller bundle and Inno Setup Setup.exe with logo, shortcuts and uninstaller (data in %APPDATA% kept); first-start checks for Node.js and Claude Code; one-command build script.

Parked: send bugs straight to Azure DevOps; a single Claude Code project for app runs.

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

From Phase 9 (2026-10-12):
- Exports are built from one report model and use the user's final status, with Claude's status alongside. A bug is left out when the user reviewed its step as anything other than Fail/Bug; unreviewed and re-run bugs stay, labelled.
- Claude writes the test account email into repro steps ("sign in as …"). Exports replace the account email and password with "the test account" / "the test password" in every text field.
- openpyxl needs Pillow only to read image size and bytes. `wta.export.xlsx.PngImage` reads both from the PNG header instead, so openpyxl is pinned to 3.1.x.
- PDF is rendered by Qt (QTextDocument → QPdfWriter). Under the offscreen test platform Qt has no fonts unless QT_QPA_FONTDIR is set; the real app (windows platform) is unaffected.
- All four formats of two real sessions were opened in Microsoft Excel and Word (read-only) with no repair prompts. The Bugs sheet uses Azure DevOps column names (Work Item Type, Title, Repro Steps, Severity, Priority, Tags).

From Phase 10 (2026-10-18):
- Claude Code saves each `claude -p` run under `<config>/projects/<run folder path with non-alphanumerics as "-">/<session id>.jsonl`, prompt included. A scan of the whole Claude folder (credential files skipped) found the test password only in the 2 real runs' session files. They are now clean (8 copies replaced) and the scan finds 0 files.
- The password has to stay in the session until the run is completely over: a `--resume` continuation still needs it to keep signing in.
- Windows: session file paths easily pass 260 characters, and then ordinary file calls report the file as missing. The first version marked such a run "cleaned" while the password stayed. Session files are now accessed in the long-path form, and a run is only marked cleaned once its session file was found.
- The placeholder now reads "[test password, removed after the run]". With plain "[test password]", a resumed Claude assumed it had typed that text and wrongly suspected an authentication bug.
- Claude Code sometimes fails with "Failed to refresh OAuth token: another Claude Code process is refreshing it" when another Claude Code session is active. It cleared after about 2 minutes. The app showed the message and marked the run failed.
