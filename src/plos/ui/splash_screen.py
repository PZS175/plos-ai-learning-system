"""品牌启动闪屏组件（全局独占遮罩）。

在原有视觉（深空蓝渐变圆角面板 + Logo 光晕 + 品牌名 + 旋转进度环 + 进度条）
基础上，接入**真实业务进度**：

- ``set_stage()`` / ``set_progress()`` 由启动编排逐阶段驱动，
  外圈圆环与底部进度条由同一个进度值渲染，节奏天然同步；
- 底部文案随阶段变化（不再循环轮播固定几句话）；
- 右下角「跳过加载」按钮，仅非核心阶段可用；
- ``ask_user()`` 在闪屏上叠加提示卡片（继续 / 退出、重置知识库等）；
- 支持按住拖拽移动窗口，加载期间显示等待光标；
- ``finish_to()`` 先让遮罩完全淡出，再显示主窗口并淡入，过渡自然。

独占遮罩语义（避免外圈露出主窗口）：
- 窗口铺满主屏且始终置顶，应用级模态，启动期间无法点击后方任何窗口；
- 整个遮罩为不透明深空底色，圆角面板居中绘制，圆角边缘不会露出后面；
- 主窗口在遮罩存在期间保持隐藏（见 ``MainWindow._deferred_show``），
  启动完成后才由本组件负责显示，因此不会提前绘制标题栏、菜单栏与背景。

用法（app.py / bootstrap.py 内）：
    splash = SplashScreen(logo=pixmap)
    splash.start()
    splash.set_stage(20, "正在检测本地 Ollama 服务…")
    ...
    splash.finish_to(window)
"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEventLoop,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..utils.logger import get_logger

logger = get_logger("ui.splash_screen")

# 品牌色（低饱和科技蓝），区别于主界面强调色，仅用于启动画面
_ACCENT = "#5B8CFF"
_ACCENT_LIGHT = "#8FB0FF"
_TRACK = QColor(255, 255, 255, 30)
_TITLE = "#F2F6FF"
_SUB = "#8FA3C6"
_STATUS = "#B7C4DA"
_MUTED = "#7C8CAD"

# 帧间隔（毫秒）：同时驱动进度平滑与旋转相位
_FRAME_MS = 30
# 单圈旋转时长（毫秒）
_LOOP_MS = 2200
# 从显示到开始收尾的最短停留时间（避免一闪而过）
_MIN_SHOW_MS = 900

# 居中品牌面板尺寸（保持原有版式，整体在遮罩中垂直居中）
_CONTENT_W = 560
_CONTENT_H = 460
# 收尾过渡时长（毫秒）：先淡出遮罩，再显示主窗口并淡入
_FADE_OUT_MS = 180
_FADE_IN_MS = 180


def _force_opaque(window: Optional[QWidget]) -> None:
    """兜底：确保主窗口最终完全不透明（过渡动画被打断时使用）。"""
    try:
        if window is not None and window.windowOpacity() < 1.0:
            window.setWindowOpacity(1.0)
    except Exception:
        pass


class SplashScreen(QWidget):
    """自绘的深色品牌启动闪屏（真实进度版）。"""

    #: 用户点击「跳过加载」
    skip_requested = pyqtSignal()

    def __init__(self, logo: Optional[QPixmap] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._logo = logo if (logo is not None and not logo.isNull()) else None
        # 无边框 + 始终置顶 + Tool（不占任务栏）；铺满主屏后即为全局独占遮罩
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 应用级模态：启动期间禁止点击后方任何窗口（含主窗口）
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setWindowOpacity(0.0)
        self.setCursor(Qt.CursorShape.BusyCursor)
        self.resize(_CONTENT_W, _CONTENT_H)

        self._target = 0.0         # 业务目标进度 0..100
        self._progress = 0.0       # 平滑渲染进度 0..100
        self._stage_text = "正在准备启动…"
        self._phase = 0.0          # 旋转相位（0→1 循环）
        self._done = False         # 进入收尾过渡后停止动效
        self._started_at = 0.0
        self._skip_requested = False
        self._skip_enabled = False
        self._drag_offset: Optional[QPoint] = None
        # 铺满主屏后拖拽会把遮罩移开露出桌面，因此全屏遮罩下禁用拖拽
        self._drag_enabled = True
        self._overlay: Optional[QWidget] = None

        self._skip_btn = QPushButton("跳过非核心模块", self)
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.setToolTip("跳过 OCR 预加载、知识库预扫描，直接进入主界面（后台静默继续）")
        self._skip_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.06);"
            f" color: {_MUTED}; border: 1px solid rgba(143,176,255,0.35);"
            " border-radius: 15px; padding: 6px 16px; font-size: 12px; }"
            f"QPushButton:hover {{ color: {_TITLE};"
            " border: 1px solid rgba(143,176,255,0.75); }"
            "QPushButton:disabled { color: rgba(124,140,173,0.45);"
            " border: 1px solid rgba(143,176,255,0.14); background: transparent; }"
        )
        self._skip_btn.clicked.connect(self._on_skip_clicked)
        self._skip_btn.setVisible(False)

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(_FRAME_MS)
        self._frame_timer.timeout.connect(self._on_frame)
        self._anim_group = None

    # ------------------------------------------------------------------
    # 对外接口：进度与阶段
    # ------------------------------------------------------------------

    def set_progress(self, percent: float, text: Optional[str] = None) -> None:
        """更新目标进度（0~100）与可选阶段文案。"""
        try:
            value = float(percent)
        except (TypeError, ValueError):
            return
        self._target = max(0.0, min(100.0, value))
        if text:
            self._stage_text = str(text)
        self.update()

    def set_stage(self, percent: float, text: str) -> None:
        """更新阶段：进度 + 底部文案。"""
        self.set_progress(percent, text)

    def stage_text(self) -> str:
        return self._stage_text

    def progress_value(self) -> float:
        return self._target

    def set_skip_available(self, available: bool) -> None:
        """控制「跳过加载」按钮（仅非核心阶段可用）。"""
        self._skip_enabled = bool(available) and not self._done
        self._skip_btn.setVisible(self._skip_enabled)
        self._skip_btn.setEnabled(self._skip_enabled)
        if self._skip_enabled:
            self._layout_children()

    def is_skip_requested(self) -> bool:
        return self._skip_requested

    def _on_skip_clicked(self) -> None:
        if not self._skip_enabled:
            return
        self._skip_requested = True
        self._skip_btn.setEnabled(False)
        self._skip_btn.setText("已跳过")
        self.set_progress(max(self._target, 60.0), "已跳过非核心初始化，正在进入主界面…")
        self.skip_requested.emit()

    def pump(self) -> None:
        """在处理耗时阶段的间隙调用：刷新动画并处理跳过 / 超时等事件。"""
        QApplication.processEvents()

    # ------------------------------------------------------------------
    # 对外接口：启动 / 收尾
    # ------------------------------------------------------------------

    def start(self) -> None:
        """铺满主屏并淡入（真正的全局独占遮罩）。"""
        self._cover_primary_screen()
        self._started_at = time.monotonic()
        self._layout_children()
        self.show()
        self.raise_()
        self.activateWindow()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(320)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._frame_timer.start()
        QApplication.processEvents()

    def _cover_primary_screen(self) -> None:
        """铺满用户所在屏幕（含任务栏区域），保证任何边缘都不会露出后面的主窗口。

        多显示器时取光标所在的那块屏：用户实际在看哪块屏，启动页就盖满哪块屏，
        避免遮罩出现在副屏、主屏却露着桌面。
        """
        screen = self._target_screen()
        if screen is None:
            return
        try:
            self.setGeometry(screen.geometry())
            # 已是全屏遮罩，拖拽会让遮罩移开露出桌面，故禁用拖拽
            self._drag_enabled = False
        except Exception as e:
            logger.warning("Failed to cover screen with splash: %s", e)

    @staticmethod
    def _target_screen():
        """返回光标所在屏幕；取不到时退回主屏。"""
        try:
            from PyQt6.QtGui import QCursor

            screen = QApplication.screenAt(QCursor.pos())
            if screen is not None:
                return screen
        except Exception:
            pass
        return QApplication.primaryScreen()

    def finish_to(self, window: Optional[QWidget]) -> None:
        """主窗口就绪后调用：保证最短展示时长后收尾过渡。"""
        remaining = _MIN_SHOW_MS - int((time.monotonic() - self._started_at) * 1000)
        delay = max(0, remaining)
        QTimer.singleShot(delay, lambda: self._begin_transition(window))

    def close_now(self) -> None:
        """直接关闭（退出流程使用，不做过渡动画）。"""
        self._done = True
        self._frame_timer.stop()
        self.hide()
        self.close()

    # ------------------------------------------------------------------
    # 对外接口：提示卡片（阻塞等待用户选择）
    # ------------------------------------------------------------------

    def ask_user(
        self,
        title: str,
        message: str,
        options: Sequence[Tuple[str, str]],
        default_key: str = "",
    ) -> str:
        """在闪屏上叠加提示卡片，阻塞等待用户选择，返回所选 key。"""
        if not options:
            return default_key
        result = {"key": default_key or options[0][1]}
        loop = QEventLoop()

        overlay = QFrame(self)
        overlay.setGeometry(self.rect())
        overlay.setStyleSheet("background: rgba(8, 12, 22, 190); border: none;")
        overlay.show()

        card = QFrame(overlay)
        card.setStyleSheet(
            "background: #16203A; border: 1px solid rgba(130,155,255,0.42);"
            "border-radius: 14px;"
        )
        card.setFixedWidth(420)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 20)
        card_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {_TITLE}; font-size: 16px; font-weight: bold;")
        card_layout.addWidget(title_label)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {_STATUS}; font-size: 13px;")
        card_layout.addWidget(body)

        button_row = QHBoxLayout()
        button_row.addStretch()

        def _choose(key: str) -> None:
            result["key"] = key
            overlay.hide()
            loop.quit()

        for index, (label, key) in enumerate(options):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if index == 0:
                button.setStyleSheet(
                    f"QPushButton {{ background: {_ACCENT}; color: #FFFFFF;"
                    " border: none; border-radius: 8px; padding: 8px 20px;"
                    " font-size: 13px; font-weight: bold; }"
                    "QPushButton:hover { background: #6E9BFF; }"
                )
            else:
                button.setStyleSheet(
                    "QPushButton { background: rgba(255,255,255,0.08);"
                    f" color: {_STATUS}; border: 1px solid rgba(143,176,255,0.35);"
                    " border-radius: 8px; padding: 8px 20px; font-size: 13px; }"
                    f"QPushButton:hover {{ color: {_TITLE}; }}"
                )
            button.clicked.connect(lambda _=False, k=key: _choose(k))
            button_row.addWidget(button)
        card_layout.addLayout(button_row)

        card.adjustSize()
        card.move(
            (self.width() - card.width()) // 2,
            (self.height() - card.height()) // 2,
        )
        self._overlay = overlay
        QApplication.processEvents()
        loop.exec()
        self._overlay = None
        overlay.deleteLater()
        return result["key"]

    # ------------------------------------------------------------------
    # 布局（子控件绝对定位）
    # ------------------------------------------------------------------

    def _layout_children(self) -> None:
        """子控件按居中面板定位（面板整体在遮罩中垂直居中）。"""
        self._skip_btn.adjustSize()
        size = self._skip_btn.size()
        left, top = self._panel_origin()
        self._skip_btn.move(
            int(left + _CONTENT_W - size.width() - 22),
            int(top + _CONTENT_H - size.height() - 20),
        )

    def _panel_origin(self) -> Tuple[float, float]:
        """居中品牌面板左上角坐标（超小屏时退化为 0）。"""
        left = (self.width() - _CONTENT_W) / 2.0
        top = (self.height() - _CONTENT_H) / 2.0
        return max(0.0, left), max(0.0, top)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_children()

    # ------------------------------------------------------------------
    # 拖拽移动
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._drag_enabled:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None

    # ------------------------------------------------------------------
    # 内部动效
    # ------------------------------------------------------------------

    def _on_frame(self) -> None:
        if self._done:
            return
        # 旋转相位持续推进（视觉上有"在动"的反馈）
        self._phase = (self._phase + _FRAME_MS / _LOOP_MS) % 1.0
        # 平滑逼近目标进度：环与进度条由同一个 _progress 渲染，节奏一致
        delta = self._target - self._progress
        if abs(delta) < 0.15:
            self._progress = self._target
        else:
            self._progress += delta * 0.2
        self.update()

    def _begin_transition(self, window: Optional[QWidget]) -> None:
        """停止动效：遮罩先完全淡出，随后再显示并淡入主窗口。

        顺序过渡（而非交叉淡化）可确保遮罩彻底消失前主窗口始终不可见，
        不会出现"外圈露出主窗口"的中间态。
        """
        self._done = True
        self._frame_timer.stop()
        self._target = 100.0
        self._progress = 100.0
        self._stage_text = "进入主程序…"
        self._skip_btn.hide()
        self.update()

        fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        fade_out.setDuration(_FADE_OUT_MS)
        fade_out.setStartValue(self.windowOpacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        fade_out.finished.connect(lambda: self._reveal_window(window))
        self._anim_group = fade_out
        fade_out.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _reveal_window(self, window: Optional[QWidget]) -> None:
        """遮罩已完全淡出：隐藏自身后再显示主窗口并淡入。"""
        self.hide()
        if window is None:
            self._on_transition_finished()
            return
        try:
            window.setWindowOpacity(0.0)
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as e:
            logger.warning("Failed to reveal main window: %s", e)
            try:
                window.setWindowOpacity(1.0)
            except Exception:
                pass
            self._on_transition_finished()
            return

        fade_in = QPropertyAnimation(window, b"windowOpacity", window)
        fade_in.setDuration(_FADE_IN_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade_in.finished.connect(self._on_transition_finished)
        self._anim_group = fade_in
        fade_in.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        # 兜底：动画被打断时主窗口也必须完全可见
        QTimer.singleShot(_FADE_IN_MS + 120, lambda w=window: _force_opaque(w))

    def _on_transition_finished(self) -> None:
        self.hide()
        self.close()
        self.deleteLater()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        panel_left, panel_top = self._panel_origin()
        logo_cy = panel_top + 168.0
        ratio = max(0.0, min(1.0, self._progress / 100.0))

        # 全屏遮罩底：不透明深空色，保证任何边缘都不会露出后面的主窗口
        backdrop = QLinearGradient(0, 0, 0, h)
        backdrop.setColorAt(0.0, QColor("#0A0F1C"))
        backdrop.setColorAt(1.0, QColor("#05070D"))
        painter.fillRect(self.rect(), backdrop)

        # 居中品牌面板：沿用原有深空渐变圆角面板（版式与尺寸不变）
        panel = QPainterPath()
        panel.addRoundedRect(
            QRectF(panel_left + 0.5, panel_top + 0.5, _CONTENT_W - 1, _CONTENT_H - 1),
            24,
            24,
        )
        grad = QLinearGradient(0, panel_top, 0, panel_top + _CONTENT_H)
        grad.setColorAt(0.0, QColor("#1D2842"))
        grad.setColorAt(1.0, QColor("#111A2E"))
        painter.fillPath(panel, grad)
        painter.setPen(QPen(QColor(130, 155, 255, 46), 1))
        painter.drawPath(panel)

        # Logo 光晕（柔和径向渐变，克制不刺眼）
        halo = QRadialGradient(QPointF(cx, logo_cy), 150)
        halo.setColorAt(0.0, QColor(91, 140, 255, 42))
        halo.setColorAt(1.0, QColor(91, 140, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(cx, logo_cy), 150, 150)

        # 外圈进度环：轨道 + 按真实进度延展的弧（叠加缓慢旋转相位）
        ring_r = 66.0
        ring_rect = QRectF(cx - ring_r, logo_cy - ring_r, ring_r * 2, ring_r * 2)
        track_pen = QPen(_TRACK, 3)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(track_pen)
        painter.drawEllipse(ring_rect)

        arc_pen = QPen(QColor(_ACCENT_LIGHT), 3)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        if self._done or ratio >= 1.0:
            painter.setPen(QPen(QColor(_ACCENT), 3))
            painter.drawEllipse(ring_rect)
        elif ratio > 0.0:
            # 弧长跟随真实进度，起点叠加旋转相位，保留"在加载"的动感
            sweep = int(360 * ratio * 16)
            start_angle = int(-90 * 16 + self._phase * 360 * 16)
            painter.drawArc(ring_rect, start_angle, sweep)
        else:
            # 尚未拿到进度时，用一段短弧循环旋转，表示已开始工作
            start_angle = int(-90 * 16 + self._phase * 360 * 16)
            painter.drawArc(ring_rect, start_angle, int(104 * 16))

        # 品牌 Logo（无图时绘制首字母 P）
        if self._logo is not None:
            size = 96
            painter.drawPixmap(
                int(cx - size / 2), int(logo_cy - size / 2), size, size, self._logo
            )
        else:
            font = QFont("Microsoft YaHei UI", 44)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(_ACCENT_LIGHT))
            logo_rect = QRectF(cx - 80, logo_cy - 80, 160, 160)
            painter.drawText(logo_rect, int(Qt.AlignmentFlag.AlignCenter), "P")

        # 品牌名与副标题（保持原有字体层级与位置）
        font = QFont("Microsoft YaHei UI", 34)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(_TITLE))
        title_rect = QRectF(panel_left, panel_top + 236, _CONTENT_W, 56)
        painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), "PLOS AI")

        font = QFont("Microsoft YaHei UI", 13)
        painter.setFont(font)
        painter.setPen(QColor(_SUB))
        sub_rect = QRectF(panel_left, panel_top + 294, _CONTENT_W, 28)
        painter.drawText(sub_rect, int(Qt.AlignmentFlag.AlignCenter), "个人学习操作系统 · Learning OS")

        # 加载进度条：宽度 = 真实进度
        line_y = panel_top + 348.0
        margin = 116.0
        line_x = panel_left + margin
        line_w = _CONTENT_W - margin * 2
        fill_w = line_w * ratio

        track_path = QPainterPath()
        track_path.addRoundedRect(QRectF(line_x, line_y, line_w, 3), 1.5, 1.5)
        painter.fillPath(track_path, QColor(255, 255, 255, 18))

        if fill_w > 0:
            fill_path = QPainterPath()
            fill_path.addRoundedRect(
                QRectF(line_x, line_y, max(3.0, fill_w), 3), 1.5, 1.5
            )
            line_grad = QLinearGradient(line_x, 0, line_x + max(3.0, fill_w), 0)
            line_grad.setColorAt(0.0, QColor(_ACCENT))
            line_grad.setColorAt(1.0, QColor(_ACCENT_LIGHT))
            painter.fillPath(fill_path, line_grad)

        # 百分比（进度条右端外侧，辅助读数）
        font = QFont("Microsoft YaHei UI", 10)
        painter.setFont(font)
        painter.setPen(QColor(_MUTED))
        percent_rect = QRectF(panel_left + _CONTENT_W - margin + 10, line_y - 8, 60, 18)
        painter.drawText(
            percent_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"{int(round(self._progress))}%",
        )

        # 阶段文案（随真实阶段变化）
        font = QFont("Microsoft YaHei UI", 12)
        painter.setFont(font)
        painter.setPen(QColor(_STATUS))
        status_rect = QRectF(panel_left, panel_top + 372, _CONTENT_W, 30)
        painter.drawText(
            status_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            self._stage_text,
        )

        painter.end()


def loading_stage_labels() -> List[str]:
    """启动阶段文案（与 bootstrap 的 6 个阶段一一对应，供测试引用）。"""
    return [
        "正在检测本地 Ollama 服务…",
        "正在读取知识库索引…",
        "正在检测硬件配置…",
        "正在加载基础 UI 组件…",
        "正在加载基础模型配置…",
        "准备完成，进入主程序…",
    ]


__all__ = ["SplashScreen", "loading_stage_labels"]
