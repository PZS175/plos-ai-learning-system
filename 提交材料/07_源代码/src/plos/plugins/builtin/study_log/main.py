"""内置插件：学习时长日志。

按科目记录每天的学习投入，帮助看清时间花在了哪里：

- 快速录入：日期（默认今天）+ 科目 + 分钟数 + 备注；
- 统计卡片：今日、本周（周一起算）、累计时长；
- 横向条形图直观对比各科时间分布；
- 明细表支持逐条删除，数据按账号保存。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from PyQt6.QtCore import QDate, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_STATE_NAME = "records"
_SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理", "其他"]


class _BarChart(QWidget):
    """按科目时长横向条形图。"""

    def __init__(self, colors: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors = colors
        self._data: List[Tuple[str, int]] = []
        self.setMinimumHeight(90)

    def set_data(self, data: List[Tuple[str, int]]) -> None:
        self._data = data
        self.setMinimumHeight(max(90, 26 * len(data) + 12))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._data:
            painter.setPen(QColor(self._colors.get("fg_muted", "#9ca3af")))
            font = QFont()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "还没有记录，先添加一条吧")
            painter.end()
            return

        maximum = max(value for _label, value in self._data) or 1
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        bar_color = QColor(self._colors.get("accent", "#4f5df5"))
        track_color = QColor(self._colors.get("bg_tertiary", "#eef0f7"))
        text_color = QColor(self._colors.get("fg_secondary", "#6b7280"))

        label_width = 46
        value_width = 62
        y = 6
        for label, value in self._data:
            painter.setPen(text_color)
            painter.drawText(QRectF(0, y, label_width, 20), Qt.AlignmentFlag.AlignVCenter, label)
            track = QRectF(label_width + 6, y + 4, self.width() - label_width - value_width - 12, 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track_color)
            painter.drawRoundedRect(track, 6, 6)
            width = track.width() * value / maximum
            painter.setBrush(bar_color)
            painter.drawRoundedRect(QRectF(track.x(), track.y(), max(4.0, width), track.height()), 6, 6)
            painter.setPen(text_color)
            painter.drawText(
                QRectF(self.width() - value_width, y, value_width, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{value} 分",
            )
            y += 26
        painter.end()


class StudyLogPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._records: List[dict] = []
        self._build_ui()
        self._load()
        self._reload_table()

    # ------------------------------------------------------------------
    def _input_style(self) -> str:
        c = self._colors
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 8px;"
        )

    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("学习时长日志")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        # 录入
        form_card = QFrame()
        form_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        form = QHBoxLayout(form_card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        form.addWidget(self._caption("日期"))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setStyleSheet(self._input_style())
        form.addWidget(self.date_edit)

        form.addWidget(self._caption("科目"))
        self.subject_box = QComboBox()
        self.subject_box.setEditable(True)
        self.subject_box.addItems(_SUBJECTS)
        self.subject_box.setStyleSheet(self._input_style())
        self.subject_box.setMinimumWidth(96)
        form.addWidget(self.subject_box)

        form.addWidget(self._caption("时长(分钟)"))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(1, 600)
        self.minutes_spin.setValue(30)
        self.minutes_spin.setStyleSheet(self._input_style())
        self.minutes_spin.setFixedWidth(84)
        form.addWidget(self.minutes_spin)

        form.addWidget(self._caption("备注"))
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("如：完成第三章习题")
        self.note_edit.setStyleSheet(self._input_style())
        self.note_edit.returnPressed.connect(self._add_record)
        form.addWidget(self.note_edit, 1)

        add_btn = QPushButton("＋ 记录")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold;"
        )
        add_btn.clicked.connect(self._add_record)
        form.addWidget(add_btn)
        root.addWidget(form_card)

        # 统计
        stats_card = QFrame()
        stats_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(16, 14, 16, 14)
        stats_layout.setSpacing(10)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(26)
        self._stat_labels = {}
        for key, caption in (("today", "今日"), ("week", "本周"), ("total", "累计"), ("days", "有记录天数")):
            box = QVBoxLayout()
            cap = QLabel(caption)
            cap.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
            value = QLabel("0")
            value.setStyleSheet(
                f"color: {c.get('accent', '#4f5df5')}; font-size: 22px; font-weight: bold;"
            )
            box.addWidget(cap)
            box.addWidget(value)
            stats_row.addLayout(box)
            self._stat_labels[key] = value
        stats_row.addStretch()
        stats_layout.addLayout(stats_row)

        self.chart = _BarChart(c, self)
        stats_layout.addWidget(self.chart)
        root.addWidget(stats_card)

        # 明细
        table_title = QLabel("明细记录")
        table_title.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 14px; font-weight: bold;"
        )
        root.addWidget(table_title)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["日期", "科目", "时长", "备注", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in (0, 1, 2, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(
            f"QTableWidget {{ background: {c.get('card_bg', '#fff')};"
            f" color: {c.get('fg_primary', '#111827')}; border-radius: 10px;"
            f" border: 1px solid {c.get('border', '#e5e7eb')}; }}"
            f"QHeaderView::section {{ background: {c.get('bg_tertiary', '#f3f4f6')};"
            f" color: {c.get('fg_primary', '#111827')}; border: none; padding: 6px; }}"
        )
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.tip_label = QLabel("")
        self.tip_label.setStyleSheet(f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 11px;")
        bottom.addWidget(self.tip_label, 1)
        clear_btn = QPushButton("清空全部记录")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(
            f"color: {c.get('error', '#ef4444')}; background: transparent; border: none;"
        )
        clear_btn.clicked.connect(self._clear_all)
        bottom.addWidget(clear_btn)
        root.addLayout(bottom)

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        return label

    # ------------------------------------------------------------------
    def _load(self) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=[])
        if isinstance(data, list):
            self._records = [
                item
                for item in data
                if isinstance(item, dict) and item.get("date") and item.get("subject")
            ]

    def _save(self) -> None:
        self._ctx.save_state(_STATE_NAME, self._records)

    def _add_record(self) -> None:
        subject = self.subject_box.currentText().strip()
        if not subject:
            self._ctx.show_warning(self, "提示", "请填写科目名称")
            return
        self._records.append(
            {
                "date": self.date_edit.date().toString("yyyy-MM-dd"),
                "subject": subject,
                "minutes": int(self.minutes_spin.value()),
                "note": self.note_edit.text().strip(),
            }
        )
        self._records.sort(key=lambda item: (item["date"], item["subject"]), reverse=True)
        self.note_edit.clear()
        self._save()
        self._reload_table()

    def _delete_record(self, index: int) -> None:
        if 0 <= index < len(self._records):
            self._records.pop(index)
            self._save()
            self._reload_table()

    def _clear_all(self) -> None:
        if not self._records:
            return
        if not self._ctx.ask_confirm(self, "确认清空", "确定清空全部学习记录吗？此操作不可撤销。"):
            return
        self._records = []
        self._save()
        self._reload_table()

    # ------------------------------------------------------------------
    def _reload_table(self) -> None:
        c = self._colors
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            values = [
                str(record.get("date", "")),
                str(record.get("subject", "")),
                f"{int(record.get('minutes', 0))} 分钟",
                str(record.get("note", "")),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column != 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)

            delete_btn = QPushButton("删除")
            delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            delete_btn.setStyleSheet(
                f"color: {c.get('error', '#ef4444')}; background: transparent;"
                f"border: 1px solid {c.get('border_strong', '#d1d5db')};"
                "border-radius: 6px; padding: 3px 10px;"
            )
            delete_btn.clicked.connect(lambda _=False, i=row: self._delete_record(i))
            self.table.setCellWidget(row, 4, delete_btn)

        self._refresh_stats()

    def _refresh_stats(self) -> None:
        today = date.today()
        today_text = today.strftime("%Y-%m-%d")
        week_start = today - timedelta(days=today.weekday())

        total = 0
        week_total = 0
        today_total = 0
        by_subject: dict = {}
        days = set()

        for record in self._records:
            try:
                record_date = datetime.strptime(str(record.get("date", "")), "%Y-%m-%d").date()
            except ValueError:
                continue
            minutes = int(record.get("minutes", 0) or 0)
            subject = str(record.get("subject", "其他"))
            total += minutes
            days.add(record_date)
            if record_date == today:
                today_total += minutes
            if record_date >= week_start:
                week_total += minutes
            by_subject[subject] = by_subject.get(subject, 0) + minutes

        self._stat_labels["today"].setText(f"{today_total}")
        self._stat_labels["week"].setText(f"{week_total}")
        self._stat_labels["total"].setText(f"{total}")
        self._stat_labels["days"].setText(f"{len(days)}")

        chart_data = sorted(by_subject.items(), key=lambda pair: pair[1], reverse=True)
        self.chart.set_data(chart_data)

        if total >= 60:
            self.tip_label.setText(
                f"累计投入 {total // 60} 小时 {total % 60} 分钟（今日 {today_text}）"
            )
        else:
            self.tip_label.setText(f"累计投入 {total} 分钟（今日 {today_text}）")


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "学习时长",
        lambda c: StudyLogPanel(c),
        icon="⏳",
        subtitle="记录每天各科投入，看清时间去哪了",
    )
