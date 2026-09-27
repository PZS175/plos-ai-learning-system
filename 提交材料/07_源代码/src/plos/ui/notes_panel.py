"""富文本笔记面板：笔记本分类、富文本编辑、截图批注、AI 辅助。

- 左侧：笔记本（文件夹）列表 + 笔记列表
- 右侧：富文本编辑器（加粗/斜体/标题/列表/插入图片）
- AI 辅助：提炼核心考点 / 生成自测练习题 / 总结摘要（后台线程，不阻塞 UI）
- 截图批注：截屏后可画笔标注，保存插入笔记
所有操作包 try-except，数据纯本地。
"""

from __future__ import annotations

import base64
from typing import Optional

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPixmap, QTextListFormat, QTextCharFormat
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.note_service import NoteService
from ..utils.logger import get_logger
from .math_text import MathTextBrowser, attach_live_preview
from .ui_utils import Debouncer, show_error, show_info, theme_colors

logger = get_logger("ui.notes_panel")


class _NoteAiWorker(QThread):
    """笔记 AI 辅助后台线程（遵循全局单 AI 请求，由调用方防重入）。"""

    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, chat_service, session_manager, prompt: str) -> None:
        super().__init__()
        self._chat = chat_service
        self._session = session_manager
        self._prompt = prompt

    def run(self) -> None:
        try:
            conv_id = self._session.create_conversation(title="笔记AI辅助")
            resp = self._chat.send_message(conv_id, self._prompt, save_history=False)
            text = getattr(resp, "content", None) or str(resp)
            self.finished_ok.emit(text)
        except Exception as e:
            self.failed.emit(str(e))


class _ScreenshotDialog(QWidget):
    """截图 + 画笔批注对话框，返回标注后的 QPixmap。"""

    def __init__(self, pixmap: QPixmap, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("截图批注 —— 拖动鼠标标注，完成后点击保存")
        self._original = pixmap
        self._canvas = pixmap.copy()
        self._drawing = False
        self._last_pos = None
        self._build_ui()
        self.resize(min(pixmap.width() + 20, 1200), min(pixmap.height() + 60, 800))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.color_btn = QPushButton("画笔颜色：红")
        self.color_btn.clicked.connect(self._pick_color)
        bar.addWidget(self.color_btn)
        self._color = QColor("#f53f3f")
        clear_btn = QPushButton("清除批注")
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)
        bar.addStretch()
        save_btn = QPushButton("保存并插入笔记")
        save_btn.setStyleSheet(f"background-color:{theme_colors()['success']};color:white;padding:4px 14px;border-radius:4px;")
        save_btn.clicked.connect(self.accept)
        bar.addWidget(save_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.close)
        bar.addWidget(cancel_btn)
        layout.addLayout(bar)

        self.image_label = QLabel()
        self.image_label.setPixmap(self._canvas)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMouseTracking(True)
        layout.addWidget(self.image_label, 1)

        self._painting_result: Optional[QPixmap] = None

    def _pick_color(self) -> None:
        from PyQt6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(self._color, self, "选择画笔颜色")
        if c.isValid():
            self._color = c
            names = {QColor("#f53f3f"): "红", QColor("#165dff"): "蓝", QColor("#00b42a"): "绿"}
            self.color_btn.setText(f"画笔颜色：{names.get(c, c.name())}")

    def _clear(self) -> None:
        self._canvas = self._original.copy()
        self.image_label.setPixmap(self._canvas)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drawing = True
            self._last_pos = self.image_label.mapFrom(self, e.position().toPoint())

    def mouseMoveEvent(self, e) -> None:
        if not self._drawing:
            return
        from PyQt6.QtGui import QPainter, QPen
        pos = self.image_label.mapFrom(self, e.position().toPoint())
        painter = QPainter(self._canvas)
        pen = QPen(self._color, 3)
        painter.setPen(pen)
        if self._last_pos is not None:
            painter.drawLine(self._last_pos, pos)
        painter.end()
        self._last_pos = pos
        self.image_label.setPixmap(self._canvas)

    def mouseReleaseEvent(self, e) -> None:
        self._drawing = False
        self._last_pos = None

    def accept(self) -> None:
        self._painting_result = self._canvas
        self.close()

    def result_pixmap(self) -> Optional[QPixmap]:
        return self._painting_result


