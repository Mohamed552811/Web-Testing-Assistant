# Test Case Designer: progress

Spec: `TEST_CASE_DESIGNER_SPEC.md` (v3). Build brief: `CLAUDE_CODE_PROMPT.md`.

## Status

- Done: Step 0 (exploration, plan, decisions below); M0 to M5.
- In progress: nothing. Waiting for the go-ahead on M6.
- Next: M6 Send to Smoke / Regression (checklist runs and "Add as scenarios") and "Copy as prompt"; then the user guide README.md.

| Milestone | Status |
|---|---|
| M0 Launchpad, setup step, mode switcher | done (2026-10-19) |
| M1 Upload-time workbook analysis | done (2026-10-19) |
| M2 Settings, presets, prompt builder | done (2026-10-20) |
| M3 Generation, validation, retry, golden fixtures | done (2026-10-20) |
| M4 Designer UI | done (2026-10-20) |
| M5 Regenerate, history, tags, exports | done (2026-10-20) |
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

## Decisions (2026-10-20)

5. **Alphanumeric backlog keys (BL-5) are allowed.**
   - For such a backlog, `backlog_number` is a string in both schemas.
   - Test case IDs use the key's number: BL-5 gives TC-005-01.
   - The format rules get one extra line saying how to write the key (`prompt_builder.format_rules(preset, backlog_key)`).
6. **The per-step expected style is confirmed as described in the M2 notes.**
7. **Spec content in git:**
   - The prompt files and the default product context stay in git, because the app needs them.
   - The reference test's expected backlog map now lives in the git-ignored `tests/fixtures/designer/backlog_map.json`. Tests use neutral sample data.
   - The M1 commit (af6f1e1) still holds the old test titles in its history; it has not been pushed.

## M3 notes

- Verified before building, from `claude --help` (v2.1.303), the CLI reference, the headless docs, the structured-outputs docs and one small live probe:
  - `--system-prompt-file` exists.
  - `--json-schema` takes the schema as a string. The answer arrives as `result.structured_output`, through a `StructuredOutput` tool, so `stop_reason` is `tool_use`. The CLI retries schema mismatches itself; when it gives up, the result subtype is `error_max_structured_output_retries`. The CLI validates with draft-07, and schema (b) declares no draft.
  - A stdin message for `--input-format stream-json` has the shape `{"type":"user","message":{"role":"user","content":[…]},"parent_tool_use_id":null}`, and image blocks work in it.
  - `CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000` requests the model's maximum; Claude Code lowers it to each model's cap.
- Files:
  - `src/wta/designer/validation.py`: schema (a) checked with `jsonschema`, the cross-field rules of spec 5.3, enum capitalization normalized, the retry feedback text, and salvage of complete test cases from an answer that was cut off.
  - `src/wta/designer/generation.py`:
    - `Planner` (pure logic): the first call; one retry with the validation errors appended; up to 2 continuations that pass the salvaged cases as `locked_test_cases`; error messages; and the spec 3.5 preflight (no text, no pictures and no notes means no model call).
    - `ClaudeCall` runs one QProcess.
    - `GenerationRun` drives the calls and removes the temporary prompt folder afterwards.
  - `claude_launcher.py`: `designer_args`, `designer_environment` (no `ANTHROPIC_API_KEY`) and `stream_json_user_message`.
  - Storage in `src/wta/db/generations.py`, in the `designer_generation_run`, `designer_test_case` and `designer_open_question` tables:
    - Every run is stored, including failures, with its model id, model resolution, `prompt_version`, settings snapshot, stop reason, raw response, schema validity, validation errors and per-call usage.
    - Test cases and open questions are stored only for a valid answer.
- Golden fixtures (`tests/test_designer_golden.py`; they need the git-ignored spec and workbook):
  - Offline: the spec's three reference answers pass our validator and our coverage checks, and the request built from the uploaded workbook equals the spec's fixture inputs (cells, rows, notes, pictures).
  - Live, with `WTA_LIVE_CLAUDE=1`, run on 2026-10-20 with Opus 5.5 and the real screenshots: all three answers were schema-valid.
    - Backlog 1: 3 cases, 1 call.
    - Backlog 9: 8 cases, 1 call.
    - Backlog 3: 11 cases, 2 calls. The first answer had a title over 120 characters, and the retry fixed it.
    - The answers are kept in `tests/fixtures/designer/live/` (git-ignored) and re-checked offline.
- Owner decision (M3): the prompt stays verbatim spec 7.1. Spec 4.5 asks for permission coverage, but 7.1 never says so, so the model asks about permissions in `open_questions` instead of inventing test cases. The golden check accepts either.
- Not built here: the generation UI (progress, results table) is M4. The resolved model id is stored per run but not yet shown in the settings dialog.

