"""主题管理器。

管理全局 QSS 样式与暗色/亮色切换，提供统一的设计系统令牌。
设计语言 "Aurora"：三阶背景层次 + 大圆角卡片 + 鸢尾蓝紫渐变主色，
微软雅黑 UI 字体，focus 光圈与 hover 反馈，深浅双主题同源令牌。
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

from ..utils.logger import get_logger

logger = get_logger("ui.theme_manager")


# 暗色主题设计令牌（Aurora Night）
_DARK_COLORS = {
    # 三阶背景：画布 < 卡片 < 悬浮
    "bg_primary": "#0E1118",
    "bg_secondary": "#161B26",
    "bg_tertiary": "#1E2534",
    "sidebar_bg": "#0B0E14",
    "fg_primary": "#E9ECF4",
    "fg_secondary": "#9AA4B8",
    "fg_muted": "#6B7488",
    # 主色：鸢尾蓝紫（accent_gradient_end 为渐变主按钮的第二色）
    "accent": "#6C7CFF",
    "accent_hover": "#8290FF",
    "accent_pressed": "#5A69F5",
    "accent_gradient_end": "#9F7CFF",
    "accent_light": "rgba(108, 124, 255, 0.16)",
    "accent_lighter": "rgba(108, 124, 255, 0.08)",
    "accent_border": "rgba(108, 124, 255, 0.45)",
    "success": "#34D399",
    "warning": "#FBBF24",
    "error": "#F87171",
    "info": "#38BDF8",
    "teal": "#2DD4BF",
    "purple": "#A78BFA",
    "border": "rgba(148, 163, 196, 0.13)",
    "border_strong": "rgba(148, 163, 196, 0.26)",
    "card_bg": "#161B26",
    "selection": "rgba(108, 124, 255, 0.30)",
    "shadow": "rgba(0, 0, 0, 0.40)",
    "chart_1": "#6C7CFF",
    "chart_2": "#34D399",
    "chart_3": "#FBBF24",
    "chart_4": "#F87171",
    "chart_5": "#38BDF8",
    "chart_6": "#A78BFA",
}

# 亮色主题设计令牌（Aurora Daylight）
_LIGHT_COLORS = {
    "bg_primary": "#F6F7FB",
    "bg_secondary": "#FFFFFF",
    "bg_tertiary": "#EEF0F7",
    "sidebar_bg": "#FFFFFF",
    "fg_primary": "#1A2030",
    "fg_secondary": "#5A6478",
    "fg_muted": "#8A93A8",
    "accent": "#4F5DF5",
    "accent_hover": "#6673FF",
    "accent_pressed": "#4350E8",
    "accent_gradient_end": "#7C5CF0",
    "accent_light": "rgba(79, 93, 245, 0.10)",
    "accent_lighter": "rgba(79, 93, 245, 0.05)",
    "accent_border": "rgba(79, 93, 245, 0.40)",
    "success": "#10B981",
    "warning": "#F59E0B",
    "error": "#EF4444",
    "info": "#0EA5E9",
    "teal": "#14B8A6",
    "purple": "#8B5CF6",
    "border": "rgba(20, 27, 51, 0.10)",
    "border_strong": "rgba(20, 27, 51, 0.18)",
    "card_bg": "#FFFFFF",
    "selection": "rgba(79, 93, 245, 0.14)",
    "shadow": "rgba(23, 30, 60, 0.10)",
    "chart_1": "#4F5DF5",
    "chart_2": "#10B981",
    "chart_3": "#F59E0B",
    "chart_4": "#EF4444",
    "chart_5": "#0EA5E9",
    "chart_6": "#8B5CF6",
}


def _build_stylesheet(colors: dict) -> str:
    """根据颜色表构建 Aurora 全局 QSS。"""
    c = colors
    return """
    /* ===== 全局基础 ===== */
    QWidget {
        background-color: %(bg_primary)s;
        color: %(fg_primary)s;
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                     "Noto Sans SC", "Segoe UI", sans-serif;
        font-size: %(base_font_size)s;
        selection-background-color: %(selection)s;
        selection-color: %(fg_primary)s;
    }

    QMainWindow {
        background-color: %(bg_primary)s;
    }

    QToolTip {
        background-color: %(bg_tertiary)s;
        color: %(fg_primary)s;
        border: 1px solid %(border_strong)s;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: %(subtitle_font_size)s;
    }

    QSplitter::handle {
        background-color: transparent;
    }

    /* ===== 底部状态栏（后台任务进度常驻区） ===== */
    QStatusBar {
        background-color: %(bg_secondary)s;
        color: %(fg_secondary)s;
        border-top: 1px solid %(border)s;
    }
    QStatusBar::item {
        border: none;
    }

    /* ===== 卡片与分组框（大圆角、细描边） ===== */
    QWidget[glass="true"],
    QWidget[card="true"] {
        background-color: %(card_bg)s;
        border: 1px solid %(border)s;
        border-radius: %(card_border_radius)s;
    }

    QGroupBox {
        background-color: %(card_bg)s;
        border: 1px solid %(border)s;
        border-radius: %(card_border_radius)s;
        margin-top: %(group_title_top)s;
        padding-top: %(group_title_top)s;
        padding-left: 14px;
        padding-right: 14px;
        padding-bottom: 14px;
        font-weight: 600;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 16px;
        padding: 0 8px;
        color: %(fg_primary)s;
    }

    /* ===== 主操作按钮：渐变主色 ===== */
    QPushButton#primary_btn {
        background-color: %(accent)s;
        color: #FFFFFF;
        border: none;
        border-radius: %(btn_border_radius)s;
        padding: %(primary_padding)s;
        font-weight: 600;
    }
    QPushButton#primary_btn:hover {
        background-color: %(accent_hover)s;
    }
    QPushButton#primary_btn:pressed {
        background-color: %(accent_pressed)s;
        /* 苹果式按压质感：内容轻微内缩（约 0.97 倍观感），松开瞬时恢复 */
        margin: 1px;
    }
    QPushButton#primary_btn:disabled {
        background-color: %(bg_tertiary)s;
        color: %(fg_muted)s;
    }

    /* ===== 危险按钮 ===== */
    QPushButton#danger_btn {
        background-color: rgba(239, 68, 68, 0.10);
        color: %(error)s;
        border: 1px solid rgba(239, 68, 68, 0.35);
        border-radius: %(btn_border_radius)s;
        padding: %(btn_padding)s;
        font-weight: 600;
    }
    QPushButton#danger_btn:hover {
        background-color: %(error)s;
        color: #FFFFFF;
    }
    QPushButton#danger_btn:pressed {
        background-color: %(error)s;
        margin: 1px;
    }
    QPushButton#danger_btn:disabled {
        background-color: %(bg_tertiary)s;
        color: %(fg_muted)s;
        border-color: %(border)s;
    }

    /* ===== 普通/幽灵按钮 ===== */
    QPushButton {
        background-color: %(accent_lighter)s;
        color: %(fg_primary)s;
        border: 1px solid %(border)s;
        border-radius: %(btn_border_radius)s;
        padding: %(btn_padding)s;
    }
    QPushButton:hover {
        background-color: %(accent_light)s;
        border: 1px solid %(accent_border)s;
        color: %(fg_primary)s;
    }
    QPushButton:pressed {
        background-color: %(accent_light)s;
        /* 苹果式按压质感：内容轻微内缩（约 0.97 倍观感），松开瞬时平滑恢复 */
        margin: 1px;
    }
    QPushButton:disabled {
        background-color: %(bg_tertiary)s;
        color: %(fg_muted)s;
        border-color: %(border)s;
    }

    /* ===== 输入框：focus 光圈 ===== */
    QLineEdit, QTextEdit, QPlainTextEdit {
        background-color: %(bg_secondary)s;
        color: %(fg_primary)s;
        border: 1px solid %(border_strong)s;
        border-radius: %(input_border_radius)s;
        padding: %(input_padding)s;
        selection-background-color: %(selection)s;
    }
    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {
        border: 1px solid %(accent_border)s;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
        border: 1px solid %(accent)s;
        background-color: %(bg_secondary)s;
    }
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
        background-color: %(bg_tertiary)s;
        color: %(fg_muted)s;
    }

    /* ===== 下拉框 ===== */
    QComboBox {
        background-color: %(bg_secondary)s;
        color: %(fg_primary)s;
        border: 1px solid %(border_strong)s;
        border-radius: %(input_border_radius)s;
        padding: %(combo_padding)s;
        min-width: 80px;
    }
    QComboBox:hover {
        border: 1px solid %(accent_border)s;
    }
    QComboBox:focus {
        border: 1px solid %(accent)s;
    }
    QComboBox::drop-down {
        border: none;
        width: 24px;
    }
    QComboBox QAbstractItemView {
        background-color: %(card_bg)s;
        color: %(fg_primary)s;
        border: 1px solid %(border_strong)s;
        selection-background-color: %(selection)s;
        border-radius: %(input_border_radius)s;
        outline: 0;
    }

    /* ===== 数字输入框 ===== */
    QSpinBox, QDoubleSpinBox {
        background-color: %(bg_secondary)s;
        color: %(fg_primary)s;
        border: 1px solid %(border_strong)s;
        border-radius: %(input_border_radius)s;
        padding-right: 20px;
        min-height: 36px;
        padding-left: 12px;
    }
    QSpinBox:focus, QDoubleSpinBox:focus {
        border: 1px solid %(accent)s;
    }
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
        background-color: %(bg_tertiary)s;
        border: none;
        width: 20px;
    }
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
        background-color: %(accent_light)s;
    }

    /* ===== 复选框/单选框 ===== */
    QCheckBox, QRadioButton {
        color: %(fg_primary)s;
        spacing: 8px;
    }
    QCheckBox::indicator, QRadioButton::indicator {
        width: %(checkbox_size)s;
        height: %(checkbox_size)s;
        border-radius: 5px;
        border: 1px solid %(border_strong)s;
        background-color: %(bg_secondary)s;
    }
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {
        border: 1px solid %(accent_border)s;
    }
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {
        background-color: %(accent)s;
        border-color: %(accent)s;
    }
    QRadioButton::indicator {
        border-radius: 9px;
    }

    /* ===== 表格/列表/树 ===== */
    QTableWidget, QTableView, QListWidget, QListView, QTreeView, QTreeWidget {
        background-color: %(bg_secondary)s;
        color: %(fg_primary)s;
        border: 1px solid %(border)s;
        border-radius: %(input_border_radius)s;
        gridline-color: %(border)s;
        selection-background-color: %(selection)s;
        selection-color: %(fg_primary)s;
        outline: 0;
    }
    QHeaderView::section {
        background-color: %(bg_tertiary)s;
        color: %(fg_secondary)s;
        border: none;
        border-bottom: 1px solid %(border)s;
        padding: 8px;
        font-weight: 600;
    }
    QTableWidget::item:selected, QListWidget::item:selected {
        background-color: %(selection)s;
        border-radius: 6px;
    }
    QTableWidget::item:hover, QListWidget::item:hover {
        background-color: %(accent_lighter)s;
    }
    QListWidget {
        padding: 6px;
    }
    QListWidget::item {
        border-radius: 6px;
        padding: 6px;
        margin: 3px 0px;
    }

    /* ===== 标签 ===== */
    QLabel {
        color: %(fg_primary)s;
        background-color: transparent;
    }
    QLabel#subtitle_label {
        color: %(fg_secondary)s;
        font-size: %(subtitle_font_size)s;
    }
    QLabel#header_title {
        font-size: %(header_font_size)s;
        font-weight: 700;
        color: %(fg_primary)s;
    }
    QLabel#section_title {
        font-size: 15px;
        font-weight: bold;
        color: %(fg_primary)s;
        background-color: transparent;
    }
    QLabel#value_label {
        font-weight: 600;
        font-size: 12px;
        color: %(fg_primary)s;
        background-color: transparent;
    }
    QLabel#accent_label {
        font-size: 12px;
        font-weight: 600;
        color: %(accent)s;
        background-color: transparent;
    }

    /* ===== 滚动条：纤细圆角 ===== */
    QScrollBar:vertical {
        background-color: transparent;
        width: %(scrollbar_size)s;
        border-radius: 4px;
        margin: 2px;
    }
    QScrollBar::handle:vertical {
        background-color: %(border_strong)s;
        border-radius: 4px;
        min-height: 28px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: %(fg_muted)s;
    }
    QScrollBar::handle:vertical:pressed {
        background-color: %(accent)s;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical {
        background: transparent;
    }
    QScrollBar:horizontal {
        background-color: transparent;
        height: %(scrollbar_size)s;
        border-radius: 4px;
        margin: 2px;
    }
    QScrollBar::handle:horizontal {
        background-color: %(border_strong)s;
        border-radius: 4px;
        min-width: 28px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: %(fg_muted)s;
    }
    QScrollBar::handle:horizontal:pressed {
        background-color: %(accent)s;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        height: 0px;
    }
    QScrollBar::sub-page:horizontal, QScrollBar::add-page:horizontal {
        background: transparent;
    }

    /* ===== 进度条/滑块 ===== */
    QProgressBar {
        border: none;
        border-radius: 4px;
        text-align: center;
        background-color: %(bg_tertiary)s;
        color: %(fg_primary)s;
        min-height: 8px;
    }
    QProgressBar::chunk {
        background-color: %(accent)s;
        border-radius: 4px;
    }
    QSlider::groove:horizontal {
        height: 4px;
        background-color: %(bg_tertiary)s;
        border-radius: 2px;
    }
    QSlider::handle:horizontal {
        background-color: %(accent)s;
        width: 14px;
        height: 14px;
        border-radius: 7px;
        margin: -5px 0;
    }
    QSlider::sub-page:horizontal {
        background-color: %(accent)s;
        border-radius: 2px;
    }

    /* ===== Tab ===== */
    QTabWidget::pane {
        border: 1px solid %(border)s;
        background-color: %(bg_primary)s;
        border-radius: %(card_border_radius)s;
    }
    QTabBar::tab {
        background-color: transparent;
        color: %(fg_secondary)s;
        border: none;
        border-bottom: 2px solid transparent;
        padding: %(tab_padding)s;
        margin: 2px;
    }
    QTabBar::tab:hover {
        color: %(fg_primary)s;
    }
    QTabBar::tab:selected {
        color: %(accent)s;
        border-bottom: 2px solid %(accent)s;
    }

    /* ===== 菜单 ===== */
    QMenuBar {
        background-color: %(bg_primary)s;
        color: %(fg_secondary)s;
    }
    QMenuBar::item:selected {
        background-color: %(accent_light)s;
        color: %(fg_primary)s;
        border-radius: 4px;
    }
    QMenu {
        background-color: %(card_bg)s;
        color: %(fg_primary)s;
        border: 1px solid %(border_strong)s;
        border-radius: 8px;
        padding: 4px;
    }
    QMenu::item {
        border-radius: 6px;
        padding: 6px 18px 6px 12px;
    }
    QMenu::item:selected {
        background-color: %(selection)s;
        color: %(fg_primary)s;
    }

    /* ===== 消息框/对话框 ===== */
    QMessageBox,
    QDialog {
        background-color: %(bg_primary)s;
        border-radius: %(dialog_border_radius)s;
    }
    QMessageBox QLabel {
        color: %(fg_primary)s;
        font-size: %(base_font_size)s;
        padding: 4px;
    }
    QMessageBox QPushButton {
        min-width: 80px;
        min-height: 32px;
        padding: 6px 16px;
    }

    /* ===== 文本浏览器 ===== */
    QTextBrowser {
        background-color: %(bg_secondary)s;
        color: %(fg_primary)s;
        border: 1px solid %(border)s;
        border-radius: %(input_border_radius)s;
        padding: %(input_padding)s;
    }
    """ % c


class ThemeManager(QObject):
    """全局主题管理器。"""

    theme_changed = pyqtSignal(str)
    demo_mode_changed = pyqtSignal(bool)

    def __init__(self, initial_theme: str = "light", demo_mode: bool = False):
        super().__init__()
        self._theme = initial_theme.lower()
        if self._theme not in ("dark", "light"):
            self._theme = "light"
        self._demo_mode = demo_mode

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def demo_mode(self) -> bool:
        return self._demo_mode

    def colors(self) -> dict:
        """返回当前主题的颜色令牌字典。"""
        return _DARK_COLORS.copy() if self._theme == "dark" else _LIGHT_COLORS.copy()

    def _size_tokens(self) -> dict:
        """根据演示模式返回带缩放的尺寸令牌。"""
        scale = 1.25 if self._demo_mode else 1.0

        def s(value: int) -> str:
            return f"{int(value * scale)}px"

        return {
            "base_font_size": s(13),
            "header_font_size": s(20),
            "subtitle_font_size": s(12),
            "card_border_radius": s(12),
            "dialog_border_radius": s(14),
            "btn_border_radius": s(8),
            "nav_border_radius": s(8),
            "input_border_radius": s(8),
            "primary_padding": f"{int(8 * scale)}px {int(20 * scale)}px",
            "btn_padding": f"{int(6 * scale)}px {int(14 * scale)}px",
            "nav_padding": f"{int(10 * scale)}px {int(14 * scale)}px",
            "nav_min_height": s(40),
            "input_padding": f"{int(8 * scale)}px {int(12 * scale)}px",
            "combo_padding": f"{int(6 * scale)}px {int(10 * scale)}px",
            "tab_padding": f"{int(8 * scale)}px {int(16 * scale)}px",
            "scrollbar_size": s(8),
            "group_title_top": s(12),
            "checkbox_size": s(16),
        }

    def apply(self, app: QApplication | None = None) -> None:
        """将当前主题样式应用到应用。"""
        target = app or QApplication.instance()
        if target is None:
            logger.warning("No QApplication instance found")
            return
        try:
            colors = self.colors()
            colors.update(self._size_tokens())
            qss = _build_stylesheet(colors)
            target.setStyleSheet(qss)
            logger.info("Applied %s theme (demo_mode=%s)", self._theme, self._demo_mode)
        except Exception as e:
            logger.error("Failed to apply theme: %s", e)

    def set_theme(self, theme: str, app: QApplication | None = None) -> None:
        """切换主题并保存偏好。"""
        theme = theme.lower()
        if theme not in ("dark", "light"):
            theme = "light"
        if theme == self._theme:
            return
        self._theme = theme
        self.apply(app)
        self.theme_changed.emit(theme)

    def set_demo_mode(self, enabled: bool, app: QApplication | None = None) -> None:
        """切换演示模式（放大 UI 元素，适合投屏）。"""
        if enabled == self._demo_mode:
            return
        self._demo_mode = enabled
        self.apply(app)
        self.demo_mode_changed.emit(enabled)

    def toggle(self, app: QApplication | None = None) -> None:
        """在暗色与亮色之间切换。"""
        new_theme = "light" if self._theme == "dark" else "dark"
        self.set_theme(new_theme, app)

    def update_widget(self, widget: QWidget) -> None:
        """刷新单个 widget 的样式（用于动态更新已有控件）。"""
        try:
            widget.setStyleSheet(widget.styleSheet())
        except Exception as e:
            logger.warning("Failed to update widget style: %s", e)
