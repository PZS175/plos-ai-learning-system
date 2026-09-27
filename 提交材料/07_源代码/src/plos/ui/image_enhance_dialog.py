"""图片预处理预览面板。

导入图片 / OCR 识别 / 错题截图前的统一前置环节：默认自动执行「一键清晰」，
用户可按需叠加笔迹去除、透视校正、二值化，左右对比确认后再进入原流程。

设计约束：
- 所有图像处理在后台线程执行，界面不卡死，处理中可取消
- 处理失败自动回退原图，绝不阻塞导入
- 面板样式跟随全局深浅色主题，静态扁平、无动效堆叠
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..services import ImageEnhanceService
from ..services.image_enhance_service import (
    PREVIEW_MAX,
    EnhanceOptions,
    EnhanceResult,
)
from ..utils.logger import get_logger
from .ui_utils import (
    ButtonBusy,
    apply_card_style,
    set_primary_button_style,
    theme_colors,
)
from .workers import CallableWorker, ThreadPool

logger = get_logger("ui.image_enhance_dialog")

_STRENGTH_ORDER = ("low", "mid", "high")
_STRENGTH_TEXT = {"low": "低", "mid": "中", "high": "高"}
_PEN_COLOR_TEXT = {"auto": "自动识别", "blue": "仅蓝色笔迹", "black": "仅黑色笔迹"}

#: 参数变化后重新处理的防抖时长（拖动滑块时不会反复重跑）
_REPROCESS_DEBOUNCE_MS = 350


def load_preview_pixmap(path: Path, max_side: int = PREVIEW_MAX) -> QPixmap:
    """用 PIL 先缩略再转 QPixmap：避免大图在 Qt 里整张解码占内存。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            data = image.tobytes("raw", "RGB")
            buffer = QImage(
                data, image.width, image.height, image.width * 3,
                QImage.Format.Format_RGB888,
            )
            return QPixmap.fromImage(buffer.copy())
    except Exception as e:
        logger.warning("Load preview failed (%s): %s", path, e)
        return QPixmap()


