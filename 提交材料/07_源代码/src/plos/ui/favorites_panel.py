"""收藏夹面板。

汇总当前用户收藏的错题与知识库文档，点击可跳转定位。
所有数据通过 service 层读取，不直接操作数据库。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..services import ErrorBookService, RAGService
from ..utils.logger import get_logger
from .math_text import attach_math_delegate
from .ui_utils import create_section_title, apply_glass_style, create_empty_state_widget

logger = get_logger("ui.favorites_panel")


class FavoritesPanel(QWidget):
    """收藏夹统一入口。"""

    navigate_to = pyqtSignal(str, int)

    def __init__(
        self,
        errorbook_service: ErrorBookService,
        rag_service: Optional[RAGService] = None,
    ):
        super().__init__()
        self.errorbook_service = errorbook_service
        self.rag_service = rag_service
        self._build_ui()
        self.refresh_data()

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

        # 左侧：收藏的错题
        left = QWidget()
        left.setProperty("glass", True)
        apply_glass_style(left)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        left_title = create_section_title("收藏的错题")
        left_layout.addWidget(left_title)

        self.error_list = QListWidget()
        self.error_list.itemClicked.connect(self._on_error_clicked)
        # 收藏的错题题干里可能带公式，单元格走混排渲染
        self._error_math_delegate = attach_math_delegate(self.error_list, 0)
        left_layout.addWidget(self.error_list)

        self.error_empty = create_empty_state_widget("暂无收藏的错题")
        left_layout.addWidget(self.error_empty)

        left_layout.addLayout(self._build_error_actions())
        splitter.addWidget(left)

        # 右侧：收藏的文档
        right = QWidget()
        right.setProperty("glass", True)
        apply_glass_style(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(10)

        right_title = create_section_title("收藏的文档")
        right_layout.addWidget(right_title)

        self.doc_list = QListWidget()
        self.doc_list.itemClicked.connect(self._on_doc_clicked)
        right_layout.addWidget(self.doc_list)

        self.doc_empty = create_empty_state_widget("暂无收藏的文档")
        right_layout.addWidget(self.doc_empty)

        right_layout.addLayout(self._build_doc_actions())
        splitter.addWidget(right)

        splitter.setSizes([600, 600])
        layout.addWidget(splitter)

    def _build_error_actions(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_data)
        layout.addWidget(btn_refresh)
        layout.addStretch()
        return layout

    def _build_doc_actions(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_data)
        layout.addWidget(btn_refresh)
        layout.addStretch()
        return layout

    def refresh_data(self) -> None:
        """刷新收藏列表。"""
        self._refresh_errors()
        self._refresh_documents()

    def _refresh_errors(self) -> None:
        try:
            errors = self.errorbook_service.list_favorites()
        except Exception as e:
            logger.error("Failed to list favorite errors: %s", e)
            errors = []

        self.error_list.clear()
        has_items = len(errors) > 0
        self.error_list.setVisible(has_items)
        self.error_empty.setVisible(not has_items)

        for err in errors:
            item = QListWidgetItem(
                f"#{err['id']} {err.get('question', '')[:60]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, err["id"])
            item.setToolTip(err.get("question", ""))
            self.error_list.addItem(item)

    def _refresh_documents(self) -> None:
        try:
            docs = self.rag_service.list_favorites() if self.rag_service else []
        except Exception as e:
            logger.error("Failed to list favorite documents: %s", e)
            docs = []

        self.doc_list.clear()
        has_items = len(docs) > 0
        self.doc_list.setVisible(has_items)
        self.doc_empty.setVisible(not has_items)

        for doc in docs:
            item = QListWidgetItem(
                f"#{doc['id']} {doc.get('filename', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, doc["id"])
            self.doc_list.addItem(item)

    def _on_error_clicked(self, item: QListWidgetItem) -> None:
        error_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.navigate_to.emit("errorbook", error_id)

    def _on_doc_clicked(self, item: QListWidgetItem) -> None:
        doc_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.navigate_to.emit("knowledge", doc_id)
