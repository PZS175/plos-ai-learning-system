"""内置插件：函数图像绘制。

输入 y = f(x) 即时绘制，最多同时 4 条曲线：

- 表达式求值复用共享的 ``plos.plugins.expr.ExprEvaluator``（ast 白名单，无 eval）；
- 支持省略乘号（``2x``、``3(x+1)``）、``^`` 幂、``π``/``e`` 常量与常用函数；
- 纯 QPainter 自绘坐标系：自适应网格、坐标轴刻度、图例、零点标注；
- 鼠标滚轮以光标为中心缩放，按住拖动平移，一键重置视图；
- 定义域外（如 1/x 的 0 点、sqrt 负数）自动断线，不会画出错误连线。
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plos.plugins.expr import ExprEvaluator

_MAX_CURVES = 4
_MAX_ZEROS = 40


class _PlotCanvas(QWidget):
    """坐标系画布：网格、曲线、零点与交互。"""

    def __init__(self, colors: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors = colors
        self._evaluator = ExprEvaluator(variables={"x"}, degrees=False)
        self._functions: List[Tuple[str, str]] = []
        self._nodes: List[Optional[object]] = []
        self._errors: List[str] = []

        self.center_x = 0.0
        self.center_y = 0.0
        self.scale = 45.0  # 每个数学单位对应的像素数
        self.show_zeros = True
        self.zeros: List[float] = []
        self.on_view_changed: Optional[Callable[[], None]] = None

        self._drag_from = None
        self.setMinimumHeight(340)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------
    # 表达式
    # ------------------------------------------------------------------
    def set_functions(self, functions: List[Tuple[str, str]]) -> None:
        """functions: [(表达式, 颜色), ...]；空表达式会被跳过。"""
        self._functions = list(functions)
        self._nodes = []
        self._errors = []
        for expression, _color in self._functions:
            if not expression.strip():
                self._nodes.append(None)
                self._errors.append("")
                continue
            try:
                self._nodes.append(self._evaluator.compile(expression))
                self._errors.append("")
            except Exception as exc:
                self._nodes.append(None)
                self._errors.append(str(exc))
        self.refresh_zeros()
        self.update()

    def compile_error(self, index: int) -> str:
        if 0 <= index < len(self._errors):
            return self._errors[index]
        return ""

    # ------------------------------------------------------------------
    # 坐标转换
    # ------------------------------------------------------------------
    def _x_to_px(self, x: float) -> float:
        return self.width() / 2 + (x - self.center_x) * self.scale

    def _y_to_px(self, y: float) -> float:
        return self.height() / 2 - (y - self.center_y) * self.scale

    def _px_to_x(self, px: float) -> float:
        return self.center_x + (px - self.width() / 2) / self.scale

    def _px_to_y(self, py: float) -> float:
        return self.center_y - (py - self.height() / 2) / self.scale

    def _safe_eval(self, node, x: float) -> Optional[float]:
        try:
            y = self._evaluator.eval_compiled(node, x=x)
        except Exception:
            return None
        if y is None or not math.isfinite(y) or abs(y) > 1e6:
            return None
        return y

    # ------------------------------------------------------------------
    # 零点
    # ------------------------------------------------------------------
    def _compute_zeros(self, node) -> List[float]:
        if node is None or not self.show_zeros:
            return []
        result: List[float] = []
        step = 2
        previous: Optional[Tuple[float, float]] = None
        for px in range(0, self.width() + 1, step):
            x = self._px_to_x(px)
            y = self._safe_eval(node, x)
            if y is None:
                previous = None
                continue
            if previous is not None:
                x0, y0 = previous
                if y0 == 0.0:
                    result.append(x0)
                elif (y0 < 0 < y) or (y > 0 > y0):
                    result.append(x0 - y0 * (x - x0) / (y - y0))
            previous = (x, y)
            if len(result) > _MAX_ZEROS:
                break
        return result

    def refresh_zeros(self) -> List[float]:
        node = self._nodes[0] if self._nodes else None
        self.zeros = self._compute_zeros(node)
        return self.zeros

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def _grid_step(self) -> float:
        raw = 70.0 / max(1e-6, self.scale)
        exponent = math.floor(math.log10(raw))
        base = raw / (10**exponent)
        if base < 1.5:
            multiple = 1
        elif base < 3.5:
            multiple = 2
        elif base < 7.5:
            multiple = 5
        else:
            multiple = 10
        return multiple * (10**exponent)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self._colors.get("card_bg", "#ffffff")))
        self._draw_grid(painter)
        self._draw_axes(painter)
        self._draw_curves(painter)
        self._draw_zeros(painter)
        self._draw_legend(painter)
        painter.end()

    def _draw_grid(self, painter: QPainter) -> None:
        step = self._grid_step()
        pen = QPen(QColor(self._colors.get("border", "#e5e7eb")))
        pen.setWidth(1)
        painter.setPen(pen)

        left = self._px_to_x(0)
        right = self._px_to_x(self.width())
        bottom = self._px_to_y(self.height())
        top = self._px_to_y(0)

        start_x = math.floor(left / step) * step
        x = start_x
        while x <= right:
            px = self._x_to_px(x)
            painter.drawLine(QPointF(px, 0), QPointF(px, self.height()))
            x += step

        start_y = math.floor(bottom / step) * step
        y = start_y
        while y <= top:
            py = self._y_to_px(y)
            painter.drawLine(QPointF(0, py), QPointF(self.width(), py))
            y += step

    def _draw_axes(self, painter: QPainter) -> None:
        step = self._grid_step()
        axis_color = QColor(self._colors.get("fg_secondary", "#6b7280"))
        pen = QPen(axis_color)
        pen.setWidth(2)
        painter.setPen(pen)

        # x 轴 / y 轴（若原点在视野外，贴边显示）
        y0 = self._y_to_px(0)
        y0 = max(0.0, min(float(self.height()), y0))
        x0 = self._x_to_px(0)
        x0 = max(0.0, min(float(self.width()), x0))
        painter.drawLine(QPointF(0, y0), QPointF(self.width(), y0))
        painter.drawLine(QPointF(x0, 0), QPointF(x0, self.height()))

        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor(self._colors.get("fg_muted", "#9ca3af")))

        left = self._px_to_x(0)
        right = self._px_to_x(self.width())
        x = math.floor(left / step) * step
        while x <= right:
            if abs(x) > 1e-9:  # 原点只标一次
                px = self._x_to_px(x)
                label = _fmt_tick(x)
                painter.drawText(QPointF(px + 3, y0 + 13), label)
            x += step

        bottom = self._px_to_y(self.height())
        top = self._px_to_y(0)
        y = math.floor(bottom / step) * step
        while y <= top:
            if abs(y) > 1e-9:
                py = self._y_to_px(y)
                label = _fmt_tick(y)
                painter.drawText(QPointF(x0 + 5, py - 3), label)
            y += step

        painter.setPen(QColor(self._colors.get("fg_secondary", "#6b7280")))
        painter.drawText(QPointF(x0 + 5, y0 + 14), "O")

    def _draw_curves(self, painter: QPainter) -> None:
        width = self.width()
        for index, (_expression, color) in enumerate(self._functions):
            node = self._nodes[index] if index < len(self._nodes) else None
            if node is None:
                continue
            pen = QPen(QColor(color))
            pen.setWidth(2)
            painter.setPen(pen)

            path = QPainterPath()
            started = False
            for px in range(0, width + 1, 2):
                x = self._px_to_x(px)
                y = self._safe_eval(node, x)
                if y is None:
                    started = False
                    continue
                py = max(-1e5, min(1e5, self._y_to_px(y)))
                if started:
                    path.lineTo(QPointF(px, py))
                else:
                    path.moveTo(QPointF(px, py))
                    started = True
            painter.drawPath(path)

    def _draw_zeros(self, painter: QPainter) -> None:
        if not self.zeros:
            return
        color = QColor(self._colors.get("warning", "#f59e0b"))
        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)
        for x in self.zeros[:_MAX_ZEROS]:
            px = self._x_to_px(x)
            py = self._y_to_px(0.0)
            if -20 <= px <= self.width() + 20 and -20 <= py <= self.height() + 20:
                painter.drawEllipse(QPointF(px, py), 4, 4)

    def _draw_legend(self, painter: QPainter) -> None:
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        y = 16
        for index, (expression, color) in enumerate(self._functions):
            if not expression.strip():
                continue
            pen = QPen(QColor(color))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.drawLine(QPointF(12, y - 4), QPointF(32, y - 4))
            painter.setPen(QColor(self._colors.get("fg_secondary", "#6b7280")))
            painter.drawText(QPointF(38, y), f"y = {expression}")
            y += 18

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if not delta:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        position = event.position()
        math_x = self._px_to_x(position.x())
        math_y = self._px_to_y(position.y())
        self.scale = max(2.0, min(4000.0, self.scale * factor))
        # 让光标下的数学坐标保持不动
        self.center_x = math_x - (position.x() - self.width() / 2) / self.scale
        self.center_y = math_y + (position.y() - self.height() / 2) / self.scale
        self._after_view_change()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = (event.position(), self.center_x, self.center_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is None:
            return
        origin, center_x, center_y = self._drag_from
        dx = (event.position().x() - origin.x()) / self.scale
        dy = (event.position().y() - origin.y()) / self.scale
        self.center_x = center_x - dx
        self.center_y = center_y + dy
        self._after_view_change()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is not None:
            self._drag_from = None
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _after_view_change(self) -> None:
        self.refresh_zeros()
        self.update()
        if self.on_view_changed is not None:
            try:
                self.on_view_changed()
            except Exception:
                pass

    def reset_view(self) -> None:
        self.center_x = 0.0
        self.center_y = 0.0
        self.scale = 45.0
        self._after_view_change()


def _fmt_tick(value: float) -> str:
    if value == int(value) and abs(value) < 1e6:
        return str(int(value))
    return f"{value:g}"


class FunctionPlotPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._rows: List[Tuple[QLineEdit, str]] = []
        self._build_ui()
        self._sync_functions()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        colors = [
            c.get("chart_1", "#4f5df5"),
            c.get("chart_2", "#10b981"),
            c.get("chart_3", "#f59e0b"),
            c.get("chart_4", "#ef4444"),
        ]

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        hint = QLabel(
            "输入自变量 x 的表达式，如 sin(x)、x^2-2、1/x、2x+1（可省略乘号）。"
            "滚轮缩放、按住拖动平移。"
        )
        hint.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        root.addWidget(hint)

        # 表达式输入区
        input_card = QFrame()
        input_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(14, 12, 14, 12)
        input_layout.setSpacing(8)

        defaults = ["sin(x)", "x^2/4", "", ""]
        for index in range(_MAX_CURVES):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {colors[index]}; font-size: 14px;")
            dot.setFixedWidth(16)
            row.addWidget(dot)

            edit = QLineEdit(defaults[index])
            edit.setPlaceholderText(f"曲线 {index + 1}：y = f(x)")
            edit.setStyleSheet(
                f"background: {c.get('bg_tertiary', '#f3f4f6')};"
                f"color: {c.get('fg_primary', '#111827')};"
                f"border: 1px solid {c.get('border', '#e5e7eb')};"
                "border-radius: 8px; padding: 6px 8px;"
            )
            edit.textChanged.connect(self._sync_functions)
            row.addWidget(edit, 1)

            self._rows.append((edit, colors[index]))
            input_layout.addLayout(row)
        root.addWidget(input_card)

        # 画布
        self.canvas = _PlotCanvas(c, self)
        self.canvas.on_view_changed = self._update_zero_label
        root.addWidget(self.canvas, 1)

        # 底部控制
        bottom = QHBoxLayout()
        self.zero_label = QLabel("")
        self.zero_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        bottom.addWidget(self.zero_label, 1)

        self.zero_check = QCheckBox("标注零点")
        self.zero_check.setChecked(True)
        self.zero_check.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')};")
        self.zero_check.toggled.connect(self._toggle_zeros)
        bottom.addWidget(self.zero_check)

        reset_btn = QPushButton("重置视图")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 6px 14px;"
        )
        reset_btn.clicked.connect(self.canvas.reset_view)
        bottom.addWidget(reset_btn)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    def _sync_functions(self) -> None:
        functions = [(edit.text().strip(), color) for edit, color in self._rows]
        self.canvas.set_functions(functions)

        c = self._colors
        for index, (edit, _color) in enumerate(self._rows):
            error = self.canvas.compile_error(index)
            if error:
                edit.setStyleSheet(
                    f"background: {c.get('bg_tertiary', '#f3f4f6')};"
                    f"color: {c.get('fg_primary', '#111827')};"
                    f"border: 1px solid {c.get('error', '#ef4444')};"
                    "border-radius: 8px; padding: 6px 8px;"
                )
                edit.setToolTip(f"表达式有误：{error}")
            else:
                edit.setStyleSheet(
                    f"background: {c.get('bg_tertiary', '#f3f4f6')};"
                    f"color: {c.get('fg_primary', '#111827')};"
                    f"border: 1px solid {c.get('border', '#e5e7eb')};"
                    "border-radius: 8px; padding: 6px 8px;"
                )
                edit.setToolTip("")
        self._update_zero_label()

    def _update_zero_label(self) -> None:
        zeros = self.canvas.zeros
        if not zeros:
            self.zero_label.setText("可见范围内未检测到零点（仅标注第一条曲线）")
            return
        shown = "、".join(f"{value:.3f}".rstrip("0").rstrip(".") for value in zeros[:8])
        more = f" 等 {len(zeros)} 个" if len(zeros) > 8 else ""
        self.zero_label.setText(f"零点（第一条曲线）：x ≈ {shown}{more}")

    def _toggle_zeros(self, checked: bool) -> None:
        self.canvas.show_zeros = bool(checked)
        self.canvas.refresh_zeros()
        self.canvas.update()
        self._update_zero_label()


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "函数图像",
        lambda c: FunctionPlotPanel(c),
        icon="📈",
        subtitle="输入表达式即时画图，数形结合看得见",
    )
