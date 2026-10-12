# Web Testing Assistant

A desktop app where Claude Code tests web apps in an embedded browser and you review every step.
Claude runs through the Claude Code CLI on your own subscription; there is no API key, server or cloud.

## Requirements

- Windows 10/11 (other platforms may work but are untested)
- Python 3.12
- Node.js 18 or later (Playwright MCP is started with `npx`)
- Claude Code CLI, signed in with a Claude subscription (Pro or Max)

Check the last two:

```bash
node --version
```

```bash
claude auth status --text
```

If Claude Code is not signed in, run `claude auth login` in a terminal. The app checks this on startup and blocks test runs until it passes.

## Setup

From the repository root:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -e .
```

This installs PySide6, SQLAlchemy, openpyxl and python-docx. The first test run downloads `@playwright/mcp` through `npx`.

## Run

```bash
.venv/Scripts/python -m wta
```

## Using it

1. **Session → New session**: enter the target URL, allowed domains, viewport and a test-environment account.
2. **Scenarios tab**: add up to 5 scenarios per batch in plain language, then press **Run batch**.
   **Smoke test tab**: describe one page (URL, how to reach it, login needed, what's on it) and Claude writes and runs a checklist.
3. **Live run tab**: follow each action, what Claude saw and its notes. **Stop** ends the run at once.
4. Review every step: **Approve**, **Override** the status, add a comment, or **Request re-run**. A step is final only after your review.
5. **Runs tab**: reopen past runs and their traces, and write **site notes**. Notes are added to the next run's prompt for that site.
6. **Session → Export report** (Ctrl+E): Excel, PDF, Word or Markdown.
   - Excel's Bugs sheet uses Azure DevOps / TFS field names, so it can be imported.
   - Markdown writes a folder with `SUMMARY.md` (test information, scope, results per test case, overall result, bugs found, attachments), one `BUG-NNN-<title>.md` per bug, and the screenshots. Key Findings, Recommendations, Impact and Notes are left empty for you to fill in.
   - Application and Test scope come from the session form; Tester comes from Settings.
7. **Session → Settings** (Ctrl+,): tester name, screenshot timing, default export format, and "pause before risky actions" (off by default).

The bar at the top shows Claude Code sign-in and your 5-hour and weekly plan usage. Usage is updated during test runs from data Claude Code reports.

## Safety

- Claude gets only browser tools and file writes inside the run folder. It has no shell access and does not load your own Claude Code settings or hooks.
- During a run the embedded browser refuses to open pages outside the session's allowed domains.
- Page content is treated as data. Instructions found on a page are ignored and reported as notices.
- **Close all browsers** stops any run and closes every process the app started, tracked by process ID. It never touches your own browsers. It also runs when the app closes.
- The test account stays in the local database. The password is redacted from run files and exported reports; the account email may appear in exported reports.

## Where data lives

`%APPDATA%\Web Testing Assistant`:

| Path | Contents |
|---|---|
| `wta.sqlite3` | Sessions, scenarios, steps, reviews, bugs, runs, site notes |
| `runs/` | One folder per scenario run: prompt, trace, `results.json`, screenshots |
| `exports/` | Default location for exported reports |
| `browser-profile/` | The embedded browser's own profile (cookies, cache) |
| `usage.json`, `settings.json`, `processes.json` | Plan usage, settings, processes the app started |

Set `WTA_DATA_DIR` to use a different folder, for example a throwaway one for trying things out.

## Development

Run the tests:

```bash
.venv/Scripts/python -m unittest discover -s tests
```

The tests use a fake `claude` and never spend plan usage.

`test_pages/` holds local pages for trying the app: a login app with a deliberate bug (`app.html`), a prompt-injection page (`trap.html`) and a notes list for the confirmation pause (`notes.html`). Serve them with:

```bash
.venv/Scripts/python -m http.server 8765 --bind 127.0.0.1 --directory test_pages
```

The local login app's test account is in `test_pages/test-account.json`.

See `CLAUDE.md` for the project rules and `PLAN.md` for the build phases and findings.

## Troubleshooting

- **Run batch is disabled**: hover over it to see why. Common reasons are that Claude Code isn't signed in (use "Check again" in the top bar after signing in) or the browser debugging port isn't ready yet.
- **"Browser debugging port" warning**: the port was taken when the app started. Restart the app.
- **Usage bar says "no data"**: it fills in during the first test run.
