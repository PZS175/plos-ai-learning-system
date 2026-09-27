"""番茄钟专注计时器：自学时的核心入口。

选择科目开始专注，倒计时结束或手动结束时把实际专注分钟数写入
statistics_records（study_duration），从而点亮学习热力图、连续
天数与学习周报——学习行为闭环由此接通。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .ui_utils import (
    apply_card_style,
    set_primary_button_style,
    show_warning,
    theme_colors,
)
from ..core.constants import POMODORO_BREAK_MINUTES, POMODORO_FOCUS_MINUTES
from ..utils.logger import get_logger

logger = get_logger("ui.pomodoro")


class PomodoroWidget(QWidget):
    """紧凑的单行专注计时卡：科目 + 倒计时 + 开始/暂停/结束。"""

    session_recorded = pyqtSignal(int, str)  # (分钟, 科目)

    def __init__(
        self,
        statistics_service=None,
        focus_minutes: int = POMODORO_FOCUS_MINUTES,
        break_minutes: int = POMODORO_BREAK_MINUTES,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.statistics_service = statistics_service
        self._focus_seconds = focus_minutes * 60
        self._break_seconds = break_minutes * 60
        self._remaining = self._focus_seconds
        # idle / focusing / paused / breaking
        self._state = "idle"
        self._subject = ""

        self.setProperty("card", True)
        apply_card_style(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 14, 20, 14)
        row.setSpacing(12)
        c = theme_colors()

        icon = QLabel("⏱")
        icon.setStyleSheet(f"font-size: 20px; color: {c['accent']}; background-color: transparent;")
        row.addWidget(icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("专注模式")
        title.setObjectName("section_title")
        title_box.addWidget(title)
        self._hint = QLabel("选一个科目，开始一段不被打扰的学习")
        self._hint.setStyleSheet(
            f"font-size: 11px; color: {c['fg_muted']}; background-color: transparent;"
        )
        title_box.addWidget(self._hint)
        row.addLayout(title_box)

        row.addSpacing(8)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("科目（可选）")
        self.subject_edit.setFixedWidth(120)
        self.subject_edit.setFixedHeight(30)
        row.addWidget(self.subject_edit)

        self.time_label = QLabel(self._format(self._remaining))
        self.time_label.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {c['fg_primary']};"
            "background-color: transparent; min-width: 96px;"
        )
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.time_label)

        row.addStretch()

        self.start_btn = QPushButton("开始专注")
        set_primary_button_style(self.start_btn)
        self.start_btn.setFixedHeight(32)
        self.start_btn.clicked.connect(self._toggle_start_pause)
        row.addWidget(self.start_btn)

        self.finish_btn = QPushButton("结束并记录")
        self.finish_btn.setFixedHeight(32)
        self.finish_btn.setEnabled(False)
        self.finish_btn.clicked.connect(self._finish_session)
        row.addWidget(self.finish_btn)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    # ---- 状态机 ----

    def _toggle_start_pause(self) -> None:
        if self._state == "focusing":
            self._pause()
        else:
            self._start()

    def _start(self) -> None:
        if self._state == "idle":
            self._remaining = self._focus_seconds
            self._subject = self.subject_edit.text().strip()
        elif self._state == "breaking":
            # 跳过休息：复位剩余时间后进入下一轮专注
            self._remaining = self._focus_seconds
        self._state = "focusing"
        self._timer.start()
        c = theme_colors()
        self.start_btn.setText("暂停")
        self.time_label.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {c['accent']};"
            "background-color: transparent; min-width: 96px;"
        )
        self._hint.setText("● 专注中——结束时会自动记录时长")
        self.finish_btn.setEnabled(True)
        self.subject_edit.setEnabled(False)

    def _pause(self) -> None:
        self._state = "paused"
        self._timer.stop()
        c = theme_colors()
        self.start_btn.setText("继续")
        self.time_label.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {c['warning']};"
            "background-color: transparent; min-width: 96px;"
        )
        self._hint.setText("已暂停")

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._on_countdown_end()
            return
        self.time_label.setText(self._format(self._remaining))

    def _on_countdown_end(self) -> None:
        """倒计时归零：专注结束进休息；休息结束复位回 idle。"""
        self._timer.stop()
        if self._state == "breaking":
            # 休息结束：不记录任何时长，回到初始状态
            self._reset()
            return
        self._record_elapsed(minutes=self._focus_seconds // 60, completed=True)
        self._state = "breaking"
        self._remaining = self._break_seconds
        c = theme_colors()
        self.start_btn.setText("跳过休息")
        self.time_label.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {c['success']};"
            "background-color: transparent; min-width: 96px;"
        )
        self._hint.setText("🍅 番茄完成！休息一下，点「跳过休息」开始下一个")
        self._timer.start()

    def _finish_session(self) -> None:
        """手动结束：休息期则直接复位；专注期记录已完成分钟数。"""
        self._timer.stop()
        if self._state == "breaking":
            self._reset()
            return
        elapsed_seconds = self._focus_seconds - self._remaining
        minutes = elapsed_seconds // 60
        if self._state in ("focusing", "paused") and minutes >= 1:
            self._record_elapsed(minutes=minutes, completed=False)
        else:
            show_warning(self, "专注模式", "专注不足 1 分钟，未记录。")
        self._reset()

    def _record_elapsed(self, minutes: int, completed: bool) -> None:
        if self.statistics_service is None:
            return
        try:
            self.statistics_service.record_study_duration(minutes, subject=self._subject)
        except Exception as e:
            logger.error("Failed to record study duration: %s", e)
            return
        logger.info("Pomodoro recorded: %d min subject=%s completed=%s", minutes, self._subject, completed)
        self.session_recorded.emit(minutes, self._subject)

    def _reset(self) -> None:
        self._state = "idle"
        self._remaining = self._focus_seconds
        self._timer.stop()
        c = theme_colors()
        self.start_btn.setText("开始专注")
        self.time_label.setText(self._format(self._remaining))
        self.time_label.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {c['fg_primary']};"
            "background-color: transparent; min-width: 96px;"
        )
        self._hint.setText("选一个科目，开始一段不被打扰的学习")
        self.finish_btn.setEnabled(False)
        self.subject_edit.setEnabled(True)

    # ---- 工具 ----

    @staticmethod
    def _format(seconds: int) -> str:
        minutes, secs = divmod(max(0, seconds), 60)
        return f"{minutes:02d}:{secs:02d}"
