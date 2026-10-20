"""Test Case Designer feature settings and output presets (spec 4.6, 5.4, 8.3).

Workspace-level, stored in the database, and snapshotted into every generation run (M3). Unknown or
invalid values fall back to the defaults, so a damaged settings row never blocks the feature.
"""

import copy
import re
from dataclasses import dataclass, field

SINGLE_EXPECTED = "steps_with_single_expected"
PER_STEP_EXPECTED = "steps_with_per_step_expected"
STEPS_STYLES = {SINGLE_EXPECTED: "Steps, one expected result at the end",
                PER_STEP_EXPECTED: "Each step with its own expected result"}

UI_POLICIES = {
    "exclude_visual_only": "Exclude visual-only checks (default)",
    "exclude_all_ui_elements": "Exclude all UI-element checks",
    "include_ui": "Include UI-element checks",
}
PRIORITY_SCALES = (("High", "Medium", "Low"), ("P1", "P2", "P3"))
OPTIONAL_FIELDS = ("tags", "preconditions", "basis", "source_reference")
REQUIRED_TAGS = ("smoke", "regression")  # the suggestion rule (spec 4.4) needs both
LATEST_OPUS = "latest_opus"
PINNED = "pinned"
MERGE_STEPS_RANGE = (5, 50)
OUTPUT_LANGUAGE = "English"  # fixed (spec 8.3); shown read-only
DEFAULT_PRODUCT_CONTEXT = ("an ERP 'Print' app (print designs, print settings, print editor, templates, data fields, "
                           "header/footer, preview, print command, permissions)")
_ID_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,9}$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,29}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")
MAX_PADDING = 6


@dataclass
class OutputPreset:
    name: str = "Default"
    builtin: bool = False
    optional_fields: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(OPTIONAL_FIELDS, True))
    id_prefix: str = "TC"
    backlog_pad: int = 3
    seq_pad: int = 2
    priority_labels: tuple[str, str, str] = PRIORITY_SCALES[0]
    steps_style: str = SINGLE_EXPECTED
    title_prefix: str = "Verify that"

    def includes(self, field_name: str) -> bool:
        return self.optional_fields.get(field_name, True) if field_name in OPTIONAL_FIELDS else True

    def to_json(self) -> dict:
        """The shape of spec 8.3 ("id_pattern" nested)."""
        return {"name": self.name, "builtin": self.builtin, "optional_fields": dict(self.optional_fields),
                "id_pattern": {"prefix": self.id_prefix, "backlog_pad": self.backlog_pad, "seq_pad": self.seq_pad},
                "priority_labels": list(self.priority_labels), "steps_style": self.steps_style,
                "title_prefix": self.title_prefix}

    @classmethod
    def from_json(cls, data) -> "OutputPreset":
        default = cls()
        if not isinstance(data, dict):
            return default
        pattern = data.get("id_pattern") if isinstance(data.get("id_pattern"), dict) else {}
        fields_in = data.get("optional_fields") if isinstance(data.get("optional_fields"), dict) else {}
        labels = tuple(data.get("priority_labels") or ())
        prefix = str(pattern.get("prefix", default.id_prefix)).strip()
        title = str(data.get("title_prefix", default.title_prefix)).strip()
        return cls(
            name=str(data.get("name") or default.name).strip()[:60] or default.name,
            builtin=bool(data.get("builtin", False)),
            optional_fields={f: bool(fields_in.get(f, True)) for f in OPTIONAL_FIELDS},
            id_prefix=prefix if _ID_PREFIX.match(prefix) else default.id_prefix,
            backlog_pad=_int(pattern.get("backlog_pad"), default.backlog_pad, 0, MAX_PADDING),
            seq_pad=_int(pattern.get("seq_pad"), default.seq_pad, 0, MAX_PADDING),
            priority_labels=labels if labels in PRIORITY_SCALES else default.priority_labels,
            steps_style=data.get("steps_style") if data.get("steps_style") in STEPS_STYLES else default.steps_style,
            title_prefix=title[:40] if title and '"' not in title else default.title_prefix,
        )


