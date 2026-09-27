"""知识库面板。

支持上传文档、查看文档列表、语义检索、文档预览阅读、文本批注高亮、
生成思维导图、TTS 朗读文档内容、删除文档。
文档列表以美化表格呈现，含搜索过滤、类型标签、时间与操作按钮。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..ai.multimodal import DocumentParser
from ..services import DiagramService, MindMapService, NoteAnnotationService, RAGService, TTSService, WebClipService
from ..utils.logger import get_logger
from .interactions import friendly_error_message
from .markdown_browser import MarkdownBrowser
from .math_text import MathTextBrowser, build_body
from .ui_utils import (
    Debouncer,
    apply_glass_style,
    ask_confirm,
    create_empty_state_widget,
    is_dark_theme,
    set_danger_button_style,
    set_primary_button_style,
    show_error,
    show_info,
    task_finished,
    task_started,
)
from .workers import DiagramWorker, MindMapWorker, RAGAddWorker, RAGSearchWorker, ThreadPool, TTSWorker, WebClipWorker

logger = get_logger("ui.knowledge_panel")


def _math_fragment(text: str) -> str:
    """把用户文本内嵌进手工拼装的 HTML 时，先把其中的公式渲染好（不解析 Markdown）。"""
    return build_body(text or "", is_dark_theme(), markdown=False, with_style=False)


class _AnnotationDialog(QDialog):
    """添加/编辑批注对话框。"""

    def __init__(self, selected_text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("添加批注")
        self.resize(420, 220)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("选中片段："))
        preview = QTextEdit()
        preview.setPlainText(selected_text)
        preview.setReadOnly(True)
        preview.setMaximumHeight(80)
        layout.addWidget(preview)

        layout.addWidget(QLabel("批注内容："))
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("输入你的批注笔记...")
        layout.addWidget(self.note_edit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def note(self) -> str:
        return self.note_edit.toPlainText().strip()


class _DocumentReaderDialog(QDialog):
    """文档阅读器对话框：展示内容、批注、思维导图、TTS。"""

    def __init__(
        self,
        document_id: int,
        title: str,
        content: str,
        note_annotation_service: NoteAnnotationService,
        mindmap_service: MindMapService,
        tts_service: Optional[TTSService] = None,
        diagram_service: Optional[DiagramService] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.document_id = document_id
        self.note_annotation_service = note_annotation_service
        self.mindmap_service = mindmap_service
        self.tts_service = tts_service
        self.diagram_service = diagram_service
        self.setWindowTitle(f"文档阅读 - {title}")
        self.resize(900, 700)
        self._build_ui(content)
        self._load_annotations()

    def _build_ui(self, content: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        annotate_btn = QPushButton("添加批注")
        annotate_btn.clicked.connect(self._add_annotation)
        btn_layout.addWidget(annotate_btn)

        mindmap_btn = QPushButton("生成思维导图")
        set_primary_button_style(mindmap_btn)
        mindmap_btn.clicked.connect(self._generate_mindmap)
        btn_layout.addWidget(mindmap_btn)

        tts_btn = QPushButton("朗读内容")
        tts_btn.clicked.connect(self._speak_content)
        btn_layout.addWidget(tts_btn)

        diagram_btn = QPushButton("生成示意图")
        set_primary_button_style(diagram_btn)
        diagram_btn.setToolTip("基于文档内容生成 AI 示意图")
        diagram_btn.clicked.connect(self._generate_diagram)
        btn_layout.addWidget(diagram_btn)
        layout.addLayout(btn_layout)

        self.content_browser = MathTextBrowser()
        # markdown=False：正文是解析出来的纯文本，保持原样排版，只额外渲染公式
        self.content_browser.set_rich_text(content, markdown=False)
        self.content_browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.content_browser.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.content_browser)

        self.annotation_list = MathTextBrowser()
        self.annotation_list.setMaximumHeight(140)
        self.annotation_list.setPlaceholderText("批注列表将显示在这里...")
        layout.addWidget(self.annotation_list)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _show_context_menu(self, position) -> None:
        menu = QMenu(self)
        selected = self.content_browser.textCursor().selectedText()
        if selected:
            action = QAction("添加批注", self)
            action.triggered.connect(lambda: self._add_annotation_for(selected))
            menu.addAction(action)
        menu.exec(self.content_browser.mapToGlobal(position))

    def _add_annotation(self) -> None:
        selected = self.content_browser.textCursor().selectedText()
        if not selected:
            QMessageBox.information(self, "提示", "请先选中要批注的文本片段。")
            return
        self._add_annotation_for(selected)

    def _add_annotation_for(self, selected_text: str) -> None:
        dialog = _AnnotationDialog(selected_text, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        note = dialog.note()
        if not note:
            return
        try:
            self.note_annotation_service.add_annotation(
                target_type="document",
                target_id=self.document_id,
                selected_text=selected_text,
                note_content=note,
            )
            self._load_annotations()
            show_info(self, "批注已保存", "批注已跟随文档持久保存。")
        except Exception as e:
            logger.error("Add annotation failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _load_annotations(self) -> None:
        try:
            annotations = self.note_annotation_service.list_annotations(
                target_type="document", target_id=self.document_id
            )
            if not annotations:
                self.annotation_list.setHtml("<i>暂无批注</i>")
                return
            html = "<b>批注：</b><br/>"
            for a in annotations:
                html += (
                    f"<div style='margin:6px 0; padding:6px; border-left:3px solid {a['highlight_color']};'>"
                    f"<b>原文：</b>{_math_fragment((a['selected_text'] or '')[:80] + '...')}<br/>"
                    f"<b>笔记：</b>{_math_fragment(a['note_content'])}</div>"
                )
            self.annotation_list.setHtml(html)
        except Exception as e:
            logger.warning("Load annotations failed: %s", e)

    def _generate_mindmap(self) -> None:
        content = self.content_browser.toPlainText()
        if not content.strip():
            QMessageBox.information(self, "提示", "文档内容为空，无法生成思维导图。")
            return
        try:
            self.setWindowTitle("文档阅读 - 正在生成思维导图...")
            worker = MindMapWorker(
                self.mindmap_service,
                title=f"文档 #{self.document_id}",
                content=content[:4000],
                target_type="document",
                target_id=self.document_id,
            )
            worker.signals.result.connect(self._on_mindmap_ready)
            worker.signals.error.connect(lambda msg: show_error(self, "生成失败", msg))
            ThreadPool.start_worker(worker)
        except Exception as e:
            logger.error("Start mindmap worker failed: %s", e)
            show_error(self, "生成失败", str(e))

    def _on_mindmap_ready(self, graph: dict) -> None:
        self.setWindowTitle("文档阅读 - 思维导图已生成")
        # 以缩进树形式展示思维导图
        lines = ["<b>思维导图：</b><br/>"]

        def _render(node, depth=0):
            text = node.get("text", "")
            if text:
                indent = "&nbsp;" * (depth * 4)
                lines.append(f"{indent}• {_math_fragment(text)}")
            for child in node.get("children", []):
                _render(child, depth + 1)

        root = graph.get("root", {})
        _render(root)
        self.annotation_list.setHtml("<br/>".join(lines))
        show_info(self, "思维导图", "已生成并保存，可在下方查看。")

    def _speak_content(self) -> None:
        if self.tts_service is None:
            QMessageBox.information(self, "TTS", "语音朗读服务未初始化")
            return
        text = self.content_browser.toPlainText()[:2000]
        worker = TTSWorker(self.tts_service, text)
        worker.signals.error.connect(lambda msg: logger.warning("TTS error: %s", msg))
        ThreadPool.start_worker(worker)

    def _generate_diagram(self) -> None:
        """基于文档内容生成 AI 示意图。"""
        if self.diagram_service is None:
            show_error(self, "功能不可用", "示意图服务未初始化")
            return
        content = self.content_browser.toPlainText().strip()
        if not content:
            QMessageBox.information(self, "提示", "文档内容为空，无法生成示意图。")
            return
        prompt = content[:600]
        self.setWindowTitle("文档阅读 - 正在生成示意图...")
        worker = DiagramWorker(
            self.diagram_service,
            prompt,
            diagram_type="flowchart",
            source_type="document",
            source_id=self.document_id,
        )
        worker.signals.result.connect(self._on_diagram_ready)
        worker.signals.error.connect(lambda msg: show_error(self, "生成失败", msg))
        worker.signals.finished.connect(lambda: self.setWindowTitle("文档阅读"))
        ThreadPool.start_worker(worker)

    def _on_diagram_ready(self, result: dict) -> None:
        image_path = result.get("image_path", "")
        diagram = result.get("diagram", {})
        diagram_id = diagram.get("id")
        mermaid_code = diagram.get("mermaid_code", "")

        dialog = QDialog(self)
        dialog.setWindowTitle("AI 生成示意图")
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)

        # 工具栏
        toolbar = QHBoxLayout()
        zoom_in_btn = QPushButton("放大")
        zoom_out_btn = QPushButton("缩小")
        zoom_fit_btn = QPushButton("适应")
        save_btn = QPushButton("保存 SVG")
        copy_btn = QPushButton("复制 Mermaid")
        edit_btn = QPushButton("编辑源码")
        toolbar.addWidget(zoom_in_btn)
        toolbar.addWidget(zoom_out_btn)
        toolbar.addWidget(zoom_fit_btn)
        toolbar.addWidget(save_btn)
        toolbar.addWidget(copy_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # SVG 显示区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        svg_widget = QSvgWidget(image_path)
        svg_widget.setObjectName("diagramSvgWidget")
        scroll.setWidget(svg_widget)
        layout.addWidget(scroll)

        # 缩放状态
        zoom_label = QLabel("100%")
        zoom_label.setStyleSheet("color: #86909c; font-size: 12px;")
        toolbar.addWidget(zoom_label)

        def _natural_size(path):
            """取 SVG 自然尺寸；对话框未 show 时 svg_widget.size() 是默认 100x30。"""
            renderer = QSvgRenderer(str(path))
            if renderer.isValid() and not renderer.defaultSize().isEmpty():
                return renderer.defaultSize()
            return svg_widget.size()

        base_size = _natural_size(image_path)
        zoom_factor = 1.0

        def apply_zoom():
            svg_widget.setFixedSize(
                int(base_size.width() * zoom_factor),
                int(base_size.height() * zoom_factor),
            )
            zoom_label.setText(f"{int(zoom_factor * 100)}%")

        def zoom_in():
            nonlocal zoom_factor
            zoom_factor = min(3.0, zoom_factor + 0.25)
            apply_zoom()

        def zoom_out():
            nonlocal zoom_factor
            zoom_factor = max(0.25, zoom_factor - 0.25)
            apply_zoom()

        def zoom_fit():
            nonlocal zoom_factor
            zoom_factor = 1.0
            apply_zoom()

        zoom_in_btn.clicked.connect(zoom_in)
        zoom_out_btn.clicked.connect(zoom_out)
        zoom_fit_btn.clicked.connect(zoom_fit)
        QShortcut(QKeySequence("Ctrl++"), dialog, activated=zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), dialog, activated=zoom_out)

        # 保存 SVG
        def save_svg():
            target, _ = QFileDialog.getSaveFileName(dialog, "保存 SVG", "diagram.svg", "SVG 文件 (*.svg)")
            if target:
                Path(target).write_bytes(Path(image_path).read_bytes())
                show_info(dialog, "保存成功", f"已保存到：{target}")

        save_btn.clicked.connect(save_svg)

        # 复制 Mermaid
        def copy_mermaid():
            from PyQt6.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(mermaid_code)
                show_info(dialog, "已复制", "Mermaid 源码已复制到剪贴板")

        copy_btn.clicked.connect(copy_mermaid)

        # 编辑源码并重新渲染
        def edit_mermaid():
            nonlocal base_size, zoom_factor
            if self.diagram_service is None or diagram_id is None:
                show_error(dialog, "功能不可用", "示意图服务未初始化")
                return
            edit_dialog = QDialog(dialog)
            edit_dialog.setWindowTitle("编辑 Mermaid 源码")
            edit_dialog.resize(600, 400)
            edit_layout = QVBoxLayout(edit_dialog)
            editor = QPlainTextEdit(mermaid_code)
            editor.setPlaceholderText("输入 Mermaid 代码，例如：\ngraph TD\n    A[开始] --> B[结束]")
            edit_layout.addWidget(editor)
            edit_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            edit_btns.accepted.connect(edit_dialog.accept)
            edit_btns.rejected.connect(edit_dialog.reject)
            edit_layout.addWidget(edit_btns)
            if edit_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_code = editor.toPlainText().strip()
            if not new_code:
                return
            try:
                self.diagram_service.update_mermaid_code(diagram_id, new_code)
                new_path = self.diagram_service.render_diagram(diagram_id)
                svg_widget.load(str(new_path))
                # 重新渲染后按新图自然尺寸重置缩放基准
                base_size = _natural_size(new_path)
                zoom_factor = 1.0
                apply_zoom()
                show_info(dialog, "渲染成功", "Mermaid 源码已更新并重新渲染")
            except Exception as e:
                show_error(dialog, "渲染失败", str(e))

        edit_btn.clicked.connect(edit_mermaid)

        info = QLabel(f"SVG 已保存：{image_path}")
        info.setStyleSheet("color: #86909c; font-size: 12px;")
        layout.addWidget(info)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        dialog.exec()


class _MindMapViewerDialog(QDialog):
    """简单的思维导图查看对话框。"""

    def __init__(self, title: str, graph: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"思维导图 - {title}")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        browser = MathTextBrowser()

        lines = [f"<h2>{title}</h2>"]

        def _render(node, depth=0):
            text = node.get("text", "")
            if text:
                tag = "h3" if depth == 0 else "div"
                indent = "&nbsp;" * (depth * 4)
                lines.append(f"<{tag}>{indent}• {_math_fragment(text)}</{tag}>")
            for child in node.get("children", []):
                _render(child, depth + 1)

        root = graph.get("root", {})
        _render(root)
        browser.setHtml("".join(lines))
        layout.addWidget(browser)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


class KnowledgePanel(QWidget):
    """本地知识库 RAG 界面。"""

    def __init__(
        self,
        rag_service: RAGService,
        note_annotation_service: Optional[NoteAnnotationService] = None,
        mindmap_service: Optional[MindMapService] = None,
        tts_service: Optional[TTSService] = None,
        web_clip_service: Optional[WebClipService] = None,
        diagram_service: Optional[DiagramService] = None,
    ):
        super().__init__()
        self.rag_service = rag_service
        self.note_annotation_service = note_annotation_service
        self.mindmap_service = mindmap_service
        self.tts_service = tts_service
        self.web_clip_service = web_clip_service
        self.diagram_service = diagram_service
        self._documents: list = []
        self._build_ui()
        self._refresh_document_list()

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
        container.setStyleSheet(
            """
            QLineEdit {
                min-height: 36px;
                max-height: 36px;
                padding: 0 12px;
                border-radius: 8px;
                font-size: 14px;
            }
            QPushButton {
                min-height: 36px;
                border-radius: 8px;
                padding: 0 14px;
            }
            QTextBrowser, QTextEdit {
                border-radius: 8px;
                padding: 8px;
                font-size: 14px;
            }
            """
        )
        scroll.setWidget(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：文档列表
        left = QWidget()
        left.setProperty("glass", True)
        apply_glass_style(left)
        left.setMinimumWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        left_title = QLabel("已入库文档")
        left_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        left_layout.addWidget(left_title)

        # 网页剪藏入口
        clip_label = QLabel("网页剪藏")
        clip_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #86909c;")
        left_layout.addWidget(clip_label)

        clip_layout = QHBoxLayout()
        self.clip_url_edit = QLineEdit()
        self.clip_url_edit.setPlaceholderText("粘贴网页链接，剪藏到知识库...")
        self.clip_url_edit.setMinimumHeight(36)
        self.clip_url_edit.setMaximumHeight(36)
        self.clip_url_edit.returnPressed.connect(self._clip_url)
        clip_layout.addWidget(self.clip_url_edit)

        self.clip_btn = QPushButton("剪藏")
        set_primary_button_style(self.clip_btn)
        self.clip_btn.setToolTip("抓取网页正文并保存到本地知识库")
        self.clip_btn.clicked.connect(self._clip_url)
        clip_layout.addWidget(self.clip_btn)
        left_layout.addLayout(clip_layout)

        # 搜索过滤框
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("搜索文件名...")
        self.filter_edit.setMinimumHeight(36)
        self.filter_edit.setMaximumHeight(36)
        # 搜索防抖（300ms）：避免打字过程中反复过滤列表
        self._filter_debounce = Debouncer(self._apply_filter, 300, self)
        self.filter_edit.textChanged.connect(self._filter_debounce.trigger)
        left_layout.addWidget(self.filter_edit)

        self.doc_table = QTableWidget()
        self.doc_table.setColumnCount(7)
        self.doc_table.setHorizontalHeaderLabels(["ID", "文件名", "类型", "时间", "阅读", "收藏", "删除"])
        self.doc_table.verticalHeader().setDefaultSectionSize(36)
        self.doc_table.horizontalHeader().setStretchLastSection(False)
        self.doc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.doc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.doc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.doc_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.doc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.doc_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.doc_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.doc_table.setColumnWidth(4, 60)
        self.doc_table.setColumnWidth(5, 60)
        self.doc_table.setColumnWidth(6, 60)
        self.doc_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doc_table.customContextMenuRequested.connect(self._show_doc_context_menu)
        self.doc_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.doc_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        left_layout.addWidget(self.doc_table)

        self.empty_state = create_empty_state_widget("暂无文档，点击上传按钮添加")
        left_layout.addWidget(self.empty_state)

        btn_layout = QHBoxLayout()
        btn_upload = QPushButton("上传文档")
        set_primary_button_style(btn_upload)
        btn_upload.clicked.connect(self._upload_document)
        btn_layout.addWidget(btn_upload)

        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_document_list)
        btn_layout.addWidget(btn_refresh)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left)

        # 右侧：检索
        right = QWidget()
        right.setProperty("glass", True)
        apply_glass_style(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        right_title = QLabel("语义检索")
        right_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        right_layout.addWidget(right_title)

        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入问题，检索知识库...")
        self.search_edit.setMinimumHeight(36)
        self.search_edit.setMaximumHeight(36)
        self.search_edit.returnPressed.connect(self._search)
        search_layout.addWidget(self.search_edit)

        btn_search = QPushButton("检索")
        set_primary_button_style(btn_search)
        btn_search.clicked.connect(self._search)
        search_layout.addWidget(btn_search)
        right_layout.addLayout(search_layout)

        self.result_browser = MarkdownBrowser()
        self.result_browser.set_placeholder_text("检索结果将显示在这里...")
        right_layout.addWidget(self.result_browser)

        splitter.addWidget(right)
        splitter.setSizes([420, 780])

        layout.addWidget(splitter)

    def refresh_data(self) -> None:
        """刷新文档列表（供用户切换后调用）。"""
        self._refresh_document_list()

    def _upload_document(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文档",
            "",
            "文档 (*.pdf *.docx *.txt *.md)",
        )
        if not file_path:
            return
        self.import_document(file_path)

    def import_document(self, file_path: str) -> None:
        """外部调用：导入指定路径文档到知识库。

        解析与向量化在后台线程执行，同时通过任务总线在底部状态栏常驻进度，
        导入期间用户可以继续浏览其他页面。
        """
        self.result_browser.set_plain_text("正在解析并入库...")
        self._import_task_id = "rag_import"
        task_started(self._import_task_id, f"正在解析并入库：{Path(file_path).name}")
        worker = RAGAddWorker(self.rag_service, Path(file_path))
        worker.signals.result.connect(self._on_upload_success)
        worker.signals.error.connect(self._on_error)
        worker.signals.finished.connect(
            lambda: task_finished(getattr(self, "_import_task_id", "rag_import"))
        )
        ThreadPool.start_worker(worker)

    def _on_upload_success(self, document_id: int) -> None:
        show_info(self, "上传成功", f"文档已入库，ID：{document_id}")
        self._refresh_document_list()

    def _clip_url(self) -> None:
        """剪藏网页到知识库。"""
        if self.web_clip_service is None:
            show_error(self, "功能不可用", "网页剪藏服务未初始化")
            return
        url = self.clip_url_edit.text().strip()
        if not url:
            return
        self.clip_btn.setEnabled(False)
        self.clip_btn.setText("剪藏中...")
        self.result_browser.set_plain_text("正在抓取网页并入库，请稍候...")
        worker = WebClipWorker(self.web_clip_service, url)
        worker.signals.result.connect(self._on_clip_success)
        worker.signals.error.connect(self._on_clip_error)
        worker.signals.finished.connect(lambda: self._reset_clip_ui())
        ThreadPool.start_worker(worker)

    def _on_clip_success(self, document_id: int) -> None:
        show_info(self, "剪藏成功", f"网页已保存为文档，ID：{document_id}")
        self.clip_url_edit.clear()
        self._refresh_document_list()

    def _on_clip_error(self, error_message: str) -> None:
        logger.error("Web clip failed: %s", error_message)
        show_error(self, "剪藏失败", friendly_error_message(error_message, "网页抓取失败，请检查链接后重试"))

    def _reset_clip_ui(self) -> None:
        self.clip_btn.setEnabled(True)
        self.clip_btn.setText("剪藏")

    def _refresh_document_list(self) -> None:
        try:
            self._documents = self.rag_service.list_documents()
        except Exception as e:
            logger.warning("Failed to list documents: %s", e)
            show_error(self, "刷新失败", f"获取文档列表失败：\n{e}")
            return
        self._apply_filter()

    def _apply_filter(self) -> None:
        """根据搜索框过滤文档列表。"""
        try:
            keyword = self.filter_edit.text().strip().lower()
            docs = [
                doc for doc in self._documents
                if not keyword or keyword in doc.get("filename", "").lower()
            ]

            self.doc_table.setRowCount(len(docs))
            has_docs = len(docs) > 0
            self.doc_table.setVisible(has_docs)
            self.empty_state.setVisible(not has_docs and len(self._documents) == 0)

            for i, doc in enumerate(docs):
                self.doc_table.setItem(i, 0, QTableWidgetItem(str(doc.get("id", ""))))
                self.doc_table.setItem(i, 1, QTableWidgetItem(doc.get("filename", "")))

                file_type = doc.get("file_type", "").lstrip(".").upper() or "未知"
                type_item = QTableWidgetItem(file_type)
                type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.doc_table.setItem(i, 2, type_item)

                created = str(doc.get("created_at") or "")[:19].replace("T", " ")
                self.doc_table.setItem(i, 3, QTableWidgetItem(created))

                doc_id = doc.get("id")
                is_favorite = bool(doc.get("is_favorite", 0))

                preview_btn = QPushButton("阅读")
                preview_btn.clicked.connect(lambda checked, did=doc_id: self._preview_document(did))
                self.doc_table.setCellWidget(i, 4, preview_btn)

                favorite_btn = QPushButton("★" if is_favorite else "☆")
                favorite_btn.setToolTip("取消收藏" if is_favorite else "收藏")
                favorite_btn.clicked.connect(lambda checked, did=doc_id: self._toggle_document_favorite(did))
                self.doc_table.setCellWidget(i, 5, favorite_btn)

                delete_btn = QPushButton("删除")
                set_danger_button_style(delete_btn)
                delete_btn.clicked.connect(lambda checked, did=doc_id: self._delete_document(did))
                self.doc_table.setCellWidget(i, 6, delete_btn)
        except Exception as e:
            logger.error("Failed to apply document filter: %s", e)

    def _preview_document(self, document_id: int) -> None:
        """打开文档阅读器。"""
        doc = next((d for d in self._documents if d.get("id") == document_id), None)
        if doc is None:
            show_error(self, "提示", "文档不存在")
            return

        file_path = Path(doc.get("file_path", ""))
        if not file_path.exists():
            show_error(self, "提示", f"原文件已移动或删除：{file_path}")
            return

        try:
            parser = DocumentParser()
            text, _, _ = parser.parse(file_path)
        except Exception as e:
            logger.error("Preview document failed: %s", e)
            show_error(self, "解析失败", str(e))
            return

        if self.note_annotation_service is None or self.mindmap_service is None:
            show_error(self, "功能不可用", "批注或思维导图服务未初始化")
            return

        dialog = _DocumentReaderDialog(
            document_id=document_id,
            title=doc.get("filename", "未知文档"),
            content=text,
            note_annotation_service=self.note_annotation_service,
            mindmap_service=self.mindmap_service,
            tts_service=self.tts_service,
            diagram_service=self.diagram_service,
            parent=self,
        )
        dialog.exec()

    def _show_doc_context_menu(self, position) -> None:
        """文档列表右键菜单。"""
        if self.rag_service is None:
            show_error(self, "功能不可用", "知识库服务未初始化，无法操作文档。")
            return

        item = self.doc_table.itemAt(position)
        if item is None:
            return
        row = item.row()
        doc_id_item = self.doc_table.item(row, 0)
        if doc_id_item is None:
            return
        try:
            doc_id = int(doc_id_item.text())
        except ValueError:
            return

        menu = QMenu(self)
        preview_action = QAction("阅读文档", self)
        preview_action.triggered.connect(lambda: self._preview_document(doc_id))
        menu.addAction(preview_action)

        mindmap_action = QAction("生成思维导图", self)
        mindmap_action.triggered.connect(lambda: self._generate_doc_mindmap(doc_id))
        menu.addAction(mindmap_action)

        delete_action = QAction("删除文档", self)
        delete_action.triggered.connect(lambda: self._delete_document(doc_id))
        menu.addAction(delete_action)
        menu.exec(self.doc_table.mapToGlobal(position))

    def _generate_doc_mindmap(self, document_id: int) -> None:
        """为指定文档生成思维导图。"""
        doc = next((d for d in self._documents if d.get("id") == document_id), None)
        if doc is None or self.mindmap_service is None:
            return
        file_path = Path(doc.get("file_path", ""))
        if not file_path.exists():
            show_error(self, "提示", "原文件不存在")
            return
        try:
            parser = DocumentParser()
            text, _, _ = parser.parse(file_path)
        except Exception as e:
            show_error(self, "解析失败", str(e))
            return

        worker = MindMapWorker(
            self.mindmap_service,
            title=doc.get("filename", f"文档 #{document_id}"),
            content=text[:4000],
            target_type="document",
            target_id=document_id,
        )
        worker.signals.result.connect(lambda g: self._show_mindmap(doc.get("filename", ""), g))
        worker.signals.error.connect(lambda msg: show_error(self, "生成失败", msg))
        ThreadPool.start_worker(worker)

    def _show_mindmap(self, title: str, graph: dict) -> None:
        dialog = _MindMapViewerDialog(title, graph, self)
        dialog.exec()

    def _toggle_document_favorite(self, document_id: int) -> None:
        """切换文档收藏状态。"""
        try:
            new_state = self.rag_service.toggle_favorite(document_id)
            show_info(self, "收藏", "已收藏" if new_state else "已取消收藏")
            self._refresh_document_list()
        except Exception as e:
            logger.error("Toggle document favorite failed: %s", e)
            show_error(self, "操作失败", str(e))

    def _delete_document(self, document_id: int) -> None:
        if not ask_confirm(self, "确认", f"确定删除文档 ID={document_id}？\n相关向量数据也会被删除。"):
            return
        try:
            self.rag_service.delete_document(document_id)
            show_info(self, "删除成功", f"文档 ID={document_id} 已删除")
            self._refresh_document_list()
        except Exception as e:
            logger.error("Failed to delete document %d: %s", document_id, e)
            show_error(self, "删除失败", str(e))

    def _search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        self.result_browser.set_plain_text("正在检索...")
        worker = RAGSearchWorker(self.rag_service, query)
        worker.signals.result.connect(self._on_search_result)
        worker.signals.error.connect(self._on_error)
        ThreadPool.start_worker(worker)

    def _on_search_result(self, results: list) -> None:
        if not results:
            self.result_browser.set_html("未找到相关结果。")
            return
        html = "<b>检索结果：</b><br/><br/>"
        for i, item in enumerate(results, 1):
            html += f"[{i}] <b>来源：</b>{item.source} | <b>相关度：</b>{item.score:.3f}<br/>"
            html += item.content.replace("\n", "<br/>") + "<br/><br/>"
        self.result_browser.set_html(html)

    def _on_error(self, error_message: str) -> None:
        """失败提示友好化：文件损坏 / 格式不支持等情况给出可读说明。"""
        logger.error("Knowledge panel error: %s", error_message)
        show_error(self, "操作失败", friendly_error_message(error_message, "文档处理失败，请确认文件完整后重试"))

    def locate_item(self, item_type: str, item_id: int) -> None:
        """根据全局搜索结果定位到文档。"""
        if item_type != "document":
            return
        for i in range(self.doc_table.rowCount()):
            id_item = self.doc_table.item(i, 0)
            if id_item and int(id_item.text()) == item_id:
                self.doc_table.selectRow(i)
                self._preview_document(item_id)
                return
