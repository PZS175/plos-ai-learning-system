"""内置插件：每周课程表。

7 天 × 节次的可编辑表格，内容实时按账号保存；
支持增加 / 删除节次、清空本周、恢复示例课表。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_DEFAULT_PERIODS = 8
_STATE_NAME = "schedule"

_SAMPLE = [
    ["语文", "数学", "英语", "物理", "数学", "", ""],
    ["数学", "语文", "物理", "英语", "化学", "", ""],
    ["英语", "化学", "数学", "语文", "物理", "", ""],
    ["物理", "英语", "语文", "化学", "英语", "", ""],
    ["体育", "政治", "历史", "地理", "生物", "", ""],
    ["音乐", "生物", "地理", "政治", "历史", "", ""],
    ["", "", "", "", "", "", ""],
    ["班会", "", "", "", "", "", ""],
]


class SchedulePanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._loading = True
        self._build_ui()
        self._load()
        self._loading = False

    def _build_ui(self) -> None:
        c = self._colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("我的课程表")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()

        for text, handler in (
            ("＋ 增加节次", self._add_period),
            ("－ 删除末节", self._remove_period),
            ("清空本周", self._clear_all),
            ("示例课表", self._load_sample),
        ):
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"background: {c.get('bg_tertiary', '#f3f4f6')};"
                f"color: {c.get('fg_primary', '#111827')};"
                f"border: 1px solid {c.get('border', '#e5e7eb')};"
                "border-radius: 8px; padding: 6px 12px;"
            )
            btn.clicked.connect(handler)
            head.addWidget(btn)
        layout.addLayout(head)

        hint = QLabel("双击单元格即可输入课程名，修改后自动保存（按当前账号隔离）。")
        hint.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        layout.addWidget(hint)

        card = QFrame()
        card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)

        self.table = QTableWidget(_DEFAULT_PERIODS, len(_DAYS))
        self.table.setHorizontalHeaderLabels(_DAYS)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet(
            f"QTableWidget {{ background: transparent;"
            f" color: {c.get('fg_primary', '#111827')};"
            " gridline-style: none; }"
            f"QHeaderView::section {{ background: {c.get('bg_tertiary', '#f3f4f6')};"
            f" color: {c.get('fg_primary', '#111827')}; border: none;"
            " font-weight: bold; padding: 6px; }"
            "QTableWidget::item { border-radius: 6px; padding: 4px; }"
            f"QTableWidget::item:selected {{ background: {c.get('accent_light', 'rgba(79,93,245,0.12)')}; }}"
        )
        self.table.itemChanged.connect(self._on_item_changed)
        card_layout.addWidget(self.table)
        layout.addWidget(card, 1)

    def _ensure_item(self, row: int, col: int) -> QTableWidgetItem:
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem("")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, item)
        return item

    def _apply_rows(self, rows: list[list[str]]) -> None:
        self._loading = True
        count = max(1, len(rows))
        self.table.setRowCount(count)
        for r in range(count):
            self.table.setVerticalHeaderItem(r, QTableWidgetItem(f"第 {r + 1} 节"))
            for col_i in range(len(_DAYS)):
                text = ""
                if col_i < len(rows[r]):
                    text = str(rows[r][col_i] or "")
                self._ensure_item(r, col_i).setText(text)
        self._loading = False

    def _read_rows(self) -> list[list[str]]:
        rows = []
        for r in range(self.table.rowCount()):
            rows.append([self._ensure_item(r, c).text().strip() for c in range(len(_DAYS))])
        return rows

    def _load(self) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=None)
        if isinstance(data, list) and data and all(isinstance(r, list) for r in data):
            self._apply_rows([[str(x or "") for x in r] for r in data])
        else:
            self._apply_rows([[""] * len(_DAYS) for _ in range(_DEFAULT_PERIODS)])

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        if self._loading:
            return
        self._ctx.save_state(_STATE_NAME, self._read_rows())

    def _add_period(self) -> None:
        rows = self._read_rows()
        rows.append([""] * len(_DAYS))
        self._apply_rows(rows)
        self._ctx.save_state(_STATE_NAME, rows)

    def _remove_period(self) -> None:
        rows = self._read_rows()
        if len(rows) <= 1:
            return
        rows.pop()
        self._apply_rows(rows)
        self._ctx.save_state(_STATE_NAME, rows)

    def _clear_all(self) -> None:
        if not self._ctx.ask_confirm(self, "确认清空", "确定清空整张课程表吗？此操作不可撤销。"):
            return
        rows = [[""] * len(_DAYS) for _ in range(self.table.rowCount())]
        self._apply_rows(rows)
        self._ctx.save_state(_STATE_NAME, rows)

    def _load_sample(self) -> None:
        if not self._ctx.ask_confirm(self, "载入示例", "将用示例课表覆盖当前内容，是否继续？"):
            return
        rows = [list(r) for r in _SAMPLE]
        self._apply_rows(rows)
        self._ctx.save_state(_STATE_NAME, rows)


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "课程表",
        lambda c: SchedulePanel(c),
        icon="📅",
        subtitle="周一到周日，每节课安排一目了然",
    )
