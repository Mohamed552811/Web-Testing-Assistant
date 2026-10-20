"""Check a model response against schema (a) and the cross-field rules of spec 5.3.

`validate()` first normalizes enum capitalization (structured output may change it, spec 5.3 note),
then reports every problem as a short sentence that can be sent back to the model on a retry.
"""

import copy
import json
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from wta.designer.prompt_builder import is_numeric_key, key_digits, response_schema
from wta.designer.settings import PER_STEP_EXPECTED, DesignerSettings

ASSERT_PREFIX = "Assert that"
_ENUM_FIELDS = ("type", "priority", "basis")


@dataclass
class ValidationResult:
    response: dict  # normalized copy
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _normalize_enum(value, allowed: list[str]):
    if isinstance(value, str):
        for option in allowed:
            if value.strip().lower() == option.lower():
                return option
    return value


def normalize(data: dict, settings: DesignerSettings) -> dict:
    """A copy with enum values in their canonical capitalization."""
    data = copy.deepcopy(data)
    preset = settings.preset()
    enums = {"type": ["happy_path", "corner_case"], "priority": list(preset.priority_labels),
             "basis": ["specified", "inferred"]}
    if isinstance(data, dict):
        data["status"] = _normalize_enum(data.get("status"), ["ok", "backlog_not_found", "insufficient_information"])
        for case in data.get("test_cases") or []:
            if isinstance(case, dict):
                for name in _ENUM_FIELDS:
                    if name in case:
                        case[name] = _normalize_enum(case[name], enums[name])
                if isinstance(case.get("tags"), list):
                    case["tags"] = [t.strip().lower() if isinstance(t, str) else t for t in case["tags"]]
    return data


def _where(path) -> str:
    parts = []
    for part in path:
        parts.append(f"[{part}]" if isinstance(part, int) else (("." if parts else "") + str(part)))
    return "".join(parts) or "response"


def expected_id(prefix: str, backlog_pad: int, seq_pad: int, number: int, sequence: int) -> str:
    return f"{prefix}-{str(number).zfill(backlog_pad)}-{str(sequence).zfill(seq_pad)}"


def validate(data, settings: DesignerSettings, backlog_key: str) -> ValidationResult:
    """Schema (a) plus the cross-field rules for the requested backlog."""
    numeric = is_numeric_key(backlog_key)
    data = normalize(data, settings) if isinstance(data, dict) else data
    result = ValidationResult(data if isinstance(data, dict) else {})
    if not isinstance(data, dict):
        result.errors.append("The response is not a JSON object.")
        return result
    preset = settings.preset()
    validator = Draft202012Validator(response_schema(preset, numeric))
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        result.errors.append(f"{_where(error.absolute_path)}: {error.message}")
    if result.errors:
        return result  # the cross-field rules assume the structure is right

    requested = int(backlog_key) if numeric else backlog_key
    cases = data["test_cases"]
    if data["backlog_number"] != requested:
        result.errors.append(f"backlog_number is {data['backlog_number']!r} but backlog {backlog_key} was requested.")
    if data["status"] != "ok":
        if cases:
            result.errors.append(f"status is {data['status']!r}, so test_cases must be empty.")
        return result

    number = key_digits(backlog_key)
    seen_corner = False
    titles: set[str] = set()
    vocabulary = set(settings.tag_vocabulary)
    high = preset.priority_labels[0]
    for index, case in enumerate(cases, 1):
        where = f"test_cases[{index - 1}] ({case.get('id')})"
        if case["backlog_number"] != requested:
            result.errors.append(f"{where}: backlog_number must be {requested!r}.")
        wanted = expected_id(preset.id_prefix, preset.backlog_pad, preset.seq_pad, number, index)
        if case["id"] != wanted:
            result.errors.append(f"{where}: the id must be {wanted} (IDs follow output order, starting at 1).")
        if case["type"] == "corner_case":
            seen_corner = True
        elif seen_corner:
            result.errors.append(f"{where}: happy_path test cases must all come before the corner_case ones.")
        if preset.steps_style == PER_STEP_EXPECTED:
            if not any(step["expected"].strip() for step in case["steps"]):
                result.errors.append(f"{where}: at least one step needs an \"expected\" check.")
        elif not any(step.startswith(ASSERT_PREFIX) for step in case["steps"]):
            result.errors.append(f"{where}: at least one step must start with \"{ASSERT_PREFIX}\".")
        title = case["title"].strip().lower()
        if title in titles:
            result.errors.append(f"{where}: the title is used twice; titles must be unique.")
        titles.add(title)
        if preset.includes("tags"):
            tags = case["tags"]
            unknown = [t for t in tags if t not in vocabulary]
            if unknown:
                result.errors.append(f"{where}: tags {unknown} are not in the vocabulary {sorted(vocabulary)}.")
            if settings.tag_suggestions:
                if "regression" not in tags:
                    result.errors.append(f"{where}: every test case needs the \"regression\" tag.")
                if case["type"] == "happy_path" and case["priority"] == high and "smoke" not in tags:
                    result.errors.append(f"{where}: a {high} happy_path needs the \"smoke\" tag.")
            elif tags:
                result.errors.append(f"{where}: tags must be empty (tag suggestions are off).")
    if not any(case["type"] == "happy_path" for case in cases):
        result.errors.append("At least one happy_path test case is required.")
    return result


