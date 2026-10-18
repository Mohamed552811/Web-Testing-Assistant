import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtGui import QColor, QIcon, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from wta import icons, settings, theme  # noqa: E402
from wta.app import MainWindow  # noqa: E402
from wta.db import Database, store  # noqa: E402
from wta.settings_dialog import SettingsDialog  # noqa: E402
from wta.welcome import AboutDialog  # noqa: E402

app = QApplication.instance() or QApplication([])


def has_color(qicon: QIcon, color: str, mode=QIcon.Mode.Normal) -> bool:
    image = qicon.pixmap(32, 32, mode).toImage()
    target = QColor(color)
    return any(
        image.pixelColor(x, y).alpha() > 200 and abs(image.pixelColor(x, y).red() - target.red()) < 12
        and abs(image.pixelColor(x, y).green() - target.green()) < 12 and abs(image.pixelColor(x, y).blue() - target.blue()) < 12
        for x in range(32) for y in range(32)
    )


class ThemeTests(unittest.TestCase):
    def tearDown(self):
        theme.apply(app, "light")

    def test_light_and_dark_use_the_brick_red_accent(self):
        self.assertEqual(theme.resolve("light").accent, "#9B2D20")
        self.assertEqual(theme.resolve("dark").accent, "#C8553D")
        theme.apply(app, "dark")
        self.assertIn("#C8553D", app.styleSheet())
        self.assertEqual(app.palette().color(QPalette.ColorRole.Highlight), QColor("#C8553D"))
        self.assertEqual(app.palette().color(QPalette.ColorRole.Window), QColor(theme.DARK.window))
        theme.apply(app, "light")
        self.assertEqual(app.palette().color(QPalette.ColorRole.Highlight), QColor("#9B2D20"))
        self.assertIn(theme.resolve("system"), (theme.LIGHT, theme.DARK))

    def test_fail_status_is_not_the_accent(self):
        from wta.step_table import STATUS_COLORS
        from wta.db.models import StepStatus
        fail = STATUS_COLORS[StepStatus.FAIL]
        self.assertNotEqual((fail.red(), fail.green(), fail.blue()), (0x9B, 0x2D, 0x20))


class IconTests(unittest.TestCase):
    def tearDown(self):
        theme.apply(app, "light")

    def test_icons_follow_the_theme(self):
        theme.apply(app, "light")
        button = QPushButton()
        icons.set_icon(button, "play")
        self.assertTrue(has_color(button.icon(), theme.LIGHT.text))
        theme.apply(app, "dark")  # re-tints every registered icon
        self.assertTrue(has_color(button.icon(), theme.DARK.text))
        accent = QPushButton()
        icons.set_icon(accent, "play", "on-accent")
        self.assertTrue(has_color(accent.icon(), "#ffffff"))
        self.assertTrue(has_color(accent.icon(), theme.DARK.muted, QIcon.Mode.Disabled))  # stays visible when disabled

    def test_every_bundled_icon_loads_and_licence_is_there(self):
        names = [p.stem for p in icons.ICON_DIR.glob("*.svg")]
        self.assertGreaterEqual(len(names), 20)
        for name in names:
            self.assertFalse(icons.icon(name, "#000000").isNull(), name)
        self.assertTrue((icons.ICON_DIR / "LICENSE-lucide.txt").is_file())

    def test_logo_files(self):
        sizes = sorted(s.width() for s in icons.logo_icon().availableSizes())
        self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256])
        self.assertEqual(icons.logo_pixmap(96).width(), 96)
        for name in ("logo.svg", "logo-16.svg", "logo.png", "logo-64.png", "app.ico"):
            self.assertTrue((icons.ASSETS / name).is_file(), name)


class SettingsThemeTests(unittest.TestCase):
    def test_theme_setting(self):
        self.assertEqual(settings.Settings().theme, "system")
        self.assertEqual(settings.Settings(theme="neon").cleaned().theme, "system")
        dialog = SettingsDialog(settings.Settings(theme="dark"))
        self.assertEqual(dialog.settings().theme, "dark")
        dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
        self.assertEqual(dialog.settings().theme, "light")
        dialog.deleteLater()


class MainWindowUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "wta.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def window(self):
        return MainWindow(self.db, app_dir=self.base, check_sign_in=False)

    def test_welcome_screen_until_a_session_is_open(self):
        a = store.create_session(self.db, store.SessionDetails(name="Shop", target_url="https://shop.example.com"))
        w = self.window()
        self.assertIs(w.left.currentWidget(), w.welcome)
        self.assertEqual(w.welcome.recent.count(), 1)
        self.assertIn("Shop", w.welcome.recent.item(0).text())
        w.welcome.recent.itemClicked.emit(w.welcome.recent.item(0))  # opening a recent session
        self.assertIs(w.left.currentWidget(), w.browser)
        self.assertEqual(w.current_session.id, a.id)
        w.deleteLater()

    def test_toolbar(self):
        w = self.window()
        texts = [a.text().replace("&", "") for a in w.toolbar.actions() if a.text()]
        self.assertEqual(texts, ["New session…", "Open session…", "Run batch", "Stop", "Export report…",
                                 "Settings…", "Close all browsers"])
        self.assertTrue(all(not a.icon().isNull() for a in w.toolbar.actions() if a.text()))
        self.assertFalse(w.run_action.isEnabled())  # no session, not signed in
        self.assertFalse(w.stop_action.isEnabled())
        self.assertFalse(w.top_bar.close_all_button.isVisibleTo(w))  # lives in the toolbar now
        w.deleteLater()

    def test_layout_is_remembered(self):
        w = self.window()
        w.resize(640, 480)  # fits the small offscreen test screen; larger sizes get clamped to it
        w.tabs.setCurrentIndex(3)
        w._save_ui_state()
        w.deleteLater()
        again = self.window()
        self.assertEqual(again.tabs.currentIndex(), 3)
        self.assertEqual((again.width(), again.height()), (640, 480))
        self.assertTrue((self.base / "ui-state.ini").is_file())
        again.deleteLater()

    def test_about_box(self):
        about = AboutDialog()
        self.assertIn("Web Testing Assistant", about.windowTitle())
        about.deleteLater()


if __name__ == "__main__":
    unittest.main()