class NotesPanel(QWidget):
    """富文本笔记面板。"""

    def __init__(self, note_service: Optional[NoteService] = None,
                 chat_service=None, session_manager=None) -> None:
        super().__init__()
        self.note_service = note_service or NoteService()
        self.chat_service = chat_service
        self.session_manager = session_manager
        self._current_note_id: Optional[int] = None
        self._current_notebook = "默认笔记本"
        self._ai_worker: Optional[_NoteAiWorker] = None
        self._build_ui()
        self.refresh_notebooks()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：笔记本 + 笔记列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        nb_row = QHBoxLayout()
        nb_row.addWidget(QLabel("笔记本："))
        self.notebook_combo = QComboBox()
        self.notebook_combo.currentTextChanged.connect(self._on_notebook_changed)
        nb_row.addWidget(self.notebook_combo, 1)
        add_nb_btn = QPushButton("+")
        add_nb_btn.setFixedWidth(28)
        add_nb_btn.setToolTip("新建笔记本")
        add_nb_btn.clicked.connect(self._new_notebook)
        nb_row.addWidget(add_nb_btn)
        left_layout.addLayout(nb_row)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索笔记标题/内容…")
        # 搜索防抖（300ms）：避免用户打字过程中反复触发数据库查询
        self._search_debounce = Debouncer(self.refresh_notes, 300, self)
        self.search_edit.textChanged.connect(self._search_debounce.trigger)
        left_layout.addWidget(self.search_edit)

        self.notes_list = QListWidget()
        self.notes_list.currentItemChanged.connect(self._on_note_selected)
        left_layout.addWidget(self.notes_list, 1)

        note_btn_row = QHBoxLayout()
        new_note_btn = QPushButton("新建笔记")
        new_note_btn.clicked.connect(self._new_note)
        note_btn_row.addWidget(new_note_btn)
        del_note_btn = QPushButton("删除")
        del_note_btn.setStyleSheet(f"color:{theme_colors()['error']};")
        del_note_btn.clicked.connect(self._delete_note)
        note_btn_row.addWidget(del_note_btn)
        left_layout.addLayout(note_btn_row)

        splitter.addWidget(left)

        # 右侧：编辑器
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        title_row = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("笔记标题…")
        self.title_edit.setStyleSheet("font-size:16px;font-weight:bold;padding:4px;")
        title_row.addWidget(self.title_edit, 1)
        self.kp_edit = QLineEdit()
        self.kp_edit.setPlaceholderText("关联知识点（可选）…")
        self.kp_edit.setMaximumWidth(200)
        title_row.addWidget(self.kp_edit)
        right_layout.addLayout(title_row)

        # 富文本工具栏
        toolbar = QHBoxLayout()
        self._add_format_btn(toolbar, "加粗", self._bold)
        self._add_format_btn(toolbar, "斜体", self._italic)
        self._add_format_btn(toolbar, "下划线", self._underline)
        toolbar.addSpacing(8)
        self.heading_combo = QComboBox()
        self.heading_combo.addItems(["正文", "标题1", "标题2", "标题3"])
        self.heading_combo.currentIndexChanged.connect(self._heading)
        toolbar.addWidget(self.heading_combo)
        toolbar.addSpacing(8)
        self._add_format_btn(toolbar, "• 列表", self._bullet_list)
        self._add_format_btn(toolbar, "插入图片", self._insert_image)
        self._add_format_btn(toolbar, "📷 截图批注", self._screenshot)
        toolbar.addStretch()
        right_layout.addLayout(toolbar)

        # AI 辅助栏
        ai_bar = QHBoxLayout()
        ai_bar.addWidget(QLabel("AI 辅助："))
        for text, kind in (("提炼核心考点", "keypoints"), ("生成自测练习题", "quiz"), ("总结摘要", "summary")):
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, k=kind: self._ai_assist(k))
            ai_bar.addWidget(btn)
        ai_bar.addStretch()
        self.ai_status = QLabel("")
        self.ai_status.setStyleSheet(f"color:{theme_colors()['fg_secondary']};font-size:12px;")
        ai_bar.addWidget(self.ai_status)
        right_layout.addLayout(ai_bar)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("在这里输入笔记内容…支持粘贴图片、OCR 文本。")
        right_layout.addWidget(self.editor, 1)

        # 笔记公式实时预览
        self.formula_preview = MathTextBrowser(self)
        self.formula_preview.setMaximumHeight(140)
        self.formula_preview.set_placeholder_text("输入 \\(公式\\) 或 \\[公式\\] 可在此预览")
        right_layout.addWidget(self.formula_preview)
        self._formula_preview_connector = attach_live_preview(self.editor, self.formula_preview)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("💾 保存笔记")
        save_btn.setStyleSheet(f"background-color:{theme_colors()['accent']};color:white;padding:6px 20px;border-radius:4px;")
        save_btn.clicked.connect(self._save_current)
        save_row.addWidget(save_btn)
        right_layout.addLayout(save_row)

        splitter.addWidget(right)
        splitter.setSizes([260, 800])
        root.addWidget(splitter)

    def _add_format_btn(self, layout, text: str, slot) -> None:
        btn = QPushButton(text)
        btn.setFixedHeight(28)
        btn.clicked.connect(slot)
        layout.addWidget(btn)

    # ------------------------------------------------------------------
    # 数据刷新
    def refresh_notebooks(self) -> None:
        try:
            names = self.note_service.list_notebooks()
            current = self.notebook_combo.currentText() or self._current_notebook
            self.notebook_combo.blockSignals(True)
            self.notebook_combo.clear()
            self.notebook_combo.addItems(names)
            if current in names:
                self.notebook_combo.setCurrentText(current)
            self.notebook_combo.blockSignals(False)
            self._current_notebook = self.notebook_combo.currentText() or "默认笔记本"
            self.refresh_notes()
        except Exception as e:
            logger.error("Refresh notebooks failed: %s", e)

    def refresh_notes(self) -> None:
        try:
            keyword = self.search_edit.text().strip()
            notes = self.note_service.list_notes(
                notebook=self._current_notebook, keyword=keyword
            )
            self.notes_list.blockSignals(True)
            self.notes_list.clear()
            for n in notes:
                item = QListWidgetItem(n.get("title", "无标题"))
                item.setData(Qt.ItemDataRole.UserRole, n.get("id"))
                preview = (n.get("content_text") or "")[:30].replace("\n", " ")
                item.setToolTip(preview)
                self.notes_list.addItem(item)
            self.notes_list.blockSignals(False)
        except Exception as e:
            logger.error("Refresh notes failed: %s", e)

    def _on_notebook_changed(self, name: str) -> None:
        if name:
            self._current_notebook = name
            self.refresh_notes()

    def _on_note_selected(self, current, _previous) -> None:
        if current is None:
            return
        note_id = current.data(Qt.ItemDataRole.UserRole)
        if not note_id:
            return
        try:
            note = self.note_service.get_note(note_id)
            if not note:
                return
            self._current_note_id = note_id
            self.title_edit.setText(note.get("title", ""))
            self.kp_edit.setText(note.get("linked_kp", ""))
            self.editor.setHtml(note.get("content_html", ""))
        except Exception as e:
            logger.error("Load note failed: %s", e)

    # ------------------------------------------------------------------
    # 操作
    def _new_notebook(self) -> None:
        try:
            name, ok = QInputDialog.getText(self, "新建笔记本", "笔记本名称：")
            if ok and name.strip():
                self._current_notebook = name.strip()
                self.refresh_notebooks()
                self.notebook_combo.setCurrentText(self._current_notebook)
        except Exception as e:
            logger.error("New notebook failed: %s", e)

    def _new_note(self) -> None:
        self._current_note_id = None
        self.title_edit.clear()
        self.kp_edit.clear()
        self.editor.clear()
        self.title_edit.setFocus()

    def _delete_note(self) -> None:
        if self._current_note_id is None:
            show_info(self, "提示", "请先在左侧选择要删除的笔记。")
            return
        try:
            r = QMessageBox.question(self, "删除笔记", "确定删除当前笔记吗？此操作不可恢复。")
            if r != QMessageBox.StandardButton.Yes:
                return
            if self.note_service.delete_note(self._current_note_id):
                show_info(self, "删除成功", "笔记已删除。")
                self._new_note()
                self.refresh_notes()
        except Exception as e:
            logger.error("Delete note failed: %s", e)
            show_error(self, "删除失败", str(e))

    def _save_current(self) -> None:
        try:
            title = self.title_edit.text().strip() or "无标题笔记"
            html = self.editor.toHtml()
            text = self.editor.toPlainText()
            note_id = self.note_service.save_note(
                title=title,
                content_html=html,
                content_text=text,
                notebook=self._current_notebook,
                linked_kp=self.kp_edit.text().strip(),
                note_id=self._current_note_id,
            )
            self._current_note_id = note_id
            show_info(self, "保存成功", f"笔记《{title}》已保存。")
            self.refresh_notes()
        except Exception as e:
            logger.error("Save note failed: %s", e)
            show_error(self, "保存失败", str(e))

    # ------------------------------------------------------------------
    # 富文本格式
    def _bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if self.editor.fontWeight() != QFont.Weight.Bold else QFont.Weight.Normal)
        self.editor.mergeCurrentCharFormat(fmt)

    def _italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self.editor.mergeCurrentCharFormat(fmt)

    def _underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self.editor.mergeCurrentCharFormat(fmt)

    def _heading(self, index: int) -> None:
        try:
            cursor = self.editor.textCursor()
            if index == 0:
                fmt = QTextCharFormat()
                fmt.setFontWeight(QFont.Weight.Normal)
                fmt.setFontPointSize(11)
                cursor.mergeCharFormat(fmt)
            else:
                sizes = {1: 20, 2: 16, 3: 14}
                fmt = QTextCharFormat()
                fmt.setFontWeight(QFont.Weight.Bold)
                fmt.setFontPointSize(sizes.get(index, 14))
                cursor.mergeCharFormat(fmt)
        except Exception as e:
            logger.warning("Heading format failed: %s", e)

    def _bullet_list(self) -> None:
        try:
            cursor = self.editor.textCursor()
            list_fmt = QTextListFormat()
            list_fmt.setStyle(QTextListFormat.Style.ListDisc)
            cursor.createList(list_fmt)
        except Exception as e:
            logger.warning("Bullet list failed: %s", e)

    def _insert_image(self) -> None:
        try:
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.gif)"
            )
            if path:
                self.editor.document().addResource(
                    __import__("PyQt6.QtGui", fromlist=["QTextDocument"]).QTextDocument.ResourceType.ImageResource,
                    __import__("PyQt6.QtCore", fromlist=["QUrl"]).QUrl.fromLocalFile(path),
                    QPixmap(path),
                )
                self.editor.insertHtml(f'<img src="{path}" width="400"/><br>')
        except Exception as e:
            logger.error("Insert image failed: %s", e)
            show_error(self, "插入图片失败", str(e))

    def _screenshot(self) -> None:
        try:
            self.hide()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(200, self._do_capture)
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            self.show()

    def _do_capture(self) -> None:
        try:
            screen = QGuiApplication.primaryScreen()
            geo = screen.geometry()
            pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            self.show()
            dlg = _ScreenshotDialog(pixmap, self)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.show()
            self._shot_dlg = dlg
            dlg.destroyed.connect(lambda: self._insert_shot(dlg))
        except Exception as e:
            logger.error("Capture failed: %s", e)
            self.show()

    def _insert_shot(self, dlg: _ScreenshotDialog) -> None:
        try:
            result = dlg.result_pixmap()
            if result is None:
                return
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            result.save(buf, "PNG")
            b64 = base64.b64encode(ba.data()).decode("ascii")
            self.editor.insertHtml(f'<img src="data:image/png;base64,{b64}" width="500"/><br>')
        except Exception as e:
            logger.error("Insert screenshot failed: %s", e)

    # ------------------------------------------------------------------
    # AI 辅助
    def _ai_assist(self, kind: str) -> None:
        if self.chat_service is None or self.session_manager is None:
            show_info(self, "AI 不可用", "AI 服务未初始化，无法使用辅助功能。")
            return
        if self._ai_worker is not None and self._ai_worker.isRunning():
            show_info(self, "请稍候", "上一个 AI 请求仍在处理中（同一时间仅允许一条 AI 请求）。")
            return
        text = self.editor.toPlainText().strip()
        if not text:
            show_info(self, "内容为空", "请先在笔记中输入内容。")
            return
        prompts = {
            "keypoints": f"请从以下学习笔记中提炼核心考点，用简洁的条目列出：\n\n{text}",
            "quiz": f"请根据以下学习笔记内容，生成 3 道自测练习题（含参考答案）：\n\n{text}",
            "summary": f"请为以下学习笔记写一段简明摘要（100字以内）：\n\n{text}",
        }
        self.ai_status.setText("AI 处理中…")
        self._ai_worker = _NoteAiWorker(
            self.chat_service, self.session_manager, prompts[kind]
        )
        self._ai_worker.finished_ok.connect(self._on_ai_done)
        self._ai_worker.failed.connect(self._on_ai_failed)
        self._ai_worker.start()

    def _on_ai_done(self, result: str) -> None:
        self.ai_status.setText("完成")
        label = {"keypoints": "【核心考点】", "quiz": "【自测练习题】", "summary": "【摘要】"}
        # 追加到编辑器
        self.editor.append(f"<br><hr><b>{label.get('','【AI】')}</b><br>{result}<br>")

    def _on_ai_failed(self, err: str) -> None:
        self.ai_status.setText("失败")
        logger.error("Note AI assist failed: %s", err)
        show_error(self, "AI 处理失败", err)
