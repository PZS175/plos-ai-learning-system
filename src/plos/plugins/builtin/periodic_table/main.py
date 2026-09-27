"""内置插件：化学元素周期表。

- 118 种元素，按标准周期表 18 列布局排布（含镧系 / 锕系单独两行）；
- 按元素类别着色，颜色随深浅主题自适应；
- 支持符号 / 中文名 / 原子序数搜索，非匹配元素淡出；
- 点击查看详情：相对原子质量、类别、是否中学重点元素。

数据为中学教学常用的相对原子质量（放射性元素取半衰期最长同位素的质量数）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# (序数, 符号, 中文名, 相对原子质量, 类别, 是否中学重点)
_ELEMENTS: List[Tuple[int, str, str, float, str, bool]] = [
    (1, "H", "氢", 1.008, "非金属", True),
    (2, "He", "氦", 4.003, "稀有气体", True),
    (3, "Li", "锂", 6.94, "碱金属", False),
    (4, "Be", "铍", 9.012, "碱土金属", False),
    (5, "B", "硼", 10.81, "类金属", False),
    (6, "C", "碳", 12.011, "非金属", True),
    (7, "N", "氮", 14.007, "非金属", True),
    (8, "O", "氧", 15.999, "非金属", True),
    (9, "F", "氟", 18.998, "卤素", False),
    (10, "Ne", "氖", 20.180, "稀有气体", False),
    (11, "Na", "钠", 22.990, "碱金属", True),
    (12, "Mg", "镁", 24.305, "碱土金属", True),
    (13, "Al", "铝", 26.982, "主族金属", True),
    (14, "Si", "硅", 28.085, "类金属", True),
    (15, "P", "磷", 30.974, "非金属", True),
    (16, "S", "硫", 32.06, "非金属", True),
    (17, "Cl", "氯", 35.45, "卤素", True),
    (18, "Ar", "氩", 39.948, "稀有气体", False),
    (19, "K", "钾", 39.098, "碱金属", True),
    (20, "Ca", "钙", 40.078, "碱土金属", True),
    (21, "Sc", "钪", 44.956, "过渡金属", False),
    (22, "Ti", "钛", 47.867, "过渡金属", False),
    (23, "V", "钒", 50.942, "过渡金属", False),
    (24, "Cr", "铬", 51.996, "过渡金属", False),
    (25, "Mn", "锰", 54.938, "过渡金属", True),
    (26, "Fe", "铁", 55.845, "过渡金属", True),
    (27, "Co", "钴", 58.933, "过渡金属", False),
    (28, "Ni", "镍", 58.693, "过渡金属", False),
    (29, "Cu", "铜", 63.546, "过渡金属", True),
    (30, "Zn", "锌", 65.38, "过渡金属", True),
    (31, "Ga", "镓", 69.723, "主族金属", False),
    (32, "Ge", "锗", 72.630, "类金属", False),
    (33, "As", "砷", 74.922, "类金属", False),
    (34, "Se", "硒", 78.971, "非金属", False),
    (35, "Br", "溴", 79.904, "卤素", False),
    (36, "Kr", "氪", 83.798, "稀有气体", False),
    (37, "Rb", "铷", 85.468, "碱金属", False),
    (38, "Sr", "锶", 87.62, "碱土金属", False),
    (39, "Y", "钇", 88.906, "过渡金属", False),
    (40, "Zr", "锆", 91.224, "过渡金属", False),
    (41, "Nb", "铌", 92.906, "过渡金属", False),
    (42, "Mo", "钼", 95.95, "过渡金属", False),
    (43, "Tc", "锝", 98.0, "过渡金属", False),
    (44, "Ru", "钌", 101.07, "过渡金属", False),
    (45, "Rh", "铑", 102.91, "过渡金属", False),
    (46, "Pd", "钯", 106.42, "过渡金属", False),
    (47, "Ag", "银", 107.87, "过渡金属", True),
    (48, "Cd", "镉", 112.41, "过渡金属", False),
    (49, "In", "铟", 114.82, "主族金属", False),
    (50, "Sn", "锡", 118.71, "主族金属", False),
    (51, "Sb", "锑", 121.76, "类金属", False),
    (52, "Te", "碲", 127.60, "类金属", False),
    (53, "I", "碘", 126.90, "卤素", True),
    (54, "Xe", "氙", 131.29, "稀有气体", False),
    (55, "Cs", "铯", 132.91, "碱金属", False),
    (56, "Ba", "钡", 137.33, "碱土金属", True),
    (57, "La", "镧", 138.91, "镧系", False),
    (58, "Ce", "铈", 140.12, "镧系", False),
    (59, "Pr", "镨", 140.91, "镧系", False),
    (60, "Nd", "钕", 144.24, "镧系", False),
    (61, "Pm", "钷", 145.0, "镧系", False),
    (62, "Sm", "钐", 150.36, "镧系", False),
    (63, "Eu", "铕", 151.96, "镧系", False),
    (64, "Gd", "钆", 157.25, "镧系", False),
    (65, "Tb", "铽", 158.93, "镧系", False),
    (66, "Dy", "镝", 162.50, "镧系", False),
    (67, "Ho", "钬", 164.93, "镧系", False),
    (68, "Er", "铒", 167.26, "镧系", False),
    (69, "Tm", "铥", 168.93, "镧系", False),
    (70, "Yb", "镱", 173.05, "镧系", False),
    (71, "Lu", "镥", 174.97, "镧系", False),
    (72, "Hf", "铪", 178.49, "过渡金属", False),
    (73, "Ta", "钽", 180.95, "过渡金属", False),
    (74, "W", "钨", 183.84, "过渡金属", False),
    (75, "Re", "铼", 186.21, "过渡金属", False),
    (76, "Os", "锇", 190.23, "过渡金属", False),
    (77, "Ir", "铱", 192.22, "过渡金属", False),
    (78, "Pt", "铂", 195.08, "过渡金属", False),
    (79, "Au", "金", 196.97, "过渡金属", False),
    (80, "Hg", "汞", 200.59, "过渡金属", True),
    (81, "Tl", "铊", 204.38, "主族金属", False),
    (82, "Pb", "铅", 207.2, "主族金属", False),
    (83, "Bi", "铋", 208.98, "主族金属", False),
    (84, "Po", "钋", 209.0, "类金属", False),
    (85, "At", "砹", 210.0, "卤素", False),
    (86, "Rn", "氡", 222.0, "稀有气体", False),
    (87, "Fr", "钫", 223.0, "碱金属", False),
    (88, "Ra", "镭", 226.0, "碱土金属", False),
    (89, "Ac", "锕", 227.0, "锕系", False),
    (90, "Th", "钍", 232.04, "锕系", False),
    (91, "Pa", "镤", 231.04, "锕系", False),
    (92, "U", "铀", 238.03, "锕系", False),
    (93, "Np", "镎", 237.0, "锕系", False),
    (94, "Pu", "钚", 244.0, "锕系", False),
    (95, "Am", "镅", 243.0, "锕系", False),
    (96, "Cm", "锔", 247.0, "锕系", False),
    (97, "Bk", "锫", 247.0, "锕系", False),
    (98, "Cf", "锎", 251.0, "锕系", False),
    (99, "Es", "锿", 252.0, "锕系", False),
    (100, "Fm", "镄", 257.0, "锕系", False),
    (101, "Md", "钔", 258.0, "锕系", False),
    (102, "No", "锘", 259.0, "锕系", False),
    (103, "Lr", "铹", 266.0, "锕系", False),
    (104, "Rf", "𬬻", 267.0, "过渡金属", False),
    (105, "Db", "𬭊", 268.0, "过渡金属", False),
    (106, "Sg", "𬭳", 269.0, "过渡金属", False),
    (107, "Bh", "𬭛", 270.0, "过渡金属", False),
    (108, "Hs", "𬭶", 269.0, "过渡金属", False),
    (109, "Mt", "鿏", 278.0, "过渡金属", False),
    (110, "Ds", "𫟼", 281.0, "过渡金属", False),
    (111, "Rg", "𬬭", 282.0, "过渡金属", False),
    (112, "Cn", "鎶", 285.0, "过渡金属", False),
    (113, "Nh", "鉨", 286.0, "主族金属", False),
    (114, "Fl", "鈇", 289.0, "主族金属", False),
    (115, "Mc", "镆", 290.0, "主族金属", False),
    (116, "Lv", "鉝", 293.0, "主族金属", False),
    (117, "Ts", "鿬", 294.0, "卤素", False),
    (118, "Og", "鿫", 294.0, "稀有气体", False),
]

# 类别 → 主题色令牌
_CATEGORY_TOKENS: Dict[str, str] = {
    "碱金属": "chart_4",
    "碱土金属": "chart_3",
    "过渡金属": "chart_2",
    "主族金属": "teal",
    "类金属": "chart_6",
    "非金属": "chart_1",
    "卤素": "purple",
    "稀有气体": "chart_5",
    "镧系": "warning",
    "锕系": "fg_muted",
}


def _build_positions() -> Dict[int, Tuple[int, int]]:
    """生成元素 → (行, 列) 的标准周期表位置（行列均从 0 开始）。"""
    positions: Dict[int, Tuple[int, int]] = {}

    def place(numbers: List[int], row: int, cols: List[int]) -> None:
        for number, col in zip(numbers, cols):
            positions[number] = (row, col)

    place([1, 2], 0, [0, 17])
    place(list(range(3, 11)), 1, [0, 1, 12, 13, 14, 15, 16, 17])
    place(list(range(11, 19)), 2, [0, 1, 12, 13, 14, 15, 16, 17])
    place(list(range(19, 37)), 3, list(range(0, 18)))
    place(list(range(37, 55)), 4, list(range(0, 18)))
    place(list(range(55, 57)), 5, [0, 1])
    place(list(range(72, 87)), 5, list(range(3, 18)))
    place(list(range(87, 89)), 6, [0, 1])
    place(list(range(104, 119)), 6, list(range(3, 18)))
    # 镧系 / 锕系单独两行
    place(list(range(57, 72)), 8, list(range(2, 17)))
    place(list(range(89, 104)), 9, list(range(2, 17)))
    return positions


_POSITIONS = _build_positions()

_BY_NUMBER = {item[0]: item for item in _ELEMENTS}


def _fmt_mass(mass: float) -> str:
    if mass == int(mass):
        return str(int(mass))
    return f"{mass:g}"


class _ElementButton(QPushButton):
    """单个元素方格。"""

    def __init__(self, element: Tuple[int, str, str, float, str, bool], parent=None) -> None:
        number, symbol, _name, _mass, _category, _key = element
        super().__init__(f"{symbol}", parent)
        self.number = number
        self.setFixedSize(54, 42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{number} {symbol} {_name}　相对原子质量 {_fmt_mass(_mass)}")
        self.setProperty("dimmed", False)


class PeriodicTablePanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._dark = QColor(self._colors.get("bg_primary", "#f6f7fb")).lightness() < 128
        self._buttons: Dict[int, _ElementButton] = {}
        self._selected: Optional[int] = None
        self._build_ui()
        self._apply_filter("")
        self._show_detail(8)  # 默认展示氧元素，直观演示

    # ------------------------------------------------------------------
    def _category_bg(self, category: str) -> str:
        token = _CATEGORY_TOKENS.get(category, "accent")
        color = QColor(self._colors.get(token, self._colors.get("accent", "#4f5df5")))
        color.setAlpha(64 if self._dark else 46)
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"

    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("化学元素周期表")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：符号 / 中文名 / 序数，如 Fe、铁、26")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(300)
        self.search_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 10px;"
        )
        self.search_edit.textChanged.connect(self._apply_filter)
        head.addWidget(self.search_edit)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)

        # 周期表网格
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        grid_host = QWidget()
        scroll.setWidget(grid_host)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(3)

        for number, symbol, name, mass, category, key in _ELEMENTS:
            row, col = _POSITIONS.get(number, (0, 0))
            btn = _ElementButton((number, symbol, name, mass, category, key))
            btn.setStyleSheet(
                f"QPushButton {{ background: {self._category_bg(category)};"
                f" color: {c.get('fg_primary', '#111827')};"
                f" border: 1px solid {c.get('border', '#e5e7eb')};"
                " border-radius: 7px; font-size: 12px; font-weight: bold; }"
                f"QPushButton:hover {{ border: 1px solid {c.get('accent', '#4f5df5')}; }}"
            )
            btn.clicked.connect(lambda _=False, n=number: self._show_detail(n))
            grid.addWidget(btn, row, col)
            self._buttons[number] = btn

        # 镧系 / 锕系占位提示
        for row, text in ((5, "57-71"), (6, "89-103")):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedSize(54, 42)
            label.setStyleSheet(
                f"color: {c.get('fg_muted', '#9ca3af')};"
                f"background: {c.get('bg_tertiary', '#f3f4f6')};"
                f"border: 1px dashed {c.get('border_strong', '#d1d5db')};"
                "border-radius: 7px; font-size: 10px;"
            )
            grid.addWidget(label, row, 2)
        body.addWidget(scroll, 3)

        # 详情卡片
        detail = QFrame()
        detail.setMinimumWidth(230)
        detail.setMaximumWidth(280)
        detail.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 14px;"
        )
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(20, 20, 20, 20)
        detail_layout.setSpacing(8)

        self.symbol_label = QLabel("—")
        self.symbol_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.symbol_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 44px; font-weight: bold;"
        )
        detail_layout.addWidget(self.symbol_label)

        self.name_label = QLabel("点击左侧元素查看详情")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 16px; font-weight: bold;"
        )
        detail_layout.addWidget(self.name_label)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.info_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        detail_layout.addWidget(self.info_label)

        self.legend_label = QLabel("")
        self.legend_label.setWordWrap(True)
        self.legend_label.setStyleSheet(
            f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 11px;"
        )
        detail_layout.addWidget(self.legend_label)
        detail_layout.addStretch()
        body.addWidget(detail, 1)

        root.addLayout(body, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        root.addWidget(self.count_label)

        legend = "　".join(
            f"{name}·{token}" for name, token in _CATEGORY_TOKENS.items()
        )
        self.legend_label.setText(f"类别涵盖：{legend}")

    # ------------------------------------------------------------------
    def _match(self, element: Tuple[int, str, str, float, str, bool], keyword: str) -> bool:
        number, symbol, name, _mass, category, _key = element
        return (
            keyword in symbol.lower()
            or keyword in name
            or keyword in str(number)
            or keyword in category
        )

    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        matched = 0
        for number, btn in self._buttons.items():
            element = _BY_NUMBER[number]
            hit = not keyword or self._match(element, keyword)
            if hit:
                matched += 1
                if element[5]:  # 中学重点元素用强调边框
                    btn.setStyleSheet(
                        f"QPushButton {{ background: {self._category_bg(element[4])};"
                        f" color: {self._colors.get('fg_primary', '#111827')};"
                        f" border: 2px solid {self._colors.get('accent', '#4f5df5')};"
                        " border-radius: 7px; font-size: 12px; font-weight: bold; }"
                    )
                else:
                    btn.setStyleSheet(
                        f"QPushButton {{ background: {self._category_bg(element[4])};"
                        f" color: {self._colors.get('fg_primary', '#111827')};"
                        f" border: 1px solid {self._colors.get('border', '#e5e7eb')};"
                        " border-radius: 7px; font-size: 12px; font-weight: bold; }"
                        f"QPushButton:hover {{ border: 1px solid {self._colors.get('accent', '#4f5df5')}; }}"
                    )
                btn.setEnabled(True)
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {self._colors.get('bg_tertiary', '#f3f4f6')};"
                    f" color: {self._colors.get('fg_muted', '#9ca3af')};"
                    f" border: 1px solid {self._colors.get('border', '#e5e7eb')};"
                    " border-radius: 7px; font-size: 12px; }"
                )
                btn.setEnabled(True)
        self.count_label.setText(
            "共 118 种元素"
            + ("，匹配 {} 种".format(matched) if keyword else "")
            + "，加粗蓝框为中学重点元素"
        )

    def _show_detail(self, number: int) -> None:
        element = _BY_NUMBER.get(number)
        if element is None:
            return
        self._selected = number
        num, symbol, name, mass, category, key = element
        self.symbol_label.setText(symbol)
        self.name_label.setText(f"{name}　{num}")
        marks = "中学重点元素" if key else "拓展了解"
        self.info_label.setText(
            f"元素符号：{symbol}\n"
            f"中文名称：{name}\n"
            f"原子序数：{num}\n"
            f"相对原子质量：{_fmt_mass(mass)}\n"
            f"元素类别：{category}\n"
            f"学习提示：{marks}"
        )


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "元素周期表",
        lambda c: PeriodicTablePanel(c),
        icon="⚛",
        subtitle="118 种元素速查，含相对原子质量与中学重点标注",
    )
