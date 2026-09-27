"""内置插件：成绩记录本。

记录每次考试 / 测验的科目、得分、满分与权重（如期中 0.3、期末 0.5），
自动给出加权平均（百分制）、算术平均、最高 / 最低与记录数，按账号保存。
"""

from __future__ import annotations

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_STATE_NAME = "grades"


class GradebookPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._rows: list[dict] = []
        self._loading = True
        self._build_ui()
        self._load()
        self._loading = False
        self._reload_table()

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("成绩记录本")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        # 统计卡片
        stats_card = QFrame()
        stats_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        stats_grid = QGridLayout(stats_card)
        stats_grid.setContentsMargins(18, 14, 18, 14)
        self._stat_labels: dict[str, QLabel] = {}
        for col, key in enumerate(("weighted", "average", "highest", "lowest", "count")):
            cap = QLabel(
                {"weighted": "加权平均", "average": "算术平均", "highest": "最高",
                 "lowest": "最低", "count": "记录数"}[key]
            )
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
            val = QLabel("--")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            color = c.get("accent", "#4f5df5") if key in ("weighted", "average") else c.get(
                "fg_primary", "#111827"
            )
            val.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: bold;")
            stats_grid.addWidget(cap, 0, col)
            stats_grid.addWidget(val, 1, col)
            self._stat_labels[key] = val
        layout.addWidget(stats_card)

        # 录入表单
        form_card = QFrame()
        form_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        form = QGridLayout(form_card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("科目，如 数学")
        self.subject_edit.setStyleSheet(self._input_style())
        self.score_spin = self._make_spin(0.0, 1000.0, 90.0)
        self.full_spin = self._make_spin(1.0, 1000.0, 100.0)
        self.weight_spin = self._make_spin(0.0, 100.0, 1.0)
        self.weight_spin.setSingleStep(0.1)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setStyleSheet(self._input_style())

        for col_i, (cap_text, widget) in enumerate(
            (
                ("日期", self.date_edit),
                ("科目", self.subject_edit),
                ("得分", self.score_spin),
                ("满分", self.full_spin),
                ("权重", self.weight_spin),
            )
        ):
            cap = QLabel(cap_text)
            cap.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
            form.addWidget(cap, 0, col_i)
            form.addWidget(widget, 1, col_i)

        add_btn = QPushButton("＋ 添加记录")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        add_btn.clicked.connect(self._add_row)
        form.addWidget(add_btn, 1, 5)
        layout.addWidget(form_card)

        # 明细表
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["日期", "科目", "得分", "满分", "权重", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col_i in (0, 2, 3, 4, 5):
            header.setSectionResizeMode(col_i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(
            f"QTableWidget {{ background: transparent; color: {c.get('fg_primary', '#111827')}; }}"
            f"QHeaderView::section {{ background: {c.get('bg_tertiary', '#f3f4f6')};"
            f" color: {c.get('fg_primary', '#111827')}; border: none; padding: 6px; }}"
        )
        layout.addWidget(self.table, 1)

        clear_btn = QPushButton("清空全部记录")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(
            f"color: {c.get('error', '#ef4444')}; background: transparent; border: none;"
        )
        clear_btn.clicked.connect(self._clear_all)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(clear_btn)
        layout.addLayout(bottom)

    def _make_spin(self, lo: float, hi: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(value)
        spin.setDecimals(1)
        spin.setStyleSheet(self._input_style())
        return spin

    # ------------------------------------------------------------------
    def _load(self) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=[])
        if isinstance(data, list):
            self._rows = [
                r for r in data
                if isinstance(r, dict) and r.get("subject") and r.get("full")
            ]

    def _save(self) -> None:
        self._ctx.save_state(_STATE_NAME, self._rows)

    def _add_row(self) -> None:
        subject = self.subject_edit.text().strip()
        if not subject:
            self._ctx.show_warning(self, "提示", "请输入科目名称")
            return
        full = self.full_spin.value()
        score = self.score_spin.value()
        if score > full:
            self._ctx.show_warning(self, "提示", "得分不能大于满分")
            return
        self._rows.append(
            {
                "date": self.date_edit.date().toString("yyyy-MM-dd"),
                "subject": subject,
                "score": score,
                "full": full,
                "weight": max(0.0, self.weight_spin.value()),
            }
        )
        self._rows.sort(key=lambda r: (r.get("date", ""), r.get("subject", "")))
        self.subject_edit.clear()
        self._save()
        self._reload_table()

    def _delete_row(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self._rows.pop(index)
            self._save()
            self._reload_table()

    def _clear_all(self) -> None:
        if not self._rows:
            return
        if not self._ctx.ask_confirm(self, "确认清空", "确定清空全部成绩记录吗？此操作不可撤销。"):
            return
        self._rows = []
        self._save()
        self._reload_table()

    def _reload_table(self) -> None:
        c = self._colors
        self.table.setRowCount(len(self._rows))
        percents = []
        weighted_num = 0.0
        weighted_den = 0.0
        for r, row in enumerate(self._rows):
            values = [
                str(row.get("date", "")),
                str(row.get("subject", "")),
                _fmt_num(row.get("score", 0)),
                _fmt_num(row.get("full", 0)),
                _fmt_num(row.get("weight", 1)),
            ]
            for col_i, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col_i != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(r, col_i, item)

            del_btn = QPushButton("删除")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet(
                f"color: {c.get('error', '#ef4444')}; background: transparent;"
                f"border: 1px solid {c.get('border_strong', '#d1d5db')};"
                "border-radius: 6px; padding: 3px 10px;"
            )
            del_btn.clicked.connect(lambda _=False, i=r: self._delete_row(i))
            self.table.setCellWidget(r, 5, del_btn)

            full = float(row.get("full", 0) or 0)
            if full > 0:
                percent = float(row.get("score", 0)) / full * 100.0
                percents.append(percent)
                weight = float(row.get("weight", 1) or 0)
                weighted_num += percent * weight
                weighted_den += weight

        self._stat_labels["weighted"].setText(
            f"{weighted_num / weighted_den:.1f}" if weighted_den > 0 else "--"
        )
        self._stat_labels["average"].setText(
            f"{sum(percents) / len(percents):.1f}" if percents else "--"
        )
        self._stat_labels["highest"].setText(f"{max(percents):.1f}" if percents else "--")
        self._stat_labels["lowest"].setText(f"{min(percents):.1f}" if percents else "--")
        self._stat_labels["count"].setText(str(len(self._rows)))


def _fmt_num(value) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{num:.1f}".rstrip("0").rstrip(".") if num != int(num) else str(int(num))


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "成绩记录本",
        lambda c: GradebookPanel(c),
        icon="📊",
        subtitle="记录每次得分，看清进步曲线",
    )
