"""对话面板。

提供会话列表、气泡消息展示、用户输入、RAG 与个性化上下文开关、
AI 回答朗读、清空对话、复制消息。所有模型调用通过 worker 后台执行。

交互层对标苹果式体验：
- 右上角 AI 状态指示器（空闲 / 收到 / 思考中 / 输出中）四态平滑过渡；
- Enter / Ctrl+Enter 发送，Shift+Enter 换行，输入框高度自适应；
- 流式输出按 ~80ms 合并刷新，打字节奏柔和，避免高频重绘造成卡顿；
- 所有数据库写入（收藏、加入错题本）均在后台线程执行。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services import ChatService, ErrorBookService, NoteService, RAGService, TTSService
from ..utils.logger import get_logger
from .interactions import (
    FRAME_INTERVAL_MS,
    friendly_error_message,
    policy,
)
from .markdown_browser import MarkdownBrowser
from .math_text import MathTextBrowser, attach_live_preview
from .theme_manager import ThemeManager
from .ui_utils import (
    theme_colors,
    apply_card_style,
    ask_confirm,
    set_clipboard_text,
    set_primary_button_style,
    shake_widget,
    show_error,
    show_success,
    show_warning_toast,
    ButtonBusy,
)
from .workers import CallableWorker, ChatStreamWorker, ThreadPool, TTSWorker

logger = get_logger("ui.chat_panel")


class AssistantStatusIndicator(QWidget):
    """对话右上角的 AI 状态指示器（苹果 Siri 式状态感知）。

    四个状态平滑过渡，且**只在活跃状态驱动低帧率定时器**：
    空闲与输出完成时定时器立即停止，不产生任何后台开销；
    低配设备 / 闲置降级时不跑动画，仅保留文字状态提示。
    """

    IDLE = "idle"
    RECEIVED = "received"
    THINKING = "thinking"
    STREAMING = "streaming"

    _TEXTS = {
        IDLE: "助教就绪",
        RECEIVED: "收到",
        THINKING: "正在思考…",
        STREAMING: "正在输出…",
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = self.IDLE
        self._phase = 0.0
        self._timer = QTimer(self)
        # 约 10fps 的轻量刷新：远低于 30fps 上限，不高刷屏
        self._timer.setInterval(FRAME_INTERVAL_MS * 3)
        self._timer.timeout.connect(self._on_tick)
        self.setFixedHeight(24)
        self.setMinimumWidth(120)
        self.setToolTip("AI 助教实时状态")

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        """切换状态并同步定时器（无变化的重复设置直接忽略）。"""
        if state not in self._TEXTS:
            state = self.IDLE
        if state == self._state:
            return
        self._state = state
        self._phase = 0.0
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        active = self._state in (self.RECEIVED, self.THINKING) and policy().enabled
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()

    def _on_tick(self) -> None:
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(126, 24)

    def paintEvent(self, event) -> None:  # noqa: N802
        try:
            c = theme_colors()
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            center_y = self.height() / 2.0
            center_x = 10.0
            idle_color = c["fg_muted"]
            active_color = c["accent"]

            if self._state == self.IDLE:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(idle_color))
                painter.drawEllipse(QPoint(int(center_x), int(center_y)), 4, 4)
            elif self._state == self.RECEIVED:
                # 蓝色低亮度脉动：柔和呼吸，不刺眼
                alpha = 0.35 + 0.45 * (0.5 + 0.5 * math.sin(self._phase * 2 * math.pi))
                color = QColor(active_color)
                color.setAlphaF(alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(QPoint(int(center_x), int(center_y)), 4, 4)
            elif self._state == self.THINKING:
                # 慢速环形轻加载（约 10fps 转动）
                pen = QPen(QColor(active_color))
                pen.setWidth(2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                rect = QRectF(center_x - 6, center_y - 6, 12, 12)
                painter.drawArc(rect, int(-self._phase * 360 * 16), int(270 * 16))
            else:  # STREAMING
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(active_color))
                painter.drawEllipse(QPoint(int(center_x), int(center_y)), 4, 4)
                tail = QColor(active_color)
                tail.setAlphaF(0.30)
                painter.setBrush(tail)
                painter.drawEllipse(QPoint(int(center_x) + 9, int(center_y)), 3, 3)

            font = self.font()
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(QColor(c["fg_secondary"] if self._state == self.IDLE else c["accent"]))
            painter.drawText(
                QRectF(24, 0, max(0, self.width() - 26), self.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self._TEXTS[self._state],
            )
            painter.end()
        except Exception:
            pass


class ChatInputEdit(QPlainTextEdit):
    """对话输入框：多行自适应高度 + 苹果式快捷键。

    - Enter / Ctrl+Enter 发送消息，Shift+Enter 换行；
    - 内容增多时自动增高，达到上限后出现垂直滚动条。
    """

    submit_requested = pyqtSignal()

    MIN_HEIGHT = 40
    MAX_HEIGHT = 140

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("chat_input")
        self.setPlaceholderText("输入问题，Enter 发送，Shift+Enter 换行…")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)
        self._height_cache = -1
        self.textChanged.connect(self._auto_resize)
        self._auto_resize()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)  # Shift+Enter 换行
                return
            self.submit_requested.emit()  # Enter / Ctrl+Enter 发送
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._auto_resize()

    def _auto_resize(self) -> None:
        """按内容行数换算出像素高度，超出上限后交给滚动条。

        注意：QPlainTextEdit 使用 QPlainTextDocumentLayout，
        其 ``documentSize().height()`` 的单位是「行数」而非像素，需要换算。
        """
        try:
            width = self.viewport().width()
            if width <= 0:
                return
            doc = self.document()
            doc.setTextWidth(width)
            lines = max(1, int(doc.documentLayout().documentSize().height()))
            text_height = lines * self.fontMetrics().lineSpacing()
            extra = int(doc.documentMargin()) * 2 + 16 + int(self.frameWidth()) * 2
            target = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, text_height + extra))
            if target != self._height_cache:
                self._height_cache = target
                self.setFixedHeight(target)
        except Exception:
            pass


class ChatPanel(QWidget):
    """AI 对话界面。"""

    def __init__(
        self,
        chat_service: ChatService,
        rag_service: Optional[RAGService] = None,
        errorbook_service: Optional[ErrorBookService] = None,
        tts_service: Optional[TTSService] = None,
        note_service: Optional[NoteService] = None,
        config: Optional[Dict[str, Any]] = None,
        theme_manager: Optional[ThemeManager] = None,
    ):
        super().__init__()
        self.chat_service = chat_service
        self.rag_service = rag_service
        self.errorbook_service = errorbook_service
        self.tts_service = tts_service
        self.note_service = note_service
        self.config = config or {}
        self.theme_manager = theme_manager
        self.current_conversation_id: int = 0
        self._transcript: list = []
        self._stream_started: bool = False
        self._last_assistant_text: str = ""
        self._last_meta: Optional[dict] = None
        # 流式输出节流：合并高频 chunk，按固定节奏渲染，避免刷屏与重绘开销
        self._chunk_buffer: str = ""
        self._placeholder_active: bool = False
        self._last_sent_text: str = ""
        self._hover_pos: Optional[QPoint] = None
        self._hover_block: int = -1
        self._build_ui()
        self._apply_config_defaults()
        self._create_conversation()

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

        # 左侧会话列表
        left = QWidget()
        left.setProperty("card", True)
        apply_card_style(left)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)

        left_title = QLabel("会话列表")
        left_title.setObjectName("section_title")
        left_layout.addWidget(left_title)

        self.conversation_list = QListWidget()
        self.conversation_list.itemClicked.connect(self._on_conversation_selected)
        self.conversation_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.conversation_list.customContextMenuRequested.connect(self._show_session_menu)
        left_layout.addWidget(self.conversation_list)

        btn_new = QPushButton("新建会话")
        btn_new.clicked.connect(self._create_conversation)
        btn_clear = QPushButton("清空当前会话")
        btn_clear.clicked.connect(self._clear_current_conversation)
        left_layout.addWidget(btn_new)
        left_layout.addWidget(btn_clear)

        splitter.addWidget(left)

        # 右侧聊天区
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # 顶部开关区
        switch_layout = QHBoxLayout()
        switch_layout.addStretch()

        self.rag_check = QCheckBox("使用知识库")
        self.rag_check.setToolTip("回答时优先检索知识库内容作为参考")
        switch_layout.addWidget(self.rag_check)

        self.context_check = QCheckBox("启用知识库&错题上下文")
        self.context_check.setToolTip("开启后自动结合当前用户的错题本和知识库生成个性化答疑")
        switch_layout.addWidget(self.context_check)

        mode_label = QLabel("教学模式：")
        switch_layout.addWidget(mode_label)

        self.teaching_mode_combo = QComboBox()
        self.teaching_mode_combo.addItem("普通模式", "normal")
        self.teaching_mode_combo.addItem("启发教学模式", "socratic")
        self.teaching_mode_combo.setToolTip("普通模式直接回答；启发教学模式引导学生独立思考")
        self.teaching_mode_combo.setMinimumWidth(120)
        switch_layout.addWidget(self.teaching_mode_combo)

        self.no_direct_answer_check = QCheckBox("禁止直接给出答案")
        self.no_direct_answer_check.setToolTip("开启后 AI 只提供提示和引导，不输出完整答案")
        switch_layout.addWidget(self.no_direct_answer_check)

        explain_label = QLabel("讲解模式：")
        switch_layout.addWidget(explain_label)

        self.explanation_mode_combo = QComboBox()
        self.explanation_mode_combo.addItem("基础版", "basic")
        self.explanation_mode_combo.addItem("进阶版", "advanced")
        self.explanation_mode_combo.addItem("应试版", "exam")
        self.explanation_mode_combo.addItem("科研深度版", "research")
        self.explanation_mode_combo.setToolTip("切换知识点的讲解深度与风格")
        self.explanation_mode_combo.setMinimumWidth(120)
        switch_layout.addWidget(self.explanation_mode_combo)

        self.tts_btn = QPushButton("朗读")
        self.tts_btn.setToolTip("朗读最近一条 AI 回答")
        self.tts_btn.setEnabled(False)
        self.tts_btn.clicked.connect(self._speak_last_reply)
        switch_layout.addWidget(self.tts_btn)

        # 右上角 AI 状态指示器：空闲 / 收到 / 思考中 / 输出中
        self.status_indicator = AssistantStatusIndicator()
        switch_layout.addWidget(self.status_indicator)

        right_layout.addLayout(switch_layout)

        self.chat_display = MarkdownBrowser()
        self.chat_display.browser.setOpenExternalLinks(True)
        self.chat_display.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_display.browser.customContextMenuRequested.connect(self._show_message_menu)
        self.chat_display.set_placeholder_text("开始一个新对话吧...")
        right_layout.addWidget(self.chat_display)
        self._install_hover_feedback()

        # 流式刷新节流定时器：约 80ms 合并一次，打字节奏柔和且不刷屏
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(80)
        self._stream_flush_timer.timeout.connect(self._flush_stream_buffer)

        # 失败重试条：模型超时 / 异常时出现，不打断其他操作
        self.retry_bar = QWidget()
        self.retry_bar.setVisible(False)
        retry_layout = QHBoxLayout(self.retry_bar)
        retry_layout.setContentsMargins(10, 6, 10, 6)
        retry_layout.setSpacing(10)
        self.retry_label = QLabel("")
        self.retry_label.setWordWrap(True)
        self.retry_label.setObjectName("subtitle_label")
        retry_layout.addWidget(self.retry_label, 1)
        self.retry_btn = QPushButton("重试")
        self.retry_btn.setMinimumWidth(72)
        self.retry_btn.clicked.connect(self._retry_last_message)
        retry_layout.addWidget(self.retry_btn)
        right_layout.addWidget(self.retry_bar)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        input_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self.input_edit = ChatInputEdit()
        self.input_edit.submit_requested.connect(self._send_message)
        self.input_edit.textChanged.connect(self._update_send_enabled)
        input_layout.addWidget(self.input_edit, 1)

        self.send_btn = QPushButton("发送")
        set_primary_button_style(self.send_btn)
        self.send_btn.setMinimumWidth(80)
        self.send_btn.setMinimumHeight(ChatInputEdit.MIN_HEIGHT)
        self.send_btn.clicked.connect(self._send_message)
        input_layout.addWidget(self.send_btn)

        # 输入区容器：输入行 + 公式实时预览
        input_area = QVBoxLayout()
        input_area.setSpacing(6)
        input_area.addLayout(input_layout)
        self.input_preview = MathTextBrowser(self)
        self.input_preview.setMaximumHeight(120)
        self.input_preview.set_placeholder_text("输入 \\(公式\\) 或 \\[公式\\] 可在此预览")
        input_area.addWidget(self.input_preview)
        self._input_preview_connector = attach_live_preview(self.input_edit, self.input_preview)
        right_layout.addLayout(input_area)
        splitter.addWidget(right)
        splitter.setSizes([240, 960])

        layout.addWidget(splitter)
        self._update_send_enabled()

    def _apply_config_defaults(self) -> None:
        """从全局配置初始化教学模式、输出约束与讲解模式默认值。"""
        chat_cfg = self.config.get("chat", {})
        mode = chat_cfg.get("teaching_mode", "normal")
        index = self.teaching_mode_combo.findData(mode)
        if index >= 0:
            self.teaching_mode_combo.setCurrentIndex(index)
        self.no_direct_answer_check.setChecked(bool(chat_cfg.get("no_direct_answer", False)))
        explain_mode = chat_cfg.get("explanation_mode", "basic")
        exp_index = self.explanation_mode_combo.findData(explain_mode)
        if exp_index >= 0:
            self.explanation_mode_combo.setCurrentIndex(exp_index)

    def _get_teaching_mode(self) -> str:
        """获取当前选中的教学模式值。"""
        return str(self.teaching_mode_combo.currentData() or "normal")

    def _get_explanation_mode(self) -> str:
        """获取当前选中的讲解模式值。"""
        return str(self.explanation_mode_combo.currentData() or "basic")

    def refresh_data(self) -> None:
        """用户切换后重置会话区，为新用户开启新会话。"""
        self.conversation_list.clear()
        self.chat_display.clear()
        self._last_assistant_text = ""
        self.current_conversation_id = 0
        self._create_conversation()

    def _create_conversation(self) -> None:
        """新建会话。"""
        try:
            conversation_id = self.chat_service.create_conversation()
        except Exception as e:
            logger.error("Failed to create conversation: %s", e)
            show_error(self, "创建会话失败", str(e))
            return

        self.current_conversation_id = conversation_id
        item = QListWidgetItem(f"会话 {conversation_id}")
        item.setData(Qt.ItemDataRole.UserRole, conversation_id)
        self.conversation_list.addItem(item)
        self.conversation_list.setCurrentItem(item)
        self.chat_display.clear()
        self._last_assistant_text = ""
        self.tts_btn.setEnabled(False)
        self._placeholder_active = False
        self._set_status(AssistantStatusIndicator.IDLE)
        self._hide_retry_bar()
        logger.info("Created conversation id=%d in UI", conversation_id)

    def _on_conversation_selected(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_conversation_id = conversation_id
        self._render_messages()

    def _render_messages(self) -> None:
        """渲染当前会话的所有消息。"""
        self.chat_display.clear()
        try:
            messages = self.chat_service.session_manager.get_messages(self.current_conversation_id)
        except Exception as e:
            logger.error("Failed to load messages: %s", e)
            show_error(self, "加载消息失败", str(e))
            return

        if not messages:
            c = theme_colors()
            self._transcript = []
            self.chat_display.set_html(
                f"<div style='color:{c['fg_secondary']}; text-align:center; padding-top:60px; font-size:14px;'>"
                f"开始一个新对话吧</div>"
            )
            self._last_assistant_text = ""
            self.tts_btn.setEnabled(False)
            return

        self._last_assistant_text = ""
        self._transcript = [(m.role.value, m.content) for m in messages]
        self._render_transcript()

    def _clear_current_conversation(self) -> None:
        """清空当前会话消息。"""
        if self.current_conversation_id == 0:
            return
        if not ask_confirm(self, "确认", "确定清空当前会话的消息记录？"):
            return
        try:
            self.chat_service.session_manager.clear_messages(self.current_conversation_id)
            self._render_messages()
        except Exception as e:
            logger.error("Failed to clear conversation: %s", e)
            show_error(self, "清空失败", str(e))

    def _delete_current_conversation(self) -> None:
        """删除当前会话。"""
        if self.current_conversation_id == 0:
            return
        if not ask_confirm(self, "确认", "确定删除当前会话？"):
            return
        try:
            self.chat_service.session_manager.delete_conversation(self.current_conversation_id)
        except Exception as e:
            logger.error("Failed to delete conversation: %s", e)
            show_error(self, "删除失败", str(e))
            return

        row = self.conversation_list.currentRow()
        self.conversation_list.takeItem(row)
        self.current_conversation_id = 0
        self.chat_display.clear()
        self._last_assistant_text = ""
        self.tts_btn.setEnabled(False)
        if self.conversation_list.count() == 0:
            self._create_conversation()
        else:
            self.conversation_list.setCurrentRow(0)
            self._on_conversation_selected(self.conversation_list.currentItem())

    def _show_session_menu(self, position) -> None:
        """会话列表右键菜单。"""
        item = self.conversation_list.itemAt(position)
        if item is None:
            return
        from PyQt6.QtWidgets import QMenu

        context_menu = QMenu(self)
        delete_action = QAction("删除会话", self)
        delete_action.triggered.connect(self._delete_current_conversation)
        context_menu.addAction(delete_action)
        context_menu.exec(self.conversation_list.mapToGlobal(position))

    def _selected_text(self) -> str:
        """当前在对话区选中的文本（无选中返回空串）。"""
        try:
            return self.chat_display.browser.textCursor().selectedText().strip()
        except Exception:
            return ""

    def _show_message_menu(self, position) -> None:
        """消息区域右键菜单（苹果式精简：复制、收藏、加入错题本）。"""
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        has_selection = bool(self._selected_text())

        copy_action = QAction("复制", self)
        copy_action.setEnabled(has_selection)
        copy_action.triggered.connect(self._copy_selected_text)
        menu.addAction(copy_action)

        favorite_action = QAction("收藏", self)
        favorite_action.setEnabled(has_selection and self.note_service is not None)
        favorite_action.triggered.connect(self._favorite_selection)
        menu.addAction(favorite_action)

        errorbook_action = QAction("加入错题本", self)
        errorbook_action.setEnabled(has_selection and self.errorbook_service is not None)
        errorbook_action.triggered.connect(self._add_selection_to_errorbook)
        menu.addAction(errorbook_action)

        if self.tts_service is not None and self.tts_service.is_available():
            menu.addSeparator()
            tts_action = QAction("朗读选中内容", self)
            tts_action.setEnabled(has_selection)
            tts_action.triggered.connect(self._speak_selected_text)
            menu.addAction(tts_action)
        menu.exec(self.chat_display.browser.mapToGlobal(position))

    def _copy_selected_text(self) -> None:
        """复制聊天区域选中的文本。"""
        text = self._selected_text()
        if text:
            set_clipboard_text(text)
            show_success(self, "内容已复制到剪贴板")

    def _speak_selected_text(self) -> None:
        """朗读聊天区域选中的文本。"""
        text = self._selected_text()
        if text and self.tts_service is not None:
            self._speak(text)

    def _favorite_selection(self) -> None:
        """把选中内容收藏为一条笔记（数据库写入放到后台线程）。"""
        text = self._selected_text()
        if not text or self.note_service is None:
            return
        title = f"对话收藏 {datetime.now().strftime('%m-%d %H:%M')}"
        content_html = f"<p>{self._escape_html(text)}</p>"

        def _task() -> int:
            return self.note_service.save_note(
                title=title,
                content_html=content_html,
                content_text=text,
            )

        worker = CallableWorker(_task)
        worker.signals.result.connect(lambda _id: show_success(self, "已收藏到笔记"))
        worker.signals.error.connect(lambda msg: show_error(self, "收藏失败", friendly_error_message(msg)))
        ThreadPool.start_worker(worker)

    def _add_selection_to_errorbook(self) -> None:
        """把选中内容作为错题加入错题本（数据库写入放到后台线程）。"""
        text = self._selected_text()
        if not text or self.errorbook_service is None:
            return

        def _task() -> int:
            return self.errorbook_service.add_error(question=text, analysis="来自 AI 对话收藏")

        worker = CallableWorker(_task)
        worker.signals.result.connect(lambda _id: show_success(self, "已加入错题本"))
        worker.signals.error.connect(
            lambda msg: show_error(self, "加入错题本失败", friendly_error_message(msg))
        )
        ThreadPool.start_worker(worker)

    def _speak_last_reply(self) -> None:
        """朗读最近一条 AI 回答。"""
        if self._last_assistant_text and self.tts_service is not None:
            self._speak(self._last_assistant_text)

    def _speak(self, text: str) -> None:
        """启动 TTS worker 朗读文本。"""
        if not self.tts_service or not self.tts_service.is_available():
            show_warning_toast(self, "语音朗读引擎未初始化")
            return
        worker = TTSWorker(self.tts_service, text)
        worker.signals.error.connect(lambda msg: logger.warning("TTS error: %s", msg))
        ThreadPool.start_worker(worker)

    def _update_send_enabled(self) -> None:
        """发送按钮动态启用：空白输入置灰不可点击，流式输出中保持禁用防重复提交。"""
        busy = getattr(self, "_send_busy", None)
        if busy is not None and getattr(busy, "_active", False):
            return
        try:
            has_text = bool(self.input_edit.toPlainText().strip())
        except Exception:
            has_text = False
        self.send_btn.setEnabled(has_text)

    def _set_status(self, state: str) -> None:
        """更新 AI 状态指示器（控件缺失时静默忽略）。"""
        indicator = getattr(self, "status_indicator", None)
        if indicator is None:
            return
        try:
            indicator.set_state(state)
        except Exception:
            pass

    def _thinking_hint(self) -> str:
        """思考空白期的引导文案，避免等待时界面像"卡死"。"""
        if self.no_direct_answer_check.isChecked() or self._get_teaching_mode() == "socratic":
            return "我来帮你梳理思路，不会直接给出答案。"
        return "我来帮你梳理一下思路，正在准备回答…"

    def _enter_thinking(self) -> None:
        """「收到」短暂停顿后切入「正在思考」。"""
        if self.status_indicator.state() == AssistantStatusIndicator.RECEIVED:
            self._set_status(AssistantStatusIndicator.THINKING)

    def _send_message(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            # 苹果式：无效提交给极轻微抖动提醒，而不是静默无反应
            shake_widget(self.input_edit)
            return
        busy = getattr(self, "_send_busy", None)
        if busy is not None and getattr(busy, "_active", False):
            return  # 流式输出中，防止重复提交
        if self.current_conversation_id == 0:
            self._create_conversation()
            if self.current_conversation_id == 0:
                return

        self._last_sent_text = text
        self._hide_retry_bar()
        self._transcript.append(("user", text))
        self.input_edit.clear()
        self._begin_reply(text)

    def _begin_reply(self, text: str) -> None:
        """进入回答阶段：占位引导文字 + 状态指示 + 后台流式请求。"""
        # 思考空白期先输出引导文字，AI 不会长时间"黑屏"等待
        self._placeholder_active = True
        self._transcript.append(("assistant", self._thinking_hint()))
        self._render_transcript()
        self._send_busy = ButtonBusy(self.send_btn, "思考中...").start()
        self._set_status(AssistantStatusIndicator.RECEIVED)
        QTimer.singleShot(320, self._enter_thinking)

        use_rag = self.rag_check.isChecked()
        use_context = self.context_check.isChecked()
        teaching_mode = self._get_teaching_mode()
        no_direct_answer = self.no_direct_answer_check.isChecked()
        explanation_mode = self._get_explanation_mode()
        try:
            gen = self.chat_service.send_message_stream(
                self.current_conversation_id,
                text,
                use_rag=use_rag,
                use_context=use_context,
                teaching_mode=teaching_mode,
                no_direct_answer=no_direct_answer,
                explanation_mode=explanation_mode,
            )
        except Exception as e:
            # 同步抛错（如模型未配置）不能冒泡到 Qt 槽函数，否则会直接崩溃退出
            logger.error("Chat request failed: %s", e)
            busy = getattr(self, "_send_busy", None)
            if busy is not None:
                busy.restore()
            self._update_send_enabled()
            self._on_error(str(e))
            return
        worker = ChatStreamWorker(gen)
        worker.signals.chunk.connect(self._on_stream_chunk)
        worker.signals.done.connect(self._on_stream_done)
        worker.signals.error.connect(self._on_stream_error)
        worker.signals.finished.connect(self._on_finished)
        self._stream_started = False
        self._chunk_buffer = ""
        ThreadPool.start_worker(worker)

    def _on_stream_chunk(self, chunk: str) -> None:
        """流式增量：先缓冲，再由定时器按 ~80ms 节奏合并渲染（柔和打字机）。"""
        if not self._stream_started:
            self._stream_started = True
            if self._placeholder_active:
                # 首个真实片段到达：引导占位文字让位给正式回答
                self._placeholder_active = False
                self._transcript[-1] = ("assistant", "")
            else:
                self._transcript.append(("assistant", ""))
        self._set_status(AssistantStatusIndicator.STREAMING)
        self._chunk_buffer += chunk
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _flush_stream_buffer(self) -> None:
        """把缓冲的增量写入最后一条气泡并重绘。"""
        if not self._chunk_buffer:
            return
        buffer, self._chunk_buffer = self._chunk_buffer, ""
        if self._transcript and self._transcript[-1][0] == "assistant":
            self._transcript[-1] = ("assistant", self._transcript[-1][1] + buffer)
        self._render_transcript(live=True)

    def _stop_stream_timer(self) -> None:
        """停止节流定时器并冲刷剩余内容。"""
        try:
            if self._stream_flush_timer.isActive():
                self._stream_flush_timer.stop()
            self._flush_stream_buffer()
        except Exception:
            pass

    def _on_stream_done(self, full: str, duration_ms: int) -> None:
        self._stop_stream_timer()
        had_placeholder = self._placeholder_active
        self._stream_started = False
        self._placeholder_active = False
        if self._transcript and self._transcript[-1][0] == "assistant":
            if full:
                self._transcript[-1] = ("assistant", full)
            elif not had_placeholder:
                self._transcript.pop()  # 空回答不留空气泡
        self._render_transcript()
        self._last_assistant_text = full
        self.tts_btn.setEnabled(True)
        self._set_status(AssistantStatusIndicator.IDLE)
        if duration_ms:
            self._append_duration({"duration_ms": duration_ms})

    def _on_stream_error(self, message: str) -> None:
        self._stop_stream_timer()
        self._stream_started = False
        self._placeholder_active = False
        self._set_status(AssistantStatusIndicator.IDLE)
        self._on_error(message)

    def _retry_last_message(self) -> None:
        """重试上一次失败的提问：清掉失败气泡后重新请求，不重复用户气泡。"""
        text = self._last_sent_text
        if not text:
            return
        busy = getattr(self, "_send_busy", None)
        if busy is not None and getattr(busy, "_active", False):
            return
        self._hide_retry_bar()
        # 清掉上一次的失败/占位气泡
        while self._transcript and self._transcript[-1][0] == "assistant":
            self._transcript.pop()
        self._begin_reply(text)

    def _hide_retry_bar(self) -> None:
        try:
            self.retry_bar.setVisible(False)
        except Exception:
            pass

    def _show_retry_bar(self, message: str) -> None:
        try:
            self.retry_label.setText(message)
            self.retry_bar.setVisible(True)
        except Exception:
            pass

    def _render_transcript(self, live: bool = False) -> None:
        """按 transcript 整段重绘气泡；live 时仅最后一条实时重渲染。"""
        try:
            self._clear_hover_highlight()
            parts = []
            for index, (role, content) in enumerate(self._transcript):
                if live and index == len(self._transcript) - 1:
                    parts.append(self._bubble_html(role, content))
                    continue
                cache = getattr(self, "_bubble_cache", None)
                key = (role, content)
                if cache is None:
                    cache = {}
                    self._bubble_cache = cache
                html = cache.get(key)
                if html is None:
                    html = self._bubble_html(role, content)
                    if len(cache) > 400:
                        cache.clear()
                    cache[key] = html
                parts.append(html)
            if parts:
                self.chat_display.set_html("".join(parts))
                if live:
                    sb = self.chat_display.browser.verticalScrollBar()
                    sb.setValue(sb.maximum())
                else:
                    self._smooth_scroll_to_bottom()
        except Exception as e:
            logger.warning("Failed to render transcript: %s", e)

    def _smooth_scroll_to_bottom(self) -> None:
        """新消息从下方平滑滚入（时长受全局动画策略限制，低配直接跳转）。"""
        try:
            sb = self.chat_display.browser.verticalScrollBar()
        except Exception:
            return
        target = sb.maximum()
        if target <= sb.value():
            return
        duration = policy().duration(160)
        if duration <= 0:
            sb.setValue(target)
            return
        try:
            previous = getattr(self, "_scroll_anim", None)
            if previous is not None:
                previous.stop()
            anim = QPropertyAnimation(sb, b"value", self)
            anim.setDuration(duration)
            anim.setStartValue(sb.value())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            self._scroll_anim = anim
        except Exception:
            sb.setValue(target)

    # ------------------------------------------------------------------
    # 气泡悬浮反馈（轻量：仅在光标跨行时重绘一次，不逐帧刷新）
    # ------------------------------------------------------------------
    def _install_hover_feedback(self) -> None:
        try:
            viewport = self.chat_display.browser.viewport()
            viewport.setMouseTracking(True)
            viewport.installEventFilter(self)
        except Exception:
            return
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(60)
        self._hover_timer.timeout.connect(self._apply_hover_highlight)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        try:
            if obj is self.chat_display.browser.viewport():
                etype = event.type()
                if etype == QEvent.Type.MouseMove:
                    self._hover_pos = event.position().toPoint()
                    if not self._hover_timer.isActive():
                        self._hover_timer.start()
                elif etype == QEvent.Type.Leave:
                    self._hover_pos = None
                    self._clear_hover_highlight()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _apply_hover_highlight(self) -> None:
        """鼠标所在消息的柔和高亮，给出即时悬浮反馈。"""
        if self._hover_pos is None or self._stream_started:
            return
        browser = self.chat_display.browser
        try:
            cursor = browser.cursorForPosition(self._hover_pos)
            block = cursor.blockNumber()
            if block == self._hover_block:
                return
            self._hover_block = block
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(
                QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
            )
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            color = QColor(theme_colors()["accent"])
            color.setAlphaF(0.07)
            fmt.setBackground(color)
            selection.format = fmt
            browser.setExtraSelections([selection])
        except Exception:
            pass

    def _clear_hover_highlight(self) -> None:
        self._hover_block = -1
        try:
            self.chat_display.browser.setExtraSelections([])
        except Exception:
            pass

    def _bubble_html(self, role: str, content: str) -> str:
        """单条消息的气泡 HTML（供整段重绘与历史渲染复用）。"""
        c = theme_colors()
        if role == "user":
            safe_content = self.chat_display.render_markdown_body(content)
            return (
                f"<div style='margin:10px 4px; text-align:right;'>"
                f"<span style='display:inline-block; max-width:72%; background-color:{c['accent']}; "
                f"color:#FFFFFF; padding:10px 14px; border-radius:14px 14px 4px 14px; "
                f"text-align:left; font-size:13px;'>"
                f"<b>你</b><br/>{safe_content}</span></div>"
            )
        if role == "assistant":
            rendered = self.chat_display.render_markdown_body(content)
            return (
                f"<div style='margin:10px 4px; text-align:left;'>"
                f"<span style='display:inline-block; max-width:78%; background-color:{c['accent_light']}; "
                f"color:inherit; padding:10px 14px; border-radius:14px 14px 14px 4px; "
                f"text-align:left; font-size:13px; border:1px solid {c['accent_light']};'>"
                f"<b style='color:{c['accent']};'>AI</b><br/>{rendered}</span></div>"
            )
        safe_content = self._escape_html(content)
        return f"<b>{role}：</b> {safe_content}<br/>"

    def _on_reply(self, content: str) -> None:
        self._last_assistant_text = content
        self._append_message("assistant", content)
        self.tts_btn.setEnabled(True)
        if self._last_meta:
            self._append_duration(self._last_meta)
            self._last_meta = None

    def _on_chat_meta(self, meta: dict) -> None:
        duration = meta.get("duration_ms")
        if duration is not None:
            self._last_meta = meta

    def _append_duration(self, meta: dict) -> None:
        """在最新消息下方追加推理耗时。"""
        duration = meta.get("duration_ms")
        if duration is None:
            return
        c = theme_colors()
        label = (
            f"<div style='margin:2px 8px 10px 8px; text-align:left;'>"
            f"<span style='color:{c['fg_secondary']}; font-size:11px;'>"
            f"推理耗时：{duration} ms</span></div>"
        )
        self.chat_display.append_html(label)

    def _on_error(self, error_message: str) -> None:
        """友好化错误提示：不向用户抛原始报错，改为自然语言 + 重试入口。"""
        logger.error("Chat error: %s", error_message)
        friendly = friendly_error_message(error_message)
        self._transcript.append(("assistant", f"（{friendly}）"))
        self._render_transcript()
        show_warning_toast(self, friendly)
        self._show_retry_bar(f"{friendly}。可以重试，或在设置中切换模型。")

    def _on_finished(self) -> None:
        busy = getattr(self, "_send_busy", None)
        if busy is not None:
            busy.restore()
        self._update_send_enabled()

    @staticmethod
    def _escape_html(text: str) -> str:
        """将文本转义为 HTML 安全内容。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )

    def _append_message(self, role: str, content: str) -> None:
        """以气泡样式追加一条消息；AI 回复支持 Markdown 与 LaTeX 渲染。"""
        try:
            c = theme_colors()
            # 清除空状态提示
            if self.chat_display.to_plain_text().startswith("开始一个新对话吧"):
                self.chat_display.clear()

            if role == "user":
                safe_content = self.chat_display.render_markdown_body(content)
                bubble = (
                    f"<div style='margin:10px 4px; text-align:right;'>"
                    f"<span style='display:inline-block; max-width:72%; background-color:{c['accent']}; "
                    f"color:#FFFFFF; padding:10px 14px; border-radius:14px 14px 4px 14px; "
                    f"text-align:left; font-size:13px;'>"
                    f"<b>你</b><br/>{safe_content}</span></div>"
                )
            elif role == "assistant":
                rendered = self.chat_display.render_markdown_body(content)
                bubble = (
                    f"<div style='margin:10px 4px; text-align:left;'>"
                    f"<span style='display:inline-block; max-width:78%; background-color:{c['accent_light']}; "
                    f"color:inherit; padding:10px 14px; border-radius:14px 14px 14px 4px; "
                    f"text-align:left; font-size:13px; border:1px solid {c['accent_light']};'>"
                    f"<b style='color:{c['accent']};'>AI</b><br/>{rendered}</span></div>"
                )
            else:
                safe_content = self._escape_html(content)
                bubble = f"<b>{role}：</b> {safe_content}<br/>"

            self.chat_display.append_html(bubble)
        except Exception as e:
            logger.warning("Failed to append chat message bubble: %s", e)

    def locate_item(self, item_type: str, item_id: int) -> None:
        """根据全局搜索结果定位到指定对话（仅支持 conversation 类型）。"""
        if item_type != "conversation":
            return
        for i in range(self.conversation_list.count()):
            item = self.conversation_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == item_id:
                self.conversation_list.setCurrentItem(item)
                self._on_conversation_selected(item)
                return
