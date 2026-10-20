# Test Case Designer: user guide

The Test Case Designer turns the product backlogs in an Excel workbook into **happy-path** and **corner-case** test cases.

- Claude writes the test cases, one backlog at a time.
- Claude looks at the backlog's text and its screenshots.
- You review the result, edit it, export it, or hand it over to a Smoke or Regression checklist.

It runs through **Claude Code on your own subscription**: no API key is needed or used. Claude Code must be installed and signed in. The Launchpad badge shows "Ready" when it is.

## 1. Open it

1. Start the app. The **Launchpad** shows three cards: **Test Case Design**, **Regression Testing** and **Smoke Testing**.
2. Choose **Test Case Design**. Next time, **Continue where you left off** brings you back to the last mode and workbook.
3. To move between modes, use the mode button in the top bar, or **Mode** in the menu (Alt+M). **Back to Launchpad** (Ctrl+L) returns to the cards.

## 2. Upload a workbook

1. Click **Upload workbook…** and choose an `.xlsx` (or `.xlsm`) file.
2. The app reads the file straight away and shows an **analysis summary** for each sheet:
   - the layout it found, either *block* (a number row with description rows below it) or *table* (a header row with one backlog per row), and how confident it is;
   - every backlog, with its rows, how many description cells it has, and its pictures (IMG-01, IMG-02 … on the first sheet; IMG-S1 … on the others);
   - warnings: unfinished text, a number used twice, pictures below the last filled row, and drawing objects that are not pictures.
3. If a sheet was read wrongly, click **Edit mapping…**. Set the number column, the title column, the description columns, the status column, the header row (tables only) and the row range. **Use automatic detection** undoes your changes.
4. Uploading a newer version of the same file creates a new version (v2, v3 …). The summary lists the backlogs that were added, removed or changed. If you set a mapping by hand before, the app offers to reuse it.
   - Test cases, history and notes follow each backlog into the new version (matched by sheet name and backlog number).
5. Choose a workbook and click **Open workspace**.

Formula cells and the status column (for example `wt`) are never used for generation.

## 3. Pick a backlog

- Choose the **sheet**. The app remembers the last one per workbook. **All sheets** searches every sheet.
- Type the number in the search box: `backlog 5`, `5` and `#5` all work, and so do codes like `BL-5`. Matches are exact, so `1` never finds `10`.
- If the number is not on the sheet, you see the numbers that are. No request is sent.
- If a number appears twice on a sheet, the app asks which one you mean.

## 4. Check what will be sent (Backlog tab)

- **Description**: the backlog's sentences and the cells they come from.
- **Pictures**: sent to Claude in the order shown.
  - **Attach…** adds any picture of the workbook. The same screenshot can belong to several backlogs.
  - **Detach** and **Move left / right** change the set and the order. **View** shows a picture at full size.
- **Notes**: definitions and clarifications, for example what a "core app" is, or answers to earlier questions. Notes are sent with every generation of this backlog.
- **Supplementary context**: text of another backlog from any uploaded workbook, or text you paste. Use it when behavior is described somewhere else.
- A backlog with no text and no pictures cannot be generated until you add a note.

## 5. Generate

1. Click **Generate test cases**. Claude (the latest Opus, or the model you pinned in Settings) writes the test cases. This takes about a minute or two.
2. The answer is checked against the schema and the rules: IDs, happy paths first, an "Assert that" check in every case, and tags.
   - If a check fails, the app asks Claude once to fix it.
   - If the answer is cut off, the app keeps the complete test cases and asks for the rest.
3. The test cases appear on the **Test cases** tab. Questions that Claude could not answer from the material appear on **Open questions**.

To **regenerate**:

- Type optional **guidance** (for example "more permission cases") and click **Regenerate test cases**.
- Lock the test cases you want to keep (the **Locked** column), and leave **Keep locked test cases** on. Claude keeps them and adds what is missing.
- **History…** lists every generation, including failed ones. **Restore this run** brings an earlier set back.

