"""内置插件：考试倒计时。

功能：
- 添加多个考试 / 重要日期（名称 + 日期时间），按账号自动保存；
- 每秒刷新，醒目展示最近一个未结束事件的剩余天 / 时 / 分 / 秒；
- 已过期事件可一键删除；
- 支持「锁定到屏幕」：无边框置顶悬浮小窗，始终显示在其他窗口之上，
  可拖动、双击回到倒计时页，位置与锁定状态按账号记住，重启自动恢复。
"""

from __future__ import annotations

import weakref
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QDate, QDateTime, Qt, QTime, QTimer
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from plos.plugins.floating import FloatingCard

_STATE_NAME = "events"
_PIN_STATE = "pin"
_DT_FMT = "%Y-%m-%d %H:%M"

# 模块级唯一悬浮窗引用（禁用插件时由 unregister() 关闭）
_pinned: Optional["PinnedCountdown"] = None
# 锁定状态变化监听器（弱引用，面板销毁后自动失效）
_pin_listeners: list = []


def _notify_pin_changed() -> None:
    for ref in list(_pin_listeners):
        cb = ref()
        if cb is None:
            try:
                _pin_listeners.remove(ref)
            except ValueError:
                pass
            continue
        try:
            cb()
        except Exception:
            pass


def _card_style(c: dict) -> str:
    return (
        f"background: {c.get('card_bg', '#ffffff')};"
        f"border: 1px solid {c.get('border', '#e5e7eb')};"
        "border-radius: 14px;"
    )


def _primary_btn_style(c: dict) -> str:
    return (
        f"background: {c.get('accent', '#4f5df5')}; color: white;"
        "border: none; border-radius: 8px; padding: 7px 16px; font-weight: bold;"
    )


def _ghost_btn_style(c: dict) -> str:
    return (
        f"background: transparent; color: {c.get('error', '#ef4444')};"
        f"border: 1px solid {c.get('border_strong', '#d1d5db')};"
        "border-radius: 8px; padding: 6px 12px;"
    )


def _input_style(c: dict) -> str:
    return (
        f"background: {c.get('bg_tertiary', '#f3f4f6')};"
        f"color: {c.get('fg_primary', '#111827')};"
        f"border: 1px solid {c.get('border', '#e5e7eb')};"
        "border-radius: 8px; padding: 6px 8px;"
    )


def _next_upcoming(events: list, now: datetime):
    """返回 (目标时间, 事件dict)；无未结束事件返回 None。"""
    upcoming = None
    for e in events:
        if not isinstance(e, dict):
            continue
        try:
            target = datetime.strptime(e.get("dt", ""), _DT_FMT)
        except Exception:
            continue
        if target > now and (upcoming is None or target < upcoming[0]):
            upcoming = (target, e)
    return upcoming


class PinnedCountdown(FloatingCard):
    """置顶悬浮倒计时小窗：无边框、总在最前、可拖动、双击回倒计时页。"""

    def __init__(self, ctx) -> None:
        super().__init__(ctx, _PIN_STATE, title="考试倒计时", size=(212, 136))
        self.closed.connect(_on_pin_closed)
        self.set_hint("双击打开倒计时页 · 可拖动")

    def build_content(self, layout: QVBoxLayout) -> None:
        c = self._colors
        self.big_label = QLabel("--")
        self.big_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.big_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 30px; font-weight: bold;"
        )
        layout.addWidget(self.big_label)

        self.small_label = QLabel("")
        self.small_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.small_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        layout.addWidget(self.small_label)

    def on_double_click(self) -> None:
        key = (
            self._ctx.panel_keys[0]
            if self._ctx.panel_keys
            else f"plugin:{self._ctx.plugin_id}:main"
        )
        self._ctx.navigate(key)

    # ------------------------------------------------------------------
    # 每秒刷新（直接读按账号保存的事件，面板里增删后悬浮窗自动同步）
    # ------------------------------------------------------------------
    def on_tick(self) -> None:
        events = self._ctx.load_state(_STATE_NAME, default=[])
        if not isinstance(events, list):
            events = []
        upcoming = _next_upcoming(events, datetime.now())
        if upcoming is None:
            self.title_label.setText("考试倒计时")
            self.big_label.setText("-- 天")
            self.small_label.setText("暂无即将到来的考试")
            return
        target, event = upcoming
        total = max(0, int((target - datetime.now()).total_seconds()))
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        self.title_label.setText(
            QFontMetrics(self.title_label.font()).elidedText(
                str(event.get("name", "考试")), Qt.TextElideMode.ElideRight, 150
            )
        )
        if days >= 1:
            self.big_label.setText(f"{days} 天")
            self.small_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        else:
            self.big_label.setText(f"{hours} 时")
            self.small_label.setText(f"{minutes:02d} 分 {seconds:02d} 秒")


def _on_pin_closed() -> None:
    """悬浮窗被 ✕ 关闭：清空单例引用并释放窗口。"""
    global _pinned
    card = _pinned
    _pinned = None
    if card is not None:
        card.deleteLater()
    _notify_pin_changed()


