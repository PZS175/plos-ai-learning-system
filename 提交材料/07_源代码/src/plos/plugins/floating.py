"""插件共享：置顶悬浮小窗基类。

供「考试倒计时」「番茄专注钟」等插件把关键信息固定在屏幕角落：
无边框、总在最前、不占任务栏（Tool 窗口）、可拖动，
位置与锁定状态按账号持久化，重启后自动恢复。

子类只需覆写 :meth:`FloatingCard.build_content` 填充卡片主体，
以及（可选）:meth:`FloatingCard.on_tick` 做定时刷新；
点击关闭按钮会保存 ``pinned=False`` 并发出 :attr:`FloatingCard.closed`，
由插件模块负责清理自身单例引用。
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FloatingCard(QWidget):
    """无边框、置顶、可拖动的悬浮卡片基类。"""

    #: 卡片被关闭（✕）时发出，插件模块据此清空单例引用
    closed = pyqtSignal()

    def __init__(
        self,
        ctx: Any,
        state_name: str,
        title: str = "",
        size: Tuple[int, int] = (224, 140),
        interval_ms: int = 1000,
        accent_border: bool = True,
    ) -> None:
        super().__init__(None)
        self._ctx = ctx
        self._state_name = state_name
        self._colors = ctx.theme_colors()
        self._drag_offset = None
        self._timer: Optional[QTimer] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(*size)

        self._build_shell(title, accent_border)
        self.build_content(self._body_layout)
        self.restore_position()

        if interval_ms and interval_ms > 0:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.on_tick)
            self._timer.start(int(interval_ms))
        self.on_tick()

    # ------------------------------------------------------------------
    # 外壳（子类一般无需改动）
    # ------------------------------------------------------------------
    def _build_shell(self, title: str, accent_border: bool) -> None:
        c = self._colors
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        border = (c.get("accent_border") if accent_border else c.get("border")) or "#e5e7eb"
        card.setStyleSheet(
            f"background: {c.get('card_bg', '#ffffff')};"
            f"border: 1px solid {border};"
            "border-radius: 14px;"
        )
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 13px; font-weight: bold;"
        )
        top.addWidget(self.title_label, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')};"
            "background: transparent; border: none; font-size: 12px;"
        )
        close_btn.clicked.connect(self.close_card)
        top.addWidget(close_btn)
        lay.addLayout(top)

        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(0, 2, 0, 0)
        self._body_layout.setSpacing(2)
        lay.addLayout(self._body_layout)

        self._hint_label = QLabel("")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setStyleSheet(
            f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 10px;"
        )
        self._hint_label.setVisible(False)
        lay.addWidget(self._hint_label)

    def set_hint(self, text: str) -> None:
        """在卡片底部显示一行小字提示。"""
        self._hint_label.setText(text)
        self._hint_label.setVisible(bool(text))

    def body_layout(self) -> QVBoxLayout:
        """卡片主体布局，供子类追加控件。"""
        return self._body_layout

    # ------------------------------------------------------------------
    # 子类钩子
    # ------------------------------------------------------------------
    def build_content(self, layout: QVBoxLayout) -> None:
        """构建卡片主体内容（子类实现）。"""

    def on_tick(self) -> None:
        """定时刷新（默认每秒一次；子类实现）。"""

    def on_double_click(self) -> None:
        """双击卡片时的行为（默认无）。"""

    # ------------------------------------------------------------------
    # 位置持久化
    # ------------------------------------------------------------------
    def save_position(self, pinned: bool = True) -> None:
        try:
            self._ctx.save_state(
                self._state_name,
                {"pinned": bool(pinned), "x": int(self.x()), "y": int(self.y())},
            )
        except Exception:
            pass

    def restore_position(self) -> None:
        state = self._ctx.load_state(self._state_name, default={})
        if (
            isinstance(state, dict)
            and isinstance(state.get("x"), int)
            and isinstance(state.get("y"), int)
        ):
            self.move(state["x"], state["y"])
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.width() - 24, geo.top() + 40)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close_card(self) -> None:
        """关闭卡片：记住解锁状态并通知插件模块释放引用。"""
        self.save_position(pinned=False)
        if self._timer is not None:
            self._timer.stop()
        self.closed.emit()
        self.close()

    def stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    # ------------------------------------------------------------------
    # 拖动 / 双击
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.save_position()

    def mouseDoubleClickEvent(self, event) -> None:
        self.on_double_click()


__all__ = ["FloatingCard"]
