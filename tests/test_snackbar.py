import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # the offscreen platform has no fonts of its own; text would render as boxes
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QDialog, QLineEdit, QMainWindow, QWidget  # noqa: E402

from wta import snackbar, theme  # noqa: E402

try:  # the shared app, closed down cleanly at exit (see qt_app.py)
    from qt_app import app  # noqa: E402
except ImportError:  # run as tests.<module>
    from tests.qt_app import app  # noqa: E402


class SnackbarTests(unittest.TestCase):
    def setUp(self):
        self.window = QMainWindow()
        self.window.setCentralWidget(QWidget())
        self.window.statusBar().showMessage("ready")
        self.window.resize(1000, 700)
        self.window.show()
        app.processEvents()

    def tearDown(self):
        self.window.deleteLater()
        app.processEvents()

    def test_bottom_right_then_replaced_and_dismissed(self):
        bar = snackbar.show(self.window.centralWidget(), "Settings saved.")
        app.processEvents()
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.text.text(), "Settings saved.")
        self.assertGreater(bar.x(), self.window.width() // 2)  # right half
        self.assertLess(bar.geometry().bottom(), self.window.statusBar().y())  # above the status bar
        self.assertGreater(bar.y(), self.window.height() // 2)  # bottom half
        again = snackbar.show(self.window, "Site note saved.", snackbar.INFO)
        self.assertIs(again, bar)  # one snackbar per window: the new message replaces the old one
        self.assertEqual(len(self.window.findChildren(snackbar.Snackbar)), 1)
        self.assertEqual((bar.text.text(), bar.kind), ("Site note saved.", snackbar.INFO))
        self.assertTrue(bar.timer.isActive())
        bar.dismiss()
        bar.fade.setCurrentTime(bar.fade.duration())  # finish the fade-out at once
        app.processEvents()
        self.assertFalse(bar.isVisible())

    def test_follows_the_window_and_keeps_focus_where_it_was(self):
        field = QLineEdit(self.window.centralWidget())
        field.show()
        field.setFocus()
        app.processEvents()
        bar = snackbar.show(field, "Answer added to the backlog's notes.")
        app.processEvents()
        self.assertIs(app.focusWidget(), field)  # typing carries on
        self.window.resize(1300, 800)
        app.processEvents()
        self.assertEqual(bar.geometry().right(), self.window.width() - snackbar.MARGIN - 1)

    def test_from_a_dialog_it_shows_on_the_main_window(self):
        dialog = QDialog(self.window)
        inner = QWidget(dialog)
        bar = snackbar.show(inner, "Test Case Design settings saved.")
        self.assertIs(bar.parentWidget(), self.window)
        dialog.deleteLater()

    def test_colours_stand_out_in_both_themes(self):
        theme.apply(app, "light")
        light = snackbar.colors(snackbar.SUCCESS)
        theme.apply(app, "dark")
        dark = snackbar.colors(snackbar.SUCCESS)
        theme.apply(app, "light")
        self.assertNotEqual(light[0], theme.LIGHT.window)
        self.assertNotEqual(dark[0], theme.DARK.window)
        self.assertNotEqual(light[0], dark[0])  # dark on the light theme, light on the dark one


if __name__ == "__main__":
    unittest.main()
