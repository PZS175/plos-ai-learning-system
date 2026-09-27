"""术语词典面板。

展示本地术语词典，支持搜索、编辑释义、删除，以及从剪贴板文本提取术语。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
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

from ..services.terminology_service import TerminologyService
from ..utils.logger import get_logger
from .math_text import MathTextBrowser
from .ui_utils import (
    create_section_title,
    apply_glass_style,
    ask_confirm,
    busy_cursor,
    set_clipboard_text,
    show_success,
)

logger = get_logger("ui.terminology_panel")


class TerminologyPanel(QWidget):
    """术语词典管理面板。"""

    def __init__(
        self,
        terminology_service: TerminologyService,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.terminology_service = terminology_service
        self._current_term_id: Optional[int] = None
        self._build_ui()
        self.refresh_data()

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧列表
        left_panel = QWidget()
        left_panel.setProperty("glass", True)
        apply_glass_style(left_panel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        title = create_section_title("术语词典")
        left_layout.addWidget(title)

        hint = QLabel("阅读笔记与 OCR 文本中提取的专业术语会汇总到这里。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #86909C; font-size: 12px;")
        left_layout.addWidget(hint)

        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索术语...")
        self.search_edit.returnPressed.connect(self._on_search)
        search_layout.addWidget(self.search_edit)

        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        search_layout.addWidget(self.search_btn)
        left_layout.addLayout(search_layout)

        self.term_list = QListWidget()
        self.term_list.currentItemChanged.connect(self._on_select_term)
        self.term_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.term_list.customContextMenuRequested.connect(self._show_term_context_menu)
        left_layout.addWidget(self.term_list)

        extract_btn = QPushButton("从剪贴板提取术语")
        extract_btn.setToolTip("读取剪贴板文本并自动提取专业术语")
        extract_btn.clicked.connect(self._extract_from_clipboard)
        left_layout.addWidget(extract_btn)

        splitter.addWidget(left_panel)

        # 右侧详情
        right_panel = QWidget()
        right_panel.setProperty("glass", True)
        apply_glass_style(right_panel)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(12)

        self.term_label = create_section_title("选择一个术语查看详情")
        right_layout.addWidget(self.term_label)

        right_layout.addWidget(QLabel("释义："))
        self.definition_edit = QTextEdit()
        self.definition_edit.setPlaceholderText("术语释义...")
        right_layout.addWidget(self.definition_edit)

        right_layout.addWidget(QLabel("来源上下文："))
        # 只读展示，改用混排浏览器：术语上下文里出现的公式可以正常渲染
        self.context_edit = MathTextBrowser()
        self.context_edit.setPlaceholderText("术语提取时的原文上下文")
        right_layout.addWidget(self.context_edit)

        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存释义")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_definition)
        btn_layout.addWidget(self.save_btn)

        self.delete_btn = QPushButton("删除术语")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete_term)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        right_layout.addLayout(btn_layout)

        splitter.addWidget(right_panel)
        splitter.setSizes([360, 840])

    def refresh_data(self) -> None:
        """刷新术语列表。"""
        self.term_list.clear()
        try:
            terms = self.terminology_service.list_terms()
        except Exception as e:
            logger.error("Failed to list terms: %s", e)
            terms = []

        if not terms:
            item = QListWidgetItem("暂无术语，可从 OCR 或剪贴板提取")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.term_list.addItem(item)
            return

        for term in terms:
            item = QListWidgetItem(term["term"])
            item.setData(Qt.ItemDataRole.UserRole, term["id"])
            self.term_list.addItem(item)

    def _on_search(self) -> None:
        keyword = self.search_edit.text().strip()
        self.term_list.clear()
        try:
            terms = (
                self.terminology_service.search_terms(keyword)
                if keyword
                else self.terminology_service.list_terms()
            )
        except Exception as e:
            logger.error("Failed to search terms: %s", e)
            terms = []

        if not terms:
            self.term_list.addItem("未找到匹配术语")
            return

        for term in terms:
            item = QListWidgetItem(term["term"])
            item.setData(Qt.ItemDataRole.UserRole, term["id"])
            self.term_list.addItem(item)

    def _on_select_term(self, current: Optional[QListWidgetItem], previous) -> None:
        if current is None or current.flags() == Qt.ItemFlag.NoItemFlags:
            self._current_term_id = None
            self.term_label.setText("选择一个术语查看详情")
            self.definition_edit.clear()
            self.context_edit.clear()
            self.save_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return

        term_id = current.data(Qt.ItemDataRole.UserRole)
        term = self.terminology_service.get_term(term_id)
        if term is None:
            return
        self._current_term_id = term_id
        self.term_label.setText(term["term"])
        self.definition_edit.setPlainText(term.get("definition", "") or "")
        self.context_edit.set_rich_text(term.get("context", "") or "", markdown=False)
        self.save_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)

    def _on_save_definition(self) -> None:
        if self._current_term_id is None:
            return
        definition = self.definition_edit.toPlainText().strip()
        try:
            self.terminology_service.update_definition(
                self._current_term_id, definition
            )
            show_success(self, "术语释义已更新")
        except Exception as e:
            logger.error("Failed to update term definition: %s", e)
            QMessageBox.warning(self, "保存失败", str(e))

    def _show_term_context_menu(self, position) -> None:
        """术语列表右键菜单：复制、删除。"""
        item = self.term_list.itemAt(position)
        if item is None:
            return
        self.term_list.setCurrentItem(item)
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        act_copy = menu.addAction("复制术语")
        menu.addSeparator()
        act_delete = menu.addAction("删除术语")
        chosen = menu.exec(self.term_list.mapToGlobal(position))
        if chosen is act_copy:
            set_clipboard_text(item.text())
            show_success(self, "术语已复制到剪贴板")
        elif chosen is act_delete:
            self._on_delete_term()

    def _on_delete_term(self) -> None:
        if self._current_term_id is None:
            return
        if not ask_confirm(self, "确认删除", "是否删除该术语？"):
            return
        try:
            self.terminology_service.delete_term(self._current_term_id)
            self._current_term_id = None
            self.refresh_data()
        except Exception as e:
            logger.error("Failed to delete term: %s", e)
            QMessageBox.warning(self, "删除失败", str(e))

    def _extract_from_clipboard(self) -> None:
        clipboard = self.clipboard()
        text = clipboard.text()
        if not text.strip():
            QMessageBox.information(self, "提示", "剪贴板中没有文本")
            return
        try:
            with busy_cursor():
                terms = self.terminology_service.extract_terms(text, source_type="clipboard")
        except Exception as e:
            logger.error("Failed to extract terms from clipboard: %s", e)
            QMessageBox.warning(self, "提取失败", str(e))
            return

        if terms:
            show_success(self, f"成功提取并保存 {len(terms)} 个术语。")
            self.refresh_data()
        else:
            show_success(self, "未提取到新术语，可能都已存在或文本中术语较少。")

    def clipboard(self):
        from PyQt6.QtWidgets import QApplication
        return QApplication.clipboard()
