# Build phases

Tick a phase only after it has been reviewed and accepted.

- [ ] 1. Project skeleton: repo, venv, PySide6 window with URL bar and embedded browser. Done when the app opens and loads any site.
- [ ] 2. Data layer: SQLite schema, models, session open/save. Done when a session survives closing and reopening.
- [ ] 3. Scenario input and review screen: scenario tab (batches of up to 5), step table, approve/override/comment controls. Done when I can enter a scenario and review results by hand.
- [ ] 4. Playwright MCP over CDP: launch QtWebEngine with a remote debugging port and attach a Playwright MCP server. Done when Claude Code, run by hand from a terminal, can click, type and take a screenshot in the visible browser. This is the biggest risk, so prove it early. If it cannot attach to QtWebEngine, tell me and propose the fallback of a Playwright-launched Chromium window beside the app.
- [ ] 5. Claude Code runner, live run view and usage bar: startup sign-in check, `claude -p` subprocess, streamed run panel, saved trace, results.json loading, and the statusLine bridge feeding the usage bar. Start with one small scenario and measure plan usage. Done when one scenario runs end to end including the login, shows every step live, fills each step's result and status, has screenshots only for Blocked/Bug steps, and the usage bar shows the 5-hour and weekly percentages after the run.
- [ ] 6. Safety controls: Stop button, tool limits, allowed-domain list, page-content-is-data rule, Close all browsers button, optional confirmation pause using `--resume` (off by default). Done when each control is shown to work against a test page built to trigger it.
- [ ] 7. Trace review and site notes: reopen past runs and read traces; notes box per site added to the next run's prompt. Done when a note written after one run visibly changes how the next run handles the same site.
- [ ] 8. Smoke test tab. Done when one real page produces a full list of checks with statuses, bug reports, and screenshots for Blocked and Bug checks.
- [ ] 9. Report export and Settings screen: Excel first (openpyxl) with Summary, Results and Bugs sheets and embedded screenshots, then PDF, Word and Markdown from the same data. Add the Settings screen with the screenshot timing and export format options. Done when I can hand over or import the file, in each format, without fixing it by hand.
