"""苹果式交互增强：动画策略、微动效、弹窗行为与友好文案。

对标苹果人机交互准则：**反馈即时、动画克制细腻、状态可见、容错友好**。

性能硬约束（保证打包后不卡顿）：
- 全部动效用 Qt 原生 ``QPropertyAnimation``，单次时长上限 180ms、帧率上限 30fps；
- **不使用任何循环无限高负载动画**；
- 低配 / 强制低配时自动降级：关闭全部微动画，仅保留文字状态提示；
- 窗口闲置 20 秒后自动暂停动画，降低 CPU 占用，用户一有操作立即恢复。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from ..utils.logger import get_logger

logger = get_logger("ui.interactions")

#: 动画帧率上限（30fps）：用于需要定时刷新的自绘动效
FRAME_INTERVAL_MS = 33
#: 单个动效时长上限：超过 200ms 的过渡一律禁止
MAX_DURATION_MS = 180
#: 微交互标准时长
DURATION_FAST = 120
DURATION_NORMAL = 160
#: 闲置多久后暂停动画
IDLE_AFTER_MS = 20000


class AnimationPolicy(QObject):
    """全局动画策略：统一时长上限、低配降级、闲置暂停。"""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._animate = True      # 是否允许微动画（低配时关闭）
        self._idle = False
        self._low_spec = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(IDLE_AFTER_MS)
        self._idle_timer.timeout.connect(self._mark_idle)
        self._idle_timer.start()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """当前是否应播放动画（低配或闲置时为 False）。"""
        return self._animate and not self._idle

    @property
    def idle(self) -> bool:
        return self._idle

    @property
    def low_spec(self) -> bool:
        return self._low_spec

    def configure(self, tier: object = None, force_low: bool = False) -> bool:
        """按硬件档次配置动画能力；低配设备自动降级为"仅文字提示"。"""
        tier_value = getattr(tier, "value", tier)
        self._low_spec = bool(force_low) or str(tier_value).lower() == "low"
        self._animate = not self._low_spec
        logger.info(
            "动画策略: enabled=%s low_spec=%s tier=%s",
            self._animate,
            self._low_spec,
            tier_value,
        )
        return self._animate

    def set_animated(self, animated: bool) -> None:
        self._animate = bool(animated)

    def note_activity(self) -> None:
        """用户有交互（鼠标/键盘）→ 立即恢复动画并重新计时。"""
        if self._idle:
            self._idle = False
        self._idle_timer.start()

    def _mark_idle(self) -> None:
        self._idle = True

    # ------------------------------------------------------------------
    # 时长
    # ------------------------------------------------------------------
    def duration(self, base_ms: int) -> int:
        """返回实际可用的动画时长：降级或闲置时为 0（调用方应直接跳到终态）。"""
        if not self.enabled:
            return 0
        return max(0, min(int(base_ms), MAX_DURATION_MS))


_policy: Optional[AnimationPolicy] = None


def policy() -> AnimationPolicy:
    """获取全局动画策略（首次调用时创建）。"""
    global _policy
    if _policy is None:
        _policy = AnimationPolicy()
    return _policy


class _ActivityFilter(QObject):
    """轻量事件过滤器：仅用于感知用户活动，让闲置动画立即恢复。"""

    _ACTIVITY_EVENTS = frozenset(
        {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
            QEvent.Type.FocusIn,
        }
    )

    def __init__(self, target: AnimationPolicy, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._target = target

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in self._ACTIVITY_EVENTS and self._target.idle:
            self._target.note_activity()
        return False


def _force_opaque(widget: Optional[QWidget]) -> None:
    """安全兜底：确保窗口最终一定是不透明的（避免动画异常导致弹窗"隐形"）。"""
    try:
        if widget is not None and widget.windowOpacity() < 1.0:
            widget.setWindowOpacity(1.0)
    except Exception:
        pass


class _DialogPolishFilter(QObject):
    """全局弹窗润色过滤器（一处生效，覆盖全部现有与后续新增弹窗）。

    - 弹窗显示时中心淡入；
    - 无边框弹窗支持按住拖拽；
    - 点击弹窗外部区域可关闭（苹果式浮层逻辑）；
    - 消息框（确认 / 报错）仍要求显式选择，避免误关闭造成误操作。
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active: Optional[QDialog] = None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            etype = event.type()
            if etype == QEvent.Type.Show and isinstance(obj, QDialog):
                self._on_dialog_shown(obj)
            elif etype in (QEvent.Type.Close, QEvent.Type.Hide) and obj is self._active:
                self._active = None
            elif etype == QEvent.Type.MouseButtonPress:
                self._maybe_close_on_outside(obj)
        except Exception as e:
            logger.debug("弹窗润色过滤器异常: %s", e)
        return False

    def _on_dialog_shown(self, dialog: QDialog) -> None:
        if getattr(dialog, "_plos_polished", False):
            return
        dialog._plos_polished = True
        if dialog.isModal():
            self._active = dialog
        try:
            duration = policy().duration(DURATION_NORMAL)
            # 延后一帧启动，确保窗口已完成首次映射
            QTimer.singleShot(0, lambda d=dialog: pop_in(d, DURATION_NORMAL))
            if duration > 0:
                QTimer.singleShot(duration + 120, lambda d=dialog: _force_opaque(d))
        except Exception:
            _force_opaque(dialog)
        enable_dialog_drag(dialog)

    def _maybe_close_on_outside(self, obj: QObject) -> None:
        dialog = self._active
        if dialog is None or not dialog.isVisible():
            return
        widget = obj if isinstance(obj, QWidget) else None
        if widget is None:
            return
        if widget is dialog or dialog.isAncestorOf(widget):
            return
        # 消息框必须显式选择，避免误关掉确认/报错框
        from PyQt6.QtWidgets import QMessageBox

        if isinstance(dialog, QMessageBox):
            return
        # 点击落在下拉列表 / 右键菜单等浮层上时不关闭
        from PyQt6.QtWidgets import QAbstractItemView, QMenu

        for popup in QApplication.topLevelWidgets():
            if not popup.isVisible() or popup is dialog:
                continue
            if isinstance(popup, (QMenu, QAbstractItemView)) and (
                popup is widget or popup.isAncestorOf(widget)
            ):
                return
        try:
            dialog.reject()
        except Exception:
            pass


