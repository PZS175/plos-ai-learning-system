"""总览 / Dashboard 面板。

展示学习核心数据、AI 学习计划、掌握度进度环、近 7 日学习趋势曲线、
错题科目分布饼图、闪卡复习正确率柱状图。

本次优化重点：
- 宽松留白、统一 8px 圆角卡片、扁平无阴影
- 顶部统计改为语义色独立小卡片，无悬浮上浮效果
- 通知栏可关闭、内边距充足、主按钮强化
- 学习计划空状态友好提示，表格进度条 + 彩色状态标签
- 图表适配深浅色主题与微软雅黑字体，0% 与无数据状态不展示死灰/空白
- 右上角增加模型/Ollama 在线状态指示器
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai import ModelManager
from ..services import (
    ChatService,
    DemoService,
    ErrorBookService,
    FlashcardService,
    RAGService,
    StatisticsService,
    StudyPlanService,
    TTSService,
    UserService,
)
from ..utils.logger import get_logger
from .math_text import MathLabel, attach_math_delegate
from .theme_manager import ThemeManager
from .ui_utils import (
    create_section_title,
    theme_colors,
    apply_card_style,
    create_empty_state_widget,
    repolish_tree,
    set_primary_button_style,
    show_error,
    show_info,
)

logger = get_logger("ui.dashboard_panel")


class _StatusBadge(QLabel):
    """状态标签，使用主题语义色，低饱和扁平。"""

    LABEL_MAP = {
        "active": "进行中",
        "completed": "已完成",
        "archived": "暂停",
        "paused": "暂停",
        "进行中": "进行中",
        "已完成": "已完成",
        "暂停": "暂停",
    }

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(self.LABEL_MAP.get(text, text), parent)
        c = theme_colors()
        status = self.text()
        if status == "已完成":
            color = c["success"]
        elif status == "暂停":
            color = c["warning"]
        elif status == "进行中":
            color = c["accent"]
        else:
            color = c["fg_secondary"]
        self.setStyleSheet(
            f"""
            QLabel {{
                color: {color};
                background-color: {color}22;
                border: 1px solid {color}55;
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            """
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(56)


def _chart_font(size: int, bold: bool = False) -> QFont:
    """返回图表使用的统一字体。"""
    font = QFont("Microsoft YaHei UI", size)
    if not QFont("Microsoft YaHei UI").exactMatch():
        font = QFont("Microsoft YaHei", size)
    if not QFont("Microsoft YaHei").exactMatch():
        font = QFont("Noto Sans CJK SC", size)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    return font


class _ProgressRing(QWidget):
    """掌握度环形进度条（支持深浅色、0% 不显示死灰）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.percentage = 0.0
        self.setMinimumSize(140, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_percentage(self, value: float) -> None:
        self.percentage = max(0.0, min(100.0, value))
        self.update()

    def paintEvent(self, event) -> None:
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            c = theme_colors()
            text_color = QColor(c["fg_primary"])
            accent = QColor(c["accent"])
            # 0% 时背景使用极淡的主题色，避免死灰
            bg_color = QColor(c["accent"])
            bg_color.setAlpha(30)

            accent_pen = QPen(accent)
            accent_pen.setWidth(10)
            accent_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            bg_pen = QPen(bg_color)
            bg_pen.setWidth(10)
            bg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

            rect = self.rect().adjusted(24, 24, -24, -24)
            start_angle = 90 * 16
            span_angle = -360 * 16

            painter.setPen(bg_pen)
            painter.drawArc(rect, start_angle, span_angle)

            progress_span = int(-360 * 16 * self.percentage / 100.0)
            painter.setPen(accent_pen)
            painter.drawArc(rect, start_angle, progress_span)

            painter.setPen(text_color)
            painter.setFont(_chart_font(18, bold=True))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{self.percentage:.0f}%")
        except Exception as e:
            logger.warning("Progress ring paint failed: %s", e)


class _LineChart(QWidget):
    """每日学习时长折线图（淡化网格、美化节点、适配主题）。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self.labels: List[str] = []
        self.values: List[float] = []
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels: List[str], values: List[float]) -> None:
        self.labels = list(labels)
        self.values = list(values)
        self.update()

    def paintEvent(self, event) -> None:
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            c = theme_colors()
            text_color = QColor(c["fg_primary"])
            secondary = QColor(c["fg_secondary"])
            accent = QColor(c["accent"])

            rect = self.rect().adjusted(40, 34, -24, -44)
            max_value = max(max(self.values) if self.values else 0, 1)

            # 淡化网格线
            grid_pen = QPen(secondary)
            grid_pen.setWidth(1)
            grid_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(grid_pen)
            for i in range(1, 5):
                y = rect.top() + rect.height() * i / 5
                painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))

            # 坐标轴
            axis_pen = QPen(secondary)
            axis_pen.setWidth(1)
            painter.setPen(axis_pen)
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.drawLine(rect.bottomLeft(), rect.topLeft())

            n = len(self.values)
            if n < 2:
                return
            width = rect.width()
            height = rect.height()
            x_positions = [rect.left() + i * width / (n - 1) for i in range(n)]
            y_positions = [rect.bottom() - (v / max_value) * height for v in self.values]

            # 填充区域
            fill_path = QPainterPath()
            fill_path.moveTo(int(x_positions[0]), int(rect.bottom()))
            for i in range(n):
                fill_path.lineTo(int(x_positions[i]), int(y_positions[i]))
            fill_path.lineTo(int(x_positions[-1]), int(rect.bottom()))
            fill_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            fill_color = QColor(accent)
            fill_color.setAlpha(30)
            painter.setBrush(fill_color)
            painter.drawPath(fill_path)

            # 折线
            line_pen = QPen(accent)
            line_pen.setWidth(3)
            line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(line_pen)
            for i in range(n - 1):
                painter.drawLine(
                    int(x_positions[i]), int(y_positions[i]),
                    int(x_positions[i + 1]), int(y_positions[i + 1]),
                )

            # 节点与标签
            for i, (x, y) in enumerate(zip(x_positions, y_positions)):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawEllipse(int(x) - 4, int(y) - 4, 8, 8)
                painter.setPen(text_color)
                painter.setFont(_chart_font(9))
                painter.drawText(int(x) - 20, int(rect.bottom()) + 18, str(self.labels[i])[-5:])
                painter.drawText(int(x) - 12, int(y) - 10, str(int(self.values[i])))

            painter.setPen(text_color)
            painter.setFont(_chart_font(12, bold=True))
            painter.drawText(rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.title)
        except Exception as e:
            logger.warning("Line chart paint failed: %s", e)


class _PieChart(QWidget):
    """错题科目分布饼图。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self.data: List[Dict[str, Any]] = []
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, data: List[Dict[str, Any]]) -> None:
        self.data = data
        self.update()

    def _palette(self) -> list[QColor]:
        c = theme_colors()
        return [
            QColor(c["chart_1"]),
            QColor(c["chart_2"]),
            QColor(c["chart_3"]),
            QColor(c["chart_4"]),
            QColor(c["chart_5"]),
            QColor(c["chart_6"]),
        ]

    def paintEvent(self, event) -> None:
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            text_color = QColor(theme_colors()["fg_primary"])
            palette = self._palette()

            rect = self.rect().adjusted(20, 44, -20, -20)
            total = sum(item.get("count", 0) for item in self.data) or 1
            start_angle = 0.0

            for i, item in enumerate(self.data):
                value = item.get("count", 0)
                span = value / total * 360.0
                color = palette[i % len(palette)]
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPie(rect, int(start_angle * 16), int(span * 16))
                start_angle += span

            # 标题
            painter.setPen(text_color)
            painter.setFont(_chart_font(12, bold=True))
            painter.drawText(self.rect().adjusted(16, 16, -16, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.title)

            # 图例
            painter.setFont(_chart_font(10))
            legend_y = rect.bottom() + 14
            x = rect.left()
            for i, item in enumerate(self.data[:6]):
                color = palette[i % len(palette)]
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(int(x), int(legend_y), 10, 10)
                painter.setPen(text_color)
                painter.drawText(int(x + 14), int(legend_y + 9), f"{item.get('subject', '')} {item.get('count', 0)}")
                x += 90
        except Exception as e:
            logger.warning("Pie chart paint failed: %s", e)


class _BarChart(QWidget):
    """闪卡复习正确率柱状图。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self.labels: List[str] = []
        self.values: List[float] = []
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels: List[str], values: List[float]) -> None:
        self.labels = list(labels)
        self.values = list(values)
        self.update()

    def paintEvent(self, event) -> None:
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            c = theme_colors()
            text_color = QColor(c["fg_primary"])
            secondary = QColor(c["fg_secondary"])
            axis_pen = QPen(secondary)
            axis_pen.setWidth(1)

            rect = self.rect().adjusted(36, 34, -24, -40)
            max_value = max(max(self.values) if self.values else 0, 1)

            painter.setPen(axis_pen)
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.drawLine(rect.bottomLeft(), rect.topLeft())

            n = len(self.values)
            if n == 0:
                return
            bar_width = rect.width() / n * 0.55
            spacing = rect.width() / n
            bar_color = QColor(c["accent"])
            for i, value in enumerate(self.values):
                x = rect.left() + i * spacing + (spacing - bar_width) / 2
                height = (value / max_value) * rect.height()
                y = rect.bottom() - height
                painter.setBrush(bar_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(int(x), int(y), int(bar_width), int(height), 4, 4)
                painter.setPen(text_color)
                painter.setFont(_chart_font(9))
                painter.drawText(int(x - 5), int(rect.bottom()) + 16, str(self.labels[i])[-3:])
                painter.drawText(int(x), int(y) - 8, f"{value:.0f}%")

            painter.setPen(text_color)
            painter.setFont(_chart_font(12, bold=True))
            painter.drawText(rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.title)
        except Exception as e:
            logger.warning("Bar chart paint failed: %s", e)


class _HorizontalBarChart(QWidget):
    """薄弱知识点 TOP5 水平柱状图。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self.labels: List[str] = []
        self.values: List[float] = []
        self.setMinimumSize(300, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels: List[str], values: List[float]) -> None:
        self.labels = list(labels)
        self.values = list(values)
        self.update()

    def paintEvent(self, event) -> None:
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            c = theme_colors()
            text_color = QColor(c["fg_primary"])
            secondary = QColor(c["fg_secondary"])

            rect = self.rect().adjusted(120, 40, -24, -24)
            max_value = max(max(self.values) if self.values else 0, 1)

            painter.setPen(text_color)
            painter.setFont(_chart_font(12, bold=True))
            painter.drawText(self.rect().adjusted(16, 16, -16, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.title)

            n = len(self.values)
            if n == 0:
                return

            bar_height = rect.height() / n * 0.55
            spacing = rect.height() / n
            bar_color = QColor(c["warning"])
            for i, value in enumerate(self.values):
                y = rect.top() + i * spacing + (spacing - bar_height) / 2
                width = (value / max_value) * rect.width()
                painter.setBrush(bar_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(int(rect.left()), int(y), int(width), int(bar_height), 4, 4)

                painter.setPen(text_color)
                painter.setFont(_chart_font(10))
                painter.drawText(
                    16, int(y), 100, int(bar_height),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    str(self.labels[i])[:12],
                )
                painter.drawText(
                    int(rect.left() + width + 8), int(y), 60, int(bar_height),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    str(int(value)),
                )
                # 标签与柱之间画一条淡分隔线
                painter.setPen(QPen(secondary, 1, Qt.PenStyle.DotLine))
                painter.drawLine(16, int(y + bar_height + spacing * 0.2), rect.left() - 8, int(y + bar_height + spacing * 0.2))
        except Exception as e:
            logger.warning("Horizontal bar chart paint failed: %s", e)


class _StatCard(QFrame):
    """顶部统计小卡片：语义色数字、扁平无阴影、无动态上浮。"""

    ICONS = {
        "总题数": "◈",
        "已掌握": "●",
        "待复习": "◉",
        "今日学习": "◐",
    }

    def __init__(
        self,
        title: str,
        value: str,
        hint: str = "",
        color: Optional[str] = None,
        fg_secondary: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        from .ui_utils import theme_colors as _tc

        _c = _tc()
        color = color or _c["accent"]
        fg_secondary = fg_secondary or _c["fg_secondary"]
        self._color = color
        self.setProperty("card", True)
        apply_card_style(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(110)
        self.setMaximumWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(8)
        from .ui_utils import create_icon_chip

        icon_label = create_icon_chip(
            self.ICONS.get(title, "◆"), size=24, fg=color,
            bg=theme_colors()["accent_lighter"], font_size=13,
        )
        top_layout.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: 13px; color: {fg_secondary}; background-color: transparent;"
        )
        top_layout.addWidget(title_label)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        self._value_label = QLabel(value)
        self._value_label.setStyleSheet(
            f"font-size: 30px; font-weight: 700; color: {color}; background-color: transparent;"
        )
        layout.addWidget(self._value_label)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setStyleSheet(
                f"font-size: 11px; color: {fg_secondary}; background-color: transparent;"
            )
            layout.addWidget(hint_label)

        layout.addStretch()

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)


class _ReminderCard(QFrame):
    """今日复习提醒条：可关闭、主题浅背景、主按钮。"""

    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        c = theme_colors()
        self.setProperty("card", True)
        apply_card_style(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        bg = c["accent_light"]
        border = "rgba(30, 111, 255, 0.35)"
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 16, 14)
        layout.setSpacing(14)

        self.icon_label = QLabel("◉")
        self.icon_label.setStyleSheet(
            f"font-size: 24px; color: {c['accent']}; background-color: transparent;"
        )
        layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        self.title_label = QLabel("今日复习提醒")
        self.title_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; background-color: transparent;"
        )
        text_layout.addWidget(self.title_label)
        self.text_label = MathLabel("正在检查今日复习任务...")
        self.text_label.setStyleSheet(
            f"font-size: 13px; color: {c['fg_secondary']}; background-color: transparent;"
        )
        text_layout.addWidget(self.text_label)
        layout.addLayout(text_layout, 1)

        self.btn = QPushButton("开始复习")
        set_primary_button_style(self.btn)
        self.btn.setMinimumHeight(34)
        self.btn.setMinimumWidth(88)
        layout.addWidget(self.btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setToolTip("关闭")
        self.close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {c['fg_secondary']};
                font-size: 14px;
                font-weight: bold;
                padding: 4px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {c['accent_lighter']};
                color: {c['fg_primary']};
                border-radius: 6px;
            }}
            """
        )
        self.close_btn.clicked.connect(lambda: self.closed.emit())
        layout.addWidget(self.close_btn)

    def set_message(self, text: str) -> None:
        self.text_label.set_rich_text(text)


class _ModelStatusIndicator(QWidget):
    """总览页右上角模型/Ollama 状态指示器。"""

    def __init__(self, model_manager: Optional[ModelManager] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._model_manager = model_manager
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        c = theme_colors()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"font-size: 10px; color: {c['success']}; background-color: transparent;")
        layout.addWidget(self._dot)

        self._model_label = QLabel("模型加载中...")
        self._model_label.setStyleSheet(
            f"font-size: 12px; color: {c['fg_secondary']}; background-color: transparent;"
        )
        layout.addWidget(self._model_label)

        self.refresh()

    def refresh(self) -> None:
        c = theme_colors()
        try:
            if self._model_manager is None:
                self._model_label.setText("未连接模型管理器")
                self._dot.setStyleSheet(
                    f"font-size: 10px; color: {c['fg_secondary']}; background-color: transparent;"
                )
                return

            backend = self._model_manager.backend_type.value
            text_model = self._model_manager.config.get("models", {}).get("text_model", "未知模型")
            is_online = self._model_manager.is_text_available()

            if backend == "cloud_api":
                self._model_label.setText(f"API 模式 · {text_model}")
                self._dot.setStyleSheet(
                    f"font-size: 10px; color: {c['accent']}; background-color: transparent;"
                )
            else:
                status = "在线" if is_online else "离线"
                color = c["success"] if is_online else c["error"]
                self._model_label.setText(f"Ollama {status} · {text_model}")
                self._dot.setStyleSheet(
                    f"font-size: 10px; color: {color}; background-color: transparent;"
                )
        except Exception as e:
            logger.warning("Model status indicator refresh failed: %s", e)
            self._model_label.setText("状态未知")
            self._dot.setStyleSheet(
                f"font-size: 10px; color: {c['fg_secondary']}; background-color: transparent;"
            )


class DashboardPanel(QWidget):
    """总览面板。"""

    navigate_to = pyqtSignal(str)

    def __init__(
        self,
        chat_service: ChatService,
        errorbook_service: ErrorBookService,
        flashcard_service: FlashcardService,
        rag_service: Optional[RAGService] = None,
        study_plan_service: Optional[StudyPlanService] = None,
        statistics_service: Optional[StatisticsService] = None,
        tts_service: Optional[TTSService] = None,
        theme_manager: Optional[ThemeManager] = None,
        model_manager: Optional[ModelManager] = None,
        user_service: Optional[UserService] = None,
        review_service=None,
    ):
        super().__init__()
        self.chat_service = chat_service
        self.errorbook_service = errorbook_service
        self.flashcard_service = flashcard_service
        self.rag_service = rag_service
        self.study_plan_service = study_plan_service
        self.statistics_service = statistics_service
        self.tts_service = tts_service
        self.theme_manager = theme_manager
        self.model_manager = model_manager
        self.user_service = user_service
        self.review_service = review_service
        # 硬件分级：低配（≤7GB）隐藏图表，仅显示数字
        try:
            from ..config.hardware import detect_hardware, get_hardware_tier, HardwareTier
            hw = detect_hardware()
            # 与主窗口一致：尊重「强制低配模式」总开关
            force_low = False
            try:
                if self.user_service is not None:
                    row = self.user_service.db.fetchone(
                        "SELECT value FROM settings WHERE key = ?", ("force_low_spec",)
                    )
                    force_low = bool(
                        row and str(row["value"]).strip().lower() in ("1", "true", "yes", "on")
                    )
            except Exception:
                force_low = False
            self._hardware_tier = get_hardware_tier(hw, force_low_spec=force_low)
        except Exception:
            from ..config.hardware import HardwareTier
            self._hardware_tier = HardwareTier.LOW
        self._build_ui()
        self._apply_hardware_visibility()
        self.refresh_data()

    def _apply_hardware_visibility(self) -> None:
        """低配机器隐藏图表组件，仅保留数字统计，节省内存。"""
        try:
            from ..config.hardware import HardwareTier
            if self._hardware_tier == HardwareTier.LOW:
                for attr in ("duration_chart", "pie_chart", "bar_chart", "weak_chart", "progress_ring"):
                    w = getattr(self, attr, None)
                    if w is not None:
                        w.hide()
                logger.info("Low-spec hardware detected, dashboard charts hidden")
        except Exception as e:
            logger.warning("Failed to apply hardware visibility: %s", e)

    def _build_ui(self) -> None:
        c = theme_colors()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        _sb = theme_colors()
        scroll.setStyleSheet(
            "QScrollBar:vertical { width: 5px; background: transparent; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {_sb['border_strong']}; border-radius: 2px; min-height: 40px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        )
        main_layout.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        # 全局宽松留白
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(24)

        # 顶部状态指示器 + 欢迎语 + 演示数据入口
        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)
        welcome = QLabel("学习总览")
        welcome.setStyleSheet(
            "font-size: 18px; font-weight: bold; background-color: transparent;"
        )
        header_layout.addWidget(welcome)
        header_layout.addStretch()

        self.load_demo_btn = QPushButton("加载演示数据")
        set_primary_button_style(self.load_demo_btn)
        self.load_demo_btn.setMinimumHeight(34)
        self.load_demo_btn.setToolTip("导入演示错题、闪卡、学习计划与统计记录")
        self.load_demo_btn.clicked.connect(self._on_load_demo_data)
        header_layout.addWidget(self.load_demo_btn)

        self.reset_demo_btn = QPushButton("重置演示数据")
        self.reset_demo_btn.setMinimumHeight(34)
        self.reset_demo_btn.setToolTip("清空并重新导入演示数据")
        self.reset_demo_btn.clicked.connect(self._on_reset_demo_data)
        header_layout.addWidget(self.reset_demo_btn)

        self.refresh_btn = QPushButton("刷新数据")
        self.refresh_btn.setMinimumHeight(34)
        self.refresh_btn.setToolTip("手动刷新仪表盘数据")
        self.refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(self.refresh_btn)

        self.model_status = _ModelStatusIndicator(self.model_manager)
        header_layout.addWidget(self.model_status)
        layout.addLayout(header_layout)

        # 顶部 4 大统计卡片
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(20)
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.total_card = _StatCard(
            "总题数", "0", "错题 + 闪卡累计",
            color=c["accent"], fg_secondary=c["fg_secondary"],
        )
        self.mastered_card = _StatCard(
            "已掌握", "0", "错题掌握度 100%",
            color=c["success"], fg_secondary=c["fg_secondary"],
        )
        self.review_card = _StatCard(
            "待复习", "0", "今日到期闪卡",
            color=c["warning"], fg_secondary=c["fg_secondary"],
        )
        self.today_card = _StatCard(
            "今日学习", "0 分钟", "今日学习时长",
            color=c["accent"], fg_secondary=c["fg_secondary"],
        )
        for card in (self.total_card, self.mastered_card, self.review_card, self.today_card):
            cards_layout.addWidget(card)
        cards_layout.addStretch()
        layout.addLayout(cards_layout)


        # 专注模式（番茄钟）：学习行为的计时入口，时长写入统计后点亮热力图/周报
        from .pomodoro_widget import PomodoroWidget

        self.pomodoro = PomodoroWidget(statistics_service=self.statistics_service)
        self.pomodoro.session_recorded.connect(lambda _m, _s: self.refresh_data())
        layout.addWidget(self.pomodoro)

        # 今日队列：可解释的"今天学什么"
        queue_card = QWidget()
        queue_card.setProperty("card", True)
        apply_card_style(queue_card)
        queue_layout = QVBoxLayout(queue_card)
        queue_layout.setContentsMargins(20, 16, 20, 16)
        queue_layout.setSpacing(6)
        queue_header = QHBoxLayout()
        self.queue_title_label = create_section_title("今天学什么")
        queue_header.addWidget(self.queue_title_label)
        queue_header.addStretch()
        self.queue_date_label = QLabel("")
        self.queue_date_label.setObjectName("subtitle_label")
        queue_header.addWidget(self.queue_date_label)
        queue_layout.addLayout(queue_header)

        from .markdown_browser import MarkdownBrowser

        self.queue_browser = MarkdownBrowser()
        self.queue_browser.set_html(
            f"<div style='color:{c['fg_secondary']}; text-align:center; padding:12px 0;'>"
            "暂无推荐任务——录入错题或制定计划后这里会给出建议</div>"
        )
        queue_layout.addWidget(self.queue_browser, 1)
        layout.addWidget(queue_card)

        # 学习热力图（近半年）+ 连续天数
        heat_card = QWidget()
        heat_card.setProperty("card", True)
        apply_card_style(heat_card)
        heat_layout = QVBoxLayout(heat_card)
        heat_layout.setContentsMargins(20, 16, 20, 16)
        heat_layout.setSpacing(10)
        heat_header = QHBoxLayout()
        heat_title = create_section_title("学习热力")
        heat_header.addWidget(heat_title)
        heat_header.addStretch()
        self.goal_label = QLabel("今日 0 分钟")
        self.goal_label.setStyleSheet(
            f"font-size: 12px; color: {c['fg_secondary']}; background-color: transparent;"
        )
        heat_header.addWidget(self.goal_label)
        self.goal_bar = QProgressBar()
        self.goal_bar.setFixedWidth(140)
        self.goal_bar.setFixedHeight(8)
        self.goal_bar.setTextVisible(False)
        heat_header.addWidget(self.goal_bar)
        self.streak_label = QLabel("连续学习 0 天")
        self.streak_label.setObjectName("accent_label")
        heat_header.addWidget(self.streak_label)
        heat_layout.addLayout(heat_header)
        from .study_heatmap import StudyHeatMapWidget

        self.heatmap = StudyHeatMapWidget()
        self.heatmap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        heat_layout.addWidget(self.heatmap)
        layout.addWidget(heat_card)

        # 本周学习速览：本周 vs 上周（时长/错题/复习/正确率）
        weekly_card = QWidget()
        weekly_card.setProperty("card", True)
        apply_card_style(weekly_card)
        weekly_layout = QVBoxLayout(weekly_card)
        weekly_layout.setContentsMargins(20, 16, 20, 16)
        weekly_layout.setSpacing(8)
        weekly_title = create_section_title("本周学习速览")
        weekly_layout.addWidget(weekly_title)
        self.weekly_label = MathLabel("暂无数据")
        self.weekly_label.setObjectName("subtitle_label")
        weekly_layout.addWidget(self.weekly_label)
        layout.addWidget(weekly_card)

        # 错因画像：四象限归因 + 训练处方
        cause_card = QWidget()
        cause_card.setProperty("card", True)
        apply_card_style(cause_card)
        cause_layout = QVBoxLayout(cause_card)
        cause_layout.setContentsMargins(20, 16, 20, 16)
        cause_layout.setSpacing(8)
        cause_header = QHBoxLayout()
        cause_header.addWidget(create_section_title("错因画像"))
        cause_header.addStretch()
        self.cause_total_label = QLabel("")
        self.cause_total_label.setObjectName("subtitle_label")
        cause_header.addWidget(self.cause_total_label)
        report_btn = QPushButton("导出家长报告")
        set_primary_button_style(report_btn)
        report_btn.setFixedHeight(28)
        report_btn.setToolTip("本地生成 PDF 周报（概览/错因/薄弱点/建议），数据不出门")
        report_btn.clicked.connect(self._export_parent_report)
        cause_header.addWidget(report_btn)
        cause_layout.addLayout(cause_header)
        self.cause_rows_layout = QVBoxLayout()
        self.cause_rows_layout.setSpacing(6)
        cause_layout.addLayout(self.cause_rows_layout)
        self.cause_empty_label = MathLabel("暂无错题数据——录入错题后这里会给出错因分析与训练处方")
        self.cause_empty_label.setObjectName("subtitle_label")
        cause_layout.addWidget(self.cause_empty_label)
        layout.addWidget(cause_card)


        # 复习提醒卡片
        self.reminder_card = _ReminderCard()
        self.reminder_card.btn.clicked.connect(lambda: self.navigate_to.emit("flashcard"))
        self.reminder_card.closed.connect(lambda: self.reminder_card.hide())
        layout.addWidget(self.reminder_card)
        self.reminder_card.hide()

        # 学习计划模块
        if self.study_plan_service is not None:
            self.plan_card = QWidget()
            self.plan_card.setProperty("card", True)
            apply_card_style(self.plan_card)
            plan_layout = QVBoxLayout(self.plan_card)
            plan_layout.setContentsMargins(22, 22, 22, 22)
            plan_layout.setSpacing(16)

            plan_header = QHBoxLayout()
            plan_title = create_section_title("AI 学习计划")
            plan_header.addWidget(plan_title)
            plan_header.addStretch()
            new_plan_btn = QPushButton("+ 新建 AI 学习计划")
            set_primary_button_style(new_plan_btn)
            new_plan_btn.setMinimumHeight(34)
            new_plan_btn.clicked.connect(lambda: self.navigate_to.emit("study_plan"))
            plan_header.addWidget(new_plan_btn)
            plan_layout.addLayout(plan_header)

            self.plan_stack = QWidget()
            plan_stack_layout = QVBoxLayout(self.plan_stack)
            plan_stack_layout.setContentsMargins(0, 0, 0, 0)

            # 空状态
            self.plan_empty = create_empty_state_widget(
                text="暂无学习计划，点击右上角「+ 新建 AI 学习计划」开始",
                icon="✦",
            )
            plan_stack_layout.addWidget(self.plan_empty)

            # 数据表格
            self.plan_tree = QTreeWidget()
            self.plan_tree.setHeaderLabels(["计划", "进度", "状态"])
            self.plan_tree.setColumnWidth(0, 320)
            self.plan_tree.setColumnWidth(1, 220)
            self.plan_tree.setColumnWidth(2, 100)
            self.plan_tree.setMinimumHeight(160)
            self.plan_tree.hide()
            # 计划标题列可能含公式，单元格走混排渲染（进度/状态列是自定义控件，不动）
            self._plan_math_delegate = attach_math_delegate(self.plan_tree, 0)
            plan_stack_layout.addWidget(self.plan_tree)

            plan_layout.addWidget(self.plan_stack, 1)
            layout.addWidget(self.plan_card, 1)

        # 图表区
        charts_grid = QWidget()
        grid_layout = QGridLayout(charts_grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(20)

        # 掌握度进度环
        ring_widget = self._build_chart_card("错题掌握度")
        ring_layout = ring_widget.layout()
        self.progress_ring = _ProgressRing()
        self.progress_ring.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ring_layout.addWidget(self.progress_ring, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        self.mastered_hint = QLabel("已掌握 0 / 错题总数 0")
        self.mastered_hint.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 12px;")
        self.mastered_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ring_layout.addWidget(self.mastered_hint)
        grid_layout.addWidget(ring_widget, 0, 0)

        # 学习时长折线图
        duration_widget = self._build_chart_card("每日学习时长")
        duration_layout = duration_widget.layout()
        self.duration_chart = _LineChart("每日学习时长（分钟）")
        self.duration_chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        duration_layout.addWidget(self.duration_chart)
        self.duration_empty = create_empty_state_widget("暂无学习时长数据，开始学习生成记录", icon="◆")
        self.duration_empty.hide()
        duration_layout.addWidget(self.duration_empty)
        grid_layout.addWidget(duration_widget, 0, 1)

        # 错题科目分布饼图
        pie_widget = self._build_chart_card("各科错题分布")
        pie_layout = pie_widget.layout()
        self.pie_chart = _PieChart("各科错题分布")
        self.pie_chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pie_layout.addWidget(self.pie_chart)
        self.pie_empty = create_empty_state_widget("暂无错题分布数据", icon="◆")
        self.pie_empty.hide()
        pie_layout.addWidget(self.pie_empty)
        grid_layout.addWidget(pie_widget, 1, 0)

        # 闪卡正确率柱状图
        bar_widget = self._build_chart_card("闪卡复习正确率")
        bar_layout = bar_widget.layout()
        self.bar_chart = _BarChart("闪卡复习正确率")
        self.bar_chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        bar_layout.addWidget(self.bar_chart)
        self.bar_empty = create_empty_state_widget("暂无闪卡正确率数据", icon="◆")
        self.bar_empty.hide()
        bar_layout.addWidget(self.bar_empty)
        grid_layout.addWidget(bar_widget, 1, 1)

        layout.addWidget(charts_grid, 1)

        # 底部建议 + 薄弱知识点
        # 底部建议 + 薄弱知识点
        bottom_area = QWidget()
        bottom_layout = QHBoxLayout(bottom_area)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(20)

        # 今日建议
        tip_card = QWidget()
        tip_card.setProperty("card", True)
        apply_card_style(tip_card)
        tip_layout = QHBoxLayout(tip_card)
        tip_layout.setContentsMargins(20, 16, 20, 16)
        tip_layout.setSpacing(10)
        tip_icon = QLabel("◉")
        tip_icon.setStyleSheet(
            f"font-size: 18px; color: {c['accent']}; background-color: transparent;"
        )
        tip_layout.addWidget(tip_icon)
        tip_text = QLabel("今日建议：根据闪卡复习进度，优先完成薄弱知识点")
        tip_text.setStyleSheet(
            f"color: {c['fg_secondary']}; font-size: 13px; background-color: transparent;"
        )
        tip_text.setWordWrap(True)
        tip_layout.addWidget(tip_text, 1)
        bottom_layout.addWidget(tip_card, 1)

        # 薄弱知识点 TOP5
        weak_widget = self._build_chart_card("薄弱知识点 TOP5")
        weak_layout = weak_widget.layout()
        self.weak_chart = _HorizontalBarChart("错误次数")
        self.weak_chart.setMinimumHeight(180)
        self.weak_chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        weak_layout.addWidget(self.weak_chart)
        self.weak_empty = create_empty_state_widget("暂无薄弱知识点数据", icon="◆")
        self.weak_empty.hide()
        weak_layout.addWidget(self.weak_empty)
        bottom_layout.addWidget(weak_widget, 2)

        layout.addWidget(bottom_area, 1)

    def _build_chart_card(self, title: str) -> QWidget:
        """构建统一风格的图表卡片：8px 圆角、充足内边距、预留空白。"""
        widget = QWidget()
        widget.setProperty("card", True)
        apply_card_style(widget)
        widget.setMinimumHeight(240)
        widget.setMaximumHeight(300)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(widget)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName("section_title")
        layout.addWidget(title_label)
        return widget

    def _run_demo_import(self, reset: bool) -> None:
        """执行演示数据导入并显示进度动画，导入完成后刷新仪表盘。"""
        progress = QProgressDialog(self)
        progress.setLabelText("正在导入演示数据，请稍候...")
        progress.setWindowTitle("演示数据")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setMinimum(0)
        progress.setMaximum(0)
        progress.setCancelButton(None)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        try:
            demo_service = DemoService(
                model_manager=self.model_manager,
                user_service=self.user_service,
            )
            counts = demo_service.load_demo_data(reset=reset)
            self.refresh_data()
            progress.close()
            if reset:
                show_info(
                    self,
                    "演示数据已重置",
                    f"已重新导入 {counts['errors']} 道错题、{counts['flashcards']} 张闪卡、"
                    f"{counts['plans']} 个学习计划。",
                )
            else:
                show_info(
                    self,
                    "演示数据已加载",
                    f"已导入 {counts['errors']} 道错题、{counts['flashcards']} 张闪卡、"
                    f"{counts['plans']} 个学习计划，"
                    f"学习时长 {counts['study_minutes']} 分钟、复习记录 {counts['reviews']} 条。",
                )
        except Exception as e:
            logger.error("Demo data import failed: %s", e)
            progress.close()
            show_error(self, "导入失败", f"导入演示数据失败：\n{e}")

    def _on_load_demo_data(self) -> None:
        """加载演示数据（追加模式，不清理已有数据）。"""
        self._run_demo_import(reset=False)

    def _on_reset_demo_data(self) -> None:
        """重置演示数据：会清空当前用户全部学习数据，必须二次确认。"""
        from .ui_utils import ask_confirm

        confirmed = ask_confirm(
            self,
            "确认重置",
            "重置将清空当前用户的全部错题、闪卡、学习计划与统计记录，"
            "并重新导入演示数据。此操作不可恢复，确定继续？",
        )
        if not confirmed:
            return
        self._run_demo_import(reset=True)

    def showEvent(self, event) -> None:
        self.refresh_data()
        super().showEvent(event)

    def on_theme_changed(self) -> None:
        """主题切换后刷新：图表绘制实时取色，其余样式整树 re-polish。"""
        repolish_tree(self)
        self.update()
        self.repaint()

    def refresh_data(self) -> None:
        """刷新仪表盘所有数据。"""
        try:
            self._refresh_cards()
            self._refresh_plans()
            self._refresh_charts()
            self._refresh_heatmap()
            self._refresh_weekly()
            self._refresh_queue()
            self._refresh_cause_profile()
            self._refresh_queue()
            if hasattr(self, "model_status"):
                self.model_status.refresh()
            # 强制立即重绘，避免数据更新后界面未刷新
            self.repaint()
            QApplication.processEvents()
        except Exception as e:
            logger.error("Failed to refresh dashboard: %s", e)

    def _refresh_cards(self) -> None:
        summary = {"total_errors": 0, "mastered": 0, "due_today": 0, "today_minutes": 0}
        if self.statistics_service is not None:
            summary = self.statistics_service.get_dashboard_summary()
        total_flashcards = 0
        if self.flashcard_service is not None:
            total_flashcards = self.flashcard_service.get_stats().get("total", 0)

        due_today = 0
        if self.flashcard_service is not None:
            due_today = self.flashcard_service.get_stats().get("due_today", 0)
        # 叠加艾宾浩斯复习系统待复习数量
        if self.review_service is not None:
            try:
                due_today += self.review_service.get_due_count()
            except Exception as e:
                logger.warning("Failed to get review due count: %s", e)

        self.total_card.set_value(str(summary.get("total_errors", 0) + total_flashcards))
        self.mastered_card.set_value(str(summary.get("mastered", 0)))
        self.review_card.set_value(str(due_today))
        self.today_card.set_value(f"{summary.get('today_minutes', 0)} 分钟")

        total_errors = summary.get("total_errors", 0)
        mastered = summary.get("mastered", 0)
        percentage = (mastered / total_errors * 100.0) if total_errors > 0 else 0.0
        self.progress_ring.set_percentage(percentage)
        self.mastered_hint.setText(f"已掌握 {mastered} / 错题总数 {total_errors}")

        self._update_review_reminder()

    def _refresh_heatmap(self) -> None:
        """刷新半年学习热力图、每日目标进度与连续天数。"""
        try:
            if self.statistics_service is None:
                return
            records = self.statistics_service.get_daily_study_duration(days=182)
            self.heatmap.set_data(records)
            streak = self.statistics_service.get_study_streak()
            self.streak_label.setText(f"🔥 连续学习 {streak} 天")

            from ..config import load_config
            from ..core.constants import DEFAULT_DAILY_GOAL_MINUTES

            goal = int(
                load_config().get("ui", {}).get(
                    "daily_goal_minutes", DEFAULT_DAILY_GOAL_MINUTES
                )
            )
            today_minutes = int(records[-1]["minutes"]) if records else 0
            self.goal_label.setText(f"今日 {today_minutes}/{goal} 分钟")
            self.goal_bar.setRange(0, max(1, goal))
            self.goal_bar.setValue(min(today_minutes, goal))
        except Exception as e:
            logger.warning("Failed to refresh heatmap: %s", e)

    def _export_parent_report(self) -> None:
        """导出家长/教师 PDF 周报（本地生成）。"""
        from datetime import date
        from pathlib import Path as _Path

        from PyQt6.QtWidgets import QFileDialog

        from .ui_utils import show_success

        default_name = f"PLOS_AI_学习周报_{date.today().isoformat()}.pdf"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存家长报告", default_name, "PDF 报告 (*.pdf)"
        )
        if not file_path:
            return
        try:
            from ..services.report_service import ReportService

            service = ReportService(
                db=None,
                user_service=self.user_service,
                statistics_service=self.statistics_service,
                errorbook_service=self.errorbook_service,
                cause_profile_service=None,
                smart_queue_service=None,
            )
            out = service.generate(_Path(file_path), user_id=self._current_user_id())
            show_success(self, "报告已生成", f"PDF 已保存到：\n{out}")
        except Exception as e:
            logger.error("Failed to export parent report: %s", e)
            show_error(self, "导出失败", str(e))

    def _current_user_id(self) -> int:
        try:
            return self.user_service.get_current_user_id()
        except Exception:
            return 0

    def _refresh_cause_profile(self) -> None:
        """刷新错因画像四象限（色点 + 数量占比 + 处方）。"""
        try:
            if self.errorbook_service is None:
                return
            from ..services.cause_profile_service import CauseProfileService
            from .ui_utils import create_icon_chip

            while self.cause_rows_layout.count():
                item = self.cause_rows_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            c = theme_colors()
            service = CauseProfileService(
                user_service=self.user_service,
                errorbook_service=self.errorbook_service,
            )
            profile = service.get_cause_profile()
            total = profile["total"]
            self.cause_total_label.setText(f"共分析 {total} 道错题" if total else "")
            self.cause_empty_label.setVisible(not total)
            for quad in profile["quadrants"]:
                if quad["count"] == 0:
                    continue
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 2, 0, 2)
                row_layout.setSpacing(10)
                color = c.get(quad["color"], c["accent"])
                row_layout.addWidget(
                    create_icon_chip(quad["label"][:1], size=22, fg=color,
                                     bg=c["accent_lighter"], font_size=11)
                )
                label = MathLabel(
                    f"<b>{quad['label']}</b>　{quad['count']} 题（{quad['ratio']:.0f}%）　"
                    f"<span style='color:{c['fg_secondary']};'>{quad['prescription']}</span>",
                    markdown=True,
                )
                row_layout.addWidget(label, 1)
                self.cause_rows_layout.addWidget(row)
        except Exception as e:
            logger.warning("Failed to refresh cause profile: %s", e)

    def _refresh_queue(self) -> None:
        """刷新今日学习队列（每项点击跳转对应面板）。"""
        try:
            if self.statistics_service is None:
                return
            from .ui_utils import theme_colors as _tc

            c = _tc()
            from ..services.smart_queue_service import SmartQueueService
            from .ui_utils import create_icon_chip  # noqa: F401  行渲染用

            service = SmartQueueService(
                user_service=self.user_service,
                flashcard_service=self.flashcard_service,
                errorbook_service=self.errorbook_service,
            )
            queue = service.build_daily_queue()
            self.queue_date_label.setText(f"更新于 {queue['date']}")
            items = queue["items"]
            if not items:
                self.queue_browser.set_html(
                    f"<div style='color:{c['fg_secondary']}; text-align:center; padding:12px 0;'>"
                    "暂无推荐任务——录入错题或制定计划后这里会给出建议</div>"
                )
                return
            tag_color = {"flashcard": c["accent"], "weak_review": c["error"],
                         "plan_task": c["teal"]}
            parts = []
            for item in items:
                tc = tag_color.get(item["type"], c["accent"])
                title = str(item["title"]).replace("<", "&lt;")
                reason = str(item["reason"]).replace("<", "&lt;")
                parts.append(
                    f"<div style='margin:6px 0; padding:6px 10px; background-color:{c['accent_lighter']};"
                    f" border-radius:8px;'>"
                    f"<span style='color:{tc}; font-weight:600;'>[{item['type_label']}]</span> "
                    f"<span style='color:{c['fg_primary']};'>{title}</span><br/>"
                    f"<span style='color:{c['fg_secondary']}; font-size:11px;'>{reason}</span>"
                    f"<a href='{item.get('navigate', 'dashboard')}' style='color:{tc}; text-decoration:none;'>"
                    f"　前往 →</a></div>"
                )
            self.queue_browser.set_html("".join(parts))
        except Exception as e:
            logger.warning("Failed to refresh queue: %s", e)

    def _refresh_weekly(self) -> None:
        """刷新本周学习速览。"""
        try:
            if self.statistics_service is None:
                return
            r = self.statistics_service.get_weekly_report()

            def trend(now: float, prev: float) -> str:
                if prev <= 0:
                    return f"上周 {prev:.0f}"
                pct = (now - prev) / prev * 100.0
                arrow = "↑" if pct >= 0 else "↓"
                return f"上周 {prev:.0f}，{arrow} {abs(pct):.0f}%"

            self.weekly_label.set_rich_text(
                f"⏱ 专注 {r['minutes']:.0f} 分钟（{trend(r['minutes'], r['minutes_prev'])}）    "
                f"✚ 新增错题 {r['errors']}（{trend(r['errors'], r['errors_prev'])}）    "
                f"🔁 复习 {r['reviews']} 张（{trend(r['reviews'], r['reviews_prev'])}）    "
                f"✓ 复习正确率 {r['accuracy']:.0f}%（上周 {r['accuracy_prev']:.0f}%）"
            )
        except Exception as e:
            logger.warning("Failed to refresh weekly report: %s", e)

    def _update_review_reminder(self) -> None:
        """检查并显示今日到期闪卡提醒。"""
        if self.flashcard_service is None:
            self.reminder_card.hide()
            return
        try:
            due_count = self.flashcard_service.get_stats().get("due_today", 0)
            if due_count > 0:
                self.reminder_card.set_message(
                    f"今日有 {due_count} 张闪卡到期，坚持复习巩固记忆！"
                )
                self.reminder_card.show()
            else:
                self.reminder_card.hide()
        except Exception as e:
            logger.warning("Failed to update review reminder: %s", e)
            self.reminder_card.hide()

    def _refresh_plans(self) -> None:
        if self.study_plan_service is None or not hasattr(self, "plan_tree"):
            return
        self.plan_tree.clear()
        plans = self.study_plan_service.list_plans()

        if not plans:
            self.plan_empty.show()
            self.plan_tree.hide()
            return

        self.plan_empty.hide()
        self.plan_tree.show()

        for plan in plans:
            item = QTreeWidgetItem()
            item.setText(0, plan.get("title", ""))
            progress = plan.get("progress", 0)
            progress_bar = QProgressBar()
            progress_bar.setValue(progress)
            progress_bar.setTextVisible(True)
            progress_bar.setFormat(f"{progress}%")
            progress_bar.setMinimumHeight(18)
            item.setText(2, "")
            self.plan_tree.addTopLevelItem(item)
            self.plan_tree.setItemWidget(item, 1, progress_bar)
            status_widget = _StatusBadge(plan.get("status", "进行中"))
            self.plan_tree.setItemWidget(item, 2, status_widget)

    def _refresh_charts(self) -> None:
        if self.statistics_service is None:
            return
        # 低配（≤7GB 或强制低配）：图表组件保持隐藏，只保留文字统计，节省内存。
        # 必须在此拦截，否则下方按数据有无调用的 chart.show() 会覆盖
        # _apply_hardware_visibility() 所做的 hide()，导致低配降级失效。
        if getattr(self, "_hardware_tier", None) is not None:
            from ..config.hardware import HardwareTier
            if self._hardware_tier == HardwareTier.LOW:
                for _chart_attr, _empty_attr in (
                    ("duration_chart", "duration_empty"),
                    ("pie_chart", "pie_empty"),
                    ("bar_chart", "bar_empty"),
                    ("weak_chart", "weak_empty"),
                ):
                    _cw = getattr(self, _chart_attr, None)
                    if _cw is not None:
                        _cw.hide()
                logger.info("Low-spec mode: dashboard charts kept hidden during refresh")
                return
        # 学习时长
        duration = self.statistics_service.get_daily_study_duration(days=7)
        if duration:
            self.duration_chart.show()
            self.duration_empty.hide()
            self.duration_chart.set_data(
                [d["date"][-5:] for d in duration],
                [d["minutes"] for d in duration],
            )
        else:
            self.duration_chart.hide()
            self.duration_empty.show()

        # 错题分布
        distribution = self.statistics_service.get_error_subject_distribution()
        if distribution:
            self.pie_chart.show()
            self.pie_empty.hide()
            self.pie_chart.set_data(distribution)
        else:
            self.pie_chart.hide()
            self.pie_empty.show()

        # 闪卡正确率
        accuracy = self.statistics_service.get_flashcard_accuracy(days=7)
        daily = accuracy.get("daily", [])
        if daily:
            self.bar_chart.show()
            self.bar_empty.hide()
            self.bar_chart.set_data(
                [d["date"][-3:] for d in daily],
                [d["accuracy"] for d in daily],
            )
        else:
            self.bar_chart.hide()
            self.bar_empty.show()

        # 薄弱知识点 TOP5
        if self.errorbook_service is not None:
            weak_points = self.errorbook_service.get_weak_knowledge_points(top_n=5)
            if weak_points:
                self.weak_chart.show()
                self.weak_empty.hide()
                self.weak_chart.set_data(
                    [p["knowledge_point"] for p in weak_points],
                    [p["error_count"] for p in weak_points],
                )
            else:
                self.weak_chart.hide()
                self.weak_empty.show()
