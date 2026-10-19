"""Build the generation prompt, the two JSON schemas and the user message from the settings (spec 5.3, 5.4, 7).

The wording lives in versioned files under prompts/<version>/; this module only selects and fills them.
Every generation run stores PROMPT_VERSION (M3).
"""

import base64
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from wta.designer.settings import PER_STEP_EXPECTED, DesignerSettings, OutputPreset

PROMPT_VERSION = "v1"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_REGEX_SPECIAL = set("\\^$.|?*+()[]{}/")


@cache
def _read(name: str, version: str = PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / version / name).read_text(encoding="utf-8")


@cache
def _json(name: str, version: str = PROMPT_VERSION) -> dict:
    return json.loads(_read(name, version))


def fill(template: str, **values) -> str:
    """Replace {{name}} placeholders in one pass (values are never re-scanned); unknown names raise KeyError."""
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


def _escape_regex(text: str) -> str:
    return "".join("\\" + ch if ch in _REGEX_SPECIAL else ch for ch in text)


# --- schemas (spec 5.3, built from the preset per 5.4) ------------------------------------------


def _digits(pad: int) -> str:
    return f"[0-9]{{{pad}}}" if pad else "[0-9]+"


def _test_case_properties(preset: OutputPreset, full: bool) -> dict:
    def text(min_length: int | None = 1, **extra) -> dict:
        prop = {"type": "string"}
        if full:
            prop.update(extra)
            if min_length:
                prop["minLength"] = min_length
        return prop

    if preset.steps_style == PER_STEP_EXPECTED:
        step = {"type": "object", "additionalProperties": False, "required": ["action", "expected"],
                "properties": {"action": text(), "expected": text(min_length=None)}}
        steps = {"type": "array", "minItems": 1, "items": step} if full else {"type": "array", "items": step}
    else:
        steps = {"type": "array", "minItems": 2, "items": text()} if full else {"type": "array", "items": text()}
    pattern = f"^{preset.id_prefix}-{_digits(preset.backlog_pad)}-{_digits(preset.seq_pad)}$"
    title = ({"type": "string", "pattern": f"^{_escape_regex(preset.title_prefix)} .{{4,}}$", "maxLength": 120}
             if full else {"type": "string"})
    properties = {
        "id": {"type": "string", "pattern": pattern} if full else {"type": "string"},
        "backlog_number": {"type": "integer", "minimum": 1} if full else {"type": "integer"},
        "title": title,
        "type": {"enum": ["happy_path", "corner_case"]},
        "priority": {"enum": list(preset.priority_labels)},
        "tags": ({"type": "array", "uniqueItems": True, "items": text()} if full
                 else {"type": "array", "items": {"type": "string"}}),
        "preconditions": {"type": "array", "items": text()},
        "steps": steps,
        "expected_result": text(),
        "basis": {"enum": ["specified", "inferred"]},
        "source_reference": text(),
    }
    return {name: prop for name, prop in properties.items() if preset.includes(name)}


