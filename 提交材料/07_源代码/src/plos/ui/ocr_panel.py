"""OCR / 识图解题面板（纯 VL 实现）。

支持选择图片、截图、剪贴板图片与拖拽上传，调用本地 Qwen2.5-VL 完成：
1. 提取图片文字（OCR）
2. 直接提问图片题目（解题）
3. 批量试卷 OCR 上传：多图识别、自动切分题目、批量导入错题本

当 Ollama 未启动或缺少 VL 模型时，图片相关按钮自动置灰并提示。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai.multimodal import ScreenshotCapture
from ..services import (
    ErrorBookService,
    ImageEnhanceService,
    OCRService,
    TerminologyService,
)
from ..utils.logger import get_logger
from .interactions import friendly_error_message, policy
from .math_text import MathTextBrowser, attach_live_preview, attach_math_delegate
from .ui_utils import (
    create_section_title,
    apply_glass_style,
    busy_cursor,
    set_clipboard_text,
    set_primary_button_style,
    show_success,
    show_toast,
    show_warning,
    task_finished,
    task_progress,
    task_started,
)
from .workers import (
    AutoTagWorker,
    BatchOCRWorker,
    CallableWorker,
    ExamPipelineWorker,
    OCRWorker,
    PhotoSolveWorker,
    ThreadPool,
    get_ai_pool,
)

logger = get_logger("ui.ocr_panel")


class _DropArea(QLabel):
    """支持拖拽与粘贴的大尺寸图片放置区。

    苹果式拖放反馈：拖入时高亮边框与底色，离开后立即恢复；
    识别过程中在图片区叠加**局部**轻量遮罩，主界面其余部分照常可操作。
    """

    fileDropped = pyqtSignal(Path)

    _EMPTY_STYLE = (
        "background-color: rgba(108, 124, 255, 0.07); "
        "border: 2px dashed rgba(108, 124, 255, 0.45); "
        "border-radius: 16px; color: #8A93A8; font-size: 14px;"
    )
    _EMPTY_HOVER_STYLE = (
        "background-color: rgba(108, 124, 255, 0.16); "
        "border: 2px dashed rgba(108, 124, 255, 0.85); "
        "border-radius: 16px; color: #6C7CFF; font-size: 14px;"
    )
    _IMAGE_STYLE = (
        "background-color: transparent; "
        "border: 2px solid rgba(108, 124, 255, 0.35); border-radius: 16px;"
    )
    _IMAGE_HOVER_STYLE = (
        "background-color: rgba(108, 124, 255, 0.10); "
        "border: 2px solid rgba(108, 124, 255, 0.80); border-radius: 16px;"
    )

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setText("拖拽图片到此处\n或点击「剪贴板」粘贴")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(380, 260)
        self.setAcceptDrops(True)
        self._has_image = False
        self._idle_style = self._EMPTY_STYLE
        self.setStyleSheet(self._idle_style)

        # 局部加载遮罩（纯文字提示，低配设备同样可用）
        self._busy_label = QLabel("", self)
        self._busy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._busy_label.setStyleSheet(
            "background-color: rgba(14, 17, 24, 0.55); color: #FFFFFF;"
            " font-size: 13px; border-radius: 16px;"
        )
        self._busy_label.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._busy_label.setGeometry(self.rect())

    def set_busy(self, busy: bool, text: str = "识别中，请稍候…") -> None:
        """显示 / 隐藏图片区域的局部加载遮罩（不阻塞界面其他操作）。"""
        try:
            self._busy_label.setText(text)
            self._busy_label.setGeometry(self.rect())
            if busy:
                self._busy_label.raise_()
                self._busy_label.show()
            else:
                self._busy_label.hide()
        except Exception:
            pass

    def set_image_style(self, has_image: bool) -> None:
        """切换「空态 / 已载入图片」两套基础样式。"""
        self._has_image = has_image
        self._idle_style = self._IMAGE_STYLE if has_image else self._EMPTY_STYLE
        self.setStyleSheet(self._idle_style)

    def _apply_drag_style(self, active: bool) -> None:
        try:
            if not active:
                self.setStyleSheet(self._idle_style)
            elif self._has_image:
                self.setStyleSheet(self._IMAGE_HOVER_STYLE)
            else:
                self.setStyleSheet(self._EMPTY_HOVER_STYLE)
        except Exception:
            pass

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            self._apply_drag_style(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._apply_drag_style(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._apply_drag_style(False)
        try:
            mime = event.mimeData()
            if mime.hasUrls():
                url = mime.urls()[0]
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    self.fileDropped.emit(path)
            elif mime.hasImage():
                image = QImage(mime.imageData())
                if not image.isNull():
                    temp_path = Path(tempfile.gettempdir()) / "plos_drop.png"
                    image.save(str(temp_path))
                    self.fileDropped.emit(temp_path)
        except Exception as e:
            logger.error("Failed to handle image drop: %s", e)
        event.acceptProposedAction()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Ctrl+V 由面板级 QShortcut 统一处理，这里仅兜底其余按键
        super().keyPressEvent(event)


class OCRPanel(QWidget):
    """图片 OCR 与识图解题界面。"""

    def __init__(
        self,
        ocr_service: OCRService,
        errorbook_service: Optional[ErrorBookService] = None,
        terminology_service: Optional[TerminologyService] = None,
        image_enhance_service: Optional[ImageEnhanceService] = None,
    ):
        super().__init__()
        self.ocr_service = ocr_service
        self.errorbook_service = errorbook_service
        self.terminology_service = terminology_service
        self.image_enhance_service = image_enhance_service or ImageEnhanceService()
        self.current_image_path: Path | None = None
        self._batch_results: List[dict] = []
        # 增强图与原图的对应关系：入库时引用增强后的图片
        self._enhanced_paths: Dict[str, str] = {}
        # 预处理弹窗引用：后台任务回调期间必须保活
        self._enhance_dialog = None
        self._build_ui()
        # 面板级 Ctrl+V：无需拖拽区获得焦点即可粘贴剪贴板图片
        paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        paste_shortcut.activated.connect(self.process_clipboard)
        self._update_image_service_status()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        main_layout.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：单张图片预览与操作
        left = QWidget()
        left.setProperty("glass", True)
        apply_glass_style(left)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(12)

        left_title = create_section_title("单张图片 OCR / 解题")
        left_layout.addWidget(left_title)

        self.image_label = _DropArea()
        self.image_label.fileDropped.connect(self.import_image)
        left_layout.addWidget(self.image_label)

        self.preprocess_check = QCheckBox("导入前先做图片预处理（增强 / 去笔迹 / 透视校正）")
        self.preprocess_check.setChecked(True)
        self.preprocess_check.setToolTip(
            "开启后，选择图片、截图、剪贴板与拖拽导入都会先进入预处理面板；"
            "在面板中可随时点「跳过」直接用原图"
        )
        left_layout.addWidget(self.preprocess_check)

        btn_layout = QHBoxLayout()
        btn_open = QPushButton("选择图片")
        btn_open.clicked.connect(self._open_image)
        btn_layout.addWidget(btn_open)

        btn_screenshot = QPushButton("截图")
        btn_screenshot.clicked.connect(self.start_screenshot)
        btn_layout.addWidget(btn_screenshot)

        btn_clipboard = QPushButton("剪贴板")
        btn_clipboard.clicked.connect(self.process_clipboard)
        btn_layout.addWidget(btn_clipboard)
        left_layout.addLayout(btn_layout)

        action_layout = QHBoxLayout()
        self.btn_ocr = QPushButton("提取图片文字(OCR)")
        set_primary_button_style(self.btn_ocr)
        self.btn_ocr.clicked.connect(self._run_ocr)
        action_layout.addWidget(self.btn_ocr)

        self.btn_solve = QPushButton("直接提问图片题目")
        set_primary_button_style(self.btn_solve)
        self.btn_solve.clicked.connect(self._run_solve)
        action_layout.addWidget(self.btn_solve)

        # 释放 VL 视觉模型：低配机器可在不做图片识别时腾出显存 / 内存
        self.btn_release = QPushButton("释放视觉模型")
        self.btn_release.setToolTip(
            "从显存 / 内存中卸载 VL 视觉模型（下次识别时自动重新加载），"
            "适合低配机器释放资源"
        )
        self.btn_release.clicked.connect(self._release_vision_model)
        action_layout.addWidget(self.btn_release)
        left_layout.addLayout(action_layout)

        left_layout.addWidget(QLabel("识别结果（可编辑）："))
        self.ocr_result_edit = QTextEdit()
        self.ocr_result_edit.setProperty("glass", True)
        apply_glass_style(self.ocr_result_edit)
        self.ocr_result_edit.setPlaceholderText("提取的文字将显示在这里，可直接编辑、复制...")
        self.ocr_result_edit.setMaximumHeight(160)
        left_layout.addWidget(self.ocr_result_edit)

        # 识别结果公式实时预览（编辑框本身保持可编辑）
        self.ocr_preview = MathTextBrowser(self)
        self.ocr_preview.setMaximumHeight(140)
        self.ocr_preview.set_placeholder_text("输入 \\(公式\\) 或 \\[公式\\] 可在此预览")
        left_layout.addWidget(self.ocr_preview)
        self._ocr_preview_connector = attach_live_preview(self.ocr_result_edit, self.ocr_preview)

        result_action_layout = QHBoxLayout()
        self.ocr_time_label = QLabel("")
        self.ocr_time_label.setStyleSheet("color: #8A93A8; font-size: 11px;")
        result_action_layout.addWidget(self.ocr_time_label)
        result_action_layout.addStretch()

        btn_format = QPushButton("一键格式化文本")
        btn_format.setToolTip("清理多余空行与换行，整理题目文本")
        btn_format.clicked.connect(self._format_ocr_text)
        result_action_layout.addWidget(btn_format)

        btn_copy = QPushButton("复制识别结果")
        btn_copy.clicked.connect(self._copy_ocr_result)
        result_action_layout.addWidget(btn_copy)

        self.btn_extract_terms = QPushButton("提取术语")
        self.btn_extract_terms.setToolTip("从识别文本中提取专业术语并存入术语词典")
        self.btn_extract_terms.clicked.connect(self._extract_terms_from_ocr)
        result_action_layout.addWidget(self.btn_extract_terms)
        left_layout.addLayout(result_action_layout)

        left_layout.addStretch()

        splitter.addWidget(left)

        # 右侧：批量试卷 OCR 与导入
        right = QWidget()
        right.setProperty("glass", True)
        apply_glass_style(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(12)

        right_title = create_section_title("批量试卷 OCR 导入")
        right_layout.addWidget(right_title)

        hint = QLabel("选择多张试卷图片，自动识别并切分题目，批量导入错题本")
        hint.setStyleSheet("color: #8A93A8; font-size: 12px;")
        hint.setWordWrap(True)
        right_layout.addWidget(hint)

        batch_btn_layout = QHBoxLayout()
        btn_batch_open = QPushButton("选择多张图片")
        btn_batch_open.clicked.connect(self._open_batch_images)
        batch_btn_layout.addWidget(btn_batch_open)

        btn_pipeline = QPushButton("全流程入库")
        btn_pipeline.setToolTip("识别 + AI 解答 + 自动入库错题本 + 联动学习计划（旗舰一键）")
        btn_pipeline.clicked.connect(self._run_pipeline)
        batch_btn_layout.addWidget(btn_pipeline)

        btn_batch_recognize = QPushButton("批量识别")
        set_primary_button_style(btn_batch_recognize)
        btn_batch_recognize.clicked.connect(self._run_batch_ocr)
        batch_btn_layout.addWidget(btn_batch_recognize)

        btn_clear_batch = QPushButton("清空")
        btn_clear_batch.clicked.connect(self._clear_batch)
        batch_btn_layout.addWidget(btn_clear_batch)
        batch_btn_layout.addStretch()
        right_layout.addLayout(batch_btn_layout)

        self.batch_tree = QTreeWidget()
        self.batch_tree.setHeaderLabels(["来源", "题目", "答案", "解析"])
        self.batch_tree.setColumnWidth(0, 120)
        self.batch_tree.setColumnWidth(1, 260)
        self.batch_tree.setColumnWidth(2, 120)
        # 题目 / 答案 / 解析列启用公式混排
        self._batch_math_delegates = [
            attach_math_delegate(self.batch_tree, 1),
            attach_math_delegate(self.batch_tree, 2),
            attach_math_delegate(self.batch_tree, 3),
        ]
        right_layout.addWidget(self.batch_tree)

        from PyQt6.QtWidgets import QProgressBar as _ProgressBar

        self.batch_progress_bar = _ProgressBar()
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setVisible(False)
        self.batch_progress_bar.setFixedHeight(8)
        self.batch_progress_bar.setTextVisible(False)
        right_layout.addWidget(self.batch_progress_bar)

        tag_layout = QHBoxLayout()
        tag_layout.addWidget(QLabel("科目标签："))
        self.subject_edit = QTextEdit()
        self.subject_edit.setPlaceholderText("批量设置科目，如：数学")
        self.subject_edit.setMaximumHeight(40)
        tag_layout.addWidget(self.subject_edit)
        right_layout.addLayout(tag_layout)

        import_layout = QHBoxLayout()
        import_layout.addStretch()
        btn_import_selected = QPushButton("导入选中题目到错题本")
        set_primary_button_style(btn_import_selected)
        btn_import_selected.clicked.connect(self._import_selected_questions)
        import_layout.addWidget(btn_import_selected)
        right_layout.addLayout(import_layout)

        self.solve_time_label = QLabel("")
        self.solve_time_label.setStyleSheet("color: #8A93A8; font-size: 11px;")
        right_layout.addWidget(self.solve_time_label)

        from .markdown_browser import MarkdownBrowser
        self.result_browser = MarkdownBrowser()
        self.result_browser.setProperty("glass", True)
        apply_glass_style(self.result_browser)
        self.result_browser.set_placeholder_text("单张解题结果或批量识别日志将显示在这里...")
        right_layout.addWidget(self.result_browser)

        splitter.addWidget(right)
        splitter.setSizes([520, 680])

        layout.addWidget(splitter)

    def _update_image_service_status(self) -> None:
        """异步检查 VL 服务可用性并更新界面状态。

        探测会访问 Ollama（列出 VL 模型），属于网络请求，
        **必须放到后台线程**，否则打开面板时会阻塞 UI 主线程导致卡顿。
        """
        self.btn_ocr.setEnabled(False)
        self.btn_solve.setEnabled(False)
        worker = CallableWorker(self._probe_image_service)
        worker.signals.result.connect(self._apply_image_service_status)
        worker.signals.error.connect(self._on_image_service_error)
        ThreadPool.start_worker(worker)

    def _probe_image_service(self):
        """后台线程执行：探测 VL 服务并返回 (可用, 状态文案)。"""
        available = self.ocr_service.is_image_service_available()
        return bool(available), self.ocr_service.get_status_message()

    def _apply_image_service_status(self, payload) -> None:
        try:
            available, message = payload
        except Exception as e:
            logger.error("Failed to apply image service status: %s", e)
            return

        self.btn_ocr.setEnabled(available)
        self.btn_solve.setEnabled(available)

        if available:
            tooltip = "使用 Qwen2.5-VL 处理图片"
            self.btn_ocr.setToolTip(tooltip)
            self.btn_solve.setToolTip(tooltip)
        else:
            self.btn_ocr.setToolTip(message)
            self.btn_solve.setToolTip(message)
            # MarkdownBrowser 的 HTML 入口是 set_html：调用 setHtml 会抛
            # AttributeError，而槽函数里的未捕获异常会让 PyQt 直接 abort 整个进程
            self.result_browser.set_html(
                f"<b>{message}</b><br/>"
                "对话、RAG、错题本、闪卡等其他功能不受影响。"
            )
            logger.warning("Image service unavailable: %s", message)

    def _on_image_service_error(self, message: str) -> None:
        logger.error("Failed to update image service status: %s", message)

    def _release_vision_model(self) -> None:
        """卸载 VL 视觉模型释放显存 / 内存（网络请求放后台线程执行）。"""
        self.btn_release.setEnabled(False)
        worker = CallableWorker(self._do_release_vision_model)
        worker.signals.result.connect(self._on_release_vision_done)
        worker.signals.error.connect(self._on_release_vision_error)
        ThreadPool.start_worker(worker)

    def _do_release_vision_model(self) -> bool:
        return bool(self.ocr_service.release_vision_model())

    def _on_release_vision_done(self, released: bool) -> None:
        self.btn_release.setEnabled(True)
        if released:
            show_success(self, "视觉模型已卸载，显存 / 内存已释放")
        else:
            show_warning(self, "当前后端无需卸载，或卸载未成功")

    def _on_release_vision_error(self, message: str) -> None:
        self.btn_release.setEnabled(True)
        logger.error("释放视觉模型失败: %s", message)

    def _load_image(self, path: Path) -> None:
        self.current_image_path = path
        try:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.image_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.image_label.setPixmap(scaled)
                self.image_label.set_image_style(True)
            else:
                self.image_label.setText("无法加载图片")
                self.image_label.set_image_style(False)
        except Exception as e:
            logger.error("Failed to load image: %s", e)
            self.image_label.setText("无法加载图片")
            self.image_label.set_image_style(False)
        self.image_label.set_busy(False)

    def _open_image(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff *.webp)",
        )
        if file_path:
            self.import_image(Path(file_path))

    def refresh_data(self) -> None:
        """用户切换后清空临时识别结果（识别记录本身按用户隔离存储）。"""
        try:
            self.ocr_result_edit.clear()
        except Exception as e:
            logger.warning("Failed to reset ocr panel after user switch: %s", e)

    def start_screenshot(self) -> None:
        """开始区域截图（供全局快捷键和按钮调用）。"""
        try:
            capture = ScreenshotCapture(on_captured=self._on_screenshot_captured)
            capture.capture_region()
        except Exception as e:
            logger.error("Failed to start screenshot: %s", e)

    def _on_screenshot_captured(self, path: Path) -> None:
        self.import_image(path)

    def process_clipboard(self) -> None:
        """处理剪贴板图片。"""
        try:
            path = ScreenshotCapture.get_clipboard_image()
            if path is None:
                QMessageBox.information(self, "剪贴板", "剪贴板中没有图片。")
                return
            self.import_image(path)
        except Exception as e:
            logger.error("Failed to process clipboard image: %s", e)

    # ------------------------------------------------------------------
    # 图片导入前置增强
    # ------------------------------------------------------------------

    def import_image(self, path: Path) -> None:
        """图片导入统一入口：按开关决定是否先进入预处理面板。"""
        target = Path(path)
        if self.preprocess_check.isChecked():
            self._open_enhance_dialog(target)
        else:
            self._load_image(target)

    def _open_enhance_dialog(self, path: Path) -> None:
        """弹出预处理面板；用户跳过/取消时分别回退原图或放弃导入。"""
        from .image_enhance_dialog import ImageEnhanceDialog

        dialog = ImageEnhanceDialog(path, service=self.image_enhance_service, parent=self)
        self._enhance_dialog = dialog
        dialog.enhance_confirmed.connect(self._on_enhance_confirmed)
        dialog.enhance_failed.connect(
            lambda _message: show_toast("处理失败，已使用原图", level="warning", duration_ms=4000)
        )
        dialog.enhance_skipped.connect(lambda original: self._load_image(Path(original)))
        try:
            dialog.exec()
        finally:
            self._enhance_dialog = None

    def _on_enhance_confirmed(self, processed: str, applied: List[str]) -> None:
        """使用增强结果：记录映射、加载图片并给出轻量反馈。"""
        source = self._enhance_dialog.image_path if self._enhance_dialog else None
        if source is not None:
            self._enhanced_paths[str(source)] = processed
        self._load_image(Path(processed))
        show_toast("图片增强完成", level="success")
        if any("笔迹" in step for step in applied):
            show_toast("已尝试去除笔迹", level="info", duration_ms=2500)

    def _enhanced_for(self, path: str) -> str:
        """取该图片对应的增强版本路径（没有则返回原路径）。"""
        return self._enhanced_paths.get(path, path)

    def _run_ocr(self) -> None:
        if self.current_image_path is None:
            QMessageBox.warning(self, "提示", "请先选择或截图一张图片。")
            return

        self.ocr_result_edit.setPlainText("正在识别...")
        # 局部加载遮罩：识别期间只在图片区提示，其他页面照常可操作
        self.image_label.set_busy(True, "正在识别图片文字…")
        worker = OCRWorker(
            self.ocr_service,
            self.current_image_path,
        )
        worker.signals.result.connect(self._on_ocr_result)
        worker.signals.meta.connect(self._on_ocr_meta)
        worker.signals.error.connect(self._on_error)
        ThreadPool.start_worker(worker)

    def _on_ocr_result(self, text: str) -> None:
        self.image_label.set_busy(False)
        # 识别完成即自动补公式标记，结果直接进入可渲染状态（与批量识别流程一致）
        self.ocr_result_edit.setPlainText(self._auto_format_ocr(text))
        self._fade_in_widget(self.ocr_result_edit)
        show_success(self, "图片文字已提取，并已自动补全公式标记")

    def _fade_in_widget(self, widget: QWidget) -> None:
        """结果文字平滑渲染（低配降级为直接显示）。"""
        try:
            duration = policy().duration(140)
            if duration <= 0 or widget is None:
                return
            from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation
            from PyQt6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", widget)
            anim.setDuration(duration)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except Exception:
            try:
                widget.setGraphicsEffect(None)
            except Exception:
                pass

    def _on_ocr_meta(self, meta: dict) -> None:
        duration = meta.get("duration_ms")
        if duration is not None:
            self.ocr_time_label.setText(f"推理耗时：{duration} ms")

    def _copy_ocr_result(self) -> None:
        text = self.ocr_result_edit.toPlainText()
        if text:
            set_clipboard_text(text)
            show_success(self, "识别结果已复制到剪贴板")

    def _extract_terms_from_ocr(self) -> None:
        """从 OCR 识别文本中提取专业术语并存入术语词典。"""
        text = self.ocr_result_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "提示", "请先进行 OCR 识别")
            return
        if self.terminology_service is None:
            QMessageBox.warning(self, "提示", "术语词典服务未初始化")
            return
        try:
            with busy_cursor():
                terms = self.terminology_service.extract_terms(
                    text, source_type="ocr", source_id=0
                )
        except Exception as e:
            logger.error("Failed to extract terms from OCR: %s", e)
            QMessageBox.warning(self, "提取失败", str(e))
            return

        if terms:
            show_success(self, f"成功提取并保存 {len(terms)} 个术语。")
        else:
            show_success(self, "未提取到新术语，可能都已存在或文本中术语较少。")

    def _format_ocr_text(self) -> None:
        """一键格式化 OCR 文本：清理乱码 + 修复数学符号 + 补 LaTeX 公式标记。"""
        text = self.ocr_result_edit.toPlainText()
        if not text.strip():
            return
        self.ocr_result_edit.setPlainText(self._auto_format_ocr(text))
        show_success(self, "已清理乱码、修复数学符号并补全公式标记")

    @classmethod
    def _auto_format_ocr(cls, text: str) -> str:
        """清理乱码 + 修复数学符号 + 补公式标记然后收拢空行。

        识别完成时自动套用，也供「一键格式化」按钮复用（两者结果一致）。
        """
        try:
            from ..services.text_format_service import get_text_format_service

            text = get_text_format_service().format_text(text)
        except Exception as e:
            logger.warning("公式格式化失败，退化为空行清理: %s", e)
        return cls._clean_ocr_text(text)

    @staticmethod
    def _clean_ocr_text(text: str) -> str:
        """清理 OCR 文本：合并多余空行、去除首尾空白、统一换行。"""
        # 1. 统一换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # 2. 去除行首行尾空白
        lines = [line.strip() for line in text.split("\n")]
        # 3. 合并连续空行为一个空行
        cleaned: list[str] = []
        for line in lines:
            if line == "":
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
            else:
                cleaned.append(line)
        # 4. 去除首尾空行
        while cleaned and cleaned[0] == "":
            cleaned.pop(0)
        while cleaned and cleaned[-1] == "":
            cleaned.pop()
        return "\n".join(cleaned)

    def _run_solve(self) -> None:
        if self.current_image_path is None:
            QMessageBox.warning(self, "提示", "请先选择或截图一张图片。")
            return

        self.result_browser.set_markdown("正在思考...")
        self.image_label.set_busy(True, "正在解题，请稍候…")
        worker = PhotoSolveWorker(
            self.ocr_service,
            self.current_image_path,
        )
        worker.signals.result.connect(self._on_solve_result)
        worker.signals.meta.connect(self._on_solve_meta)
        worker.signals.error.connect(self._on_error)
        ThreadPool.start_worker(worker)

    def _on_solve_result(self, answer: str) -> None:
        self.image_label.set_busy(False)
        self.result_browser.set_html(
            f"<b>解题思路与步骤：</b><br/>{answer.replace(chr(10), '<br/>')}"
        )

    def _on_solve_meta(self, meta: dict) -> None:
        duration = meta.get("duration_ms")
        if duration is not None:
            self.solve_time_label.setText(f"推理耗时：{duration} ms")

    def _open_batch_images(self) -> None:
        """打开文件对话框选择多张图片。"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择试卷图片（可多张）",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff *.webp)",
        )
        if not file_paths:
            return

        self._batch_results = []
        self.batch_tree.clear()
        for path in file_paths:
            item = QTreeWidgetItem()
            item.setText(0, Path(path).name)
            item.setText(1, "待识别")
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            self.batch_tree.addTopLevelItem(item)

    def _run_pipeline(self) -> None:
        """旗舰一键：识别 + 解答 + 入库 + 计划联动。"""
        paths: List[Path] = []
        for i in range(self.batch_tree.topLevelItemCount()):
            item = self.batch_tree.topLevelItem(i)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                paths.append(Path(path))
        if not paths:
            QMessageBox.warning(self, "提示", "请先选择试卷图片。")
            return

        from ..services.exam_pipeline_service import ExamPipelineService

        pipeline = ExamPipelineService(
            ocr_service=self.ocr_service,
            errorbook_service=self.errorbook_service,
        )
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setVisible(True)
        self.result_browser.set_markdown(f"全流程启动：{len(paths)} 张图片…")
        # 底部状态栏常驻进度：任务在后台跑，界面其余部分照常可操作
        self._batch_task_id = "ocr_pipeline"
        task_started(self._batch_task_id, f"全流程处理中（{len(paths)} 张图片）…")
        worker = ExamPipelineWorker(pipeline, paths)
        worker.signals.result.connect(self._on_pipeline_done)
        worker.signals.progress.connect(self._on_batch_progress)
        worker.signals.error.connect(
            lambda m: (
                self.batch_progress_bar.setVisible(False),
                task_finished(self._batch_task_id),
                self._on_error(m),
            )
        )
        worker.signals.finished.connect(
            lambda: (
                self.batch_progress_bar.setVisible(False),
                task_finished(self._batch_task_id),
            )
        )
        ThreadPool.start_worker(worker)

    def _on_pipeline_done(self, summary: dict) -> None:
        failed = len(summary.get("failed", []))
        text = (
            f"全流程完成：入库 {summary.get('errors_added', 0)} 题到错题本，"
            f"生成变式 {summary.get('variants', 0)} 题，学习计划已联动调优。"
        )
        if failed:
            text += f"\n失败 {failed} 项（详见日志）"
        self.result_browser.set_markdown(text)
        show_success(self, "全流程完成", text)

    def _run_batch_ocr(self) -> None:
        """执行批量 OCR 识别。"""
        paths: List[Path] = []
        for i in range(self.batch_tree.topLevelItemCount()):
            item = self.batch_tree.topLevelItem(i)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                paths.append(Path(path))

        if not paths:
            QMessageBox.warning(self, "提示", "请先选择试卷图片。")
            return

        self.result_browser.set_markdown(f"正在批量识别 {len(paths)} 张图片...")
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setVisible(True)
        self._batch_task_id = "ocr_batch"
        task_started(self._batch_task_id, f"批量识别中（{len(paths)} 张图片）…")
        worker = BatchOCRWorker(self.ocr_service, paths, split_questions=True)
        worker.signals.result.connect(self._on_batch_result)
        worker.signals.progress.connect(self._on_batch_progress)
        worker.signals.meta.connect(self._on_solve_meta)
        worker.signals.error.connect(self._on_error)
        worker.signals.finished.connect(
            lambda: (
                self.batch_progress_bar.setVisible(False),
                task_finished(self._batch_task_id),
            )
        )
        ThreadPool.start_worker(worker)

    def _on_batch_progress(self, percent: int, message: str) -> None:
        """批量识别进度反馈：面板内进度条 + 底部状态栏同步。"""
        if hasattr(self, "batch_progress_bar"):
            self.batch_progress_bar.setValue(percent)
        task_progress(getattr(self, "_batch_task_id", "ocr_batch"), percent, message)
        self.result_browser.set_markdown(message)

    def _on_batch_result(self, results: List[dict]) -> None:
        """展示批量识别结果。"""
        self._batch_results = results
        self.batch_tree.clear()

        total_questions = 0
        for result in results:
            source_path = str(result.get("image_path", ""))
            image_name = Path(source_path).name
            error = result.get("error", "")
            if error:
                parent = QTreeWidgetItem()
                parent.setText(0, image_name)
                parent.setText(1, f"识别失败：{error}")
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                self.batch_tree.addTopLevelItem(parent)
                continue

            questions = result.get("questions", [])
            if not questions:
                questions = [{"question": result.get("raw_text", ""), "answer": "", "analysis": ""}]

            parent = QTreeWidgetItem()
            parent.setText(0, image_name)
            parent.setText(1, f"识别到 {len(questions)} 道题目")
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.batch_tree.addTopLevelItem(parent)

            for idx, q in enumerate(questions, 1):
                # 记录来源图片（若该图做过增强，则引用增强版本），供错题记录归档
                q.setdefault("image_path", self._enhanced_for(source_path))
                child = QTreeWidgetItem()
                child.setText(0, f"题 {idx}")
                child.setText(1, q.get("question", "")[:120])
                child.setText(2, q.get("answer", "")[:60])
                child.setText(3, q.get("analysis", "")[:60])
                child.setData(0, Qt.ItemDataRole.UserRole, q)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                parent.addChild(child)
                total_questions += 1

        self.result_browser.set_markdown(f"批量识别完成，共识别 {total_questions} 道题目。请勾选需要导入的题目并设置科目标签。")

    def _clear_batch(self) -> None:
        """清空批量识别结果。"""
        self.batch_tree.clear()
        self._batch_results = []
        self.result_browser.clear()

    def _import_selected_questions(self) -> None:
        """将选中的题目导入错题本，自动去重。"""
        if self.errorbook_service is None:
            QMessageBox.warning(self, "功能不可用", "错题本服务未初始化")
            return

        questions_to_import: List[dict] = []
        for i in range(self.batch_tree.topLevelItemCount()):
            parent = self.batch_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    q = child.data(0, Qt.ItemDataRole.UserRole)
                    if q and q.get("question", "").strip():
                        questions_to_import.append(q)

        if not questions_to_import:
            QMessageBox.warning(self, "提示", "请先勾选要导入的题目。")
            return

        subject = self.subject_edit.toPlainText().strip()
        imported = 0
        skipped = 0
        try:
            existing = self.errorbook_service.list_errors(limit=10000)
            existing_questions = {e["question"].strip() for e in existing}

            for q in questions_to_import:
                question_text = q.get("question", "").strip()
                # 简单去重：题目文本完全一致或高度相似则跳过
                if question_text in existing_questions:
                    skipped += 1
                    continue

                new_id = self.errorbook_service.add_error(
                    question=question_text,
                    answer=q.get("answer", ""),
                    analysis=q.get("analysis", ""),
                    knowledge_tags=subject,
                    subject=subject,
                    image_path=q.get("image_path") or None,
                )
                # OCR 导入后自动触发 AI 知识点打标（走有界 AI 线程池，防止并发打满模型）
                tag_worker = AutoTagWorker(self.errorbook_service, error_id=new_id)
                get_ai_pool().start(tag_worker)
                existing_questions.add(question_text)
                imported += 1

            QMessageBox.information(
                self,
                "导入完成",
                f"成功导入 {imported} 道题目，去重跳过 {skipped} 道。",
            )
            self.result_browser.set_markdown(f"导入完成：成功 {imported} 道，跳过 {skipped} 道。")
        except Exception as e:
            logger.error("Batch import failed: %s", e)
            QMessageBox.warning(self, "导入失败", str(e))

    def _on_error(self, error_message: str) -> None:
        """失败提示友好化：不向用户暴露原始堆栈，改为自然语言说明。"""
        logger.error("OCR/Solve error: %s", error_message)
        self.image_label.set_busy(False)
        friendly = friendly_error_message(error_message)
        QMessageBox.warning(self, "处理失败", friendly)
        self.result_browser.set_markdown(f"处理失败：{friendly}")

    def locate_item(self, item_type: str, item_id: int) -> None:
        """全局搜索定位到 OCR 记录（目前仅高亮提示）。"""
        if item_type != "ocr_record":
            return
        self.result_browser.set_markdown(f"定位到 OCR 记录 #{item_id}，可在历史记录中查看。")