def show_pin(ctx) -> "PinnedCountdown":
    """显示（或唤起）置顶悬浮倒计时，并记住锁定状态。"""
    global _pinned
    if _pinned is None:
        _pinned = PinnedCountdown(ctx)
    _pinned.show()
    _pinned.raise_()
    _pinned.save_position()
    _notify_pin_changed()
    return _pinned


def hide_pin() -> None:
    """关闭置顶悬浮倒计时并记住解锁状态。"""
    global _pinned
    if _pinned is None:
        return
    pin = _pinned
    _pinned = None
    try:
        pin.save_position(pinned=False)
        pin.stop_timer()
        pin.close()
        pin.deleteLater()
    except Exception:
        pass
    _notify_pin_changed()


def is_pinned() -> bool:
    return _pinned is not None


class CountdownPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._events: list[dict] = []
        self._load()
        self._build_ui()
        # 悬浮窗被 ✕ 关闭 / 重启恢复时，同步本面板按钮文案（弱引用，面板销毁自动失效）
        _pin_listeners.append(weakref.WeakMethod(self._sync_pin_button))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        # 倒计时主卡片
        self.hero = QFrame()
        self.hero.setStyleSheet(_card_style(self._colors))
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(24, 22, 24, 22)
        hero_layout.setSpacing(10)

        self.hero_title = QLabel("距离考试还有")
        self.hero_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_title.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 15px;"
        )
        hero_layout.addWidget(self.hero_title)

        self.hero_event = QLabel("")
        self.hero_event.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_event.setStyleSheet(
            f"color: {self._colors.get('fg_primary', '#111827')};"
            "font-size: 24px; font-weight: bold;"
        )
        hero_layout.addWidget(self.hero_event)

        digits = QGridLayout()
        digits.setSpacing(10)
        self._digit_labels: dict[str, QLabel] = {}
        for col, key in enumerate(("days", "hours", "minutes", "seconds")):
            box = QFrame()
            box.setStyleSheet(
                f"background: {self._colors.get('accent_light', 'rgba(79,93,245,0.1)')};"
                "border-radius: 12px;"
            )
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(8, 12, 8, 12)
            num = QLabel("--")
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(
                f"color: {self._colors.get('accent', '#4f5df5')};"
                "font-size: 34px; font-weight: bold;"
            )
            cap = QLabel({"days": "天", "hours": "时", "minutes": "分", "seconds": "秒"}[key])
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setStyleSheet(
                f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 12px;"
            )
            box_layout.addWidget(num)
            box_layout.addWidget(cap)
            digits.addWidget(box, 0, col)
            self._digit_labels[key] = num
        hero_layout.addLayout(digits)

        self.hero_target = QLabel("")
        self.hero_target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_target.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        hero_layout.addWidget(self.hero_target)

        pin_row = QHBoxLayout()
        pin_row.addStretch()
        self.pin_btn = QPushButton("📌 锁定到屏幕")
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setStyleSheet(_primary_btn_style(self._colors))
        self.pin_btn.setToolTip("把剩余天数置顶显示在屏幕角落，可拖动，双击回到本页")
        self.pin_btn.clicked.connect(self._toggle_pin)
        pin_row.addWidget(self.pin_btn)
        pin_row.addStretch()
        hero_layout.addLayout(pin_row)

        layout.addWidget(self.hero)

        # 添加事件
        form_card = QFrame()
        form_card.setStyleSheet(_card_style(self._colors))
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(18, 16, 18, 16)
        form_layout.setSpacing(10)

        form_title = QLabel("添加考试 / 重要日期")
        form_title.setStyleSheet(
            f"color: {self._colors.get('fg_primary', '#111827')};"
            "font-size: 15px; font-weight: bold;"
        )
        form_layout.addWidget(form_title)

        row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("事件名称，如：期末考试")
        self.name_edit.setStyleSheet(_input_style(self._colors))
        row.addWidget(self.name_edit, 2)

        self.date_edit = QDateEdit(QDate.currentDate().addDays(7))
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setStyleSheet(_input_style(self._colors))
        row.addWidget(self.date_edit, 1)

        self.time_edit = QTimeEdit(QTime(9, 0))
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setStyleSheet(_input_style(self._colors))
        row.addWidget(self.time_edit, 1)

        add_btn = QPushButton("＋ 添加")
        add_btn.setStyleSheet(_primary_btn_style(self._colors))
        add_btn.clicked.connect(self._add_event)
        row.addWidget(add_btn)
        form_layout.addLayout(row)
        layout.addWidget(form_card)

        # 事件列表
        list_card = QFrame()
        list_card.setStyleSheet(_card_style(self._colors))
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(18, 16, 18, 16)
        list_layout.setSpacing(8)

        list_head = QHBoxLayout()
        list_title = QLabel("全部事件")
        list_title.setStyleSheet(
            f"color: {self._colors.get('fg_primary', '#111827')};"
            "font-size: 15px; font-weight: bold;"
        )
        list_head.addWidget(list_title)
        list_head.addStretch()
        clear_btn = QPushButton("清除已过期")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; background: transparent;"
            "border: none;"
        )
        clear_btn.clicked.connect(self._clear_expired)
        list_head.addWidget(clear_btn)
        list_layout.addLayout(list_head)

        self.event_list = QListWidget()
        self.event_list.setFrameShape(QFrame.Shape.NoFrame)
        self.event_list.setStyleSheet(
            f"background: transparent; color: {self._colors.get('fg_primary', '#111827')};"
        )
        list_layout.addWidget(self.event_list)
        layout.addWidget(list_card, 1)
        layout.addStretch()

        self._reload_list()
        self._sync_pin_button()

    # ------------------------------------------------------------------
    # 屏幕锁定（置顶悬浮窗）
    # ------------------------------------------------------------------
    def _toggle_pin(self) -> None:
        if is_pinned():
            hide_pin()
        else:
            show_pin(self._ctx)
        self._sync_pin_button()

    def _sync_pin_button(self) -> None:
        self.pin_btn.setText("📌 取消屏幕锁定" if is_pinned() else "📌 锁定到屏幕")

    # ------------------------------------------------------------------
    def _load(self) -> None:
        data = self._ctx.load_state(_STATE_NAME, default=[])
        if isinstance(data, list):
            self._events = [e for e in data if isinstance(e, dict) and e.get("name") and e.get("dt")]

    def _save(self) -> None:
        self._ctx.save_state(_STATE_NAME, self._events)

    def _add_event(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self._ctx.show_warning(self, "提示", "请输入事件名称")
            return
        qdt = QDateTime(self.date_edit.date(), self.time_edit.time())
        dt_str = qdt.toString("yyyy-MM-dd HH:mm")
        self._events.append({"name": name, "dt": dt_str})
        self._events.sort(key=lambda e: e["dt"])
        self._save()
        self.name_edit.clear()
        self._reload_list()
        self._tick()

    def _delete_event(self, index: int) -> None:
        if 0 <= index < len(self._events):
            self._events.pop(index)
            self._save()
            self._reload_list()
            self._tick()

    def _clear_expired(self) -> None:
        now = datetime.now()
        kept = []
        for e in self._events:
            try:
                if datetime.strptime(e["dt"], _DT_FMT) >= now:
                    kept.append(e)
            except Exception:
                kept.append(e)
        if len(kept) != len(self._events):
            self._events = kept
            self._save()
            self._reload_list()
            self._tick()

    def _reload_list(self) -> None:
        self.event_list.clear()
        now = datetime.now()
        for idx, event in enumerate(self._events):
            item = QListWidgetItem()
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 4, 4, 4)
            try:
                target = datetime.strptime(event["dt"], _DT_FMT)
                delta = target - now
                if delta.total_seconds() > 0:
                    summary = f"还剩 {delta.days} 天 {delta.seconds // 3600} 时"
                    color = self._colors.get("success", "#10b981")
                else:
                    summary = "已结束"
                    color = self._colors.get("fg_muted", "#9ca3af")
            except Exception:
                target = None
                summary = "日期无效"
                color = self._colors.get("error", "#ef4444")

            label = QLabel(f"{event['name']}　·　{event['dt']}")
            label.setStyleSheet(f"color: {self._colors.get('fg_primary', '#111827')};")
            rl.addWidget(label, 2)
            state = QLabel(summary)
            state.setStyleSheet(f"color: {color}; font-size: 12px;")
            rl.addWidget(state, 1)
            del_btn = QPushButton("删除")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet(_ghost_btn_style(self._colors))
            del_btn.clicked.connect(lambda _=False, i=idx: self._delete_event(i))
            rl.addWidget(del_btn)
            item.setSizeHint(row.sizeHint())
            self.event_list.addItem(item)
            self.event_list.setItemWidget(item, row)

    def _tick(self) -> None:
        now = datetime.now()
        upcoming = _next_upcoming(self._events, now)

        if upcoming is None:
            self.hero_event.setText("暂无即将到来的考试")
            self.hero_target.setText("在下方添加你的下一个目标日期")
            for key, lbl in self._digit_labels.items():
                lbl.setText("--")
            return

        target, event = upcoming
        delta = target - now
        total_seconds = max(0, int(delta.total_seconds()))
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        self.hero_event.setText(event["name"])
        self.hero_target.setText(f"目标时间：{event['dt']}")
        self._digit_labels["days"].setText(str(days))
        self._digit_labels["hours"].setText(f"{hours:02d}")
        self._digit_labels["minutes"].setText(f"{minutes:02d}")
        self._digit_labels["seconds"].setText(f"{seconds:02d}")


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "考试倒计时",
        lambda c: CountdownPanel(c),
        icon="⏰",
        subtitle="盯住目标日期，把大目标拆成每一天",
    )
    # 重启后自动恢复「锁定到屏幕」状态
    pin_state = ctx.load_state(_PIN_STATE, default={})
    if isinstance(pin_state, dict) and pin_state.get("pinned"):
        try:
            show_pin(ctx)
        except Exception as e:
            ctx.logger.warning("Restore pinned countdown failed: %s", e)


def unregister(ctx) -> None:
    """插件被禁用 / 卸载时关闭置顶悬浮窗。"""
    hide_pin()