def install_interactions(
    app: Optional[QApplication] = None,
    tier: object = None,
    force_low: bool = False,
) -> AnimationPolicy:
    """在应用上安装全局交互策略（启动时调用一次）。"""
    pol = policy()
    pol.configure(tier=tier, force_low=force_low)
    target_app = app or QApplication.instance()
    if target_app is not None:
        target_app.installEventFilter(_ActivityFilter(pol, target_app))
        target_app.installEventFilter(_DialogPolishFilter(target_app))
    return pol


# ----------------------------------------------------------------------
# 微动效
# ----------------------------------------------------------------------

def shake(widget: Optional[QWidget], intensity: int = 6, duration_ms: int = 320) -> None:
    """输入无效时的**轻微**横向抖动（苹果式提醒，低强度不夸张）。

    低配 / 闲置降级时不播放动画，由调用方改用文字提示。
    """
    if widget is None:
        return
    duration = policy().duration(duration_ms)
    if duration <= 0:
        return
    try:
        origin = widget.pos()
        anim = QPropertyAnimation(widget, b"pos", widget)
        anim.setDuration(duration)
        # 左右小幅往返 3 次后回到原位
        steps = [0, -intensity, intensity, -intensity // 2, intensity // 2, 0]
        total = len(steps) - 1
        for index, offset in enumerate(steps):
            anim.setKeyValueAt(index / total, QPoint(origin.x() + offset, origin.y()))
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        widget.setProperty("_shake_anim", anim)
    except Exception as e:
        logger.debug("抖动动画失败: %s", e)


def pop_in(widget: Optional[QWidget], duration_ms: int = DURATION_NORMAL, grow: bool = True) -> None:
    """对**已显示**的窗口做中心淡入 + 微放大（时长受全局策略限制）。

    低配 / 闲置降级时直接跳到终态，不做任何动画。
    顶层窗口动画只改 opacity；frameless 窗口额外做 96%→100% 的轻微放大，
    避免带系统标题栏的窗口缩放时出现抖动。
    """
    if widget is None:
        return
    duration = policy().duration(duration_ms)
    try:
        if duration <= 0:
            widget.setWindowOpacity(1.0)
            return
        frameless = bool(widget.windowFlags() & Qt.WindowType.FramelessWindowHint)
        end_geo = widget.geometry()
        start_geo = end_geo
        if grow and frameless and end_geo.width() > 0 and end_geo.height() > 0:
            shrink = 0.96
            dw = int(end_geo.width() * (1 - shrink) / 2)
            dh = int(end_geo.height() * (1 - shrink) / 2)
            start_geo = end_geo.adjusted(dw, dh, -dw, -dh)

        widget.setWindowOpacity(0.0)
        anim = QPropertyAnimation(widget, b"windowOpacity", widget)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        widget.setProperty("_fade_anim", anim)

        if start_geo != end_geo:
            geo_anim = QPropertyAnimation(widget, b"geometry", widget)
            geo_anim.setDuration(duration)
            geo_anim.setStartValue(start_geo)
            geo_anim.setEndValue(end_geo)
            geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            geo_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            widget.setProperty("_grow_anim", geo_anim)
    except Exception as e:
        logger.debug("弹入动画失败: %s", e)
        try:
            widget.setWindowOpacity(1.0)
        except Exception:
            pass


def fade_in(widget: Optional[QWidget], duration_ms: int = DURATION_NORMAL, grow: bool = True) -> None:
    """弹窗 / 页面淡入：先显示，再从中心淡入 + 微放大。"""
    if widget is None:
        return
    try:
        widget.setWindowOpacity(0.0)
        widget.show()
    except Exception:
        pass
    pop_in(widget, duration_ms=duration_ms, grow=grow)


def fade_out_and_close(widget: Optional[QWidget], duration_ms: int = DURATION_FAST) -> None:
    """反向平滑退出后关闭（弹窗关闭的统一动效）。"""
    if widget is None:
        return
    duration = policy().duration(duration_ms)
    if duration <= 0:
        widget.close()
        return
    try:
        anim = QPropertyAnimation(widget, b"windowOpacity", widget)
        anim.setDuration(duration)
        anim.setStartValue(widget.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(widget.close)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        widget.setProperty("_fadeout_anim", anim)
    except Exception as e:
        logger.debug("淡出动画失败: %s", e)
        widget.close()


def enable_dialog_drag(dialog: Optional[QWidget]) -> None:
    """让无边框弹窗支持按住任意空白处拖拽（不覆盖已有实现）。"""
    if dialog is None or getattr(dialog, "_drag_enabled", False):
        return
    try:
        state = {"offset": None}

        def _press(event) -> None:
            from PyQt6.QtCore import Qt

            if event.button() == Qt.MouseButton.LeftButton:
                state["offset"] = event.globalPosition().toPoint() - dialog.frameGeometry().topLeft()

        def _move(event) -> None:
            from PyQt6.QtCore import Qt

            if state["offset"] is not None and event.buttons() & Qt.MouseButton.LeftButton:
                dialog.move(event.globalPosition().toPoint() - state["offset"])

        def _release(_event) -> None:
            state["offset"] = None

        dialog.mousePressEvent = _press
        dialog.mouseMoveEvent = _move
        dialog.mouseReleaseEvent = _release
        dialog._drag_enabled = True
        dialog.setProperty("_drag_state", state)
    except Exception as e:
        logger.debug("弹窗拖拽安装失败: %s", e)


# ----------------------------------------------------------------------
# 友好文案（避免把原始报错抛给用户）
# ----------------------------------------------------------------------

_FRIENDLY_RULES = (
    (("timeout", "timed out", "超时"), "模型响应比较慢，可以重试或切换模型"),
    (("connection", "connect", "unreachable", "refused"), "连接不上服务，请检查网络或本地模型是否已启动"),
    (("not a database", "database disk image is malformed", "corrupt"), "知识库文件已损坏，可以尝试重置知识库"),
    (("badzipfile", "zip", "not a zip"), "文件已损坏或格式不支持，请换一个文件试试"),
    (("unsupported", "invalid format", "decode"), "文件格式不支持，请改用常见格式后重试"),
    (("permission", "access is denied", "being used by another"), "文件被占用或没有访问权限，请关闭占用它的程序后重试"),
    (("no such file", "not found", "filenotfound"), "找不到文件，可能已被移动或删除"),
    (
        # 注意：不使用宽泛的「空间不足」，避免把缓存上限等已有具体文案的提示覆盖掉
        ("no space left", "disk full", "not enough space", "errno 28", "磁盘空间不足"),
        "磁盘空间不足，请清理磁盘或更换保存位置后重试",
    ),
    (("memory", "out of memory"), "内存不足，建议关闭部分功能或卸载模型后重试"),
    (("model", "ollama"), "模型暂时不可用，可以稍后重试或在设置中切换模型"),
)


def friendly_error_message(error: object, fallback: str = "操作未成功，请稍后重试") -> str:
    """把异常 / 原始报错翻译成用户能看懂的自然语言。

    绝不把堆栈、模块名、错误码直接展示给用户。
    """
    try:
        name = type(error).__name__.lower() if not isinstance(error, str) else ""
        text = str(error) if not isinstance(error, str) else error
        haystack = f"{name} {text}".lower()
        for keywords, message in _FRIENDLY_RULES:
            if any(keyword in haystack for keyword in keywords):
                return message
    except Exception:
        pass
    return fallback


__all__ = [
    "AnimationPolicy",
    "policy",
    "install_interactions",
    "shake",
    "fade_in",
    "pop_in",
    "fade_out_and_close",
    "enable_dialog_drag",
    "friendly_error_message",
    "FRAME_INTERVAL_MS",
    "MAX_DURATION_MS",
    "DURATION_FAST",
    "DURATION_NORMAL",
]
