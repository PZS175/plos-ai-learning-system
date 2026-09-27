"""内置插件：番茄专注钟。

番茄工作法：专注 25 分钟 → 短休 5 分钟，每完成 4 个番茄进入长休 15 分钟，
各阶段时长均可自定义。提供：

- 圆环进度 + 大号倒计时，开始 / 暂停 / 重置 / 跳过；
- 今日统计（完成的番茄个数、累计专注分钟），跨天自动归零，按账号保存；
- 「锁定到屏幕」置顶迷你窗，随时看到剩余时间，可拖动、双击回到本页。

计时状态由模块级 ``_Engine`` 统一持有，面板与置顶迷你窗共享同一份计时，
关闭面板后计时继续，迷你窗也不会与面板不同步。
"""

from __future__ import annotations

import weakref
from datetime import datetime
from typing import Callable, List, Optional

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from plos.plugins.floating import FloatingCard

_SETTINGS_STATE = "settings"
_STATS_STATE = "stats"
_PIN_STATE = "pin"

_PHASE_NAMES = {"focus": "专注", "short": "短休", "long": "长休"}
_PHASE_COLORS = {"focus": "accent", "short": "success", "long": "info"}

_DEFAULT_SETTINGS = {"focus": 25, "short": 5, "long": 15, "cycle": 4}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _input_style(c: dict) -> str:
    return (
        f"background: {c.get('bg_tertiary', '#f3f4f6')};"
        f"color: {c.get('fg_primary', '#111827')};"
        f"border: 1px solid {c.get('border', '#e5e7eb')};"
        "border-radius: 8px; padding: 6px 8px;"
    )


class _Engine:
    """番茄钟计时内核：状态、计时、统计与持久化。"""

    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self.settings = self._load_settings()
        self.stats = self._load_stats()
        self.phase = "focus"
        self.completed = 0
        self.running = False
        self.remaining = self.duration("focus")
        self.notice = "准备开始第一个番茄"
        self._listeners: List[Callable[[], None]] = []
        self._closed = False

        self._timer = QTimer()
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load_settings(self) -> dict:
        data = self._ctx.load_state(_SETTINGS_STATE, default={})
        merged = dict(_DEFAULT_SETTINGS)
        if isinstance(data, dict):
            for key, default in _DEFAULT_SETTINGS.items():
                try:
                    value = int(data.get(key, default))
                except (TypeError, ValueError):
                    value = default
                merged[key] = max(1, min(180, value))
        return merged

    def save_settings(self) -> None:
        self._ctx.save_state(_SETTINGS_STATE, self.settings)

    def _load_stats(self) -> dict:
        data = self._ctx.load_state(_STATS_STATE, default={})
        if isinstance(data, dict) and data.get("date") == _today():
            return {
                "date": _today(),
                "count": int(data.get("count", 0) or 0),
                "minutes": int(data.get("minutes", 0) or 0),
            }
        return {"date": _today(), "count": 0, "minutes": 0}

    def _save_stats(self) -> None:
        self._ctx.save_state(_STATS_STATE, self.stats)

    def _check_new_day(self) -> None:
        if self.stats.get("date") != _today():
            self.stats = {"date": _today(), "count": 0, "minutes": 0}
            self._save_stats()

    # ------------------------------------------------------------------
    # 阶段与计时
    # ------------------------------------------------------------------
    def duration(self, phase: str) -> int:
        return int(self.settings.get(phase, 25)) * 60

    def set_phase(self, phase: str) -> None:
        if phase not in _PHASE_NAMES:
            return
        self.phase = phase
        self.remaining = self.duration(phase)
        self.running = False
        self._timer.stop()
        self.notice = f"已切换到{_PHASE_NAMES[phase]}"
        self._emit()

    def start(self) -> None:
        self.running = True
        self._timer.start()
        self.notice = f"{_PHASE_NAMES[self.phase]}中，保持专注"
        self._emit()

    def pause(self) -> None:
        self.running = False
        self._timer.stop()
        self.notice = "已暂停"
        self._emit()

    def toggle(self) -> None:
        self.pause() if self.running else self.start()

    def reset(self) -> None:
        self.remaining = self.duration(self.phase)
        self.running = False
        self._timer.stop()
        self.notice = "已重置本阶段"
        self._emit()

    def skip(self) -> None:
        self.notice = f"已跳过{_PHASE_NAMES[self.phase]}"
        self._advance()
        self._emit()

    def _on_tick(self) -> None:
        self._check_new_day()
        if self.running and self.remaining > 0:
            self.remaining -= 1
            if self.remaining <= 0:
                self._finish_phase()
        self._emit()

    def _finish_phase(self) -> None:
        finished = self.phase
        if finished == "focus":
            self.completed += 1
            self.stats["count"] = int(self.stats.get("count", 0)) + 1
            self.stats["minutes"] = int(self.stats.get("minutes", 0)) + int(
                self.settings.get("focus", 25)
            )
            self._save_stats()
        try:
            QApplication.beep()
        except Exception:
            pass
        self.notice = f"{_PHASE_NAMES[finished]}结束，休息一下吧"
        self._advance()

    def _advance(self) -> None:
        if self.phase == "focus":
            cycle = max(1, int(self.settings.get("cycle", 4)))
            self.phase = "long" if self.completed > 0 and self.completed % cycle == 0 else "short"
        else:
            self.phase = "focus"
        self.remaining = self.duration(self.phase)
        self.running = False
        self._timer.stop()

    # ------------------------------------------------------------------
    # 监听（面板 / 迷你窗刷新）
    # ------------------------------------------------------------------
    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _emit(self) -> None:
        for ref in list(self._listeners):
            cb = ref() if isinstance(ref, weakref.WeakMethod) else ref
            if cb is None:
                try:
                    self._listeners.remove(ref)
                except ValueError:
                    pass
                continue
            try:
                cb()
            except Exception:
                pass

    def progress(self) -> float:
        total = max(1, self.duration(self.phase))
        return max(0.0, min(1.0, 1.0 - self.remaining / total))

    def stop(self) -> None:
        self._closed = True
        self._timer.stop()