class _PreviewBox(QLabel):
    """等比缩放显示预览图，尺寸变化时自动重排。"""

    def __init__(self, caption: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._caption = caption
        self._source: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(300, 300))
        self.setWordWrap(True)
        self._apply_style()
        self.set_pixmap_source(None)

    def _apply_style(self) -> None:
        c = theme_colors()
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {c['bg_tertiary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                color: {c['fg_muted']};
                font-size: 12px;
            }}
            """
        )

    def set_pixmap_source(self, pixmap: Optional[QPixmap]) -> None:
        self._source = pixmap
        if pixmap is None or pixmap.isNull():
            self.setPixmap(QPixmap())
            self.setText("暂无预览")
            return
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None:
            return
        self.setPixmap(
            self._source.scaled(
                self.size() - QSize(12, 12),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()


class _RegionSelectDialog(QDialog):
    """手动框选保留区域：在缩略图上拖拽，返回原图坐标的矩形四点。"""

    def __init__(self, image_path: Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("框选保留区域")
        self.resize(860, 660)
        self._image_path = Path(image_path)

        self._pixmap = load_preview_pixmap(self._image_path, 820)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        tip = QLabel("在图片上按住左键拖拽，框出要保留的区域（会自动做透视拉正）。")
        tip.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 12px;")
        layout.addWidget(tip)

        self.canvas = _SelectionCanvas(self._pixmap, self)
        layout.addWidget(self.canvas, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        confirm = QPushButton("使用该区域")
        set_primary_button_style(confirm)
        confirm.clicked.connect(self._confirm)
        bottom.addWidget(confirm)
        layout.addLayout(bottom)

    def _confirm(self) -> None:
        if self.canvas.selection_in_source() is None:
            return
        self.accept()

    def selected_quad(self) -> Optional[List[List[float]]]:
        """返回原图坐标的四点（左上/右上/右下/左下）。"""
        box = self.canvas.selection_in_source()
        if box is None:
            return None
        x, y, width, height = box
        return [
            [float(x), float(y)],
            [float(x + width), float(y)],
            [float(x + width), float(y + height)],
            [float(x), float(y + height)],
        ]


class _SelectionCanvas(QWidget):
    """显示缩略图并支持拖拽框选，负责换算回原图坐标。"""

    def __init__(self, pixmap: QPixmap, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._origin = (0.0, 0.0)
        self._display_scale = 1.0
        self._selection: Optional[QRect] = None
        self._anchor = None
        self.setMinimumSize(QSize(600, 420))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        c = theme_colors()
        self.setStyleSheet(
            f"background-color: {c['bg_tertiary']}; border: 1px solid {c['border']};"
            " border-radius: 8px;"
        )

    def _scaled_size(self) -> QSize:
        if self._pixmap.isNull():
            return QSize(0, 0)
        return self._pixmap.size().scaled(
            self.size() - QSize(16, 16), Qt.AspectRatioMode.KeepAspectRatio
        )

    def _update_geometry(self) -> None:
        if self._pixmap.isNull():
            return
        scaled = self._scaled_size()
        if scaled.width() <= 0:
            return
        self._display_scale = scaled.width() / float(self._pixmap.width())
        self._origin = (
            (self.width() - scaled.width()) / 2.0,
            (self.height() - scaled.height()) / 2.0,
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._pixmap.isNull():
            return
        self._update_geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled = self._scaled_size()
        target = QRect(int(self._origin[0]), int(self._origin[1]), scaled.width(), scaled.height())
        painter.drawPixmap(target, self._pixmap)
        if self._selection is not None:
            pen = QPen(QColor(theme_colors()["accent"]), 2)
            painter.setPen(pen)
            painter.drawRect(self._selection)
            painter.fillRect(self._selection, QColor(79, 93, 245, 40))

    def _clamp_to_image(self, point) -> QRect:
        rect = QRect(self._anchor, point).normalized()
        scaled = self._scaled_size()
        bounds = QRect(
            int(self._origin[0]), int(self._origin[1]), scaled.width(), scaled.height()
        )
        return rect.intersected(bounds)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap.isNull():
            return
        self._anchor = event.position().toPoint()
        self._selection = QRect(self._anchor, self._anchor)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._anchor is None:
            return
        self._selection = self._clamp_to_image(event.position().toPoint())
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._anchor is None:
            return
        self._selection = self._clamp_to_image(event.position().toPoint())
        self._anchor = None
        self.update()

    def selection_in_source(self) -> Optional[Tuple[int, int, int, int]]:
        """把显示坐标的选区换算成原图坐标的 (x, y, w, h)。"""
        if self._selection is None or self._display_scale <= 0:
            return None
        rect = self._selection
        if rect.width() < 12 or rect.height() < 12:
            return None
        x = (rect.left() - self._origin[0]) / self._display_scale
        y = (rect.top() - self._origin[1]) / self._display_scale
        width = rect.width() / self._display_scale
        height = rect.height() / self._display_scale
        return int(max(0, x)), int(max(0, y)), int(width), int(height)


class ImageEnhanceDialog(QDialog):
    """图片预处理面板。"""

    enhance_confirmed = pyqtSignal(str, list)   # 处理后图片路径, 生效步骤
    enhance_skipped = pyqtSignal(str)           # 原图路径
    enhance_cancelled = pyqtSignal()
    enhance_failed = pyqtSignal(str)            # 失败原因（已自动回退原图）
    _progress_changed = pyqtSignal(int, str)

    def __init__(
        self,
        image_path: Path,
        service: Optional[ImageEnhanceService] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.image_path = Path(image_path)
        self.service = service or ImageEnhanceService()

        self._result: Optional[EnhanceResult] = None
        self._busy = False
        self._cancel_event: Optional[threading.Event] = None
        self._manual_quad: Optional[List[List[float]]] = None
        self._original_pixmap = load_preview_pixmap(self.image_path)

        self.setWindowTitle("图片预处理")
        self.resize(1000, 760)
        self._build_ui()
        self._progress_changed.connect(self._on_progress)

        self._reprocess_timer = QTimer(self)
        self._reprocess_timer.setSingleShot(True)
        self._reprocess_timer.setInterval(_REPROCESS_DEBOUNCE_MS)
        self._reprocess_timer.timeout.connect(self._process)

        self._left.set_pixmap_source(self._original_pixmap)
        self._right.set_pixmap_source(self._original_pixmap)
        self._left_caption.setText(f"原图　{self.image_path.name}")

        # 打开即自动执行「一键清晰」，让用户直接看到效果。
        # 用挂在弹窗上的 QTimer（而非 QTimer.singleShot）：弹窗被关闭销毁时
        # 定时器一并销毁，不会在对象释放后回调触发崩溃。
        self._autostart_timer = QTimer(self)
        self._autostart_timer.setSingleShot(True)
        self._autostart_timer.timeout.connect(self._process)
        self._autostart_timer.start(0)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("图片预处理")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(
            "默认已执行「一键清晰」。可继续叠加笔迹去除、透视校正与二值化，确认后再进入识别/入库流程。"
        )
        subtitle.setObjectName("card_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # 预览对比区
        preview_row = QHBoxLayout()
        preview_row.setSpacing(12)

        left_column = QVBoxLayout()
        left_column.setSpacing(6)
        self._left_caption = QLabel("原图")
        self._left_caption.setObjectName("card_subtitle")
        left_column.addWidget(self._left_caption)
        self._left = _PreviewBox("原图")
        left_column.addWidget(self._left, 1)
        preview_row.addLayout(left_column, 1)

        right_column = QVBoxLayout()
        right_column.setSpacing(6)
        right_header = QHBoxLayout()
        self._right_caption = QLabel("处理后")
        self._right_caption.setObjectName("card_subtitle")
        right_header.addWidget(self._right_caption)
        right_header.addStretch()
        self._compare_btn = QPushButton("按住对比原图")
        self._compare_btn.setToolTip("按住时右侧显示原图，松开回到处理结果")
        self._compare_btn.pressed.connect(lambda: self._show_compare(True))
        self._compare_btn.released.connect(lambda: self._show_compare(False))
        right_header.addWidget(self._compare_btn)
        right_column.addLayout(right_header)
        self._right = _PreviewBox("处理后")
        right_column.addWidget(self._right, 1)
        preview_row.addLayout(right_column, 1)

        layout.addLayout(preview_row, 1)

        self._status_label = QLabel("正在准备…")
        self._status_label.setObjectName("card_subtitle")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # 处理选项
        layout.addWidget(self._build_options())

        # 局部加载区
        self._progress_row = QWidget()
        progress_layout = QHBoxLayout(self._progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(10)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self._progress_bar, 1)
        self._cancel_process_btn = QPushButton("取消处理")
        self._cancel_process_btn.clicked.connect(self._cancel_processing)
        progress_layout.addWidget(self._cancel_process_btn)
        self._progress_row.setVisible(False)
        layout.addWidget(self._progress_row)

        # 底部操作
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._skip_btn = QPushButton("跳过")
        self._skip_btn.setToolTip("不处理，直接用原图继续导入")
        self._skip_btn.clicked.connect(self._skip)
        bottom.addWidget(self._skip_btn)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setToolTip("放弃本次导入")
        self._cancel_btn.clicked.connect(self._cancel)
        bottom.addWidget(self._cancel_btn)
        self._reprocess_btn = QPushButton("重新处理")
        self._reprocess_btn.clicked.connect(self._process)
        bottom.addWidget(self._reprocess_btn)
        self._confirm_btn = QPushButton("确认使用")
        set_primary_button_style(self._confirm_btn)
        self._confirm_btn.setMinimumWidth(120)
        self._confirm_btn.clicked.connect(self._confirm)
        self._confirm_busy = ButtonBusy(self._confirm_btn, "处理中…")
        bottom.addWidget(self._confirm_btn)
        layout.addLayout(bottom)

    def _build_options(self) -> QWidget:
        card = QFrame()
        apply_card_style(card)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        head = QLabel("处理选项")
        head.setStyleSheet("font-size: 13px; font-weight: 600;")
        outer.addWidget(head)

        self._clear_check = QCheckBox("一键清晰增强（背景净化 / 对比度 / 锐化 / 降噪）")
        self._clear_check.setChecked(True)
        outer.addWidget(self._clear_check)

        deskew_row = QHBoxLayout()
        self._deskew_check = QCheckBox("透视校正与裁切（自动检测纸张边缘，裁掉桌面背景）")
        self._deskew_check.setChecked(True)
        deskew_row.addWidget(self._deskew_check)
        self._region_btn = QPushButton("手动框选…")
        self._region_btn.setToolTip("在图片上框出要保留的区域，优先于自动检测")
        self._region_btn.clicked.connect(self._pick_region)
        deskew_row.addWidget(self._region_btn)
        self._region_label = QLabel("未框选")
        self._region_label.setObjectName("card_subtitle")
        deskew_row.addWidget(self._region_label)
        self._clear_region_btn = QPushButton("清除")
        self._clear_region_btn.setToolTip("清除手动框选，恢复自动检测纸张边缘")
        self._clear_region_btn.setVisible(False)
        self._clear_region_btn.clicked.connect(self._clear_region)
        deskew_row.addWidget(self._clear_region_btn)
        deskew_row.addStretch()
        outer.addLayout(deskew_row)

        self._binary_check = QCheckBox("二值化（白底黑字，便于打印与 OCR；中档保留图表与图片层次）")
        outer.addWidget(self._binary_check)

        pen_row = QHBoxLayout()
        self._pen_check = QCheckBox("笔迹去除（保留印刷体题目与图表线条）")
        pen_row.addWidget(self._pen_check)
        pen_row.addWidget(QLabel("着色："))
        self._pen_color_combo = QComboBox()
        for key, text in _PEN_COLOR_TEXT.items():
            self._pen_color_combo.addItem(text, key)
        pen_row.addWidget(self._pen_color_combo)
        pen_row.addStretch()
        outer.addLayout(pen_row)

        strength_row = QHBoxLayout()
        strength_row.addWidget(QLabel("处理强度："))
        self._strength_slider = QSlider(Qt.Orientation.Horizontal)
        self._strength_slider.setRange(0, len(_STRENGTH_ORDER) - 1)
        self._strength_slider.setValue(_STRENGTH_ORDER.index("mid"))
        self._strength_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._strength_slider.setTickInterval(1)
        self._strength_slider.setMaximumWidth(220)
        strength_row.addWidget(self._strength_slider)
        self._strength_label = QLabel(_STRENGTH_TEXT["mid"])
        strength_row.addWidget(self._strength_label)
        strength_row.addStretch()
        outer.addLayout(strength_row)

        # 任一参数变化都触发防抖重处理：拖动滑块时不会反复重跑
        for widget in (self._clear_check, self._deskew_check, self._binary_check, self._pen_check):
            widget.toggled.connect(self._on_options_changed)
        self._pen_color_combo.currentIndexChanged.connect(self._on_options_changed)
        self._strength_slider.valueChanged.connect(self._on_strength_changed)
        return card

    # ------------------------------------------------------------------
    # 选项 → 参数
    # ------------------------------------------------------------------

    def _strength(self) -> str:
        return _STRENGTH_ORDER[self._strength_slider.value()]

    def options(self) -> EnhanceOptions:
        return EnhanceOptions(
            auto_clear=self._clear_check.isChecked(),
            remove_pen=self._pen_check.isChecked(),
            pen_color=self._pen_color_combo.currentData() or "auto",
            strength=self._strength(),
            deskew=self._deskew_check.isChecked(),
            binarize=self._binary_check.isChecked(),
            manual_quad=self._manual_quad,
        ).normalized()

    def _on_strength_changed(self, value: int) -> None:
        self._strength_label.setText(_STRENGTH_TEXT[_STRENGTH_ORDER[value]])
        self._on_options_changed()

    def _on_options_changed(self) -> None:
        """参数变化后延迟重跑，避免连续点击时反复处理。"""
        if self._busy:
            return
        self._status_label.setText("参数已更改，正在重新处理…")
        self._reprocess_timer.start()

    def _show_compare(self, show_original: bool) -> None:
        if show_original:
            self._right.set_pixmap_source(self._original_pixmap)
            self._right_caption.setText("原图（对比中）")
        else:
            self._render_result()

    def _pick_region(self) -> None:
        dialog = _RegionSelectDialog(self.image_path, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        quad = dialog.selected_quad()
        if not quad:
            return
        self._manual_quad = quad
        self._region_label.setText("已框选")
        self._clear_region_btn.setVisible(True)
        self._deskew_check.setChecked(True)
        self._on_options_changed()

    def _clear_region(self) -> None:
        self._manual_quad = None
        self._region_label.setText("未框选")
        self._clear_region_btn.setVisible(False)
        self._on_options_changed()

    # ------------------------------------------------------------------
    # 处理
    # ------------------------------------------------------------------

    def _process(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._progress_bar.setValue(0)
        self._status_label.setText("正在整理图片…")

        options = self.options()
        self._cancel_event = threading.Event()
        task = self.service.enhance
        worker = CallableWorker(
            task,
            self.image_path,
            options,
            self._progress_changed.emit,
            self._cancel_event,
        )
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_worker_error)
        ThreadPool.start_worker(worker)

    def _on_progress(self, percent: int, message: str) -> None:
        self._progress_bar.setValue(max(0, min(100, int(percent))))
        if message:
            self._status_label.setText(message)

    def _on_result(self, result: object) -> None:
        self._set_busy(False)
        if not isinstance(result, EnhanceResult) or not result.ok:
            self._handle_failure(getattr(result, "error", "") or "未知原因")
            return
        self._result = result
        self._render_result()
        steps = "、".join(result.applied) if result.applied else "无"
        suffix = "（低配简化处理）" if result.degraded else ""
        self._status_label.setText(
            f"处理完成，用时 {result.duration_ms} ms　已应用：{steps}{suffix}"
        )

    def _render_result(self) -> None:
        if self._result is None or self._result.output_path is None:
            return
        self._right.set_pixmap_source(load_preview_pixmap(self._result.output_path))
        steps = "、".join(self._result.applied) if self._result.applied else "无"
        self._right_caption.setText(f"处理后（{steps}）")

    def _handle_failure(self, message: str) -> None:
        """失败一律回退原图，保证导入流程可继续。"""
        self._result = None
        logger.warning("Enhance failed, fallback to original: %s", message)
        self._right.set_pixmap_source(self._original_pixmap)
        self._right_caption.setText("处理后（失败，已回退原图）")
        self._status_label.setText(f"处理失败，已使用原图：{message}")
        self.enhance_failed.emit(message)

    def _on_worker_error(self, message: str) -> None:
        self._set_busy(False)
        self._handle_failure(message or "未知原因")

    def _cancel_processing(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self._status_label.setText("正在取消…")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._progress_row.setVisible(busy)
        self._reprocess_btn.setEnabled(not busy)
        self._skip_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(not busy)
        self._region_btn.setEnabled(not busy)
        if busy:
            self._confirm_busy.start()
        else:
            self._confirm_busy.restore()

    # ------------------------------------------------------------------
    # 结束动作
    # ------------------------------------------------------------------

    def _confirm(self) -> None:
        if self._result is not None and self._result.ok:
            self.enhance_confirmed.emit(
                str(self._result.output_path), list(self._result.applied)
            )
        else:
            # 没有可用结果时按「跳过」处理，不让用户卡在这一步
            self.enhance_skipped.emit(str(self.image_path))
        self.accept()

    def _skip(self) -> None:
        self.enhance_skipped.emit(str(self.image_path))
        self.accept()

    def _cancel(self) -> None:
        self.enhance_cancelled.emit()
        self.reject()

    def closeEvent(self, event) -> None:
        """处理中先取消后台任务，避免留下无主线程。"""
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().closeEvent(event)
