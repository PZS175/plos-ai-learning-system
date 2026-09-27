"""共享 UI 工具函数。

封装常用的消息框、加载遮罩、剪贴板操作、玻璃拟态样式、按钮风格等，
保持各面板代码简洁一致。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _repolish(widget: QWidget) -> None:
    """重新应用样式，使动态属性/对象名生效。"""
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
    except Exception:
        pass


def apply_glass_style(widget: QWidget) -> None:
    """为控件应用玻璃拟态样式（依赖主题管理器中的 QSS）。"""
    try:
        widget.setProperty("glass", True)
        _repolish(widget)
    except Exception:
        pass


def apply_card_style(widget: QWidget) -> None:
    """为控件应用卡片样式。"""
    try:
        widget.setProperty("card", True)
        _repolish(widget)
    except Exception:
        pass


def set_primary_button_style(button: QPushButton) -> None:
    """将按钮设为主操作按钮样式。"""
    try:
        button.setObjectName("primary_btn")
        _repolish(button)
    except Exception:
        pass


def set_danger_button_style(button: QPushButton) -> None:
    """将按钮设为危险操作样式。"""
    try:
        button.setObjectName("danger_btn")
        _repolish(button)
    except Exception:
        pass


def create_header_widget(title: str, subtitle: str = "") -> QWidget:
    """创建页面顶部标题区控件。"""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(4, 4, 4, 12)
    layout.setSpacing(4)

    title_label = QLabel(title)
    title_label.setObjectName("header_title")
    layout.addWidget(title_label)

    if subtitle:
        sub_label = QLabel(subtitle)
        sub_label.setObjectName("subtitle_label")
        sub_label.setWordWrap(True)
        layout.addWidget(sub_label)

    return widget


def create_badge_label(text: str, color: Optional[str] = None) -> QLabel:
    """创建圆角标签徽章；color 为 None 时跟随主题 accent 色。"""
    if color is None:
        color = _theme_colors()["accent"]
    label = QLabel(text)
    label.setStyleSheet(
        f"""
        QLabel {{
            color: {color};
            background-color: {color}22;
            border: 1px solid {color}55;
            border-radius: 10px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
        }}
        """
    )
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def show_info(parent: Optional[QWidget], title: str, message: str) -> None:
    """显示信息提示框，采用与主题一致的消息框样式。"""
    try:
        msg = QMessageBox(parent)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        msg.exec()
    except Exception:
        pass


def show_warning(parent: Optional[QWidget], title: str, message: str) -> None:
    """显示警告提示框，采用与主题一致的消息框样式。"""
    try:
        msg = QMessageBox(parent)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        msg.exec()
    except Exception:
        pass


def show_error(parent: Optional[QWidget], title: str, message: str) -> None:
    """显示错误提示框，采用与主题一致的消息框样式。"""
    try:
        msg = QMessageBox(parent)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        msg.exec()
    except Exception:
        pass


def ask_confirm(
    parent: Optional[QWidget],
    title: str,
    message: str,
) -> bool:
    """询问用户确认，默认「否」按钮高亮，返回是否点击「是」。"""
    try:
        msg = QMessageBox(parent)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        return msg.exec() == QMessageBox.StandardButton.Yes
    except Exception:
        return False


class TaskStatusBus(QObject):
    """后台长任务状态总线（全局单例）。

    面板负责发布「任务开始 / 进度 / 结束」，主窗口底部状态栏订阅展示，
    面板因此无需反向依赖主窗口，长任务（知识库导入、批量解析等）
    在后台跑的同时界面其余部分照常可操作。
    """

    started = pyqtSignal(str, str)           # task_id, message
    progressed = pyqtSignal(str, int, str)   # task_id, percent, message
    finished = pyqtSignal(str)               # task_id


_task_status_bus: Optional[TaskStatusBus] = None


def task_status_bus() -> TaskStatusBus:
    """获取全局任务状态总线（首次调用时创建）。"""
    global _task_status_bus
    if _task_status_bus is None:
        _task_status_bus = TaskStatusBus()
    return _task_status_bus


def task_started(task_id: str, message: str) -> None:
    """发布任务开始（底部状态栏出现常驻进度）。"""
    task_status_bus().started.emit(task_id, message)


def task_progress(task_id: str, percent: int, message: str = "") -> None:
    """发布任务进度（percent 为 0~100）。"""
    task_status_bus().progressed.emit(task_id, int(percent), message)


def task_finished(task_id: str) -> None:
    """发布任务结束（该任务从状态栏移除）。"""
    task_status_bus().finished.emit(task_id)


def shake_widget(widget: Optional[QWidget], intensity: int = 6) -> None:
    """无效输入时轻微横向抖动提醒（苹果式）。

    低配设备 / 闲置降级时不播放动画，调用方应同时给出文字提示。
    """
    try:
        from .interactions import shake

        shake(widget, intensity=intensity)
    except Exception:
        pass


def set_clipboard_text(text: str) -> None:
    """将文本写入系统剪贴板。"""
    try:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
    except Exception:
        pass


_toast_manager: Optional[Any] = None


def set_toast_manager(manager: Optional[Any]) -> None:
    """注册全局 Toast 管理器（通常由主窗口在启动时创建并注入）。"""
    global _toast_manager
    _toast_manager = manager


def show_toast(
    message: str,
    level: str = "success",
    duration_ms: int = 3000,
    actions: Optional[Any] = None,
) -> bool:
    """通过全局 Toast 管理器显示非阻塞反馈，返回是否成功显示。

    actions: 可选的快捷操作按钮列表 [(文案, 回调), ...]。
    """
    if _toast_manager is None:
        return False
    try:
        if actions:
            _toast_manager.show(
                message, level=level, duration_ms=duration_ms, actions=actions
            )
        else:
            _toast_manager.show(message, level=level, duration_ms=duration_ms)
        return True
    except Exception:
        return False


def show_success(parent: Optional[QWidget], message: str) -> None:
    """轻量成功反馈：优先使用 Toast，未注册 Toast 时降级为模态信息框。"""
    if show_toast(message, "success"):
        return
    show_info(parent, "成功", message)


def show_warning_toast(parent: Optional[QWidget], message: str) -> None:
    """轻量警告反馈：优先使用 Toast，未注册 Toast 时降级为模态警告框。"""
    if show_toast(message, "warning"):
        return
    show_warning(parent, "提示", message)


class ButtonBusy:
    """统一的按钮忙碌状态（禁用 + 文案切换）。

    同步场景配合 with 语句使用；后台 worker 场景调用 start() 启动，
    在 finished 回调中调用 restore() 恢复。
    """

    def __init__(self, button: QPushButton, busy_text: str = "处理中..."):
        self._button = button
        self._busy_text = busy_text
        self._original_text = button.text()
        self._was_enabled = button.isEnabled()
        self._active = False

    def start(self) -> "ButtonBusy":
        self._original_text = self._button.text()
        self._was_enabled = self._button.isEnabled()
        self._button.setEnabled(False)
        self._button.setText(self._busy_text)
        self._active = True
        return self

    def restore(self) -> None:
        """恢复按钮的文字与可用状态。"""
        if not self._active:
            return
        self._active = False
        self._button.setText(self._original_text)
        self._button.setEnabled(self._was_enabled)

    def __enter__(self) -> "ButtonBusy":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.restore()
        return False


@contextmanager
def busy_cursor():
    """在 with 块内显示等待光标，结束后自动恢复。

    用于无法轻易拆成后台线程的短耗时同步调用（如列表刷新、连接测试），
    避免界面在等待期间看起来像卡死。
    """
    try:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        yield
    finally:
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass


def _is_dark_theme() -> bool:
    """判断是否为暗色主题：优先读配置文件，回退到调色板亮度。

    QSS 不会改变 QPalette，深色系统的调色板会让纯调色板判断
    与配置主题相悖；配置是唯一可信来源。
    """
    try:
        from ..config import load_config

        return load_config().get("ui", {}).get("theme", "light") != "light"
    except Exception:
        palette = QApplication.palette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128


_theme_manager_ref: Optional[Any] = None


def set_theme_manager(theme_manager: Optional[Any]) -> None:
    """注册全局主题管理器（主窗口启动时调用），供 theme_colors 统一取色。"""
    global _theme_manager_ref
    _theme_manager_ref = theme_manager


def get_theme_manager() -> Optional[Any]:
    """返回已注册的全局主题管理器；未注册时为 None。

    供需要感知主题切换的控件（如公式渲染器）订阅 theme_changed 信号。
    """
    return _theme_manager_ref


def is_dark_theme() -> bool:
    """当前是否为深色主题（对外的公开入口）。"""
    return _is_dark_theme()


def theme_colors() -> dict:
    """返回当前主题颜色令牌。

    优先从注册的 ThemeManager 获取完整令牌表；
    未注册时（如独立测试环境）回退为基于应用调色板的最小令牌集。
    """
    if _theme_manager_ref is not None:
        try:
            return _theme_manager_ref.colors()
        except Exception:
            pass
    is_dark = _is_dark_theme()
    return {
        "bg_primary": "#0E1118" if is_dark else "#F6F7FB",
        "bg_secondary": "#161B26" if is_dark else "#FFFFFF",
        "bg_tertiary": "#1E2534" if is_dark else "#EEF0F7",
        "sidebar_bg": "#0B0E14" if is_dark else "#FFFFFF",
        "fg_primary": "#E9ECF4" if is_dark else "#1A2030",
        "fg_secondary": "#9AA4B8" if is_dark else "#5A6478",
        "fg_muted": "#6B7488" if is_dark else "#8A93A8",
        "accent": "#6C7CFF" if is_dark else "#4F5DF5",
        "accent_hover": "#8290FF" if is_dark else "#6673FF",
        "accent_pressed": "#5A69F5" if is_dark else "#4350E8",
        "accent_gradient_end": "#9F7CFF" if is_dark else "#7C5CF0",
        "accent_light": "rgba(108, 124, 255, 0.16)" if is_dark else "rgba(79, 93, 245, 0.10)",
        "accent_lighter": "rgba(108, 124, 255, 0.08)" if is_dark else "rgba(79, 93, 245, 0.05)",
        "accent_border": "rgba(108, 124, 255, 0.45)" if is_dark else "rgba(79, 93, 245, 0.40)",
        "success": "#34D399" if is_dark else "#10B981",
        "warning": "#FBBF24" if is_dark else "#F59E0B",
        "error": "#F87171" if is_dark else "#EF4444",
        "info": "#38BDF8" if is_dark else "#0EA5E9",
        "teal": "#2DD4BF" if is_dark else "#14B8A6",
        "purple": "#A78BFA" if is_dark else "#8B5CF6",
        "border": "rgba(148, 163, 196, 0.13)" if is_dark else "rgba(20, 27, 51, 0.10)",
        "border_strong": "rgba(148, 163, 196, 0.26)" if is_dark else "rgba(20, 27, 51, 0.18)",
        "card_bg": "#161B26" if is_dark else "#FFFFFF",
        "selection": "rgba(108, 124, 255, 0.30)" if is_dark else "rgba(79, 93, 245, 0.14)",
        "shadow": "rgba(0, 0, 0, 0.40)" if is_dark else "rgba(23, 30, 60, 0.10)",
        "chart_1": "#6C7CFF" if is_dark else "#4F5DF5",
        "chart_2": "#34D399" if is_dark else "#10B981",
        "chart_3": "#FBBF24" if is_dark else "#F59E0B",
        "chart_4": "#F87171" if is_dark else "#EF4444",
        "chart_5": "#38BDF8" if is_dark else "#0EA5E9",
        "chart_6": "#A78BFA" if is_dark else "#8B5CF6",
    }


# 兼容旧调用名
_theme_colors = theme_colors


class Debouncer:
    """输入防抖：频繁触发时只在停顿 delay_ms 毫秒后执行一次。

    用于搜索框实时查询，避免用户打字过程中反复触发数据库查询或 AI 请求
    （打包后这类同步查询最容易造成界面掉帧）。
    """

    def __init__(self, callback, delay_ms: int = 300, parent=None):
        from PyQt6.QtCore import QTimer

        self._callback = callback
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(delay_ms))
        self._timer.timeout.connect(self._invoke)

    def _invoke(self) -> None:
        try:
            self._callback()
        except Exception:
            # 防抖回调失败不应影响界面（各面板自身已有错误处理）
            pass

    def trigger(self, *_args, **_kwargs) -> None:
        """重新计时（取消上一次尚未执行的调用）。"""
        self._timer.start()

    def flush(self) -> None:
        """立即执行（例如用户按回车确认时）。"""
        self._timer.stop()
        self._invoke()


def repolish_tree(widget: QWidget) -> None:
    """递归重新应用控件及其全部子控件的样式（主题切换后刷新 QSS 生效范围）。"""
    try:
        for w in widget.findChildren(QWidget):
            _repolish(w)
        _repolish(widget)
    except Exception:
        pass


def on_theme_changed_default(widget: QWidget) -> None:
    """面板未实现 on_theme_changed 时的默认刷新：整树 re-polish。"""
    repolish_tree(widget)


def apply_form_container_style(widget: QWidget) -> None:
    """统一表单容器内输入控件的密度（高度/圆角/字号）。

    各面板共用一份容器级 QSS，避免多处复制后圆角、高度逐渐漂移。
    """
    try:
        widget.setStyleSheet(
            """
            QLineEdit, QComboBox {
                min-height: 36px;
                max-height: 36px;
                padding: 0 12px;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton {
                min-height: 36px;
                border-radius: 6px;
                padding: 0 14px;
            }
            QTextEdit, QTextBrowser {
                border-radius: 6px;
                padding: 8px;
                font-size: 14px;
            }
            """
        )
    except Exception:
        pass


def create_icon_chip(glyph: str, size: int = 26, fg: Optional[str] = None,
                     bg: Optional[str] = None, font_size: Optional[int] = None) -> QLabel:
    """图标芯片：统一尺寸的圆角容器承载字形图标。

    解决字体字形大小/基线/粗细不一导致的视觉混乱：
    所有图标都落在同规格的圆角芯片里，水平垂直居中。
    fg/bg 缺省时跟随主题 accent（accent_light 底 + accent 字）。
    """
    c = _theme_colors()
    fg = fg or c["accent"]
    bg = bg or c["accent_light"]
    fs = font_size or max(12, int(size * 0.55))
    label = QLabel(glyph)
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"QLabel {{ color: {fg}; background-color: {bg}; border-radius: {max(6, size // 3)}px;"
        f" font-size: {fs}px; font-family: 'Segoe UI Symbol', 'Microsoft YaHei UI', sans-serif;"
        f" font-weight: 600; }}"
    )
    return label


def create_section_title(text: str) -> QLabel:
    """创建统一的卡片小节标题。

    颜色由全局 QSS 的 ``QLabel#section_title`` 规则提供，
    主题切换时随应用样式表自动刷新，不烘焙内联色值。
    """
    label = QLabel(text)
    label.setObjectName("section_title")
    return label


class EmptyStateWidget(QWidget):
    """主题适配的友好空状态提示控件（支持运行时更新提示文案）。"""

    def __init__(
        self,
        text: str,
        parent: Optional[QWidget] = None,
        icon: str = "◻",
        button_text: str = "",
        on_button_clicked: Optional[Callable[..., None]] = None,
    ):
        super().__init__(parent)
        c = _theme_colors()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setWordWrap(True)
        icon_label.setStyleSheet(
            f"color: {c['accent']}; font-size: 42px; background-color: transparent;"
        )
        layout.addWidget(icon_label)

        self._text_label = QLabel(text)
        self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_label.setWordWrap(True)
        self._text_label.setStyleSheet(
            f"color: {c['fg_secondary']}; font-size: 14px; background-color: transparent; padding: 0 20px;"
        )
        layout.addWidget(self._text_label)

        if button_text and on_button_clicked is not None:
            btn = QPushButton(button_text)
            set_primary_button_style(btn)
            btn.setMaximumWidth(200)
            btn.setMinimumHeight(34)
            btn.clicked.connect(on_button_clicked)
            layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_message(self, message: str) -> None:
        """更新空状态提示文案。"""
        self._text_label.setText(message)


def create_empty_state_widget(
    text: str,
    parent: Optional[QWidget] = None,
    icon: str = "◻",
    button_text: str = "",
    on_button_clicked: Optional[Callable[..., None]] = None,
) -> EmptyStateWidget:
    """创建主题适配的友好空状态提示控件。

    Args:
        text: 提示文本。
        parent: 父控件。
        icon: 前置扁平符号，默认空方块。
        button_text: 可选引导按钮文本。
        on_button_clicked: 引导按钮点击回调（与 button_text 搭配使用）。
    """
    return EmptyStateWidget(
        text,
        parent=parent,
        icon=icon,
        button_text=button_text,
        on_button_clicked=on_button_clicked,
    )




# 零宽字符与 BIDI 双向文本控制字符集合
# 包含零宽空格、零宽连接符/非连接符、方向标记、BIDI 控制符、
# 方向隔离符、格式化字符、变体选择器、BOM、阿拉伯字母标记等
_INVISIBLE_CONTROLS = frozenset(
    chr(c)
    for c in (
        list(range(0x200B, 0x2010))  # U+200B..U+200F
        + list(range(0x202A, 0x202F))  # U+202A..U+202E BIDI 控制
        + list(range(0x2060, 0x2070))  # U+2060..U+206F 格式化/不可见
        + list(range(0xFE00, 0xFE10))  # 变体选择器
    )
).union({"\u061C", "\u180E", "\uFEFF"})


def sanitize_bidi(text: Optional[str]) -> str:
    """清除字符串中的零宽字符与 BIDI 双向不可见控制字符，防止输入框乱码。"""
    if text is None:
        return ""
    return "".join(ch for ch in str(text) if ch not in _INVISIBLE_CONTROLS)


def fix_spinbox_text(spin_box) -> None:
    """修复 QSpinBox/QDoubleSpinBox 数字乱码方块问题。

    清空 prefix/suffix，过滤 lineEdit 中的零宽/BIDI 字符，重置方向与
    光标移动样式，并沿父容器链强制 LeftToRight 布局方向。
    """
    try:
        spin_box.setPrefix("")
        spin_box.setSuffix("")
        spin_box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        line_edit = spin_box.lineEdit()
        if line_edit is not None:
            line_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            line_edit.setCursorMoveStyle(Qt.CursorMoveStyle.VisualMoveStyle)
            # 过滤当前文本中的零宽与 BIDI 控制字符
            current_text = line_edit.text() or ""
            cleaned = sanitize_bidi(current_text)
            if cleaned != current_text:
                line_edit.setText(cleaned)
    except Exception:
        pass

    # 沿父容器链强制 LeftToRight，防止父布局方向影响数字显示
    try:
        parent = spin_box.parentWidget()
        while parent is not None:
            parent.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            parent = parent.parentWidget()
    except Exception:
        pass
