"""插件库插件：打字速度测验。

中英文短文打字训练：

- 6 篇内置范文（中英文各 3 篇，难度递增），可随时换一篇重打；
- 首次输入自动开始计时，实时显示用时、进度、正确率与速度
  （中文按「字 / 分」，英文按「WPM = 字符数 ÷ 5 ÷ 分钟」的标准算法）；
- 输入与原文逐字符比对，完成后给出成绩并保存最近 10 次记录（按账号隔离）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_STATE_NAME = "scores"

# (标题, 语言, 正文)
_TEXTS: List[Tuple[str, str, str]] = [
    (
        "中文 · 入门",
        "zh",
        "一年之计在于春，一日之计在于晨。少年易老学难成，一寸光阴不可轻。"
        "读书须有三到：谓心到、眼到、口到。",
    ),
    (
        "中文 · 进阶",
        "zh",
        "学习从来不是一件轻松的事，但它一定是一件值得的事。"
        "当你在深夜里为一个问题反复推敲，当你在草稿纸上写满密密麻麻的算式，"
        "那些看似枯燥的重复，正在悄悄把你带到更高的地方。",
    ),
    (
        "中文 · 挑战",
        "zh",
        "予观夫巴陵胜状，在洞庭一湖。衔远山，吞长江，浩浩汤汤，横无际涯；"
        "朝晖夕阴，气象万千。此则岳阳楼之大观也，前人之述备矣。",
    ),
    (
        "English · Easy",
        "en",
        "The quick brown fox jumps over the lazy dog. "
        "Practice makes perfect, so keep your fingers moving.",
    ),
    (
        "English · Medium",
        "en",
        "Learning is a journey rather than a race. "
        "Every mistake you correct today becomes a step you can stand on tomorrow.",
    ),
    (
        "English · Hard",
        "en",
        "It is not the strongest of the species that survives, nor the most intelligent, "
        "but the one most responsive to change. Curiosity and persistence, "
        "more than talent, decide how far a learner can finally go.",
    ),
]


def compare_text(source: str, typed: str) -> Tuple[int, int]:
    """返回 (正确字符数, 已输入字符数)。"""
    correct = 0
    for index, char in enumerate(typed):
        if index < len(source) and char == source[index]:
            correct += 1
    return correct, len(typed)


class TypingPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._source = _TEXTS[0][2]
        self._language = _TEXTS[0][1]
        self._seconds = 0
        self._running = False
        self._finished = False
        self._correct = 0
        self._typed = 0
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_second)

        self._load_scores()
        self._update_stats()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("打字速度测验")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()

        self.text_box = QComboBox()
        self.text_box.addItems([item[0] for item in _TEXTS])
        self.text_box.setStyleSheet(self._input_style())
        self.text_box.setMinimumWidth(150)
        self.text_box.currentIndexChanged.connect(self._on_text_changed)
        head.addWidget(self.text_box)

        self.restart_btn = QPushButton("↺ 重新开始")
        self.restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.restart_btn.clicked.connect(self._restart)
        head.addWidget(self.restart_btn)
        root.addLayout(head)

        # 实时统计
        stats_card = QFrame()
        stats_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        stats_row = QHBoxLayout(stats_card)
        stats_row.setContentsMargins(18, 12, 18, 12)
        stats_row.setSpacing(28)
        self._stat_labels: Dict[str, QLabel] = {}
        for key, caption in (("time", "用时"), ("progress", "进度"),
                             ("accuracy", "正确率"), ("speed", "速度")):
            box = QVBoxLayout()
            cap = QLabel(caption)
            cap.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
            value = QLabel("—")
            value.setStyleSheet(
                f"color: {c.get('accent', '#4f5df5')}; font-size: 20px; font-weight: bold;"
            )
            box.addWidget(cap)
            box.addWidget(value)
            stats_row.addLayout(box)
            self._stat_labels[key] = value
        stats_row.addStretch()
        root.addWidget(stats_card)

        # 原文
        self.source_box = QTextBrowser()
        self.source_box.setOpenExternalLinks(False)
        self.source_box.setFrameShape(QFrame.Shape.NoFrame)
        self.source_box.setMinimumHeight(110)
        self.source_box.setMaximumHeight(160)
        self.source_box.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            "border-radius: 10px; font-size: 16px; padding: 12px;"
        )
        root.addWidget(self.source_box)

        # 输入区
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("在这里照着上面的文字打字，开始输入即自动计时…")
        self.input_edit.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 10px; padding: 12px; font-size: 16px;"
        )
        self.input_edit.textChanged.connect(self._on_input)
        root.addWidget(self.input_edit, 1)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("font-size: 14px;")
        root.addWidget(self.result_label)

        history_title = QLabel("最近成绩")
        history_title.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 14px; font-weight: bold;"
        )
        root.addWidget(history_title)
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(110)
        self.history_list.setStyleSheet(
            f"QListWidget {{ background: {c.get('card_bg', '#fff')};"
            f" border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 10px;"
            f" color: {c.get('fg_primary', '#111827')}; padding: 4px; }}"
        )
        root.addWidget(self.history_list)

        self._render_source()

    def _input_style(self) -> str:
        c = self._colors
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 8px;"
        )

    def _render_source(self) -> None:
        self.source_box.setText(
            f"<div style='line-height:170%'>{self._source}</div>"
        )

    # ------------------------------------------------------------------
    # 测验流程
    # ------------------------------------------------------------------
    def _on_text_changed(self, index: int) -> None:
        if 0 <= index < len(_TEXTS):
            _title, self._language, self._source = _TEXTS[index]
        self._restart()

    def _restart(self) -> None:
        self._timer.stop()
        self._seconds = 0
        self._running = False
        self._finished = False
        self._correct = 0
        self._typed = 0
        self.input_edit.blockSignals(True)
        self.input_edit.clear()
        self.input_edit.blockSignals(False)
        self._render_source()
        self.result_label.setText("")
        self.result_label.setStyleSheet("font-size: 14px;")
        self._update_stats()
        self.input_edit.setFocus()

    def _on_input(self) -> None:
        if self._finished:
            return
        typed = self.input_edit.toPlainText()
        if not self._running and typed:
            self._running = True
            self._timer.start()
        self._correct, self._typed = compare_text(self._source, typed)
        self._update_stats()
        if len(typed) >= len(self._source):
            self._finish()

    def _on_second(self) -> None:
        if self._running and not self._finished:
            self._seconds += 1
            self._update_stats()

    def _speed(self) -> float:
        if self._seconds <= 0:
            return 0.0
        minutes = self._seconds / 60.0
        if self._language == "en":
            return (self._correct / 5.0) / minutes  # WPM
        return self._correct / minutes  # 字 / 分

    def _speed_unit(self) -> str:
        return "WPM" if self._language == "en" else "字/分"

    def _update_stats(self) -> None:
        accuracy = (self._correct / self._typed * 100) if self._typed else 100.0
        speed = self._speed()
        self._stat_labels["time"].setText(f"{self._seconds} 秒")
        self._stat_labels["progress"].setText(f"{self._typed}/{len(self._source)}")
        self._stat_labels["accuracy"].setText(f"{accuracy:.0f}%")
        self._stat_labels["speed"].setText(f"{speed:.0f} {self._speed_unit()}" if speed else "—")

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._running = False
        self._timer.stop()
        accuracy = (self._correct / self._typed * 100) if self._typed else 0.0
        speed = self._speed()
        c = self._colors
        color = c.get("success", "#10b981") if accuracy >= 90 else c.get("warning", "#f59e0b")
        self.result_label.setText(
            f"完成！用时 {self._seconds} 秒 · 正确率 {accuracy:.0f}% · "
            f"速度 {speed:.0f} {self._speed_unit()}"
            + ("" if accuracy >= 90 else "　（错字较多，慢一点更准哦）")
        )
        self.result_label.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: bold;")
        self._save_score(accuracy, speed)

    # ------------------------------------------------------------------
    # 成绩
    # ------------------------------------------------------------------
    def _load_scores(self) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=[])
        if not isinstance(data, list):
            data = []
        self._scores = [item for item in data if isinstance(item, dict)]
        self._render_scores()

    def _save_score(self, accuracy: float, speed: float) -> None:
        record = {
            "time": datetime.now().strftime("%m-%d %H:%M"),
            "title": self.text_box.currentText(),
            "accuracy": round(accuracy),
            "speed": round(speed),
            "seconds": self._seconds,
        }
        self._scores = (getattr(self, "_scores", []) + [record])[-10:]
        self._ctx.save_state(_STATE_NAME, self._scores)
        self._render_scores()

    def _render_scores(self) -> None:
        self.history_list.clear()
        for record in reversed(getattr(self, "_scores", [])):
            self.history_list.addItem(
                f"{record.get('time', '')}　{record.get('title', '')}　"
                f"正确率 {record.get('accuracy', 0)}%　"
                f"速度 {record.get('speed', 0)}　用时 {record.get('seconds', 0)}s"
            )


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "打字测验",
        lambda c: TypingPanel(c),
        icon="⌨",
        subtitle="中英文打字速度与准确率训练",
    )