def _int(value, default: int, low: int, high: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else default


DEFAULT_PRESET = OutputPreset(name="Default", builtin=True)
# Always present after Default: the user's own format, editable but never deleted or renamed.
CUSTOM_PRESET_NAME = "Custom"


@dataclass
class DesignerSettings:
    merge_same_flow: bool = True
    merge_max_steps: int = 15
    avoid_heavy_and_performance: bool = True
    ui_testing_policy: str = "exclude_visual_only"
    tag_suggestions: bool = True
    tag_vocabulary: list[str] = field(default_factory=lambda: list(REQUIRED_TAGS))
    output_preset: str = "Default"  # name of the selected preset
    output_presets: list[OutputPreset] = field(default_factory=lambda: [
        copy.deepcopy(DEFAULT_PRESET),
        OutputPreset.from_json({**DEFAULT_PRESET.to_json(), "name": CUSTOM_PRESET_NAME, "builtin": False})])
    model_mode: str = LATEST_OPUS
    pinned_model_id: str | None = None
    # Set per workbook (with its mapping); this value is only the fallback for a workbook without one.
    product_context: str = DEFAULT_PRODUCT_CONTEXT

    def with_product_context(self, workbook_context: str) -> "DesignerSettings":
        """A copy using the workbook's product context (this fallback when the workbook has none)."""
        result = self.copy()
        if workbook_context.strip():
            result.product_context = workbook_context.strip()
        return result

    @property
    def output_language(self) -> str:
        return OUTPUT_LANGUAGE

    def preset(self) -> OutputPreset:
        """The selected preset (the built-in Default if the name is unknown)."""
        return next((p for p in self.output_presets if p.name == self.output_preset), self.output_presets[0])

    def cli_model(self) -> str:
        """The `claude --model` value: the CLI's `opus` alias resolves to the latest Opus (decision 1)."""
        return self.pinned_model_id if self.model_mode == PINNED and self.pinned_model_id else "opus"

    def problems(self) -> list[str]:
        """Why these settings cannot be saved as they are (shown in the dialog)."""
        found = []
        if self.tag_suggestions and not set(REQUIRED_TAGS) <= set(self.tag_vocabulary):
            found.append("Tag suggestions need \"smoke\" and \"regression\" in the vocabulary.")
        if any(not _TAG.match(t) for t in self.tag_vocabulary):
            found.append("Tags are lowercase words: letters, digits, - and _ (up to 30 characters).")
        if self.model_mode == PINNED and not _MODEL_ID.match(self.pinned_model_id or ""):
            found.append("Enter the model id to pin, for example claude-opus-5-5.")
        names = [p.name for p in self.output_presets]
        if len(set(names)) != len(names):
            found.append("Preset names must be unique.")
        return found

    def to_json(self) -> dict:
        """The shape of spec 8.3."""
        return {
            "merge_same_flow": self.merge_same_flow,
            "merge_max_steps": self.merge_max_steps,
            "avoid_heavy_and_performance": self.avoid_heavy_and_performance,
            "ui_testing_policy": self.ui_testing_policy,
            "output_language": OUTPUT_LANGUAGE,
            "tags": {"suggest": self.tag_suggestions, "vocabulary": list(self.tag_vocabulary)},
            "output_preset": self.output_preset,
            "output_presets": [p.to_json() for p in self.output_presets],
            "model": {"mode": self.model_mode, "pinned_id": self.pinned_model_id},
            "product_context": self.product_context,
        }

    @classmethod
    def from_json(cls, data) -> "DesignerSettings":
        default = cls()
        if not isinstance(data, dict):
            return default
        tags = data.get("tags") if isinstance(data.get("tags"), dict) else {}
        vocabulary = tags.get("vocabulary")
        vocabulary = ([t.strip().lower() for t in vocabulary if isinstance(t, str) and _TAG.match(t.strip().lower())]
                      if isinstance(vocabulary, list) else default.tag_vocabulary)
        vocabulary = list(dict.fromkeys(vocabulary))
        suggest = bool(tags.get("suggest", default.tag_suggestions))
        if suggest:  # the suggestion rule names these two tags
            vocabulary = list(dict.fromkeys([*REQUIRED_TAGS, *vocabulary]))
        presets = [OutputPreset.from_json(p) for p in data.get("output_presets") or [] if isinstance(p, dict)]
        presets = [p for p in presets if not p.builtin]  # the built-in Default always comes from the code
        names: set[str] = {DEFAULT_PRESET.name, CUSTOM_PRESET_NAME}
        custom = next((p for p in presets if p.name == CUSTOM_PRESET_NAME),
                      OutputPreset.from_json({**DEFAULT_PRESET.to_json(), "name": CUSTOM_PRESET_NAME, "builtin": False}))
        unique = []
        for preset in presets:
            if preset.name not in names:
                names.add(preset.name)
                unique.append(preset)
        model = data.get("model") if isinstance(data.get("model"), dict) else {}
        pinned = model.get("pinned_id") if isinstance(model.get("pinned_id"), str) else None
        mode = PINNED if model.get("mode") == PINNED and pinned and _MODEL_ID.match(pinned) else LATEST_OPUS
        context = data.get("product_context")
        selected = data.get("output_preset")
        return cls(
            merge_same_flow=bool(data.get("merge_same_flow", default.merge_same_flow)),
            merge_max_steps=_int(data.get("merge_max_steps"), default.merge_max_steps, *MERGE_STEPS_RANGE),
            avoid_heavy_and_performance=bool(data.get("avoid_heavy_and_performance",
                                                      default.avoid_heavy_and_performance)),
            ui_testing_policy=(data.get("ui_testing_policy") if data.get("ui_testing_policy") in UI_POLICIES
                               else default.ui_testing_policy),
            tag_suggestions=suggest,
            tag_vocabulary=vocabulary,
            output_preset=selected if selected in names else DEFAULT_PRESET.name,
            output_presets=[copy.deepcopy(DEFAULT_PRESET), custom, *unique],
            model_mode=mode,
            pinned_model_id=pinned if mode == PINNED else None,
            product_context=context.strip() if isinstance(context, str) and context.strip()
            else default.product_context,
        )

    def copy(self) -> "DesignerSettings":
        return DesignerSettings.from_json(self.to_json())


def snapshot(settings: DesignerSettings) -> dict:
    """What a generation run stores: the settings plus the selected preset in full."""
    data = settings.to_json()
    data["selected_preset"] = settings.preset().to_json()
    return data

