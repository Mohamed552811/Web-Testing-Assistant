# PyInstaller spec for Web Testing Assistant (a folder app; Inno Setup packs it into Setup.exe).
# Built by tools/build_installer.py; run from the repository root.

from pathlib import Path

import re

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821 (set by PyInstaller)
SRC = ROOT / "src"

# The exe's own name and version (Task Manager, file properties, "Open with"), from src/wta/__init__.py.
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (SRC / "wta" / "__init__.py").read_text("utf-8")).group(1)
NUMBERS = tuple(int(n) for n in (VERSION.split(".") + ["0", "0", "0"])[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Mohamed Hesham"),
            StringStruct("FileDescription", "Web Testing Assistant"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "WebTestingAssistant"),
            StringStruct("OriginalFilename", "WebTestingAssistant.exe"),
            StringStruct("ProductName", "Web Testing Assistant"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

datas = (
    collect_data_files("wta", subdir="assets")  # logo, app icon and the bundled Lucide icons
    + collect_data_files("wta", subdir="designer/prompts")  # Test Case Designer prompt templates
    + collect_data_files("docx")  # python-docx's default document template (Word export)
    + collect_data_files("jsonschema_specifications")  # the JSON Schema metaschemas jsonschema loads
)

a = Analysis(  # noqa: F821
    [str(ROOT / "tools" / "wta_entry.py")],
    pathex=[str(SRC)],
    datas=datas,
    # Qt modules the app imports inside functions (dialogs opened later), so the analysis may not see them.
    hiddenimports=["PySide6.QtCharts", "PySide6.QtWebSockets", "PySide6.QtSvg", "wta.self_check"],
    excludes=["tkinter", "unittest", "pydoc_data"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WebTestingAssistant",
    console=False,  # a window app: no console behind it
    icon=str(SRC / "wta" / "assets" / "app.ico"),
    version=version_info,
)
COLLECT(exe, a.binaries, a.datas, name="WebTestingAssistant")  # noqa: F821
