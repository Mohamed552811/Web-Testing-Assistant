# PyInstaller spec for Web Testing Assistant (a folder app; Inno Setup packs it into Setup.exe).
# Built by tools/build_installer.py; run from the repository root.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent  # noqa: F821 (set by PyInstaller)
SRC = ROOT / "src"

datas = (
    collect_data_files("wta", subdir="assets")  # logo, app icon and the bundled Lucide icons
    + collect_data_files("wta", subdir="designer/prompts")  # Test Case Designer prompt templates
    + collect_data_files("docx")  # python-docx's default document template (Word export)
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
)
COLLECT(exe, a.binaries, a.datas, name="WebTestingAssistant")  # noqa: F821
