"""Extract the reference workbook's screenshots as Test Case Designer fixtures (spec 0.3 / 3.7).

    .venv\\Scripts\\python tools\\extract_designer_fixtures.py [workbook.xlsx] [output folder]

Writes IMG-01.png … IMG-14.png, IMG-S1.png and manifest.json (owner sheet and backlog, anchor cell, size, MD5)
using the app's own upload analysis, so the fixtures match what the app sends to the model.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wta.designer.layout import analyze_workbook  # noqa: E402
from wta.designer.xlsx_reader import read_workbook  # noqa: E402

DEFAULT_WORKBOOK = ROOT / "docs" / "test-case-designer" / "Print_designer_6.xlsx"
DEFAULT_OUTPUT = ROOT / "tests" / "fixtures" / "designer"
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


def extract(workbook: Path, output: Path) -> list[dict]:
    analysis = analyze_workbook(read_workbook(workbook))
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for sheet in analysis.sheets:
        owners = {index: backlog.number for backlog in sheet.backlogs for index in backlog.picture_indexes}
        for index, labeled in enumerate(sheet.pictures):
            picture = labeled.picture
            name = labeled.label + EXTENSIONS.get(picture.mime, ".bin")
            (output / name).write_bytes(picture.data)
            manifest.append({
                "image_id": labeled.label, "file": name, "sheet": sheet.name, "owner_backlog": owners.get(index),
                "anchor_cell": picture.anchor_cell, "width": picture.width, "height": picture.height,
                "md5": picture.md5,
            })
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKBOOK
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    for entry in extract(source, target):
        print(f"{entry['image_id']:7} {entry['sheet']:10} backlog {entry['owner_backlog']!s:3} "
              f"{entry['anchor_cell']:5} {entry['width']}x{entry['height']} {entry['md5']}")