# ----------------------------------------------------------------------
# 模块级单例
# ----------------------------------------------------------------------
_engine: Optional[_Engine] = None
_pinned: Optional["PinnedPomodoro"] = None
# 置顶窗开关监听（弱引用，面板销毁后自动失效）
_pin_listeners: List[Callable[[], None]] = []


def _notify_pin_changed() -> None:
    for ref in list(_pin_listeners):
        callback = ref() if isinstance(ref, weakref.WeakMethod) else ref
        if callback is None:
            try:
                _pin_listeners.remove(ref)
            except ValueError:
                pass
            continue
        try:
            callback()
        except Exception:
            pass


def _get_engine(ctx) -> _Engine:
    global _engine
    if _engine is None or getattr(_engine, "_closed", False):
        _engine = _Engine(ctx)
    return _engine


class _RingWidget(QWidget):
    """圆环进度 + 中央大号倒计时。"""

    def __init__(self, colors: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors = colors
        self._progress = 0.0
        self._text = "25:00"
        self._caption = "准备开始"
        self._accent = colors.get("accent", "#4f5df5")
        self.setMinimumSize(240, 240)

    def set_state(self, progress: float, text: str, caption: str, accent_key: str) -> None:
        self._progress = progress
        self._text = text
        self._caption = caption
        self._accent = self._colors.get(accent_key, self._colors.get("accent", "#4f5df5"))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height()) - 24
        rect = QRectF(
            (self.width() - side) / 2,
            (self.height() - side) / 2,
            side,
            side,
        )

        track = QPen(QColor(self._colors.get("bg_tertiary", "#eef0f7")))
        track.setWidth(14)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, 0, 360 * 16)

        arc = QPen(QColor(self._accent))
        arc.setWidth(14)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * self._progress))

        painter.setPen(QColor(self._colors.get("fg_primary", "#111827")))
        font = QFont()
        font.setPointSize(30)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(0, -12, 0, -12),
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )

        painter.setPen(QColor(self._colors.get("fg_secondary", "#6b7280")))
        font.setPointSize(12)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(0, 46, 0, 46),
            Qt.AlignmentFlag.AlignCenter,
            self._caption,
        )
        painter.end()


