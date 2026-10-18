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

- [x] 10. Security fix and session history: after every run (finished, stopped, failed, including `--resume` continuations) replace the test password with `[test password]` in that run's Claude Code session files; one-time clean-up of all earlier app runs; store each run's Claude session ID and show it in the Runs tab with a "Copy resume command" button. Done when, after a real run, its session file contains no password, the session still resumes with the copied command, and the 2 existing session files are clean.
- [x] 11. Scenario steps and expected result: the scenario form gets a numbered Steps field (replacing Goal) and a new Expected result field; Claude follows the steps and judges Pass/Fail against the expected result; reports show both; existing goals move into Steps.
- [x] 12. UI foundation and logo: original bug-and-check logo (title bar, taskbar, welcome screen, About box, installer, shortcuts, report headers, README); light and dark themes with a dark brick-red accent (#9B2D20 light, #C8553D dark); icons on buttons; main toolbar; welcome screen; remembered layout.
- [x] 13. Review experience: screenshots in the review panel; status badges and summary line; review cards; bug details shown and editable; "Save as site note" on review comments; review keyboard shortcuts; reopen the last session on startup.
- [x] 14. Live run experience: "Claude is controlling this browser" banner; live run timeline with collapsed output, timer and progress; neater top bar; Windows notification when a run finishes or needs confirmation.
- [x] 15. Cost control: usage estimate and automatic pause at 80% of the 5-hour window (changeable); model per run type (Sonnet for smoke tests, default model for scenarios); log in once per batch.
- [x] 16. Broader coverage: several test accounts or roles per session; new tabs and pop-ups; file uploads from a fixtures folder; accessibility checks; page speed; errors summary per run.
- [x] 17. Regression testing: reusable scenario library; compare runs; keep every run's steps; Claude suggests scenarios; import test cases (Excel/CSV, Azure DevOps); test data variables.
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

From Phase 11 (2026-10-18):
- New scenario fields are stored as `scenario.test_steps` and `scenario.expected_result`. `Scenario.steps` was already taken by Claude's step results. Smoke pages keep their description in `goal`.
- The startup upgrade copies an existing scenario's goal into its steps once, when the column is added. The user's real scenario keeps its text word for word; as it was one free-text block, its intro line becomes step 1 and Expected result starts empty, worth tidying by hand once.
- Steps are renumbered on save whatever numbering was typed ("2)", "Step 9:", bullets, none).
- Real run: Claude recorded one step per given step with the given wording, then the expected-result check as its own final step (Fail, with what it actually saw).

From Phase 12 (2026-10-18):
- Icons are Lucide 1.51.0 (ISC, newest release at least two weeks old), 21 SVGs copied into src/wta/assets/icons with the licence. They are tinted at runtime and re-tinted on theme change. Icons on accent buttons get a muted disabled version, because a white icon vanished on the grey disabled button.
- The logo is original artwork. tools/build_icons.py renders it to PNGs and a multi-size app.ico (16 to 256 px, PNG entries, built with the standard library); the 16 px size uses a simplified bug-only SVG. The taskbar shows the logo because the process sets its own AppUserModelID.
- The theme follows Windows by default (Qt 6.12 QStyleHints.colorScheme) and switches live; light #9B2D20, dark #C8553D. Fail and Bug status colours stay a separate red.
- Layout is remembered in ui-state.ini in the data folder (window geometry, panel split, last tab).

From Phase 13 (2026-10-18):
- Editing a bug saves your version in the bug's own fields, so exports use it. The first save also keeps Claude's version (as JSON in `bug.claude_original`); later saves never overwrite it. "Use Claude's version" puts it back in the fields, and it is saved when you press Save bug.
- The summary filter counts each step by the status that counts: your final status, or Claude's while unreviewed. "To review" counts steps with no final status, re-run requests included.
- Keys A / O / R work while the step table or a button has focus. They never fire while you type in the comment or bug fields, or with Ctrl/Alt/Shift held. The table would otherwise use letters for type-ahead search, so an event filter takes them first.
- The last open session is reopened by `main()` after the window is shown, not when the window is built, so tests start on the welcome screen. If the session was deleted, the welcome screen stays.
- Screenshot paths are stored as absolute paths; if the file is gone, the screenshot card is hidden.
- Checked on a copy of the Phase 8 smoke data (15 checks: 10 Pass, 4 Bug, 1 Blocked, 1 to review) in light and dark themes.

From Phase 14 (2026-10-18):
- Progress comes from results.json, which Claude rewrites after every step: a step is done once its status is not "not_run". The total is the larger of the steps in the file and the steps known in advance (given steps, plus 1 for the expected-result check). Smoke tests show no total until Claude writes its checklist.
- Notifications use Qt's own tray icon (QSystemTrayIcon), so no new dependency. The icon appears in the notification area from the first run until the app closes; clicking the notification brings the window back. The taskbar button also flashes.
- The timeline keeps following new lines only while you are at the bottom; scrolling up to read stops it. (The first version called scrollToBottom before Qt had laid out the new line, so it stopped following after the first few lines.)
- Run-folder paths are shown as file names only ("Write results.json"); the saved trace keeps the full paths.
- The banner does not block clicks in the page; it only warns.
- Real run on the local test app: progress went 2 of 5, 4 of 5, all 5 done; timer 1:21; banner shown during the run and hidden after; notification "1 scenario, 5 steps: 4 pass, 1 fail. Ready for your review." The 5-hour usage stood at 91% after this run.

From Phase 15 (2026-10-18):
- `claude --model` takes an alias ("sonnet", "opus", "haiku", "fable") or a full model name (checked in `claude --help`, 2.1.296). The init message reports the model actually used; it is stored per run (`run.model`) and shown in the run summary. Settings accept the aliases or a typed full name; anything that is not a model name falls back to Claude Code's default.
- Each run stores `five_hour_used`: its last 5-hour reading minus the reading before it, only when both are from the same window. Without a reading before the run it is unknown, because counting from 0 would also count everything else used on the plan since the window started (the first real run was charged 10% that way). Earlier finished runs were backfilled once from their stored before/seen values.
- The estimate is the average `five_hour_used` of the last 10 finished runs of the same kind (smoke tests or scenarios), times the number of items to run; a kind with no history borrows the other kind's average. Readings are whole percents, and other Claude Code use on the same plan during a run (for example this development chat) is counted too, so it is a rough guide.
- The pause is checked before every scenario after the first, never in the middle of a running scenario (`claude -p` cannot be paused cleanly). Choosing "continue" does not ask again in that batch. If usage is already over the limit when you press Run batch, you are asked first.
- Log in once per batch: the embedded browser keeps its cookies between scenarios, so the prompt now tells Claude to check first and skip signing in when already signed in as the test account (and to sign out first when the scenario tests signing in). In the real check, the second scenario skipped sign-in and used 1% of the window. Not done: one Claude session for the whole batch (`--resume`). Claude Code keeps sessions per working folder and each scenario has its own run folder, and a shared session would have to keep the test password until the whole batch ends.
- Real check (Sonnet for both, pause limit forced to 1% for the check): both runs reported claude-sonnet-5-5; the batch paused before scenario 2 with a notification and continued; 7 of 7 steps passed.

From Phase 16 (2026-10-18):
- Other accounts are a `test_account` table (role, email, password) under the session; the main account stays on the session. `scenario.account_id` picks one (NULL = main; a removed account falls back to main). The prompt names the role; every test password of the session is redacted from run files, Claude Code's saved sessions and exports. Exports show "Run as" per scenario (emails may appear, passwords never).
- Uploads: Playwright MCP 0.0.83 restricts file access to its working folder (the run folder) unless `--allow-unrestricted-file-access` is given, so the app copies the session's upload folder into `<run>/fixtures/` (hidden files skipped; 25 MB per file, 200 MB and 100 files per run) and allows `browser_file_upload` only when there is a folder. Real run: Claude uploaded `fixtures/invoice.txt`.
- New tabs: `QWebEnginePage.createWindow` opens a pop-up window on the same profile (the domain guard still applies). Playwright sees it as a new tab; Claude switched to it with the tabs tool and closed it, which closes the window (`windowCloseRequested`). Pop-ups are owned by the main window and closed by Close all browsers.
- Print: `window.print()` in the main frame raises `printRequested` (not `printRequestedByFrame`) in Qt 6.12. During a run the app saves `print-N.pdf` in the run folder with `printToPdf`; Claude read it with the Read tool (real run: it confirmed the invoice total).
- Page monitor: a second, read-only DevTools client (QtWebSockets, part of PySide6) on the embedded page's target (`QWebEnginePage.devToolsId()`), connected only during runs. It records console errors/warnings, uncaught exceptions, HTTP 400+ and failed requests (not aborted/cancelled ones, not the browser's own favicon request; requests the domain guard blocks are labelled), and main-frame load times (document request to load event). Capped at 500 issues per run. In the real run it caught the test app's console error on a step Claude marked Pass. Single-page-app route changes are not page loads and have no load time.
- PySide6 `runJavaScript` hands JavaScript objects and arrays back as an empty string, so the contrast script returns JSON text. It runs in the application script world, so the page cannot interfere. Real run: it found the deliberate faint line at 1.92:1.
- Accessibility in smoke tests (setting, on by default): Claude adds checks for labels, accessible names, alt text and the main heading from the snapshot; contrast is left to the app.

