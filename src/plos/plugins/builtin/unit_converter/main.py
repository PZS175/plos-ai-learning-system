"""内置插件：单位换算器。

长度 / 重量 / 温度 / 面积 / 体积 / 时间六类常用单位互算，
输入即换算，覆盖中学物理、化学、日常生活常用单位（含市制单位）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# 线性单位：数值 = 基准单位倍数（温度单独处理）
_UNITS: dict[str, list[tuple[str, str, float]]] = {
    "长度": [
        ("千米", "km", 1000.0),
        ("米", "m", 1.0),
        ("分米", "dm", 0.1),
        ("厘米", "cm", 0.01),
        ("毫米", "mm", 0.001),
        ("微米", "μm", 1e-6),
        ("纳米", "nm", 1e-9),
        ("里", "里", 500.0),
        ("丈", "丈", 10.0 / 3.0),
        ("尺", "尺", 1.0 / 3.0),
        ("寸", "寸", 1.0 / 30.0),
        ("英里", "mi", 1609.344),
        ("码", "yd", 0.9144),
        ("英尺", "ft", 0.3048),
        ("英寸", "in", 0.0254),
    ],
    "重量": [
        ("吨", "t", 1e6),
        ("千克", "kg", 1000.0),
        ("克", "g", 1.0),
        ("毫克", "mg", 1e-3),
        ("微克", "μg", 1e-6),
        ("斤", "斤", 500.0),
        ("两", "两", 50.0),
        ("钱", "钱", 5.0),
        ("磅", "lb", 453.59237),
        ("盎司", "oz", 28.349523125),
        ("克拉", "ct", 0.2),
    ],
    "面积": [
        ("平方千米", "km²", 1e6),
        ("公顷", "ha", 1e4),
        ("亩", "亩", 10000.0 / 15.0),
        ("平方米", "m²", 1.0),
        ("平方分米", "dm²", 0.01),
        ("平方厘米", "cm²", 1e-4),
        ("平方毫米", "mm²", 1e-6),
        ("平方英里", "mi²", 2_589_988.110336),
        ("英亩", "ac", 4046.8564224),
        ("平方码", "yd²", 0.83612736),
        ("平方英尺", "ft²", 0.09290304),
        ("平方英寸", "in²", 6.4516e-4),
    ],
    "体积": [
        ("立方米", "m³", 1000.0),
        ("升", "L", 1.0),
        ("毫升", "mL", 1e-3),
        ("立方厘米", "cm³", 1e-3),
        ("立方毫米", "mm³", 1e-6),
        ("立方英尺", "ft³", 28.316846592),
        ("立方英寸", "in³", 0.016387064),
        ("加仑(美)", "gal", 3.785411784),
        ("夸脱(美)", "qt", 0.946352946),
        ("品脱(美)", "pt", 0.473176473),
        ("桶(石油)", "bbl", 158.987294928),
    ],
    "时间": [
        ("毫秒", "ms", 1e-3),
        ("秒", "s", 1.0),
        ("分钟", "min", 60.0),
        ("小时", "h", 3600.0),
        ("天", "d", 86400.0),
        ("周", "周", 604800.0),
        ("月(按30天)", "月", 2_592_000.0),
        ("年(按365天)", "年", 31_536_000.0),
    ],
}

_TEMPERATURE = [("摄氏度", "°C"), ("华氏度", "°F"), ("开尔文", "K")]


def _to_celsius(value: float, unit: str) -> float:
    if unit == "°C":
        return value
    if unit == "°F":
        return (value - 32.0) * 5.0 / 9.0
    return value - 273.15  # K


def _from_celsius(value: float, unit: str) -> float:
    if unit == "°C":
        return value
    if unit == "°F":
        return value * 9.0 / 5.0 + 32.0
    return value + 273.15  # K


def _fmt(value: float) -> str:
    if value == 0:
        return "0"
    absv = abs(value)
    if absv >= 1e12 or absv < 1e-6:
        return f"{value:.6g}"
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text


class ConverterPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._build_ui()

    def _build_ui(self) -> None:
        c = self._colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        hint = QLabel("选择单位类别与换算方向，输入数值即时得到结果。")
        hint.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')};")
        layout.addWidget(hint)

        card = QFrame()
        card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 14px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 22)
        card_layout.setSpacing(14)

        self.category_box = QComboBox()
        self.category_box.addItems(list(_UNITS.keys()) + ["温度"])
        self.category_box.setStyleSheet(self._combo_style())
        self.category_box.currentTextChanged.connect(self._on_category_changed)
        card_layout.addWidget(self.category_box)

        row = QHBoxLayout()
        self.input_edit = QLineEdit("1")
        self.input_edit.setPlaceholderText("输入数值")
        self.input_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 10px; font-size: 18px;"
        )
        self.input_edit.textChanged.connect(self._convert)
        row.addWidget(self.input_edit, 2)

        self.from_box = QComboBox()
        self.from_box.setStyleSheet(self._combo_style())
        self.from_box.currentIndexChanged.connect(self._convert)
        row.addWidget(self.from_box, 1)

        swap_btn = QPushButton("⇄")
        swap_btn.setFixedWidth(40)
        swap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        swap_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.1)')};"
            f"color: {c.get('accent', '#4f5df5')}; border: none; border-radius: 8px; font-size: 16px;"
        )
        swap_btn.clicked.connect(self._swap)
        row.addWidget(swap_btn)

        self.to_box = QComboBox()
        self.to_box.setStyleSheet(self._combo_style())
        self.to_box.currentIndexChanged.connect(self._convert)
        row.addWidget(self.to_box, 1)
        card_layout.addLayout(row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 26px; font-weight: bold;"
        )
        card_layout.addWidget(self.result_label)

        self.formula_label = QLabel("")
        self.formula_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.formula_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        card_layout.addWidget(self.formula_label)
        layout.addWidget(card)
        layout.addStretch()

        self._populate_units("长度")
        self._convert()

    def _combo_style(self) -> str:
        c = self._colors
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 8px;"
        )

    def _populate_units(self, category: str) -> None:
        self.from_box.blockSignals(True)
        self.to_box.blockSignals(True)
        self.from_box.clear()
        self.to_box.clear()
        if category == "温度":
            for name, symbol in _TEMPERATURE:
                self.from_box.addItem(f"{name} ({symbol})", symbol)
                self.to_box.addItem(f"{name} ({symbol})", symbol)
            self.to_box.setCurrentIndex(1)
        else:
            for name, symbol, _factor in _UNITS[category]:
                self.from_box.addItem(f"{name} ({symbol})", symbol)
                self.to_box.addItem(f"{name} ({symbol})", symbol)
            self.to_box.setCurrentIndex(min(2, self.to_box.count() - 1))
        self.from_box.blockSignals(False)
        self.to_box.blockSignals(False)

    def _on_category_changed(self, category: str) -> None:
        self._populate_units(category)
        self._convert()

    def _swap(self) -> None:
        i, j = self.from_box.currentIndex(), self.to_box.currentIndex()
        self.from_box.setCurrentIndex(j)
        self.to_box.setCurrentIndex(i)
        self._convert()

    def _convert(self) -> None:
        text = self.input_edit.text().strip().replace(",", "")
        if text in ("", "-", ".", "-."):
            self.result_label.setText("—")
            self.formula_label.setText("")
            return
        try:
            value = float(text)
        except ValueError:
            self.result_label.setText("请输入有效数字")
            self.formula_label.setText("")
            return

        from_sym = self.from_box.currentData()
        to_sym = self.to_box.currentData()
        category = self.category_box.currentText()
        try:
            if category == "温度":
                result = _from_celsius(_to_celsius(value, from_sym), to_sym)
            else:
                units = _UNITS[category]
                factor_map = {sym: factor for _n, sym, factor in units}
                result = value * factor_map[from_sym] / factor_map[to_sym]
        except Exception:
            self.result_label.setText("换算失败")
            return

        from_text = self.from_box.currentText()
        to_text = self.to_box.currentText()
        self.result_label.setText(f"{_fmt(result)} {to_sym}")
        self.formula_label.setText(f"{_fmt(value)} {from_text} ＝ {_fmt(result)} {to_text}")


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "单位换算",
        lambda c: ConverterPanel(c),
        icon="⇄",
        subtitle="长度、重量、温度、面积、体积、时间即时互算",
    )
