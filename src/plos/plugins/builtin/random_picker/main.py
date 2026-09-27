"""内置插件：随机抽签与分组。

适合课堂提问、小组学习与背诵抽查：

- 名单批量粘贴（换行 / 逗号 / 空格分隔均可），自动去重并保持顺序；
- 随机抽取：可一次抽 1～N 人，抽取过程有滚动动画，结果大字展示；
- 随机分组：指定组数，随机且尽量均衡地分组，结果一键复制；
- 名单按账号保存，下次打开继续用。
"""

from __future__ import annotations

import random
import re
from typing import List

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

_STATE_NAME = "roster"
_SPLIT_RE = re.compile(r"[\s,，、;；]+")
_ROLL_INTERVAL_MS = 60
_ROLL_TICKS = 14


def parse_roster(text: str) -> List[str]:
    """把任意分隔的名单解析为去重后的有序列表。"""
    names: List[str] = []
    seen = set()
    for raw in _SPLIT_RE.split(text or ""):
        name = raw.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def split_groups(names: List[str], group_count: int) -> List[List[str]]:
    """随机且尽量均衡地把名单分成 group_count 组。"""
    if not names:
        return []
    group_count = max(1, min(int(group_count), len(names)))
    shuffled = list(names)
    random.shuffle(shuffled)
    groups: List[List[str]] = [[] for _ in range(group_count)]
    for index, name in enumerate(shuffled):
        groups[index % group_count].append(name)
    return groups


class RandomPickerPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._rolling = False
        self._roll_ticks = 0
        self._pending_result: List[str] = []
        self._build_ui()

        self._roll_timer = QTimer(self)
        self._roll_timer.setInterval(_ROLL_INTERVAL_MS)
        self._roll_timer.timeout.connect(self._on_roll_tick)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._persist_roster)

        self._load_roster()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("随机抽签与分组")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel(
            "粘贴名单（换行、逗号、空格分隔均可，自动去重），然后抽取或分组。"
            "名单会按账号保存。"
        )
        hint.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(14)

        # 名单
        roster_card = QFrame()
        roster_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        roster_layout = QVBoxLayout(roster_card)
        roster_layout.setContentsMargins(14, 12, 14, 12)
        roster_layout.setSpacing(8)

        roster_head = QHBoxLayout()
        roster_title = QLabel("名单")
        roster_title.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 14px; font-weight: bold;"
        )
        roster_head.addWidget(roster_title)
        roster_head.addStretch()
        self.count_label = QLabel("0 人")
        self.count_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        roster_head.addWidget(self.count_label)
        roster_layout.addLayout(roster_head)

        self.roster_edit = QPlainTextEdit()
        self.roster_edit.setPlaceholderText("张三\n李四\n王五\n…（也可一行用逗号分隔）")
        self.roster_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px; font-size: 14px;"
        )
        self.roster_edit.textChanged.connect(self._on_roster_changed)
        roster_layout.addWidget(self.roster_edit, 1)

        save_row = QHBoxLayout()
        sample_btn = self._plain_button("填入示例", self._fill_sample)
        save_row.addWidget(sample_btn)
        clear_btn = self._plain_button("清空名单", self._clear_roster)
        save_row.addWidget(clear_btn)
        save_row.addStretch()
        roster_layout.addLayout(save_row)
        body.addWidget(roster_card, 1)

        # 结果区
        result_card = QFrame()
        result_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(16, 14, 16, 14)
        result_layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.addWidget(self._caption("抽取人数"))
        self.pick_spin = QSpinBox()
        self.pick_spin.setRange(1, 50)
        self.pick_spin.setValue(1)
        self.pick_spin.setStyleSheet(self._input_style())
        self.pick_spin.setFixedWidth(70)
        controls.addWidget(self.pick_spin)
        self.pick_btn = QPushButton("🎯 随机抽取")
        self.pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.pick_btn.clicked.connect(self._start_pick)
        controls.addWidget(self.pick_btn)
        controls.addStretch()
        result_layout.addLayout(controls)

        controls2 = QHBoxLayout()
        controls2.addWidget(self._caption("分成组数"))
        self.group_spin = QSpinBox()
        self.group_spin.setRange(2, 20)
        self.group_spin.setValue(4)
        self.group_spin.setStyleSheet(self._input_style())
        self.group_spin.setFixedWidth(70)
        controls2.addWidget(self.group_spin)
        self.group_btn = QPushButton("👥 随机分组")
        self.group_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.group_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.1)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.group_btn.clicked.connect(self._do_group)
        controls2.addWidget(self.group_btn)
        controls2.addStretch()
        result_layout.addLayout(controls2)

        self.result_label = QLabel("等待抽取")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setWordWrap(True)
        self.result_label.setMinimumHeight(80)
        self.result_label.setStyleSheet(
            f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 26px; font-weight: bold;"
        )
        result_layout.addWidget(self.result_label)

        detail_title = QLabel("分组 / 抽取结果")
        detail_title.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 13px; font-weight: bold;"
        )
        result_layout.addWidget(detail_title)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 14px;"
        )
        result_layout.addWidget(self.detail_label, 1)

        copy_row = QHBoxLayout()
        copy_row.addStretch()
        self.copy_btn = QPushButton("📋 复制结果")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 7px 14px;"
        )
        self.copy_btn.clicked.connect(self._copy_result)
        copy_row.addWidget(self.copy_btn)
        result_layout.addLayout(copy_row)
        body.addWidget(result_card, 2)

        root.addLayout(body, 1)

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        return label

    def _plain_button(self, text: str, handler) -> QPushButton:
        c = self._colors
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 12px;"
        )
        button.clicked.connect(handler)
        return button

    def _input_style(self) -> str:
        c = self._colors
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 8px;"
        )

    # ------------------------------------------------------------------
    # 名单
    # ------------------------------------------------------------------
    def _load_roster(self) -> None:
        names = self._ctx.load_state(_STATE_NAME, default=[])
        if not isinstance(names, list):
            names = []
        if names:
            self.roster_edit.setPlainText("\n".join(str(name) for name in names))
        self._on_roster_changed()

    def _save_roster(self, names: List[str]) -> None:
        self._ctx.save_state(_STATE_NAME, names)

    def _on_roster_changed(self) -> None:
        names = parse_roster(self.roster_edit.toPlainText())
        self.count_label.setText(f"{len(names)} 人")
        # 输入过程中防抖保存，避免每次按键都写文件
        self._save_timer.start()

    def _persist_roster(self) -> None:
        self._save_roster(self.names())

    def _fill_sample(self) -> None:
        self.roster_edit.setPlainText(
            "张三\n李四\n王五\n赵六\n钱七\n孙八\n周九\n吴十\n郑一\n王二"
        )

    def _clear_roster(self) -> None:
        self.roster_edit.clear()

    def names(self) -> List[str]:
        return parse_roster(self.roster_edit.toPlainText())

    # ------------------------------------------------------------------
    # 抽取
    # ------------------------------------------------------------------
    def _start_pick(self) -> None:
        c = self._colors
        names = self.names()
        if not names:
            self._ctx.show_warning(self, "提示", "请先填写名单")
            return
        count = min(int(self.pick_spin.value()), len(names))
        self._pending_result = random.sample(names, count)
        self._roll_ticks = _ROLL_TICKS + count
        self._rolling = True
        self.pick_btn.setEnabled(False)
        self.result_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 26px; font-weight: bold;"
        )
        self._roll_timer.start()

    def _on_roll_tick(self) -> None:
        names = self.names()
        if not names or self._roll_ticks <= 0:
            self._finish_pick()
            return
        self._roll_ticks -= 1
        self.result_label.setText(random.choice(names))

    def _finish_pick(self) -> None:
        self._roll_timer.stop()
        self._rolling = False
        self.pick_btn.setEnabled(True)
        result = self._pending_result
        if not result:
            return
        c = self._colors
        if len(result) == 1:
            self.result_label.setText(result[0])
        else:
            self.result_label.setText("　".join(result))
        self.result_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 26px; font-weight: bold;"
        )
        self.detail_label.setText("抽取结果：" + "、".join(result))

    # ------------------------------------------------------------------
    # 分组
    # ------------------------------------------------------------------
    def _do_group(self) -> None:
        c = self._colors
        names = self.names()
        if len(names) < 2:
            self._ctx.show_warning(self, "提示", "至少需要 2 人才能分组")
            return
        groups = split_groups(names, int(self.group_spin.value()))
        self.result_label.setText(f"已分成 {len(groups)} 组")
        self.result_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 24px; font-weight: bold;"
        )
        lines = [
            f"第 {index + 1} 组（{len(members)} 人）：{'、'.join(members)}"
            for index, members in enumerate(groups)
        ]
        self.detail_label.setText("\n".join(lines))

    # ------------------------------------------------------------------
    def _copy_result(self) -> None:
        text = self.detail_label.text().strip()
        if not text:
            text = self.result_label.text().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制结果"))


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "抽签分组",
        lambda c: RandomPickerPanel(c),
        icon="🎲",
        subtitle="随机点名与分组，名单自动保存",
    )