def response_schema(preset: OutputPreset) -> dict:
    """Schema (a): the full validation schema, checked locally on every response."""
    properties = _test_case_properties(preset, full=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "test-case-designer-response",
        "title": "Test Case Designer response",
        "type": "object",
        "additionalProperties": False,
        "required": ["backlog_number", "backlog_title", "status", "test_cases", "open_questions"],
        "properties": {
            "backlog_number": {"type": "integer", "minimum": 1},
            "backlog_title": {"type": "string", "minLength": 1},
            "status": {"enum": ["ok", "backlog_not_found", "insufficient_information"]},
            "test_cases": {"type": "array", "items": {"$ref": "#/$defs/test_case"}},
            "open_questions": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
        "$defs": {
            "test_case": {
                "title": "Test case",
                "type": "object",
                "additionalProperties": False,
                "required": list(properties),
                "properties": properties,
            }
        },
    }


def api_schema(preset: OutputPreset) -> dict:
    """Schema (b): structure only, for structured output (`claude --json-schema`); the rest is checked locally."""
    properties = _test_case_properties(preset, full=False)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["backlog_number", "backlog_title", "status", "test_cases", "open_questions"],
        "properties": {
            "backlog_number": {"type": "integer"},
            "backlog_title": {"type": "string"},
            "status": {"enum": ["ok", "backlog_not_found", "insufficient_information"]},
            "test_cases": {"type": "array", "items": {"$ref": "#/$defs/test_case"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
        "$defs": {
            "test_case": {
                "type": "object",
                "additionalProperties": False,
                "required": list(properties),
                "properties": properties,
            }
        },
    }


# --- system prompt (spec 7.1, 7.2) --------------------------------------------------------------


def rule_blocks(settings: DesignerSettings) -> list[str]:
    """One block per setting, in the order of spec 7.2 (no tags block when the preset has no tags field)."""
    blocks = _json("rule_blocks.json")
    preset = settings.preset()
    result = [
        fill(blocks["merging_on"], merge_max_steps=settings.merge_max_steps) if settings.merge_same_flow
        else blocks["merging_off"],
        blocks["heavy_on"] if settings.avoid_heavy_and_performance else blocks["heavy_off"],
        blocks[f"ui_{settings.ui_testing_policy}"],
    ]
    if preset.includes("tags"):
        result.append(fill(blocks["tags_on"], tag_vocabulary=json.dumps(settings.tag_vocabulary),
                           priority_high=preset.priority_labels[0])
                      if settings.tag_suggestions else blocks["tags_off"])
    return result


def format_rules(preset: OutputPreset) -> list[str]:
    lines = _json("format_rules.json")

    def padding(digits: int) -> str:
        return fill(lines["padding"], digits=digits) if digits else ""

    steps_rule = lines["steps_per_step"] if preset.steps_style == PER_STEP_EXPECTED else lines["steps_single"]
    high, medium, low = preset.priority_labels
    result = [
        lines["output"],
        fill(lines["language_title_steps"], title_prefix=preset.title_prefix, steps_rule=steps_rule),
        fill(lines["id"], id_prefix=preset.id_prefix, backlog_padding=padding(preset.backlog_pad),
             seq_padding=padding(preset.seq_pad), first_sequence="1".zfill(max(preset.seq_pad, 1))),
        fill(lines["type_priority"], priority_high=high, priority_medium=medium, priority_low=low),
    ]
    result += [lines[name] for name in ("basis", "source_reference") if preset.includes(name)]
    result += [lines["status"], lines["open_questions"]]
    return result


def system_prompt(settings: DesignerSettings) -> str:
    preset = settings.preset()
    return fill(_read("system.txt"), product_context=settings.product_context,
                rule_blocks="\n".join(rule_blocks(settings)), format_rules="\n".join(format_rules(preset)),
                response_schema=json.dumps(response_schema(preset), indent=2, ensure_ascii=False))


# --- user message (spec 7.3) ----------------------------------------------------------------------


@dataclass
class PromptImage:
    image_id: str  # IMG-04
    anchor_cell: str
    mime: str
    data: bytes
    transcription: str = ""  # tests, or when the picture itself is unavailable


def backlog_number_value(number: str) -> int | str:
    return int(number) if number.isdigit() else number


def backlog_row_data(number: str, title: str, sheet: str, first_row: int, last_row: int,
                     text_blocks: list[tuple[str, str]], images: list[PromptImage], domain_notes: list[str],
                     status_code: str | None = None) -> dict:
    """The backlog as the model sees it, in the shape of the Section 6 inputs."""
    data = {"backlog_number": backlog_number_value(number)}
    if status_code:
        data["status_code"] = status_code  # traceability only; the prompt says it is ignored
    data.update({
        "title": title,
        "source": {"sheet": sheet, "first_row": first_row, "last_row": last_row},
        "description_blocks": [{"cell": cell, "text": text} for cell, text in text_blocks],
        "domain_notes": list(domain_notes),
        "images": [dict({"image_id": i.image_id, "anchor_cell": i.anchor_cell},
                        **({"transcription": i.transcription} if i.transcription else {})) for i in images],
    })
    return data


def _lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item.strip())


def user_message_text(row_data: dict, domain_notes: list[str], supplementary: list[str] = (),
                      user_guidance: str = "", locked_test_cases: list[dict] = ()) -> str:
    source = row_data["source"]
    return fill(_read("user_message.txt"), backlog_number=row_data["backlog_number"], sheet_name=source["sheet"],
                first_row=source["first_row"], last_row=source["last_row"],
                backlog_row_data=json.dumps(row_data, indent=2, ensure_ascii=False),
                domain_notes=_lines(domain_notes), supplementary_context=_lines(list(supplementary)),
                user_guidance=user_guidance.strip(),
                locked_test_cases=json.dumps(list(locked_test_cases), ensure_ascii=False) if locked_test_cases else "")


def user_content(images: list[PromptImage], text: str) -> list[dict]:
    """Content blocks: each image after a short label, then the text (spec 7.3)."""
    blocks = []
    label = _read("image_label.txt").strip()
    for image in images:
        if not image.data:
            continue  # transcription only (in backlog_row_data)
        blocks.append({"type": "text", "text": fill(label, image_id=image.image_id, anchor_cell=image.anchor_cell)})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": image.mime,
                                                   "data": base64.b64encode(image.data).decode("ascii")}})
    blocks.append({"type": "text", "text": text})
    return blocks
