"""Hand test cases over to the Smoke and Regression modes (spec 8.6, decision 2).

- Checklist runs: see wta.db.checklists.
- "Copy as prompt": a ready-made text (versioned template) to paste into Claude for a printable checklist.
- "Add as scenarios": regression scenarios in the existing library, for Claude Code to run in the browser.
"""

import json

from wta.db.library import ItemInput
from wta.db.scenarios import ScenarioInput
from wta.designer import prompt_builder as pb
from wta.designer.settings import DesignerSettings

NAME_LIMIT = 200


def copy_as_prompt(mode: str, cases: list[dict], settings: DesignerSettings) -> str:
    """Spec 8.6 template; the cases are in the active output preset (as stored)."""
    return pb.fill(pb._read("copy_as_prompt.txt"), mode=mode, product_context=settings.product_context,
                   selected_test_cases_json=json.dumps(cases, indent=2, ensure_ascii=False))


def _step_text(step) -> str:
    if isinstance(step, dict):
        return step.get("action", "") + (f" — {step['expected']}" if step.get("expected") else "")
    return str(step)


def scenario_input(case: dict, source: str) -> ItemInput:
    """One test case as a library scenario: the title is the name, preconditions open the steps."""
    steps = []
    preconditions = [p for p in case.get("preconditions") or [] if p.strip()]
    if preconditions:
        steps.append("Make sure that: " + "; ".join(preconditions))
    steps += [_step_text(s) for s in case.get("steps") or [] if _step_text(s).strip()]
    name = f"{case.get('id', '')} {case.get('title', '')}".strip()[:NAME_LIMIT]
    scenario = ScenarioInput(name=name, steps="\n".join(steps), expected_result=str(case.get("expected_result", "")))
    return ItemInput(scenario=scenario, source=f"Test Case Designer: {source}")