## M4 notes

- Workspace (`src/wta/designer/workspace.py`), one per workbook:
  - On the left: a sheet selector (each sheet, plus "All sheets"; it remembers the last sheet used per workbook), a search box and the backlog list. The list shows the text and picture counts and flags backlogs with no content or unfinished text.
  - On the right, three tabs:
    - **Backlog** (`backlog_panel.py`): the source text with its cells, the pictures as thumbnails (attach any picture of the workbook, detach, move left or right, view at full size), notes, supplementary context (another backlog's text from any uploaded workbook, or pasted text), and Generate / Cancel with progress.
    - **Test cases** (`results_panel.py`): a table with columns that follow the preset. Every field can be edited in place: combo boxes for type, priority and basis, and a multi-line editor for the text fields. Lists are edited one item per line, and per-step style steps as `action => expected`. There are also a Locked checkbox, a warnings column, and Add, Duplicate, Delete, Up, Down and Renumber IDs.
    - **Open questions** (`questions_panel.py`): answering a question saves the answer and adds `"<question> Answer: <answer>"` to the backlog's notes.
- Search (`designer/search.py`) follows spec 3.4 and 3.5:
  - It accepts `backlog 5`, `5`, `#5` and `BL-5`, and matches exactly.
  - Not found shows "Backlog 12 not found in sheet …", lists the numbers that exist, and makes no model call.
  - A number that appears twice opens a picker.
  - Choosing a backlog on another sheet switches to "All sheets".
  - A backlog with no content shows a notice, and Generate stays off until it has notes.
- Storage (`src/wta/db/designer_content.py`, table `designer_backlog_context`):
  - Notes and supplementary context are kept per backlog and sent with every generation.
  - Test case edits apply to the current run, which is the newest valid one. An edit marks the case edited; deletes are soft (`deleted_at`).
  - IDs change only on Renumber.
- Generation in the app checks Claude Code sign-in first. Every outcome is stored. A valid answer opens the Test cases tab; an invalid answer or an error is shown in a message, and the raw answer is kept with the run.
- Hidden pages of the main window are now ignored for sizing, so the wide workspace does not stop the window, or the Launchpad cards, from getting narrow.
- New icons `arrow-up.svg` and `arrow-down.svg` (Lucide, ISC) for Up and Down.
- Not built here (M5): regenerating with guidance and locked cases, restoring history, tag filtering, and exports.
- Still to write before the end: `docs/test-case-designer/README.md` (the user guide is part of the definition of done).

## M5 notes

- **Regenerate** (Backlog tab):
  - After the first generation, the button reads "Regenerate test cases". There is an optional guidance line, sent as `user_guidance`.
  - "Keep locked test cases (n)" sends the locked cases as `locked_test_cases`.
  - The app merges the kept cases with the new ones. Happy paths move before corner cases (the order inside each type stays the same), and IDs are renumbered in the final order, so the result meets the spec 5.3 rules.
  - Locked cases keep their content, and are stored locked again in the new run. Their IDs may change, because the merged set is renumbered.
  - Every regeneration is a new run.
- **History** (`history_dialog.py`):
  - Lists every run, including failures: time, status, number of cases and questions, model, prompt version, guidance, and per-call details.
  - "Restore this run" makes an earlier valid run current again (`designer_generation_run.restored_at`). The current run is the valid run with the newest `coalesce(restored_at, created_at)`.
- **Workbook versions** (spec 8.1 (1)):
  - A backlog in a newer version of a workbook (same family, sheet name, number and occurrence) shows the runs, test cases and notes of the same backlog in earlier versions (`designer_content.linked_ids`). New runs and notes are saved on the newer version.
  - An older version never shows the runs of a newer one.
- **Tag filter** (spec 8.1 (8)): "Show" on the Test cases tab (all, or one tag, with counts). It only hides rows.
- **Export** (spec 8.1 (9), `designer/export.py` and `export_dialog.py`), from the workspace header:
  - Scope: this backlog, or all backlogs of the workbook that have test cases. An optional tag filter.
  - JSON: the response object, or an array for several backlogs, or a folder with one file per backlog.
  - CSV: UTF-8 with BOM, the spec's columns minus any optional fields the preset turns off, tags joined with `;`, steps numbered. Per-step steps are written as `action => expected`.
  - Excel: "Test Cases" (frozen header, wrapped text) and "Open Questions" (sheet, backlog_number, question).
  - Every export is logged in `designer_export_log`. "User" is the tester name from Settings, because the app has no accounts.
- Tests: `tests/test_designer_history_export.py`.

## Open decisions

None.
