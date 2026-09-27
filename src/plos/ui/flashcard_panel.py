"""闪卡面板。

支持创建闪卡、列表管理、SM-2 复习评分、导入导出 JSON/CSV/Markdown。
新增科目/标签筛选、TTS 朗读、全局搜索定位。
复习区采用大尺寸玻璃卡片翻牌效果，底部四色评级按钮。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.enums import FlashcardRating
from ..services import FlashcardService, TTSService
from ..utils.logger import get_logger
from .math_text import MathLabel
from .ui_utils import (
    create_section_title,
    apply_form_container_style,
    apply_glass_style,
    ask_confirm,
    create_empty_state_widget,
    repolish_tree,
    set_clipboard_text,
    set_danger_button_style,
    set_primary_button_style,
    show_error,
    show_info,
    show_success,
    show_warning,
    theme_colors,
)
from .workers import TTSWorker

logger = get_logger("ui.flashcard_panel")


# 评分按钮语义色映射到主题令牌（忘记=error 困难=warning 良好=success 简单=teal）
_RATING_TOKENS = {
    FlashcardRating.FORGOT: ("error", "rgba(244, 91, 91, 0.12)"),
    FlashcardRating.HARD: ("warning", "rgba(240, 169, 45, 0.12)"),
    FlashcardRating.GOOD: ("success", "rgba(45, 177, 127, 0.12)"),
    FlashcardRating.EASY: ("teal", "rgba(6, 182, 212, 0.12)"),
}


def _rating_style(rating: FlashcardRating) -> tuple[str, str]:
    """返回评分按钮的（前景色, 背景色），跟随当前主题令牌。"""
    token, bg = _RATING_TOKENS[rating]
    return theme_colors().get(token, "#6C7CFF"), bg

_FLASHCARD_STATUS = {
    "mastered": ("已掌握", "success"),
    "due": ("待复习", "info"),
    "weak": ("薄弱考点", "error"),
    "review": ("复习中", "info"),
}


class _FlipCard(MathLabel):
    """可点击翻牌的玻璃卡片。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("", parent, markdown=True)
        self.setProperty("glass", True)
        apply_glass_style(self)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_rich_text("点击「开始复习」抽取今日到期闪卡")
        self.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme_colors()['fg_primary']}; padding: 24px;"
        )
        self._front_text = ""
        self._back_text = ""
        self._showing_back = False

    def set_card(self, front: str, back: str) -> None:
        """设置卡片正反面内容。"""
        try:
            self._front_text = front
            self._back_text = back
            self._showing_back = False
            self._render()
        except Exception as e:
            logger.warning("Failed to set flip card: %s", e)

    def reset(self) -> None:
        """重置为空状态。"""
        self._front_text = ""
        self._back_text = ""
        self._showing_back = False
        try:
            text_color = self.palette().color(self.foregroundRole()).name()
        except Exception:
            text_color = "#8A93A8"
        self.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {text_color}; padding: 24px;"
        )
        self.set_rich_text("点击「开始复习」抽取今日到期闪卡")

    def flip(self) -> None:
        """在正反面之间切换。"""
        if self._front_text and self._back_text:
            self._showing_back = not self._showing_back
            self._render()

    def mousePressEvent(self, event) -> None:
        self.flip()
        super().mousePressEvent(event)

    def _render(self) -> None:
        try:
            # 使用 palette 获取当前主题文本色，避免在浅色主题下文字不可见
            text_color = self.palette().color(self.foregroundRole()).name()
            if self._showing_back:
                self.set_rich_text(f"<b>背面（答案）</b><br/><br/>{self._back_text}")
                self.setStyleSheet(
                    f"font-size: 16px; color: {text_color}; padding: 24px; "
                    "background-color: rgba(139, 92, 246, 0.18);"
                )
            else:
                self.set_rich_text(f"<b>正面（问题）</b><br/><br/>{self._front_text}")
                self.setStyleSheet(
                    f"font-size: 18px; font-weight: bold; color: {text_color}; padding: 24px;"
                )
        except Exception as e:
            logger.warning("Failed to render flip card: %s", e)