class PinnedPomodoro(FloatingCard):
    """置顶迷你窗：阶段名 + 剩余时间 + 今日番茄数。"""

    def __init__(self, ctx, engine: _Engine) -> None:
        self._engine = engine
        super().__init__(ctx, _PIN_STATE, title="番茄专注钟", size=(212, 132))
        self.closed.connect(_on_pin_closed)
        self.set_hint("双击打开番茄钟 · 可拖动")
        engine.add_listener(weakref.WeakMethod(self.on_tick))

    def build_content(self, layout: QVBoxLayout) -> None:
        c = self._colors
        self.time_label = QLabel("25:00")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 28px; font-weight: bold;"
        )
        layout.addWidget(self.time_label)

        self.state_label = QLabel("")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        layout.addWidget(self.state_label)

    def on_double_click(self) -> None:
        key = (
            self._ctx.panel_keys[0]
            if self._ctx.panel_keys
            else f"plugin:{self._ctx.plugin_id}:main"
        )
        self._ctx.navigate(key)

    def on_tick(self) -> None:
        engine = self._engine
        color_key = _PHASE_COLORS.get(engine.phase, "accent")
        self.time_label.setStyleSheet(
            f"color: {self._colors.get(color_key, '#4f5df5')};"
            "font-size: 28px; font-weight: bold;"
        )
        self.time_label.setText(_mmss(engine.remaining))
        self.title_label.setText(_PHASE_NAMES.get(engine.phase, "番茄钟"))
        state = "进行中" if engine.running else "已暂停"
        self.state_label.setText(f"{state} · 今日 {engine.stats.get('count', 0)} 个")


def _on_pin_closed() -> None:
    global _pinned
    card = _pinned
    _pinned = None
    if card is not None:
        card.deleteLater()
    _notify_pin_changed()


def show_pin(ctx, engine: _Engine) -> "PinnedPomodoro":
    """显示（或唤起）置顶迷你窗。"""
    global _pinned
    if _pinned is None:
        _pinned = PinnedPomodoro(ctx, engine)
    _pinned.show()
    _pinned.raise_()
    _pinned.save_position()
    _pinned.on_tick()
    _notify_pin_changed()
    return _pinned


def hide_pin() -> None:
    global _pinned
    if _pinned is None:
        return
    card = _pinned
    _pinned = None
    try:
        card.save_position(pinned=False)
        card.stop_timer()
        card.close()
        card.deleteLater()
    except Exception:
        pass
    _notify_pin_changed()


def is_pinned() -> bool:
    return _pinned is not None


class PomodoroPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._engine = _get_engine(ctx)
        self._build_ui()
        self._engine.add_listener(weakref.WeakMethod(self._refresh))
        # 置顶窗被 ✕ 关闭 / 重启恢复时同步按钮文案
        _pin_listeners.append(weakref.WeakMethod(self._sync_pin_button))
        self._refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        # 统计与置顶
        head = QHBoxLayout()
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        head.addWidget(self.stats_label)
        head.addStretch()
        self.pin_btn = QPushButton("📌 锁定到屏幕")
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.1)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 7px 14px; font-weight: bold;"
        )
        self.pin_btn.setToolTip("把剩余时间置顶显示在屏幕角落，可拖动")
        self.pin_btn.clicked.connect(self._toggle_pin)
        head.addWidget(self.pin_btn)
        root.addLayout(head)

        # 圆环
        card = QFrame()
        card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 16px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(12)

        self.ring = _RingWidget(c, self)
        card_layout.addWidget(self.ring, 1, Qt.AlignmentFlag.AlignHCenter)

        # 阶段切换
        phase_row = QHBoxLayout()
        phase_row.addStretch()
        self._phase_buttons: dict[str, QPushButton] = {}
        for phase in ("focus", "short", "long"):
            btn = QPushButton(_PHASE_NAMES[phase])
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumWidth(76)
            btn.clicked.connect(lambda _=False, p=phase: self._engine.set_phase(p))
            phase_row.addWidget(btn)
            self._phase_buttons[phase] = btn
        phase_row.addStretch()
        card_layout.addLayout(phase_row)

        # 控制按钮
        ctrl_row = QHBoxLayout()
        ctrl_row.addStretch()
        self.start_btn = QPushButton("▶ 开始专注")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setMinimumWidth(120)
        self.start_btn.clicked.connect(self._engine.toggle)
        ctrl_row.addWidget(self.start_btn)

        reset_btn = self._plain_button("重置", self._engine.reset)
        ctrl_row.addWidget(reset_btn)
        skip_btn = self._plain_button("跳过", self._engine.skip)
        ctrl_row.addWidget(skip_btn)
        ctrl_row.addStretch()
        card_layout.addLayout(ctrl_row)

        self.notice_label = QLabel("")
        self.notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        card_layout.addWidget(self.notice_label)
        root.addWidget(card, 1)

        # 时长设置
        setting_card = QFrame()
        setting_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        setting_row = QHBoxLayout(setting_card)
        setting_row.setContentsMargins(16, 12, 16, 12)
        setting_row.setSpacing(10)
        title = QLabel("时长设置（分钟）")
        title.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        setting_row.addWidget(title)

        self._spins: dict[str, QSpinBox] = {}
        for key, label_text in (("focus", "专注"), ("short", "短休"), ("long", "长休"), ("cycle", "长休间隔")):
            cap = QLabel(label_text)
            cap.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
            setting_row.addWidget(cap)
            spin = QSpinBox()
            spin.setRange(1, 180 if key != "cycle" else 12)
            spin.setValue(int(self._engine.settings.get(key, 25)))
            spin.setStyleSheet(_input_style(c))
            spin.setFixedWidth(64)
            spin.valueChanged.connect(lambda value, k=key: self._on_setting_changed(k, value))
            setting_row.addWidget(spin)
            self._spins[key] = spin
        setting_row.addStretch()
        root.addWidget(setting_card)

    def _plain_button(self, text: str, handler) -> QPushButton:
        c = self._colors
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(76)
        btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 7px 14px;"
        )
        btn.clicked.connect(handler)
        return btn

    # ------------------------------------------------------------------
    def _on_setting_changed(self, key: str, value: int) -> None:
        engine = self._engine
        engine.settings[key] = int(value)
        engine.save_settings()
        # 未开始时立刻按新时长刷新当前阶段
        if not engine.running and key == engine.phase:
            engine.remaining = engine.duration(engine.phase)
        engine._emit()

    def _toggle_pin(self) -> None:
        if is_pinned():
            hide_pin()
        else:
            show_pin(self._ctx, self._engine)
        self._sync_pin_button()

    def _sync_pin_button(self) -> None:
        self.pin_btn.setText("📌 取消屏幕锁定" if is_pinned() else "📌 锁定到屏幕")

    def _refresh(self) -> None:
        try:
            engine = self._engine
            color_key = _PHASE_COLORS.get(engine.phase, "accent")
            caption = "进行中" if engine.running else "已暂停"
            self.ring.set_state(
                engine.progress(),
                _mmss(engine.remaining),
                f"{_PHASE_NAMES[engine.phase]} · {caption}",
                color_key,
            )
            self.start_btn.setText("⏸ 暂停" if engine.running else "▶ 开始专注")
            self.stats_label.setText(
                f"今日：{engine.stats.get('count', 0)} 个番茄 · "
                f"专注 {engine.stats.get('minutes', 0)} 分钟"
            )
            if engine.notice:
                self.notice_label.setText(engine.notice)

            for phase, btn in self._phase_buttons.items():
                active = phase == engine.phase
                bg = (
                    self._colors.get("accent_light", "rgba(79,93,245,0.12)")
                    if active
                    else "transparent"
                )
                fg = (
                    self._colors.get("accent", "#4f5df5")
                    if active
                    else self._colors.get("fg_secondary", "#6b7280")
                )
                border = (
                    self._colors.get("accent_border", "rgba(79,93,245,0.45)")
                    if active
                    else self._colors.get("border", "#e5e7eb")
                )
                btn.setStyleSheet(
                    f"background: {bg}; color: {fg}; border: 1px solid {border};"
                    "border-radius: 8px; padding: 6px 12px;"
                )

            for key, spin in self._spins.items():
                if spin.value() != int(engine.settings.get(key, spin.value())):
                    spin.blockSignals(True)
                    spin.setValue(int(engine.settings.get(key, spin.value())))
                    spin.blockSignals(False)
        except Exception:
            pass


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "番茄专注钟",
        lambda c: PomodoroPanel(c),
        icon="🍅",
        subtitle="专注 25 分钟，休息 5 分钟，用节奏带动效率",
    )
    # 重启后恢复置顶迷你窗
    pin_state = ctx.load_state(_PIN_STATE, default={})
    if isinstance(pin_state, dict) and pin_state.get("pinned"):
        try:
            show_pin(ctx, _get_engine(ctx))
        except Exception as e:
            ctx.logger.warning("Restore pinned pomodoro failed: %s", e)


def unregister(ctx) -> None:
    """插件被禁用 / 卸载时关闭迷你窗并停止计时。"""
    hide_pin()
    if _engine is not None:
        _engine.stop()
