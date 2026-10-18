"""Build the Windows installer in one command, from the repository root:

    .venv\\Scripts\\python tools\\build_installer.py

1. PyInstaller bundles the app with Python, PySide6 and QtWebEngine  -> dist\\WebTestingAssistant\\
2. the bundle checks itself (--self-check)                           -> build\\self-check.json
3. Inno Setup wraps it in a normal Setup.exe                          -> dist\\installer\\WebTestingAssistant-Setup-<version>.exe

Needs the build tools in tools\\build-requirements.txt (pip) and Inno Setup 6 (ISCC.exe; found in its
usual folders, on PATH, or through the ISCC environment variable).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "tools" / "wta.spec"
ISS = ROOT / "tools" / "installer.iss"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
BUNDLE = DIST / "WebTestingAssistant"
EXE = BUNDLE / "WebTestingAssistant.exe"
ICON = ROOT / "src" / "wta" / "assets" / "app.ico"
LOGO_SVG = ROOT / "src" / "wta" / "assets" / "logo.svg"


def app_version() -> str:
    text = (ROOT / "src" / "wta" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("No __version__ in src/wta/__init__.py")
    return match.group(1)


def find_iscc(environ: dict | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    candidates = [env.get("ISCC"), shutil.which("ISCC"),
                  Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
                  Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
                  Path(env.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe"]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def pyinstaller_command() -> list[str]:
    return [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean",
            "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller")]


def iscc_command(iscc: Path, version: str, images: dict[str, list[Path]]) -> list[str]:
    return [str(iscc), f"/DAppVersion={version}", f"/DSourceDir={BUNDLE}", f"/DOutputDir={DIST / 'installer'}",
            f"/DIconFile={ICON}", "/DWizardImage=" + ",".join(map(str, images["large"])),
            "/DWizardSmallImage=" + ",".join(map(str, images["small"])), str(ISS)]


def wizard_images(folder: Path) -> dict[str, list[Path]]:
    """The installer's side and corner pictures, drawn from the app logo (100% and 200% sizes, BMP)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(LOGO_SVG))
    folder.mkdir(parents=True, exist_ok=True)
    made: dict[str, list[Path]] = {"large": [], "small": []}
    for kind, (width, height), background, logo_share in (("large", (164, 314), "#ffffff", 0.62),
                                                          ("small", (55, 58), "#ffffff", 0.9)):
        for scale in (1, 2):
            image = QImage(width * scale, height * scale, QImage.Format.Format_RGB32)
            image.fill(QColor(background))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            side = min(image.width(), image.height()) * logo_share
            renderer.render(painter, QRectF((image.width() - side) / 2, (image.height() - side) / 2, side, side))
            painter.end()
            path = folder / f"wizard-{kind}-{scale * 100}.bmp"
            if not image.save(str(path), "BMP"):
                raise SystemExit(f"Could not write {path}")
            made[kind].append(path)
    return made


def self_check() -> dict:
    """Start the bundled app with --self-check (no window) and return its report."""
    report = BUILD / "self-check.json"
    report.unlink(missing_ok=True)
    env = dict(os.environ, WTA_DATA_DIR=str(BUILD / "self-check-data"))
    code = subprocess.run([str(EXE), "--self-check", str(report)], env=env, timeout=300).returncode
    result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"bundle_ok": False}
    result["exit_code"] = code
    return result


def run(command: list[str]) -> None:
    print(">", " ".join(f'"{c}"' if " " in c else c for c in command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Web Testing Assistant installer.")
    parser.add_argument("--skip-installer", action="store_true", help="only build and check the app folder")
    parser.add_argument("--iscc", type=Path, help="path to Inno Setup's ISCC.exe")
    args = parser.parse_args(argv)
    version = app_version()
    iscc = args.iscc or find_iscc()
    if not args.skip_installer and iscc is None:
        raise SystemExit("Inno Setup 6 (ISCC.exe) not found. Install it (winget install JRSoftware.InnoSetup) "
                         "or pass --iscc.")

    print(f"Web Testing Assistant {version}", flush=True)
    run(pyinstaller_command())
    report = self_check()
    checks = report.get("checks", {})
    for name, result in checks.items():
        print(f"  {'ok ' if result['ok'] else 'NO '} {name}: {result['detail']}", flush=True)
    if not report.get("bundle_ok") or report.get("exit_code") != 0:
        raise SystemExit("The bundled app failed its self-check; see build/self-check.json.")
    if args.skip_installer:
        print(f"App folder: {BUNDLE}")
        return 0
    run(iscc_command(iscc, version, wizard_images(BUILD / "installer")))
    setup = DIST / "installer" / f"WebTestingAssistant-Setup-{version}.exe"
    print(f"Installer: {setup} ({setup.stat().st_size / 1024 / 1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