class FlashcardPanel(QWidget):
    """闪卡记忆界面。"""

    def __init__(
        self,
        flashcard_service: FlashcardService,
        tts_service: Optional[TTSService] = None,
    ):
        super().__init__()
        self.flashcard_service = flashcard_service
        self.tts_service = tts_service
        self.selected_id: int | None = None
        self._review_card_id: int | None = None
        self._review_queue: list[dict] = []
        self._current_subject_filter: str = ""
        self._current_tag_filter: str = ""
        self._build_ui()
        self._refresh_list()
        self._init_shortcuts()

    def _init_shortcuts(self) -> None:
        """Ctrl+S 保存当前编辑的闪卡。"""
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        save_shortcut.activated.connect(self._save_card)

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
        apply_form_container_style(container)
        scroll.setWidget(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：列表与统计
        left = QWidget()
        left.setProperty("glass", True)
        apply_glass_style(left)
        left.setMinimumWidth(280)
        left.setMaximumWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        self.stats_label = create_section_title("闪卡统计")
        left_layout.addWidget(self.stats_label)

        # 筛选区
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(8)

        self.subject_combo = QComboBox()
        self.subject_combo.setEditable(False)
        self.subject_combo.addItem("全部科目", "")
        self.subject_combo.currentTextChanged.connect(self._on_subject_filter_changed)
        filter_layout.addWidget(QLabel("科目："))
        filter_layout.addWidget(self.subject_combo, 1)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("标签筛选...")
        self.tag_edit.textChanged.connect(self._on_tag_filter_changed)
        filter_layout.addWidget(QLabel("标签："))
        filter_layout.addWidget(self.tag_edit, 1)

        left_layout.addLayout(filter_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "正面", "背面", "类型", "科目", "标签", "下次复习", "状态"]
        )
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemClicked.connect(self._on_item_selected)
        self.table.cellDoubleClicked.connect(lambda row, col: self._on_item_selected(self.table.item(row, 0)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        left_layout.addWidget(self.table)

        self.empty_state = create_empty_state_widget("暂无闪卡，点击新增按钮添加")
        left_layout.addWidget(self.empty_state)

        btn_layout = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_list)
        btn_layout.addWidget(btn_refresh)

        btn_import = QPushButton("导入")
        btn_import.clicked.connect(self._import_cards)
        btn_layout.addWidget(btn_import)

        btn_export = QPushButton("导出")
        btn_export.clicked.connect(self._export_cards)
        btn_layout.addWidget(btn_export)

        btn_export_md = QPushButton("导出 Markdown")
        btn_export_md.setObjectName("primary_btn")
        btn_export_md.clicked.connect(self._export_markdown)
        btn_layout.addWidget(btn_export_md)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left)

        # 右侧：编辑与复习
        right = QWidget()
        right.setProperty("glass", True)
        apply_glass_style(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        # 编辑区
        edit_title = create_section_title("编辑闪卡")
        right_layout.addWidget(edit_title)

        right_layout.addWidget(QLabel("正面："))
        self.front_edit = QTextEdit()
        self.front_edit.setPlaceholderText("正面内容（问题）...")
        self.front_edit.setMinimumHeight(80)
        self.front_edit.setMaximumHeight(120)
        right_layout.addWidget(self.front_edit)

        right_layout.addWidget(QLabel("背面："))
        self.back_edit = QTextEdit()
        self.back_edit.setPlaceholderText("背面内容（答案）...")
        self.back_edit.setMinimumHeight(80)
        self.back_edit.setMaximumHeight(120)
        right_layout.addWidget(self.back_edit)

        meta_layout = QHBoxLayout()
        meta_layout.addWidget(QLabel("科目："))
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("如：数学")
        meta_layout.addWidget(self.subject_edit, 1)
        meta_layout.addWidget(QLabel("标签："))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用逗号分隔，如：函数,易错")
        meta_layout.addWidget(self.tags_edit, 2)
        right_layout.addLayout(meta_layout)

        edit_btn_layout = QHBoxLayout()
        btn_add = QPushButton("新增")
        btn_add.clicked.connect(self._add_card)
        edit_btn_layout.addWidget(btn_add)

        btn_save = QPushButton("保存")
        set_primary_button_style(btn_save)
        btn_save.clicked.connect(self._save_card)
        edit_btn_layout.addWidget(btn_save)

        btn_tts = QPushButton("朗读")
        btn_tts.setToolTip("朗读当前编辑内容")
        btn_tts.clicked.connect(self._speak_edit_content)
        edit_btn_layout.addWidget(btn_tts)

        btn_del = QPushButton("删除")
        set_danger_button_style(btn_del)
        btn_del.clicked.connect(self._delete_card)
        edit_btn_layout.addWidget(btn_del)
        right_layout.addLayout(edit_btn_layout)

        # 复习区
        review_header = QHBoxLayout()
        review_title = create_section_title("今日复习")
        review_header.addWidget(review_title)
        review_header.addStretch()
        self.review_progress_label = QLabel("")
        self.review_progress_label.setStyleSheet("color: #8A93A8; font-size: 12px;")
        review_header.addWidget(self.review_progress_label)
        right_layout.addLayout(review_header)

        self.flip_card = _FlipCard()
        self.flip_card.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        right_layout.addWidget(self.flip_card, 1)

        hint_label = QLabel("点击卡片或按空格键翻面 · 按 1-4 数字键评分")
        hint_label.setStyleSheet("color: #8A93A8; font-size: 11px;")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(hint_label)

        # 复习快捷键：绑定在翻牌卡片上，仅当卡片获得焦点时生效，
        # 不影响在其他输入框中正常打字
        flip_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.flip_card)
        flip_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        flip_shortcut.activated.connect(self.flip_card.flip)
        self._rating_shortcuts = []
        for key_index, rating in enumerate(FlashcardRating, start=1):
            shortcut = QShortcut(QKeySequence(str(key_index)), self.flip_card)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda checked=False, r=rating: self._rate_card(r))
            self._rating_shortcuts.append(shortcut)

        review_action_layout = QHBoxLayout()
        btn_study = QPushButton("开始复习")
        set_primary_button_style(btn_study)
        btn_study.clicked.connect(self._start_review)
        review_action_layout.addWidget(btn_study)

        btn_speak_review = QPushButton("朗读当前卡片")
        btn_speak_review.setToolTip("朗读当前复习卡片的正/反面")
        btn_speak_review.clicked.connect(self._speak_review_card)
        review_action_layout.addWidget(btn_speak_review)

        btn_skip = QPushButton("跳过")
        btn_skip.setToolTip("暂时跳过这张卡，它会排到本轮复习的最后")
        btn_skip.clicked.connect(self._skip_current_card)
        review_action_layout.addWidget(btn_skip)
        right_layout.addLayout(review_action_layout)

        # 将剩余空间分配给闪卡主体，评分按钮固定在底部不被挤压
        right_layout.addStretch(1)

        self._rating_buttons = []
        rating_layout = QHBoxLayout()
        for key_index, rating in enumerate(FlashcardRating, start=1):
            btn = QPushButton(f"{rating.label}（{key_index}）")
            btn.setToolTip(f"{rating.description}（快捷键 {key_index}）")
            btn.setMinimumHeight(36)
            self._apply_rating_style(btn, rating)
            btn.clicked.connect(lambda checked, r=rating: self._rate_card(r))
            rating_layout.addWidget(btn)
            self._rating_buttons.append((btn, rating))
        right_layout.addLayout(rating_layout)

        splitter.addWidget(right)
        splitter.setSizes([300, 900])

        layout.addWidget(splitter)

    def _on_subject_filter_changed(self, text: str) -> None:
        """科目筛选变更。"""
        data = self.subject_combo.currentData()
        self._current_subject_filter = data if data is not None else ""
        self._refresh_list()

    def _on_tag_filter_changed(self, text: str) -> None:
        """标签筛选变更。"""
        self._current_tag_filter = text.strip()
        self._refresh_list()

    def _refresh_subject_filter(self) -> None:
        """刷新科目筛选下拉框。"""
        try:
            subjects = self.flashcard_service.list_subjects()
        except Exception as e:
            logger.warning("Failed to list subjects: %s", e)
            return
        current = self.subject_combo.currentData()
        # 暂时阻塞信号，避免 addItem/clear 触发 currentTextChanged 导致递归刷新
        self.subject_combo.blockSignals(True)
        try:
            self.subject_combo.clear()
            self.subject_combo.addItem("全部科目", "")
            for subj in subjects:
                self.subject_combo.addItem(subj, subj)
            if current:
                index = self.subject_combo.findData(current)
                if index >= 0:
                    self.subject_combo.setCurrentIndex(index)
        finally:
            self.subject_combo.blockSignals(False)

    def _refresh_list(self) -> None:
        """刷新闪卡列表。"""
        try:
            cards = self.flashcard_service.list_cards(
                limit=200,
                subject=self._current_subject_filter or None,
                tag=self._current_tag_filter or None,
            )
            stats = self.flashcard_service.get_stats()
        except Exception as e:
            logger.error("Failed to list flashcards: %s", e)
            show_error(self, "刷新失败", str(e))
            return

        self._refresh_subject_filter()
        self.stats_label.setText(f"总闪卡：{stats['total']} | 今日到期：{stats['due_today']}")
        self.table.setRowCount(len(cards))
        has_cards = len(cards) > 0
        self.table.setVisible(has_cards)
        self.empty_state.setVisible(not has_cards)

        for i, row in enumerate(cards):
            self.table.setItem(i, 0, QTableWidgetItem(str(row["id"])))
            self.table.setItem(i, 1, QTableWidgetItem(row["front_content"][:60]))
            self.table.setItem(i, 2, QTableWidgetItem(row["back_content"][:60]))
            self.table.setItem(i, 3, QTableWidgetItem(row["card_type"]))
            self.table.setItem(i, 4, QTableWidgetItem(row.get("subject", "")))
            self.table.setItem(i, 5, QTableWidgetItem(row.get("tags", "")))
            self.table.setItem(i, 6, QTableWidgetItem(row["next_review"]))

            status = self._resolve_card_status(row)
            status_text, status_token = _FLASHCARD_STATUS[status]
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(QColor(theme_colors().get(status_token, theme_colors()["fg_primary"])))
            self.table.setItem(i, 7, status_item)

    @staticmethod
    def _resolve_card_status(row: dict) -> str:
        """根据 SM-2 数据解析闪卡状态。"""
        try:
            next_review = datetime.fromisoformat(row.get("next_review", ""))
            is_due = next_review <= datetime.now()
        except Exception:
            is_due = False

        interval = int(row.get("interval", 0) or 0)
        repetitions = int(row.get("repetitions", 0) or 0)

        if is_due:
            return "due"
        if repetitions >= 3 and interval >= 7:
            return "mastered"
        if repetitions >= 3 and interval < 2:
            return "weak"
        return "due" if is_due else "review"

    def _on_item_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        card_id = int(self.table.item(row, 0).text())
        card = self.flashcard_service.get_card(card_id)
        if card is None:
            return
        self.selected_id = card_id
        self.front_edit.setPlainText(card["front_content"])
        self.back_edit.setPlainText(card["back_content"])
        self.subject_edit.setText(card.get("subject", ""))
        self.tags_edit.setText(card.get("tags", ""))

    def _show_table_context_menu(self, position) -> None:
        """闪卡列表右键菜单：编辑、复制内容、删除。"""
        item = self.table.itemAt(position)
        if item is None:
            return
        row = item.row()
        card_id_item = self.table.item(row, 0)
        if card_id_item is None:
            return
        self.table.selectRow(row)
        self._on_item_selected(card_id_item)

        front = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        back = self.table.item(row, 2).text() if self.table.item(row, 2) else ""

        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        act_edit = menu.addAction("编辑此卡")
        act_copy_front = menu.addAction("复制正面内容")
        act_copy_back = menu.addAction("复制背面内容")
        menu.addSeparator()
        act_delete = menu.addAction("删除闪卡")
        chosen = menu.exec(self.table.mapToGlobal(position))
        if chosen is None:
            return
        if chosen is act_edit:
            return  # 已载入编辑区
        if chosen is act_copy_front:
            set_clipboard_text(front)
            show_success(self, "正面内容已复制到剪贴板")
        elif chosen is act_copy_back:
            set_clipboard_text(back)
            show_success(self, "背面内容已复制到剪贴板")
        elif chosen is act_delete:
            self._delete_card()

    def _add_card(self) -> None:
        self.selected_id = None
        self.front_edit.clear()
        self.back_edit.clear()
        self.subject_edit.clear()
        self.tags_edit.clear()

    def _save_card(self) -> None:
        front = self.front_edit.toPlainText().strip()
        back = self.back_edit.toPlainText().strip()
        if not front or not back:
            show_warning(self, "提示", "正面与背面内容不能为空。")
            return

        subject = self.subject_edit.text().strip()
        tags = self.tags_edit.text().strip()

        try:
            if self.selected_id is None:
                self.flashcard_service.add_card(
                    front_content=front,
                    back_content=back,
                    subject=subject,
                    tags=tags,
                )
            else:
                # 通过 service 方法更新，带 user_id 隔离
                self.flashcard_service.update_card(
                    card_id=self.selected_id,
                    front_content=front,
                    back_content=back,
                    subject=subject,
                    tags=tags,
                )
            show_success(self, "闪卡已保存")
            self._refresh_list()
        except Exception as e:
            logger.error("Save flashcard failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _delete_card(self) -> None:
        if self.selected_id is None:
            return
        if not ask_confirm(self, "确认", "确定删除这张闪卡？"):
            return
        try:
            self.flashcard_service.delete_card(self.selected_id)
            self.selected_id = None
            self._add_card()
            self._refresh_list()
        except Exception as e:
            logger.error("Delete flashcard failed: %s", e)
            show_error(self, "删除失败", str(e))

    @staticmethod
    def _apply_rating_style(btn: QPushButton, rating: FlashcardRating) -> None:
        """按当前主题令牌为评分按钮着色。"""
        color, bg = _rating_style(rating)
        btn.setStyleSheet(
            f"background-color: {bg}; color: {color}; border: 1px solid {color}77; "
            f"border-radius: 8px; padding: 8px 14px; font-weight: 600;"
        )

    def on_theme_changed(self) -> None:
        """主题切换后重刷评分按钮颜色与整树样式。"""
        for btn, rating in getattr(self, "_rating_buttons", []):
            self._apply_rating_style(btn, rating)
        repolish_tree(self)
        self.flip_card.update()

    def _start_review(self) -> None:
        try:
            due = self.flashcard_service.get_due_cards(limit=50)
        except Exception as e:
            logger.error("Failed to get due cards: %s", e)
            show_error(self, "复习失败", str(e))
            return

        if not due:
            show_success(self, "今日没有到期的闪卡，复习任务已全部完成！")
            self._review_card_id = None
            self._review_queue = []
            self._update_review_progress()
            self.flip_card.reset()
            return

        self._review_queue = list(due)
        self._review_stats = {"total": 0, "skipped": 0}
        self._show_next_review_card()
        self.flip_card.setFocus()
        self.flip_card.setFocus()

    def _show_next_review_card(self) -> None:
        """展示队列中的下一张到期闪卡；队列空则展示小结并结束本轮。"""
        if not self._review_queue:
            self._review_card_id = None
            self._update_review_progress()
            self.flip_card.reset()
            stats = getattr(self, "_review_stats", None)
            if stats and stats.get("total", 0) + stats.get("skipped", 0) > 0:
                self.review_progress_label.setText(
                    f"本轮完成 {stats.get('total', 0)} 张，跳过 {stats.get('skipped', 0)} 张"
                )
            return

        card = self._review_queue[0]
        self._review_card_id = card["id"]
        self.flip_card.set_card(card["front_content"], card["back_content"])
        self._update_review_progress()
        # 焦点交给翻牌卡：1~4 评分与空格翻卡无需先用鼠标点卡片
        self.flip_card.setFocus()

    def _update_review_progress(self) -> None:
        """更新复习区剩余到期数量提示。"""
        if self._review_queue:
            self.review_progress_label.setText(f"剩余 {len(self._review_queue)} 张待复习")
        else:
            self.review_progress_label.setText("")

    def _rate_card(self, rating: FlashcardRating) -> None:
        card_id = self._review_card_id
        if card_id is None:
            show_warning(self, "提示", "请先点击「开始复习」。")
            return
        try:
            self.flashcard_service.review_card(card_id, rating)
        except Exception as e:
            logger.error("Review card failed: %s", e)
            show_error(self, "评分失败", str(e))
            return

        # 评分后自动切换到下一张，形成连续复习流
        if self._review_queue and self._review_queue[0].get("id") == card_id:
            self._review_queue.pop(0)
        stats = getattr(self, "_review_stats", None)
        if stats is not None:
            stats["total"] = stats.get("total", 0) + 1
        self._refresh_list()
        if self._review_queue:
            show_success(self, f"已记录：{rating.label}，剩余 {len(self._review_queue)} 张")
            self._show_next_review_card()
            self.flip_card.setFocus()
        else:
            show_success(
                self,
                f"今日复习完成！共 {stats.get('total', 0) if stats else 1} 张"
                + (f"，跳过 {stats.get('skipped', 0)} 张" if stats and stats.get("skipped") else ""),
            )
            self._show_next_review_card()

    def _skip_current_card(self) -> None:
        """跳过当前卡片：排到本轮队列末尾稍后再来。"""
        card_id = self._review_card_id
        if card_id is None or not self._review_queue:
            show_warning(self, "提示", "请先点击「开始复习」。")
            return
        current = self._review_queue.pop(0)
        self._review_queue.append(current)
        stats = getattr(self, "_review_stats", None)
        if stats is not None:
            stats["skipped"] = stats.get("skipped", 0) + 1
        show_success(self, f"已跳过，稍后再来（剩余 {len(self._review_queue)} 张）")
        self._show_next_review_card()
        self.flip_card.setFocus()

    def _speak_edit_content(self) -> None:
        """朗读编辑区正/背面内容。"""
        text = f"正面：{self.front_edit.toPlainText().strip()}。背面：{self.back_edit.toPlainText().strip()}"
        self._speak(text)

    def _speak_review_card(self) -> None:
        """朗读当前复习卡片。"""
        if not self._review_card_id:
            show_info(self, "提示", "请先开始复习。")
            return
        text = self.flip_card._front_text if not self.flip_card._showing_back else self.flip_card._back_text
        prefix = "正面" if not self.flip_card._showing_back else "背面"
        self._speak(f"{prefix}：{text}")

    def _speak(self, text: str) -> None:
        if not text.strip():
            return
        if self.tts_service is None:
            show_info(self, "提示", "TTS 服务未启用。")
            return
        try:
            worker = TTSWorker(self.tts_service, text)
            from .workers import ThreadPool

            ThreadPool.start_worker(worker)
        except Exception as e:
            logger.error("TTS failed: %s", e)
            show_error(self, "朗读失败", str(e))

    def _import_cards(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入闪卡",
            "",
            "JSON/CSV (*.json *.csv)",
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            if path.suffix.lower() == ".json":
                count = self.flashcard_service.import_from_json(path)
            elif path.suffix.lower() == ".csv":
                count = self.flashcard_service.import_from_csv(path)
            else:
                show_error(self, "格式不支持", "仅支持 JSON 或 CSV 文件")
                return
            show_info(self, "导入成功", f"成功导入 {count} 张闪卡")
            self._refresh_list()
        except Exception as e:
            logger.error("Import flashcards failed: %s", e)
            show_error(self, "导入失败", str(e))

    def _export_cards(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出闪卡",
            "flashcards.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            if path.suffix.lower() == ".json":
                count = self.flashcard_service.export_to_json(path)
            elif path.suffix.lower() == ".csv":
                count = self.flashcard_service.export_to_csv(path)
            else:
                show_error(self, "格式不支持", "仅支持 JSON 或 CSV 文件")
                return
            show_info(self, "导出成功", f"成功导出 {count} 张闪卡到 {path}")
        except Exception as e:
            logger.error("Export flashcards failed: %s", e)
            show_error(self, "导出失败", str(e))

    def _export_markdown(self) -> None:
        """导出当前筛选后的闪卡为 Markdown。"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出闪卡为 Markdown",
            "flashcards.md",
            "Markdown (*.md)",
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            count = self.flashcard_service.export_to_markdown(path)
            show_info(self, "导出成功", f"成功导出 {count} 张闪卡到 {path}")
        except Exception as e:
            logger.error("Export markdown failed: %s", e)
            show_error(self, "导出失败", str(e))

    def refresh_data(self) -> None:
        """用户切换后刷新数据。"""
        self._add_card()
        self._review_card_id = None
        self._review_queue = []
        self._update_review_progress()
        self.flip_card.reset()
        self._refresh_list()

    def locate_item(self, item_type: str, item_id: int) -> None:
        """全局搜索定位到指定闪卡。"""
        if item_type != "flashcard":
            return
        card = self.flashcard_service.get_card(item_id)
        if card is None:
            return
        self.selected_id = item_id
        self.front_edit.setPlainText(card["front_content"])
        self.back_edit.setPlainText(card["back_content"])
        self.subject_edit.setText(card.get("subject", ""))
        self.tags_edit.setText(card.get("tags", ""))
        # 高亮列表行
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0) and int(self.table.item(row, 0).text()) == item_id:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                break
