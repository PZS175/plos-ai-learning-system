"""内置插件：科学计算器。

安全设计：表达式不使用 eval，求值统一走共享的
``plos.plugins.expr.ExprEvaluator``（ast 白名单），
只允许数字、π/e/Ans 常量、白名单数学函数与四则 / 幂 / 取模运算符。
支持 sin/cos/tan 及反函数（可切角度 / 弧度）、√、log、ln、^、括号、Ans。
"""

from __future__ import annotations

import math

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plos.plugins.expr import ExprEvaluator


class CalculatorPanel(QWidget):
    # (按钮文字, 插入文本或动作, 跨列数)
    _KEYS = [
        [("sin", "sin(", 1), ("cos", "cos(", 1), ("tan", "tan(", 1), ("⌫", "__back__", 1)],
        [("asin", "asin(", 1), ("acos", "acos(", 1), ("atan", "atan(", 1), ("C", "__clear__", 1)],
        [("√", "sqrt(", 1), ("log", "log(", 1), ("ln", "ln(", 1), ("^", "^", 1)],
        [("π", "π", 1), ("e", "e", 1), ("Ans", "Ans", 1), ("%", "%", 1)],
        [("(", "(", 1), (")", ")", 1), ("7", "7", 1), ("8", "8", 1)],
        [("9", "9", 1), ("4", "4", 1), ("5", "5", 1), ("6", "6", 1)],
        [("1", "1", 1), ("2", "2", 1), ("3", "3", 1), ("÷", "÷", 1)],
        [("×", "×", 1), ("−", "−", 1), ("0", "0", 1), (".", ".", 1)],
        [("+", "+", 2), ("=", "__eq__", 2)],
    ]

    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._ans = 0.0
        self._degrees = True
        self._build_ui()

    def _build_ui(self) -> None:
        c = self._colors
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addStretch()

        card = QFrame()
        card.setMaximumWidth(420)
        card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 16px;"
        )
        wrap = QHBoxLayout()
        wrap.addStretch()
        wrap.addWidget(card)
        wrap.addStretch()
        outer.addLayout(wrap)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.mode_btn = QPushButton("角度 DEG")
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.1)')};"
            f"color: {c.get('accent', '#4f5df5')}; border: none; border-radius: 8px; padding: 6px 12px;"
        )
        self.mode_btn.clicked.connect(self._toggle_mode)
        head.addWidget(self.mode_btn)
        head.addStretch()
        ans_label = QLabel("Ans = 0")
        ans_label.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        head.addWidget(ans_label)
        self._ans_label = ans_label
        layout.addLayout(head)

        self.preview = QLabel("")
        self.preview.setMinimumHeight(20)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.preview.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;")
        layout.addWidget(self.preview)

        self.display = QLineEdit()
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setPlaceholderText("0")
        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        self.display.setFont(font)
        self.display.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 10px; padding: 10px;"
        )
        self.display.returnPressed.connect(self._calculate)
        layout.addWidget(self.display)

        grid = QGridLayout()
        grid.setSpacing(6)
        for r, row in enumerate(self._KEYS):
            col = 0
            for text, payload, span in row:
                btn = QPushButton(text)
                btn.setMinimumHeight(42)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(self._button_style(text))
                grid.addWidget(btn, r, col, 1, span)
                btn.clicked.connect(lambda _=False, p=payload: self._on_key(p))
                col += span
        layout.addLayout(grid)
        outer.addStretch()

    def _button_style(self, text: str) -> str:
        c = self._colors
        if text == "=":
            return (
                f"background: {c.get('accent', '#4f5df5')}; color: white;"
                "border: none; border-radius: 10px; font-size: 16px; font-weight: bold;"
            )
        if text in ("C", "⌫"):
            return (
                f"background: {c.get('bg_tertiary', '#f3f4f6')};"
                f"color: {c.get('error', '#ef4444')};"
                f"border: 1px solid {c.get('border', '#e5e7eb')};"
                "border-radius: 10px; font-size: 15px;"
            )
        if text in ("+", "−", "×", "÷", "^", "%"):
            return (
                f"background: {c.get('accent_light', 'rgba(79,93,245,0.1)')};"
                f"color: {c.get('accent', '#4f5df5')};"
                "border: none; border-radius: 10px; font-size: 17px; font-weight: bold;"
            )
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 10px; font-size: 14px;"
        )

    def _toggle_mode(self) -> None:
        self._degrees = not self._degrees
        self.mode_btn.setText("角度 DEG" if self._degrees else "弧度 RAD")

    def _on_key(self, payload: str) -> None:
        if payload == "__back__":
            self.display.backspace()
        elif payload == "__clear__":
            self.display.clear()
            self.preview.setText("")
        elif payload == "__eq__":
            self._calculate()
        else:
            self.display.insert(payload)
        self.display.setFocus()

    def _calculate(self) -> None:
        expression = self.display.text().strip()
        if not expression:
            return
        try:
            result = ExprEvaluator(ans=self._ans, degrees=self._degrees).eval(expression)
            if not math.isfinite(result):
                raise ValueError("结果不是有效数字（如除以 0）")
        except Exception as exc:
            self.preview.setText("无法计算")
            self._ctx.show_warning(self, "计算错误", str(exc))
            return
        self._ans = result
        shown = f"{result:.12g}"
        self.preview.setText(f"{expression} =")
        self.display.setText(shown)
        self._ans_label.setText(f"Ans = {shown}")


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "科学计算器",
        lambda c: CalculatorPanel(c),
        icon="🧮",
        subtitle="函数与表达式安全求值，支持角度 / 弧度",
    )
