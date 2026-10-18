"""Trends dashboard: pass rate by day and open bugs per session, for one site."""

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QDateTimeAxis,
    QLineSeries,
    QValueAxis,
)
from PySide6.QtCore import QDateTime, QTime, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wta import theme, trends
from wta.db.database import Database


class TrendsDialog(QDialog):
    def __init__(self, db: Database, site: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Trends")
        self.resize(1000, 760)
        self.site = QComboBox()
        self.site.addItems(trends.sites(db))
        if site and self.site.findText(site) >= 0:
            self.site.setCurrentText(site)
        self.empty = QLabel()
        self.empty.setProperty("muted", True)
        self.pass_view = QChartView()
        self.pass_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.bugs_view = QChartView()
        self.bugs_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Day", "Runs", "Steps", "Pass rate", "Bugs found"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setFixedHeight(150)
        top = QHBoxLayout()
        top.addWidget(QLabel("Site"))
        top.addWidget(self.site)
        top.addStretch(1)
        note = QLabel("Pass rate uses Claude's status per run; open bugs use your review where there is one.")
        note.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.empty)
        layout.addWidget(self.pass_view, 1)
        layout.addWidget(self.bugs_view, 1)
        layout.addWidget(self.table)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.site.currentTextChanged.connect(lambda _s: self.reload())
        self.reload()

    def _chart(self, title: str) -> QChart:
        chart = QChart()
        chart.setTitle(title)
        chart.legend().hide()
        dark = theme.current() is theme.DARK
        chart.setTheme(QChart.ChartTheme.ChartThemeDark if dark else QChart.ChartTheme.ChartThemeLight)
        chart.setBackgroundBrush(QColor(theme.current().panel))
        chart.setTitleBrush(QColor(theme.current().text))
        return chart

    def reload(self) -> None:
        site = self.site.currentText()
        days = trends.runs_by_day(self.db, site) if site else []
        sessions = trends.bugs_by_session(self.db, site) if site else []
        self.empty.setText("" if days else "No finished runs for this site yet." if site else "No sessions yet.")
        self.pass_view.setChart(self._pass_chart(days))
        self.bugs_view.setChart(self._bugs_chart(sessions))
        self.table.setRowCount(len(days))
        for row, stat in enumerate(reversed(days)):
            for column, text in enumerate((stat.day.isoformat(), str(stat.runs), str(stat.steps),
                                           f"{stat.pass_rate:.0f}%", str(stat.failed))):
                self.table.setItem(row, column, QTableWidgetItem(text))

    def _pass_chart(self, days: list[trends.DayStat]) -> QChart:
        chart = self._chart("Pass rate by day (%)")
        series = QLineSeries()
        series.setColor(QColor("#2E8B57"))
        series.setPointsVisible(True)
        for stat in days:
            moment = QDateTime(stat.day, QTime(12, 0))
            series.append(moment.toMSecsSinceEpoch(), stat.pass_rate)
        chart.addSeries(series)
        x = QDateTimeAxis()
        x.setFormat("dd MMM")
        if days:
            first, last = QDateTime(days[0].day, QTime(0, 0)), QDateTime(days[-1].day, QTime(23, 59))
            x.setRange(first.addDays(-1), last.addDays(1))
            x.setTickCount(min(max(len(days) + 2, 3), 10))
        y = QValueAxis()
        y.setRange(0, 100)
        y.setLabelFormat("%d")
        chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(x)
        series.attachAxis(y)
        return chart

    def _bugs_chart(self, sessions: list[trends.SessionBugs]) -> QChart:
        chart = self._chart("Open bugs per session")
        bars = QBarSet("Open bugs")
        bars.setColor(QColor("#E5484D"))
        names = []
        for item in sessions:
            bars.append(item.open_bugs)
            names.append(f"{item.name}\n{item.created:%d %b}")
        series = QBarSeries()
        series.append(bars)
        chart.addSeries(series)
        x = QBarCategoryAxis()
        x.append(names or [""])
        y = QValueAxis()
        y.setRange(0, max([s.open_bugs for s in sessions] + [4]))
        y.setLabelFormat("%d")
        y.setTickCount(5)
        chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(x)
        series.attachAxis(y)
        return chart