## 6. Review and edit (Test cases tab)

- Double-click a cell to edit it. Type, Priority and Basis are lists. Preconditions and steps are one per line; with the per-step style, a step is written as `action => expected`.
- Edited test cases show ✎. **Add**, **Duplicate**, **Delete**, **Up** and **Down** change the list, and **Renumber IDs** puts the IDs back in order.
- The **Warnings** column points out things to fix, such as the title prefix, a missing "Assert that", the order, unknown tags or IDs out of sequence. Warnings never block you.
- **Show** filters the table by tag.

## 7. Answer open questions

Choose a question, type the answer and click **Add answer to notes**. The question and answer become a note, so the next generation uses them.

## 8. Export

Click **Export…** in the workspace header. Choose this backlog, or every backlog of the workbook that has test cases, and optionally only one tag.

| Format | What you get |
|---|---|
| JSON | The response object, an array for several backlogs, or a folder with one file per backlog |
| CSV | UTF-8 with BOM, one row per test case, tags joined with `;`, numbered steps |
| Excel | Sheet "Test Cases" (frozen header, wrapped text) and sheet "Open Questions" |

The columns follow the selected output preset. Every export is logged.

## 9. Hand over to Smoke or Regression

1. On the Test cases tab, tick test cases in the **✓** column. Or pick a tag in **Show** and click **Select shown**.
2. Choose what to do with them:
   - **Create Smoke run…** or **Create Regression run…** makes a **manual checklist run**. You can name a new run (default `Smoke – <workbook> – <date>`) or add the cases to an open run. Nothing is sent automatically.
   - **Add as scenarios…** puts the test cases into the scenario **library**. Claude Code can then run them in the browser: in Regression Testing, use **Add from library** on the Scenarios tab.
   - **Copy as prompt…** copies a ready-made prompt with the test cases to the clipboard. Paste it into Claude for a printable checklist.
3. In the dialog you can also choose **every test case of the workbook with a tag** instead of the selection.

To follow a checklist run, open **Smoke Testing** or **Regression Testing** and go to the **Checklists** tab. The Launchpad cards show how many runs are open.

- For each test case you see the ID, title, preconditions, numbered steps and expected result.
- Record **Pass**, **Fail**, **Blocked** or **Skipped** (the next case is selected for you). Add notes, and **Attach screenshot…** for failures.
- The progress line counts the results. **Export…** writes the run to Excel or CSV. **Complete run** closes it, and **Reopen run** opens it again.
- Each case in a run is a snapshot, so later edits in the designer do not change it. While a run has no results yet, **Refresh from designer** takes the current versions.

## 10. Settings

Open **Settings…** on the setup page or in the workspace.

| Setting | Default | What it does |
|---|---|---|
| Merge same-flow test cases | on, about 15 steps | Checks along one flow become one test case; different final actions stay separate |
| Avoid performance and heavy test cases | on | No load, speed or large-data test cases |
| UI testing policy | exclude visual-only | No checks of color, position or fonts; existence and behavior are tested |
| Suggest tags | on (`smoke`, `regression`) | `smoke` on high-priority happy paths, `regression` on all; the vocabulary can be extended |
| Output preset | Default | Optional fields, ID pattern (e.g. `TC-003-02` or `PD-3-2`), priority labels (High/Medium/Low or P1/P2/P3), steps style, title prefix. Save your own with **Save as new preset…** |
| Model | latest Opus | Or a pinned model id |
| Product context | the app under test | Describes the product to Claude; change it for other apps |

Output is always in English.

## Notes

- Workbooks, pictures, test cases and runs stay in the app's local database on this machine. Only the backlog being generated (text, pictures and your notes) is sent to Claude, one backlog per request.
- The prompt wording lives in versioned files (`src/wta/designer/prompts/v1/`). Every generation stores the prompt version, the model and the settings it used, so you can see them in **History**.
