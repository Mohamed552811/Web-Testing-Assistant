<img src="src/wta/assets/logo.png" alt="Web Testing Assistant logo" width="96">

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

## Install with Setup.exe

The easiest way on another Windows machine: run `WebTestingAssistant-Setup-<version>.exe` (built as below). It installs for your user without administrator rights, adds a Start-menu shortcut (a desktop shortcut is optional) and an uninstaller. Uninstalling keeps your sessions, runs, settings and backups in `%APPDATA%\Web Testing Assistant`.

The machine still needs Node.js and a signed-in Claude Code; on first start the app checks both and explains how to install what is missing (also under **Help → Check setup**).

### Build the installer

Needs Inno Setup 6 (`winget install JRSoftware.InnoSetup`) and the build tools:

```bash
.venv/Scripts/python -m pip install -r tools/build-requirements.txt
```

```bash
.venv/Scripts/python tools/build_installer.py
```

This bundles the app with PyInstaller (`dist/WebTestingAssistant/`), runs the bundle's self-check, and writes `dist/installer/WebTestingAssistant-Setup-<version>.exe`. The version comes from `src/wta/__init__.py`. An installed copy can check itself without opening a window: `WebTestingAssistant.exe --self-check report.json`.

## Setup (from source)

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

1. **Session → New session**: enter the target URL, allowed domains, viewport and a test-environment account. You can add **other accounts** (roles such as "Sales manager") and choose a **folder of files Claude may upload**; each scenario or smoke page can then **run as** one of the accounts.
2. **Scenarios tab**: add up to 5 scenarios per batch in plain language, then press **Run batch**.
   - **Suggest…**: Claude proposes scenarios from a pasted user story or from the page open in the browser; tick, edit and add them.
   - **Save to library** / **Add from library…**: keep scenarios and smoke pages to run again, for example on each new build. The library can **import test cases** from Excel or CSV, including Azure DevOps test case exports.
   - Steps, expected result and test data may use variables filled in fresh for each run: `{{random_email}}`, `{{random_name}}`, `{{random_number}}`, `{{random_text}}`, `{{uuid}}`, `{{today}}`, `{{tomorrow}}`, `{{yesterday}}`, `{{now}}`, `{{timestamp}}`.
   **Smoke test tab**: describe one page (URL, how to reach it, login needed, what's on it) and Claude writes and runs a checklist.
3. **Live run tab**: a timeline of each action, what Claude saw (one line; click to expand) and its notes, with "Step 4 of 5" progress and a timer. **Stop** ends the run at once. A banner over the browser reminds you not to click in the page while Claude works, and Windows shows a notification when a run finishes or Claude asks for confirmation.
4. Review every step: **Approve**, **Override** the status, add a comment, or **Request re-run**. A step is final only after your review.
5. **Runs tab**: reopen past runs and their traces, and write **site notes**. Notes are added to the next run's prompt for that site. Every run keeps its own steps. **Compare with previous run** shows what changed since the previous run of the same test (also in an earlier session, for scenarios added from the library): regressions, fixes, other changes. **Errors and speed** lists what the app itself saw on the page during the run: console errors, uncaught JavaScript errors, failed requests, page load times (slow from 3 s) and, for smoke tests, low text contrast. It covers every step, including ones that passed.
6. **Session → Export report** (Ctrl+E): Excel, PDF, Word or Markdown.
   - Excel's Bugs sheet uses Azure DevOps / TFS field names, so it can be imported.
   - Markdown writes a folder with `SUMMARY.md` (test information, scope, results per test case, overall result, bugs found, attachments), one `BUG-NNN-<title>.md` per bug, and the screenshots. Key Findings, Recommendations, Impact and Notes are left empty for you to fill in.
   - Application and Test scope come from the session form; Tester comes from Settings.
7. **Tools menu**: **Search everything** (Ctrl+Shift+F) across all sessions — scenarios, steps, bugs, site notes and the library; double-click a result to open it. **Trends** shows the pass rate by day and the open bugs per session for a site. **Clean up and back up** shows how much disk the runs folder uses, removes run folders older than N days (each scenario's latest run is kept), and backs up or restores the database. Backups stay in the data folder on this machine, because they include the test accounts; a restore takes effect at the next start.
8. **Session → Settings** (Ctrl+,): tester name, screenshot timing, default export format, "pause before risky actions" (off by default), the plan usage limit at which a batch pauses (80% of the 5-hour window by default), and the model for scenarios (Claude Code's default) and for smoke tests (Sonnet).
   Next to **Run batch** the app shows an estimate of the plan usage, from your recent runs. Before each scenario after the first, a batch pauses and asks you once usage reaches the limit.

The bar at the top shows Claude Code sign-in and your 5-hour and weekly plan usage. Usage is updated during test runs from data Claude Code reports.

## Safety

- Claude gets only browser tools and file writes inside the run folder. It has no shell access and does not load your own Claude Code settings or hooks.
- During a run the embedded browser refuses to open pages outside the session's allowed domains.
- Uploads are possible only from the session's upload folder: its files are copied into the run folder, and Claude can upload nothing else.
- Pages the site opens in a new tab appear in their own window; Claude may switch to them and close them. When a page prints, the app saves the printout as a PDF in the run folder.
- For Bug and Blocked steps Claude names the element that is wrong; the app takes its own screenshot with a box around it (`step-N-highlight.png`, shown first in the review), keeping Claude's screenshot as it is.
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

`test_pages/` holds local pages for trying the app: a login app with a deliberate bug (`app.html`), a prompt-injection page (`trap.html`), a notes list for the confirmation pause (`notes.html`), an upload form with a deliberately faint line (`upload.html`) and a printable invoice that opens in a new tab (`print.html`). Serve them with:

```bash
.venv/Scripts/python -m http.server 8765 --bind 127.0.0.1 --directory test_pages
```

The local login app's test accounts (a main one and a Manager role) are in `test_pages/test-account.json`.

See `CLAUDE.md` for the project rules and `PLAN.md` for the build phases and findings.

## Troubleshooting

- **Run batch is disabled**: hover over it to see why. Common reasons are that Claude Code isn't signed in (use "Check again" in the top bar after signing in) or the browser debugging port isn't ready yet.
- **"Browser debugging port" warning**: the port was taken when the app started. Restart the app.
- **Usage bar says "no data"**: it fills in during the first test run.
