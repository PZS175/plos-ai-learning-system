"""内置插件：化学方程式配平。

输入形如 ``Fe + O2 = Fe3O4``（也接受 ``→``、``->``），自动求出最小整数系数。

算法：
1. 解析每个化学式为「元素 → 原子个数」，支持括号与嵌套括号（Ca(OH)2、Fe2(SO4)3）；
2. 以元素为行、化合物为列构造矩阵（生成物一侧取负），即求 A·x = 0；
3. 用 ``fractions.Fraction`` 做有理数高斯消元求零空间，取自由变量为 1 得到分数解，
   再乘最小公倍数化为互质整数解。

全过程为整数运算，不存在浮点误差。
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Dict, List, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_ARROW_TOKENS = ("→", "->", "=>", "＝")


def parse_formula(text: str) -> Dict[str, int]:
    """把化学式解析为 {元素: 原子个数}，支持括号嵌套。"""
    text = text.strip()
    if not text:
        raise ValueError("存在空的化学式")
    return _parse_group(text)


def _parse_group(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    index = 0
    while index < len(text):
        char = text[index]
        if char == "(":
            depth = 1
            end = index + 1
            while end < len(text) and depth > 0:
                if text[end] == "(":
                    depth += 1
                elif text[end] == ")":
                    depth -= 1
                end += 1
            if depth != 0:
                raise ValueError(f"括号不匹配：{text}")
            inner = text[index + 1 : end - 1]
            cursor = end
            digits = ""
            while cursor < len(text) and text[cursor].isdigit():
                digits += text[cursor]
                cursor += 1
            multiplier = int(digits) if digits else 1
            for element, number in _parse_group(inner).items():
                counts[element] = counts.get(element, 0) + number * multiplier
            index = cursor
        elif char.isupper():
            end = index + 1
            while end < len(text) and text[end].islower():
                end += 1
            element = text[index:end]
            cursor = end
            digits = ""
            while cursor < len(text) and text[cursor].isdigit():
                digits += text[cursor]
                cursor += 1
            counts[element] = counts.get(element, 0) + (int(digits) if digits else 1)
            index = cursor
        elif char.isdigit():
            raise ValueError(f"化学式不应以数字开头（{text}），请只填写化学式本身")
        else:
            raise ValueError(f"无法识别的字符「{char}」（{text}）")
    return counts


def _split_equation(equation: str) -> Tuple[List[str], List[str]]:
    text = equation.strip()
    if not text:
        raise ValueError("请输入化学方程式")
    for token in _ARROW_TOKENS:
        text = text.replace(token, "=")
    if text.count("=") != 1:
        raise ValueError("方程式中需要且只能有一个等号（或 →）")
    left_text, right_text = text.split("=")

    def _split_side(side: str, side_name: str) -> List[str]:
        items = [item.strip() for item in side.replace("＋", "+").split("+")]
        items = [item for item in items if item]
        if not items:
            raise ValueError(f"{side_name}没有有效的化学式")
        return items

    return _split_side(left_text, "反应物"), _split_side(right_text, "生成物")


def _rref(matrix: List[List[Fraction]], columns: int) -> List[int]:
    """就地化为行最简形，返回主元所在列。"""
    rows = len(matrix)
    pivots: List[int] = []
    row = 0
    for column in range(columns):
        pivot = None
        for candidate in range(row, rows):
            if matrix[candidate][column] != 0:
                pivot = candidate
                break
        if pivot is None:
            continue
        matrix[row], matrix[pivot] = matrix[pivot], matrix[row]
        divisor = matrix[row][column]
        matrix[row] = [value / divisor for value in matrix[row]]
        for other in range(rows):
            if other != row and matrix[other][column] != 0:
                factor = matrix[other][column]
                matrix[other] = [
                    a - factor * b for a, b in zip(matrix[other], matrix[row])
                ]
        pivots.append(column)
        row += 1
        if row == rows:
            break
    return pivots


def balance_equation(equation: str) -> Tuple[List[int], List[int]]:
    """返回 (反应物系数, 生成物系数)，均为最小互质正整数。"""
    left_formulas, right_formulas = _split_equation(equation)
    left_counts = [parse_formula(item) for item in left_formulas]
    right_counts = [parse_formula(item) for item in right_formulas]

    elements = sorted({element for counts in left_counts + right_counts for element in counts})
    if not elements:
        raise ValueError("未解析出任何元素")

    total = len(left_formulas) + len(right_formulas)
    matrix: List[List[Fraction]] = []
    for element in elements:
        row = [Fraction(0)] * total
        for index, counts in enumerate(left_counts):
            row[index] = Fraction(counts.get(element, 0))
        for index, counts in enumerate(right_counts):
            row[len(left_formulas) + index] = Fraction(-counts.get(element, 0))
        matrix.append(row)

    pivots = _rref(matrix, total)
    free_columns = [column for column in range(total) if column not in pivots]
    if not free_columns:
        raise ValueError("该方程式无法配平：请检查化学式是否写错或缺少物质")

    solution = [Fraction(0)] * total
    solution[free_columns[-1]] = Fraction(1)
    for row_index, column in enumerate(pivots):
        total_value = Fraction(0)
        for free in free_columns:
            total_value += matrix[row_index][free] * solution[free]
        solution[column] = -total_value

    if any(value == 0 for value in solution) or any(value < 0 for value in solution):
        raise ValueError("该方程式无法用正整数配平：请检查化学式是否正确")

    # 化为最小互质整数
    denominator = 1
    for value in solution:
        denominator = denominator * value.denominator // math.gcd(denominator, value.denominator)
    integers = [int(value * denominator) for value in solution]
    divisor = 0
    for value in integers:
        divisor = math.gcd(divisor, value)
    if divisor > 1:
        integers = [value // divisor for value in integers]

    return integers[: len(left_formulas)], integers[len(left_formulas) :]


def format_equation(
    left_formulas: List[str], right_formulas: List[str], left: List[int], right: List[int]
) -> str:
    def _side(formulas: List[str], coefficients: List[int]) -> str:
        parts = []
        for formula, coefficient in zip(formulas, coefficients):
            parts.append(formula if coefficient == 1 else f"{coefficient}{formula}")
        return " + ".join(parts)

    return f"{_side(left_formulas, left)} = {_side(right_formulas, right)}"


class EquationBalancerPanel(QWidget):
    _EXAMPLES = [
        ("铁在氧气中燃烧", "Fe + O2 = Fe3O4"),
        ("电解水", "H2O = H2 + O2"),
        ("实验室制氧气", "KClO3 = KCl + O2"),
        ("甲烷燃烧", "CH4 + O2 = CO2 + H2O"),
        ("氢氧化钙与硫酸反应", "Ca(OH)2 + H2SO4 = CaSO4 + H2O"),
        ("铁与硫酸铜溶液", "Fe + CuSO4 = FeSO4 + Cu"),
        ("硫酸铁与氢氧化钠", "Fe2(SO4)3 + NaOH = Fe(OH)3 + Na2SO4"),
        ("高锰酸钾分解", "KMnO4 = K2MnO4 + MnO2 + O2"),
    ]

    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._build_ui()

    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("化学方程式配平")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel(
            "输入反应物与生成物，用 + 连接、= （或 →）分隔，例如：\n"
            "Fe + O2 = Fe3O4　　Ca(OH)2 + H2SO4 = CaSO4 + H2O\n"
            "只需填写化学式本身，不要写系数；支持括号与嵌套括号。"
        )
        hint.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        input_card = QFrame()
        input_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 14, 16, 14)
        input_layout.setSpacing(10)

        row = QHBoxLayout()
        self.equation_edit = QLineEdit()
        self.equation_edit.setPlaceholderText("例如：Fe + O2 = Fe3O4")
        self.equation_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 10px; font-size: 15px;"
        )
        self.equation_edit.returnPressed.connect(self._balance)
        self.equation_edit.textChanged.connect(self._on_text_changed)
        row.addWidget(self.equation_edit, 1)

        solve_btn = QPushButton("配平")
        solve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        solve_btn.setMinimumWidth(96)
        solve_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 10px 18px; font-weight: bold;"
        )
        solve_btn.clicked.connect(self._balance)
        row.addWidget(solve_btn)
        input_layout.addLayout(row)

        self.result_label = QLabel("等待输入…")
        self.result_label.setWordWrap(True)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setMinimumHeight(46)
        self.result_label.setStyleSheet(
            f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 15px;"
        )
        input_layout.addWidget(self.result_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        input_layout.addWidget(self.detail_label)
        root.addWidget(input_card)

        # 示例
        example_card = QFrame()
        example_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        example_layout = QVBoxLayout(example_card)
        example_layout.setContentsMargins(16, 14, 16, 14)
        example_layout.setSpacing(8)

        example_title = QLabel("常见方程式（点击载入）")
        example_title.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 14px; font-weight: bold;"
        )
        example_layout.addWidget(example_title)

        for name, equation in self._EXAMPLES:
            btn = QPushButton(f"{name}　{equation}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: {c.get('bg_tertiary', '#f3f4f6')};"
                f" color: {c.get('fg_primary', '#111827')};"
                f" border: 1px solid {c.get('border', '#e5e7eb')};"
                " border-radius: 8px; padding: 7px 12px; text-align: left; }"
                f"QPushButton:hover {{ border: 1px solid {c.get('accent', '#4f5df5')}; }}"
            )
            btn.clicked.connect(lambda _=False, value=equation: self._load_example(value))
            example_layout.addWidget(btn)
        root.addWidget(example_card, 1)

    # ------------------------------------------------------------------
    def _load_example(self, equation: str) -> None:
        self.equation_edit.setText(equation)
        self._balance()

    def _on_text_changed(self) -> None:
        self.result_label.setText("等待配平…")
        self.result_label.setStyleSheet(
            f"color: {self._colors.get('fg_muted', '#9ca3af')}; font-size: 15px;"
        )
        self.detail_label.setText("")

    def _balance(self) -> None:
        c = self._colors
        equation = self.equation_edit.text().strip()
        if not equation:
            self._show_error("请输入化学方程式")
            return
        try:
            left_formulas, right_formulas = _split_equation(equation)
            left_coefficients, right_coefficients = balance_equation(equation)
        except Exception as exc:
            self._show_error(str(exc))
            return

        balanced = format_equation(
            left_formulas, right_formulas, left_coefficients, right_coefficients
        )
        self.result_label.setText(balanced)
        self.result_label.setStyleSheet(
            f"color: {c.get('success', '#10b981')}; font-size: 17px; font-weight: bold;"
        )
        total = sum(left_coefficients) + sum(right_coefficients)
        self.detail_label.setText(
            f"已配平：{len(left_coefficients)} 种反应物、{len(right_coefficients)} 种生成物，"
            f"系数之和 {total}（系数已化为最小互质整数）"
        )

    def _show_error(self, message: str) -> None:
        c = self._colors
        self.result_label.setText(f"✗ {message}")
        self.result_label.setStyleSheet(
            f"color: {c.get('error', '#ef4444')}; font-size: 14px;"
        )
        self.detail_label.setText("")


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "方程式配平",
        lambda c: EquationBalancerPanel(c),
        icon="⚗",
        subtitle="输入方程式自动配平，附常见反应示例",
    )
