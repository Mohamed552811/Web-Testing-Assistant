# Test Case Designer: progress

Spec: `TEST_CASE_DESIGNER_SPEC.md` (v3). Build brief: `CLAUDE_CODE_PROMPT.md`.

## Status

- Done: Step 0 (exploration, plan, decisions below); M0; M1; M2.
- In progress: nothing. Waiting for the go-ahead on M3 and answers to the open decisions below.
- Next: M3 generation through `claude -p`, validation, retry and continuation, golden fixtures.

| Milestone | Status |
|---|---|
| M0 Launchpad, setup step, mode switcher | done (2026-10-19) |
| M1 Upload-time workbook analysis | done (2026-10-19) |
| M2 Settings, presets, prompt builder | done (2026-10-20) |
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

## M1 notes

- Files:
  - `src/wta/designer/xlsx_reader.py`: reads cells and drawings from the ZIP with `zipfile` and `ElementTree`. Pictures keep their original bytes, so MD5s match the file.
  - `src/wta/designer/layout.py`: detects block or table layout, applies a `Mapping` override, builds the backlog map and attaches pictures.
  - `src/wta/db/designer_models.py` and `src/wta/db/workbooks.py`: storage, versions, remapping and attachments.
  - `src/wta/designer/summary.py`: the analysis summary.
  - `src/wta/designer/mapping_dialog.py`: the manual mapping screen.
  - `tools/extract_designer_fixtures.py`: writes `tests/fixtures/designer/` (IMG-01 … IMG-14, IMG-S1, `manifest.json`).
- Result: the reference workbook reproduces the spec 3.3 map exactly.
  - 10 backlogs on "Print d." and backlog 5 on "Sheet1".
  - 15 attachments; IMG-13 has the same MD5 as IMG-04, and both are kept.
  - The status column ("wt") is stored and never used.
- Deviations from the spec, and choices where it was silent:
  - Tables are prefixed `designer_`.
  - `backlog_item` is unique on (sheet, number, occurrence) instead of (sheet, number), so a number repeated on one sheet can be stored. The upload warns about it; M4 lets the user pick which one.
  - Picture files are stored once in `designer_image_blob`, keyed by MD5.
  - The uploaded file is kept in `designer_workbook.data`, so a sheet can be re-analyzed with a new mapping.
  - Spec 3.7 asks for "at least two entries" in the number column, but `Sheet1` has only one backlog. A single entry is therefore accepted with low confidence (60%), and the app suggests checking the mapping.
  - Picture labels: IMG-01, IMG-02 … on the first sheet, in anchor order (row, then column). On the other sheets, IMG-S1, IMG-S2 … are counted across those sheets.
  - The confidence score combines three things: how many numbers have a title to their right, whether the numbers increase, and whether they are consecutive. Below 70% the summary flags the sheet and the app offers the mapping screen.
  - Versions: uploads whose file names differ only by a trailing version number ("Print_designer_6" → family "print designer") form one family and are numbered v1, v2 … The summary lists backlogs added, removed or changed since the previous version, using a content hash of the title, text and attached picture MD5s. Hand-set mappings for sheets with the same name are offered for reuse.
  - The same file uploaded again (same SHA-256) is not stored twice.
  - Applying a new mapping rebuilds that sheet's backlogs, so any pictures attached by hand on that sheet return to the defaults.
- Not committed: the GitHub repository is public, so the spec, the reference workbook and the extracted screenshots in `tests/fixtures/designer/` are left out until the owner decides. Tests that need them are skipped when the files are missing.

## M2 notes

- Files:
  - `src/wta/designer/settings.py`: `DesignerSettings` and `OutputPreset`, with the defaults of spec 8.3. Bad stored values fall back to the defaults.
  - `src/wta/db/designer_settings.py`: settings stored in the `designer_setting` table, in one row (scope `workspace`, name `feature`).
  - `src/wta/designer/prompt_builder.py`: schemas (a) and (b) built from the preset, the system prompt, the rule blocks, the format rules, `backlog_row_data`, the user-message text and the content blocks (images first, each with a label).
  - Versioned wording in `src/wta/designer/prompts/v1/`: `system.txt`, `rule_blocks.json`, `format_rules.json`, `user_message.txt` and `image_label.txt`. `PROMPT_VERSION = "v1"`. The folder is included in package data and in the PyInstaller spec.
  - `src/wta/designer/settings_dialog.py`: opened from "Settings…" on the setup page and in the workspace.
- Verified against the local spec (these tests are skipped when the spec is absent):
  - The default system prompt equals spec 7.1, with the placeholders filled.
  - All nine rule blocks equal spec 7.2.
  - The default schemas (a) and (b) equal spec 5.3.
- `system.txt` keeps the trailing space on the owner's first instruction. A test that does not need the spec checks it.
- Choices where the spec was silent:
  - "Steps with per-step expected": each step is `{action, expected}`, and `expected` is an "Assert that …" check or an empty string. At least one step has a check. `expected_result` stays, because spec 5.4 says it is always present. Schema (a) requires at least 1 step here; the single-expected style keeps the spec's minimum of 2.
  - Padding 0 in the ID pattern means "no padding": the pattern becomes `[0-9]+`, and the prompt says "starting at 1".
  - The tags rule says "smoke" goes on happy paths with the preset's first priority label ("High" or "P1"). With tag suggestions on, "smoke" and "regression" must stay in the vocabulary.
  - When a preset turns the tags field off, the TAGS rule block is left out of the prompt.
  - The built-in Default preset is read-only. Use "Save as new preset" to change a copy.
  - Model: "latest Opus" is `claude --model opus`; a pinned id is passed to `--model` as it is.
  - In the user message, notes and supplementary context are listed as "- " lines, and locked test cases as a JSON array. Empty sections stay empty.
- Not yet built: storing notes and supplementary context per backlog (`backlog_context`). That is M4 UI work; the prompt builder already accepts them as lists.

## Open decisions

1. **Alphanumeric backlog numbers.**
   - Spec 3.4 allows keys like `BL-5`, but schema (a) makes `backlog_number` an integer, and the ID pattern only allows digits.
   - Today such a backlog is sent with `"backlog_number": "BL-5"`, which the schema would reject.
   - The options: (a) only numeric keys can be generated, and the user is told; (b) for alphanumeric keys, allow a string `backlog_number` and use the key's digits in the ID.
2. **Per-step expected style:** confirm the interpretation above.
3. **Content from the spec in committed files.** Three places carry it:
   - the prompt files (the owner's instructions and the examples in spec 7.1);
   - the default product context ("ERP 'Print' app");
   - the M1 reference test's backlog titles in `tests/test_designer_layout.py`.
   - The prompt files have to ship with the app. The reference test titles could move into the git-ignored fixtures folder instead.

None.
