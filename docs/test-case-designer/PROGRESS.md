# Test Case Designer: progress

Spec: `TEST_CASE_DESIGNER_SPEC.md` (v3). Build brief: `CLAUDE_CODE_PROMPT.md`.

## Status

- Done: Step 0 (exploration, plan, decisions below); M0.
- In progress: nothing. Waiting for the go-ahead on M1.
- Next: M1 upload-time workbook analysis.

| Milestone | Status |
|---|---|
| M0 Launchpad, setup step, mode switcher | done (2026-10-19) |
| M1 Upload-time workbook analysis | not started |
| M2 Settings, presets, prompt builder | not started |
| M3 Generation, validation, retry, golden fixtures | not started |
| M4 Designer UI | not started |
| M5 Regenerate, history, tags, exports | not started |
| M6 Send to Smoke / Regression, Copy as prompt | not started |

## Decisions (2026-10-19)

1. **Claude is called through the Claude Code CLI, not an API key.** CLAUDE.md is unchanged: no `ANTHROPIC_API_KEY`.
   - The call is `claude -p --model opus --json-schema <schema (b)> --system-prompt ...`, with the screenshots sent as image blocks through `--input-format stream-json`.
   - "Latest Opus" comes from the CLI's `opus` alias, not from the Models API. The model the CLI reports is stored with each generation run.
   - The Launchpad badge "Setup required" / "Ready" checks Claude Code sign-in instead of an API key.
   - Spec 0.3, the API-key parts of 7.3 and the `.env.example` requirement do not apply.
2. **Send to Smoke / Regression does both:**
   - It creates manual checklist runs as in spec 8.6: `checklist_run` and `checklist_item` tables, plus a checklist screen with Pass / Fail / Blocked / Skipped, notes and a screenshot.
   - It also offers "Add as scenarios", which turns the selected test cases into regression scenarios for Claude to run. This reuses the existing scenario and import code.
3. **`jsonschema` is added** for schema (a). The cross-field rules (spec 5.3) are hand-written.
4. **Desktop translation of the web terms in the brief:**
   - Routes and pages become widgets in a `QStackedWidget` in the main window.
   - Endpoints become Python service modules under `src/wta/designer/`.
   - Database tables follow spec 8.2, written as SQLAlchemy models in the existing database.

## Plan

- **M0:**
  - `launchpad.py` is the first page. It has three card buttons with badges and "Continue where you left off".
  - Test Case Design opens a workbook setup page (pick an earlier workbook or upload a new one, then see the analysis summary).
  - Smoke and Regression open the existing session picker, then the Smoke test tab or the Scenarios tab.
  - A mode switcher in the toolbar has a dropdown and "Back to Launchpad". The last mode used is saved in settings.
- **M1:**
  - `designer/workbook.py` parses the `.xlsx` with `zipfile` and `ElementTree` to read cells, formula flags, drawings, anchors and the original image bytes with their MD5s.
  - It detects block and table layouts and builds the many-to-many image attachments, the analysis summary and the manual mapping override.
  - Fixtures are PNGs extracted from `Print_designer_6.xlsx` into `tests/fixtures/designer/`.
- **M2:**
  - Settings and output presets.
  - A prompt builder whose templates live in the versioned folder `src/wta/designer/prompts/v1/`.
  - A unit test for every rule block.
- **M3:**
  - CLI generation, validation against schema (a) and the cross-field rules, one retry carrying the validation error, and continuation with `locked_test_cases`.
  - Golden fixtures for backlogs 1, 9 and 3, with structural checks only.
- **M4 to M6:** follow spec 8.1, 8.5 and 8.6.

## M0 notes

- Files:
  - `src/wta/launchpad.py`: the cards and the mode names.
  - `src/wta/designer/setup_page.py`: the setup page and a placeholder workspace.
  - In `src/wta/app.py`: `set_mode`, `show_launchpad`, `continue_last_mode` and `_build_mode_bar`.
  - Tests: `tests/test_launchpad.py`.
- The app always opens on the Launchpad. It no longer reopens the last session at start; "Continue where you left off" does that.
- The last mode is saved as `mode` in `ui-state.ini`.
- Smoke and Regression share the existing window: the mode picks the tab, and the session picker on the left is their "choose a run" step. Opening a session from anywhere switches to Regression if no testing mode is active.
- Badges:
  - Test Case Design shows "Checking setup…", "Ready" or "Setup required", depending on Claude Code sign-in.
  - Smoke and Regression show no badge, because their automated runs are never left open. The checklist runs from M6 can add an open-run count later.
- The card texts for Smoke and Regression use the brief's wording ("manual checklists"). That wording fits once M6 adds checklist runs.
- Placeholders until M1: "Upload workbook…" only shows a status message, and the workbook list is empty.

## Open decisions

None.
