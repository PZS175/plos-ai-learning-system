"""轻量级 Toast 悬浮消息组件。

提供非阻塞的成功 / 警告 / 错误反馈，自动消失，支持手动关闭。
可选的快捷操作按钮（如「打开文件」「打开所在文件夹」）在消息下方一行展示。
所有样式适配深色/浅色主题，配合玻璃拟态设计。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .ui_utils import apply_glass_style


_TOAST_COLORS = {
    "success": ("#10B981", "rgba(16, 185, 129, 0.15)"),
    "warning": ("#F59E0B", "rgba(245, 158, 11, 0.15)"),
    "error": ("#EF4444", "rgba(239, 68, 68, 0.15)"),
    "info": ("#4F5DF5", "rgba(79, 93, 245, 0.15)"),
}

_ICONS = {
    "success": "✓",
    "warning": "⚠",
    "error": "✕",
    "info": "ℹ",
}


def _anim_duration(base_ms: int) -> int:
    """按全局动画策略换算实际时长；低配 / 闲置降级时返回 0（直接终态）。"""
    try:
        from .interactions import policy

        return policy().duration(base_ms)
    except Exception:
        return base_ms


class Toast(QWidget):
    """单条 Toast 消息。"""

    closed = pyqtSignal()

    def __init__(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = 3000,
        parent: Optional[QWidget] = None,
        actions: Optional[List[Tuple[str, Callable[[], None]]]] = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("toast")
        self.setProperty("glass", True)
        apply_glass_style(self)

        self.duration_ms = duration_ms
        self._shown_once = False
        self.setWindowOpacity(1.0)
        self._setup_ui(message, level, actions or [])
        self._setup_shadow()
        self._setup_animations()

    def _setup_ui(
        self,
        message: str,
        level: str,
        actions: Optional[List[Tuple[str, Callable[[], None]]]] = None,
    ) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 10, 10)
        outer.setSpacing(8)

        layout = QHBoxLayout()
        layout.setSpacing(10)

        color, tint = _TOAST_COLORS.get(level, _TOAST_COLORS["info"])
        icon = _ICONS.get(level, _ICONS["info"])

        icon_label = QLabel(icon)
        icon_label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
        layout.addWidget(icon_label)

        self.message_label = QLabel(message)
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("font-size: 13px;")
        self.message_label.setMinimumWidth(180)
        self.message_label.setMaximumWidth(360)
        layout.addWidget(self.message_label, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {color};
                border: none;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {tint};
                border-radius: 4px;
            }}
            """
        )
        close_btn.clicked.connect(self._close)
        layout.addWidget(close_btn)

        outer.addLayout(layout)

        if actions:
            action_row = QHBoxLayout()
            action_row.setSpacing(8)
            action_row.addStretch()
            for label, callback in actions:
                action_btn = QPushButton(label)
                action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                action_btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: transparent;
                        color: {color};
                        border: 1px solid {color}66;
                        border-radius: 4px;
                        padding: 3px 12px;
                        font-size: 12px;
                    }}
                    QPushButton:hover {{ background-color: {tint}; }}
                    """
                )
                action_btn.clicked.connect(
                    lambda _checked=False, cb=callback: self._run_action(cb)
                )
                action_row.addWidget(action_btn)
            outer.addLayout(action_row)

        # 苹果式圆角玻璃卡片：柔和描边、大圆角，颜色语义由图标承担
        from .ui_utils import theme_colors

        c = theme_colors()
        self.setStyleSheet(
            f"""
            QWidget#toast {{
                background-color: {c['card_bg']};
                border: 1px solid {c['border_strong']};
                border-radius: 12px;
            }}
            """
        )

    def _run_action(self, callback: Callable[[], None]) -> None:
        """执行快捷操作后再关闭 Toast，避免按钮残留在屏幕上。"""
        try:
            callback()
        except Exception:
            pass
        self._close()

    def _setup_shadow(self) -> None:
        try:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(16)
            shadow.setColor(QColor(0, 0, 0, 60))
            shadow.setOffset(0, 4)
            self.setGraphicsEffect(shadow)
        except Exception:
            pass

    def _setup_animations(self) -> None:
        self._opacity_animation = QPropertyAnimation(self, b"windowOpacity")
        self._opacity_animation.setDuration(_anim_duration(120))

    def animate_in(self, from_pos: Optional[QPoint] = None) -> None:
        """苹果式入场：淡入 + 从下方轻微上移（时长受全局策略限制）。

        低配 / 闲置降级时直接显示终态，不做任何动画。
        """
        if self._shown_once:
            return
        self._shown_once = True
        duration = _anim_duration(160)
        if duration <= 0:
            self.setWindowOpacity(1.0)
            return
        try:
            end_pos = self.pos()
            self.setWindowOpacity(0.0)
            self._enter_opacity = QPropertyAnimation(self, b"windowOpacity", self)
            self._enter_opacity.setDuration(duration)
            self._enter_opacity.setStartValue(0.0)
            self._enter_opacity.setEndValue(1.0)
            self._enter_opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._enter_opacity.start()
            if from_pos is not None and from_pos != end_pos:
                self._enter_pos = QPropertyAnimation(self, b"pos", self)
                self._enter_pos.setDuration(duration)
                self._enter_pos.setStartValue(from_pos)
                self._enter_pos.setEndValue(end_pos)
                self._enter_pos.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._enter_pos.start()
        except Exception:
            self.setWindowOpacity(1.0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.duration_ms > 0:
            QTimer.singleShot(self.duration_ms, self._fade_out)

    def _fade_out(self) -> None:
        try:
            duration = _anim_duration(120)
            if duration <= 0:
                self._close()
                return
            self._opacity_animation.setDuration(duration)
            self._opacity_animation.setStartValue(self.windowOpacity())
            self._opacity_animation.setEndValue(0.0)
            self._opacity_animation.setEasingCurve(QEasingCurve.Type.InCubic)
            self._opacity_animation.finished.connect(self._close)
            self._opacity_animation.start()
        except Exception:
            self._close()

    def _close(self) -> None:
        try:
            self.closed.emit()
            self.close()
            self.deleteLater()
        except Exception:
            pass


class ToastManager:
    """管理窗口内的 Toast 消息队列，在父窗口右下角堆叠显示。

    苹果式排布：最新的消息贴近右下角，历史消息依次向上堆叠。
    """

    def __init__(self, parent: QWidget):
        self._parent = parent
        self._toasts: list[Toast] = []
        self._spacing = 10
        self._margin = 24

    def show(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = 3000,
        actions: Optional[List[Tuple[str, Callable[[], None]]]] = None,
    ) -> None:
        """显示一条 Toast 消息，可附带若干快捷操作按钮。"""
        toast = Toast(
            message,
            level=level,
            duration_ms=duration_ms,
            parent=self._parent,
            actions=actions,
        )
        toast.closed.connect(lambda t=toast: self._remove_toast(t))
        toast.show()
        self._toasts.append(toast)
        self._reposition()

    def reposition(self) -> None:
        """重新计算所有 Toast 的位置。"""
        self._reposition()

    def _remove_toast(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reposition()

    def _reposition(self) -> None:
        """重新计算所有 Toast 的位置：右下角起，向上堆叠。"""
        if not self._toasts:
            return

        parent_rect = self._parent.rect()
        # Toast 是顶层 Tool 窗口，move() 使用屏幕坐标，需把父窗口客户区换算到全局
        origin = self._parent.mapToGlobal(QPoint(0, 0))
        x = origin.x() + parent_rect.width() - self._margin
        y = origin.y() + parent_rect.height() - self._margin

        for toast in self._toasts:
            toast.adjustSize()
            toast_width = toast.width()
            toast_height = toast.height()
            target_y = y - toast_height
            toast.move(x - toast_width, target_y)
            # 首次定位后播放淡入 + 上移入场；后续重排不动画
            try:
                toast.animate_in(from_pos=QPoint(x - toast_width, target_y + 12))
            except Exception:
                pass
            y = target_y - self._spacing