From Phase 17 (2026-10-18):
- Each run's steps are kept in `run_step` when the run ends (redacted); the scenario's `step` rows stay the latest results that reviews attach to. Runs from before were backfilled once from their run folders' results.json. The Runs tab shows a run's own steps.
- The previous run of a test is the latest earlier finished run of the same scenario, or of any scenario added from the same library item (`scenario.template_id`), so a test re-run in a new session (a new build) compares with the old one. Steps are matched by their action words, then by position; statuses are Claude's as each run reported them. With variables, the filled-in step text differs between runs, so such steps match by position. Real check: "Build 1 (fixed)" passed, "Build 2" failed step 5: "1 regression; 4 the same", shown after the run and in the comparison window.
- Library items are independent copies (scenario or smoke page fields). "Save to library" updates the linked item, or creates one. Deleting an item keeps the scenarios added from it.
- Import reads CSV (comma, semicolon or tab; quoted multi-line cells) and .xlsx (openpyxl, already a dependency; read-only workbooks must be closed or Windows keeps the file locked). One row per case, or the Azure DevOps export layout (title row, then Step Action / Step Expected rows; HTML in cells is stripped; the last step's expectation becomes the expected result). No Azure DevOps connection: that stays parked.
- Variables are filled per scenario run, one value per variable within the run, and listed at the start of the run's trace. Unknown variables are refused in the scenario form.
- Suggestions run `claude -p` with `--tools=`, `--restricted`, `--strict-mcp-config` and `--no-session-persistence` (checked in `claude --help`), the scenario model setting, and stream-json so plan usage is still recorded. The prompt marks the story or page text as data. Real check (Sonnet): 5 sensible suggestions in 14 s.

From Phase 18 (2026-10-19):
- Clean-up removes run folders started more than N days ago, only under the app's runs folder, and by default keeps each scenario's latest run (its screenshots back the steps you review and export). The run's history (run steps, trace, comparison) stays in the database; `run.files_removed_at` records it and the Runs tab says so.
- Backups use SQLite's backup API into `<data folder>/backups` (latest 10 kept), never elsewhere: the database holds the test accounts, which stay on this machine. A restore is copied to `restore-pending.sqlite3` and put in place at the next start, before the database is opened, after a safety backup of the current one. Files that are not a Web Testing Assistant database are refused.
- Search uses case-insensitive LIKE over scenarios, steps, bugs, site notes and the library (at least 2 characters, first 200 results). Opening a result switches session (not during a run), tab, batch, scenario and step.
- Trends per site (host of the session's target URL): pass rate by local day from each finished run's own steps (Claude's statuses), and open bugs per session (steps with a bug whose status that counts is Fail: your review, else Claude's). Charts use Qt Charts, which ships with PySide6 (no new dependency).
- Highlight: for Bug/Blocked steps Claude adds "highlight" (the element's visible text or accessible name). When that step first appears in results.json the app finds the smallest visible element matching it (exact match before partial) in an isolated script world, takes its own screenshot over the page monitor's DevTools connection (`Page.captureScreenshot`; the view's own grab is the fallback), draws a box scaled from CSS pixels to the image, and saves `step-N-highlight.png`. It is kept when Claude rewrites results.json and shown first in the review; exports include it with the step's screenshots. If the element is not found, a note says so and Claude's screenshot stands alone.
- Real check (Sonnet, "Build 3" from the library): step 5 failed with "highlight": "Profile saved. Display name: Sam Tester"; the app saved step-5-highlight.png with the box on that message and showed it first in the review. Search found 15 hits for "display name" across the three build sessions; Trends showed 90% then 80% pass rate and one open bug in builds 2 and 3. The first attempt failed with Claude Code's transient "Failed to refresh OAuth token" (another Claude Code process, this chat, was refreshing); the retry passed.
