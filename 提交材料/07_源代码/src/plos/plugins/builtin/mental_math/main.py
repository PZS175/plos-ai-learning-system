"""内置插件：速算口算练习。

针对中学计算能力训练：

- 题型可组合：加减、乘除、乘方、混合运算（含运算优先级）；
- 三档难度（数值范围不同），题量可选；
- 实时反馈对错、连击与用时，字大醒目，回车即可提交；
- 结束后给出正确率与错题清单，便于复盘；
- 历史练习成绩按账号保存，可看到最近几次的正确率变化。
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import List, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

_STATE_NAME = "history"

_LEVELS = [
    ("简单", 20),
    ("中等", 99),
    ("困难", 999),
]

_TYPES = [("加减", "add"), ("乘除", "mul"), ("乘方", "pow"), ("混合", "mix")]


def _generate(types: List[str], bound: int) -> Tuple[str, int]:
    """按题型生成题目，返回 (题面, 正确答案)。"""
    kind = random.choice(types)

    if kind == "add":
        a = random.randint(2, bound)
        b = random.randint(2, bound)
        if random.random() < 0.5:
            return f"{a} + {b}", a + b
        high, low = max(a, b), min(a, b)
        return f"{high} − {low}", high - low

    if kind == "mul":
        if random.random() < 0.5:
            a = random.randint(2, max(2, min(bound, 30)))
            b = random.randint(2, max(2, min(bound, 30)))
            return f"{a} × {b}", a * b
        b = random.randint(2, max(2, min(bound, 30)))
        product = b * random.randint(2, max(2, min(bound, 30)))
        return f"{product} ÷ {b}", product // b

    if kind == "pow":
        if random.random() < 0.5:
            n = random.randint(2, max(2, min(bound, 30)))
            return f"{n}²", n * n
        n = random.randint(2, max(2, min(bound, 15)))
        return f"{n}³", n * n * n

    # 混合运算：a + b × c 或 (a + b) × c，注意运算优先级
    if random.random() < 0.5:
        a = random.randint(2, max(2, min(bound, 40)))
        b = random.randint(2, max(2, min(bound, 12)))
        c = random.randint(2, max(2, min(bound, 12)))
        return f"{a} + {b} × {c}", a + b * c
    a = random.randint(2, max(2, min(bound, 30)))
    b = random.randint(2, max(2, min(bound, 30)))
    c = random.randint(2, max(2, min(bound, 12)))
    return f"({a} + {b}) × {c}", (a + b) * c


class MentalMathPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._problems: List[Tuple[str, int]] = []
        self._index = 0
        self._correct = 0
        self._answered = 0
        self._streak = 0
        self._best_streak = 0
        self._wrong: List[Tuple[str, str, int]] = []
        self._seconds = 0
        self._running = False
        self._checkboxes: dict[str, QCheckBox] = {}

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_second)

        self._load_history()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("速算口算练习")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        # 设置
        setting_card = QFrame()
        setting_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        setting_layout = QVBoxLayout(setting_card)
        setting_layout.setContentsMargins(16, 14, 16, 14)
        setting_layout.setSpacing(10)

        type_row = QHBoxLayout()
        type_row.addWidget(self._caption("题型"))
        for label, key in _TYPES:
            box = QCheckBox(label)
            box.setChecked(key in ("add", "mul"))
            box.setStyleSheet(f"color: {c.get('fg_primary', '#111827')};")
            type_row.addWidget(box)
            self._checkboxes[key] = box
        type_row.addSpacing(16)
        type_row.addWidget(self._caption("难度"))
        self.level_box = QComboBox()
        self.level_box.addItems([name for name, _bound in _LEVELS])
        self.level_box.setCurrentIndex(1)
        self.level_box.setStyleSheet(self._input_style())
        type_row.addWidget(self.level_box)
        type_row.addSpacing(12)
        type_row.addWidget(self._caption("题量"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(5, 50)
        self.count_spin.setValue(10)
        self.count_spin.setStyleSheet(self._input_style())
        self.count_spin.setFixedWidth(70)
        type_row.addWidget(self.count_spin)
        type_row.addStretch()
        self.start_btn = QPushButton("开始练习")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 20px; font-weight: bold;"
        )
        self.start_btn.clicked.connect(self._start)
        type_row.addWidget(self.start_btn)
        setting_layout.addLayout(type_row)
        root.addWidget(setting_card)

        # 答题区
        quiz_card = QFrame()
        quiz_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        quiz_layout = QVBoxLayout(quiz_card)
        quiz_layout.setContentsMargins(20, 18, 20, 18)
        quiz_layout.setSpacing(12)

        self.progress_label = QLabel("点击「开始练习」出题")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        quiz_layout.addWidget(self.progress_label)

        self.question_label = QLabel("准备就绪")
        self.question_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.question_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')};"
            "font-size: 40px; font-weight: bold;"
        )
        quiz_layout.addWidget(self.question_label)

        answer_row = QHBoxLayout()
        answer_row.addStretch()
        self.answer_edit = QLineEdit()
        self.answer_edit.setPlaceholderText("输入答案后回车")
        self.answer_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.answer_edit.setFixedWidth(220)
        self.answer_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 10px; padding: 10px; font-size: 20px;"
        )
        self.answer_edit.returnPressed.connect(self._submit)
        self.answer_edit.setEnabled(False)
        answer_row.addWidget(self.answer_edit)

        self.submit_btn = QPushButton("提交")
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.setEnabled(False)
        self.submit_btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 10px; padding: 10px 20px;"
        )
        self.submit_btn.clicked.connect(self._submit)
        answer_row.addWidget(self.submit_btn)
        answer_row.addStretch()
        quiz_layout.addLayout(answer_row)

        self.feedback_label = QLabel("")
        self.feedback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feedback_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        quiz_layout.addWidget(self.feedback_label)
        root.addWidget(quiz_card)

        # 统计
        stats_row = QHBoxLayout()
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        stats_row.addWidget(self.stats_label, 1)
        root.addLayout(stats_row)

        # 错题
        wrong_title = QLabel("错题复盘")
        wrong_title.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 14px; font-weight: bold;"
        )
        root.addWidget(wrong_title)
        self.wrong_list = QListWidget()
        self.wrong_list.setStyleSheet(
            f"QListWidget {{ background: {c.get('card_bg', '#fff')};"
            f" border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 10px;"
            f" color: {c.get('fg_primary', '#111827')}; padding: 4px; }}"
        )
        root.addWidget(self.wrong_list, 1)

        self.history_label = QLabel("")
        self.history_label.setWordWrap(True)
        self.history_label.setStyleSheet(
            f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 11px;"
        )
        root.addWidget(self.history_label)

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        return label

    def _input_style(self) -> str:
        c = self._colors
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 8px;"
        )

    # ------------------------------------------------------------------
    # 练习流程
    # ------------------------------------------------------------------
    def _start(self) -> None:
        types = [key for key, box in self._checkboxes.items() if box.isChecked()]
        if not types:
            self._ctx.show_warning(self, "提示", "请至少选择一种题型")
            return
        bound = _LEVELS[self.level_box.currentIndex()][1]
        total = int(self.count_spin.value())
        self._problems = [_generate(types, bound) for _ in range(total)]
        self._index = 0
        self._correct = 0
        self._answered = 0
        self._streak = 0
        self._best_streak = 0
        self._wrong = []
        self._seconds = 0
        self._running = True
        self.wrong_list.clear()
        self._timer.start()
        self.answer_edit.setEnabled(True)
        self.submit_btn.setEnabled(True)
        self.start_btn.setText("重新开始")
        self._show_problem()

    def _show_problem(self) -> None:
        if self._index >= len(self._problems):
            self._finish()
            return
        text, _answer = self._problems[self._index]
        self.progress_label.setText(f"第 {self._index + 1} / {len(self._problems)} 题")
        self.question_label.setText(f"{text} = ?")
        self.answer_edit.clear()
        self.answer_edit.setFocus()
        self._refresh_stats()

    def _submit(self) -> None:
        if not self._running or self._index >= len(self._problems):
            return
        raw = self.answer_edit.text().strip()
        if not raw:
            return
        try:
            value = float(raw)
        except ValueError:
            self._feedback("请输入数字答案", "error")
            return

        text, answer = self._problems[self._index]
        self._answered += 1
        if abs(value - answer) < 1e-6:
            self._correct += 1
            self._streak += 1
            self._best_streak = max(self._best_streak, self._streak)
            suffix = f"　连击 ×{self._streak}" if self._streak >= 3 else ""
            self._feedback(f"✓ 正确{suffix}", "success")
        else:
            self._streak = 0
            self._wrong.append((text, raw, answer))
            self.wrong_list.addItem(f"{text} = {answer}　（你答：{raw}）")
            self._feedback(f"✗ 正确答案是 {answer}", "error")

        self._index += 1
        self._show_problem()

    def _finish(self) -> None:
        self._running = False
        self._timer.stop()
        self.answer_edit.setEnabled(False)
        self.submit_btn.setEnabled(False)
        self.question_label.setText("练习完成")
        rate = self._correct / self._answered * 100 if self._answered else 0
        self.progress_label.setText(
            f"共 {self._answered} 题 · 正确 {self._correct} 题 · "
            f"正确率 {rate:.0f}% · 用时 {self._seconds} 秒"
        )
        self._feedback(
            "太棒了！继续保持" if rate >= 90 else "继续加油，看看下面的错题",
            "success" if rate >= 60 else "error",
        )
        self._save_history(rate, self._answered, self._seconds)

    def _feedback(self, text: str, level: str) -> None:
        color = {
            "success": self._colors.get("success", "#10b981"),
            "error": self._colors.get("error", "#ef4444"),
        }.get(level, self._colors.get("fg_secondary", "#6b7280"))
        self.feedback_label.setText(text)
        self.feedback_label.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: bold;")

    def _on_second(self) -> None:
        if self._running:
            self._seconds += 1
            self._refresh_stats()

    def _refresh_stats(self) -> None:
        rate = self._correct / self._answered * 100 if self._answered else 0
        self.stats_label.setText(
            f"已答 {self._answered} · 正确 {self._correct} · 正确率 {rate:.0f}% · "
            f"连击 {self._streak}（最高 {self._best_streak}）· 用时 {self._seconds}s"
        )

    # ------------------------------------------------------------------
    # 历史成绩
    # ------------------------------------------------------------------
    def _load_history(self) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=[])
        if isinstance(data, list) and data:
            parts = []
            for item in data[-5:]:
                if isinstance(item, dict):
                    parts.append(
                        f"{item.get('time', '')[-5:]}　{item.get('rate', 0)}%"
                        f"（{item.get('correct', 0)}/{item.get('count', 0)}）"
                    )
            if parts:
                self.history_label.setText("最近练习：" + "　|　".join(parts))

    def _save_history(self, rate: float, count: int, seconds: int) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=[])
        if not isinstance(data, list):
            data = []
        data.append(
            {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "rate": round(rate),
                "correct": self._correct,
                "count": count,
                "seconds": seconds,
            }
        )
        self._ctx.save_state(_STATE_NAME, data[-20:])
        self._load_history()


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "速算练习",
        lambda c: MentalMathPanel(c),
        icon="🧠",
        subtitle="口算速度与准确率训练，错题自动汇总",
    )