def case_warnings(cases: list[dict], settings: DesignerSettings, backlog_key: str) -> list[list[str]]:
    """Warnings per test case for the editor (spec 8.1 (7)): title prefix, an "Assert that" check, order,
    vocabulary, unique titles and IDs in sequence. Edits are never blocked by these."""
    preset = settings.preset()
    vocabulary = set(settings.tag_vocabulary)
    number = key_digits(backlog_key) if any(ch.isdigit() for ch in backlog_key) else None
    titles = [str(c.get("title", "")).strip().lower() for c in cases]
    seen_corner = False
    result = []
    for index, case in enumerate(cases):
        warnings = []
        title = str(case.get("title", "")).strip()
        if not title.startswith(preset.title_prefix + " "):
            warnings.append(f"Title should start with \"{preset.title_prefix}\".")
        if len(title) > 120:
            warnings.append("Title is longer than 120 characters.")
        if titles.count(title.lower()) > 1:
            warnings.append("Another test case has the same title.")
        steps = case.get("steps") or []
        if preset.steps_style == PER_STEP_EXPECTED:
            if not any(isinstance(s, dict) and str(s.get("expected", "")).strip() for s in steps):
                warnings.append("No step has an expected check.")
        elif not any(isinstance(s, str) and s.startswith(ASSERT_PREFIX) for s in steps):
            warnings.append(f"No step starts with \"{ASSERT_PREFIX}\".")
        if not str(case.get("expected_result", "")).strip():
            warnings.append("Expected result is empty.")
        if case.get("type") == "corner_case":
            seen_corner = True
        elif seen_corner:
            warnings.append("Happy paths should come before corner cases.")
        if preset.includes("tags"):
            unknown = [t for t in case.get("tags") or [] if t not in vocabulary]
            if unknown:
                warnings.append(f"Tags not in the vocabulary: {', '.join(unknown)}.")
        if number is not None and case.get("id") != expected_id(preset.id_prefix, preset.backlog_pad,
                                                                 preset.seq_pad, number, index + 1):
            warnings.append("ID is out of sequence (use Renumber).")
        result.append(warnings)
    return result


def feedback(errors: list[str], limit: int = 25) -> str:
    """The retry note appended to the user message (spec 7.3)."""
    shown = errors[:limit]
    more = f"\n- … and {len(errors) - limit} more" if len(errors) > limit else ""
    return ("Your previous answer failed validation. Fix these problems and return the complete JSON object again:\n"
            + "\n".join(f"- {e}" for e in shown) + more)


def salvage_test_cases(text: str) -> list[dict]:
    """Complete test case objects from a JSON answer that was cut off (spec 7.3, continuation)."""
    if not text:
        return []
    start = text.find('"test_cases"')
    if start < 0:
        return []
    bracket = text.find("[", start)
    if bracket < 0:
        return []
    decoder = json.JSONDecoder()
    cases, position = [], bracket + 1
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] != "{":
            break
        try:
            obj, position = decoder.raw_decode(text, position)
        except ValueError:
            break  # the cut-off object
        if isinstance(obj, dict):
            cases.append(obj)
    return cases
