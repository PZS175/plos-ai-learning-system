"""错题本面板。

支持录入错题、OCR/手动导入、AI 自动考点打标、标签筛选、编辑答案解析、
标记掌握度、AI 生成变式题、加入闪卡、TTS 朗读、文本批注高亮、思维导图、导入导出。
左侧为卡片式列表，右侧为玻璃拟态编辑区。
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services import (
    ErrorBookService,
    ErrorPaperService,
    FlashcardService,
    MindMapService,
    NoteAnnotationService,
    TTSService,
)
from ..utils.logger import get_logger
from .markdown_browser import MarkdownBrowser
from .math_text import MathLabel, MathTextBrowser, attach_live_preview
from .ui_utils import (
    create_section_title,
    apply_form_container_style,
    apply_glass_style,
    ask_confirm,
    create_badge_label,
    create_empty_state_widget,
    set_danger_button_style,
    set_primary_button_style,
    show_error,
    show_info,
    show_success,
    show_toast,
    show_warning,
)
from .dialogs import AnnotationDialog, MindMapViewerDialog
from .workers import (
    AutoTagWorker,
    MindMapWorker,
    ThreadPool,
    TTSWorker,
    VariantQuestionWorker,
)

logger = get_logger("ui.errorbook_panel")


_MASTERY_LABELS = ["未掌握", "基本掌握", "已掌握"]
_MASTERY_COLORS = ["#EF4444", "#F59E0B", "#10B981"]


_ERROR_STATUS = {
    "mastered": ("已掌握", "#10B981"),
    "review": ("待复习", "#6366F1"),
    "weak": ("薄弱考点", "#EF4444"),
}


class _ErrorCard(QWidget):
    """左侧错题卡片，带状态小图标。"""

    def __init__(
        self,
        question: str,
        tags: str,
        mastery_level: int,
        subject: str = "",
        difficulty: int = 1,
        question_type: str = "",
    ):
        super().__init__()
        # 数据库字段可能为 NULL，统一兜底为空字符串，避免切片/成员判断抛异常
        question = question or ""
        tags = tags or ""
        subject = subject or ""
        question_type = question_type or ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 卡片标题就是题干，可能带公式标记，用混排标签渲染
        title = MathLabel(question[:60] + ("..." if len(question) > 60 else ""))
        title.setWordWrap(False)  # 保持卡片原有的单行高度
        title.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        bottom = QHBoxLayout()
        badge_text = subject or (tags.split(",")[0].strip() if "," in tags else tags.strip())
        if badge_text:
            bottom.addWidget(create_badge_label(badge_text, "#8B5CF6"))

        if question_type:
            bottom.addWidget(create_badge_label(question_type, "#06B6D4"))

        status = self._resolve_status(mastery_level, difficulty)
        status_text, status_color = _ERROR_STATUS[status]
        bottom.addWidget(create_badge_label(status_text, status_color))
        bottom.addStretch()

        mastery_text = _MASTERY_LABELS[mastery_level] if 0 <= mastery_level < 3 else "未知"
        mastery_color = _MASTERY_COLORS[mastery_level] if 0 <= mastery_level < 3 else "#8A93A8"
        bottom.addWidget(create_badge_label(mastery_text, mastery_color))
        layout.addLayout(bottom)

    @staticmethod
    def _resolve_status(mastery_level: int, difficulty: int) -> str:
        """根据掌握度和难度解析状态标签。"""
        if difficulty >= 4 or mastery_level == 0:
            return "weak"
        if mastery_level == 2:
            return "mastered"
        return "review"


class _SubjectiveGradesDialog(QDialog):
    """查看某条错题的主观题批改记录弹窗。"""

    def __init__(
        self,
        errorbook_service: ErrorBookService,
        error_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.errorbook_service = errorbook_service
        self.error_id = error_id
        self.setWindowTitle("主观题批改记录")
        self.resize(720, 560)
        self._build_ui()
        self._load_grades()

    def _build_ui(self) -> None:
        self.setProperty("glass", True)
        apply_glass_style(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = create_section_title("主观题批改记录")
        layout.addWidget(title)

        self.stack = QWidget()
        self.stack_layout = QVBoxLayout(self.stack)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)
        self.stack_layout.setSpacing(12)
        layout.addWidget(self.stack, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load_grades(self) -> None:
        # 清空已有内容
        while self.stack_layout.count():
            item = self.stack_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            grades = self.errorbook_service.list_subjective_grades(self.error_id)
        except Exception as e:
            logger.warning("Failed to load subjective grades: %s", e)
            grades = []

        if not grades:
            empty = create_empty_state_widget("暂无主观题批改记录")
            self.stack_layout.addWidget(empty)
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        for idx, grade in enumerate(grades, start=1):
            card = self._build_grade_card(grade, idx)
            content_layout.addWidget(card)
        content_layout.addStretch()
        scroll.setWidget(content)
        self.stack_layout.addWidget(scroll)

    @staticmethod
    def _format_list(items: list, color: str = "#3B82F6") -> str:
        if not items:
            return '<span style="color:#8A93A8;">无</span>'
        parts = []
        for item in items:
            parts.append(f'<li style="margin:4px 0;">{item}</li>')
        return f'<ul style="margin:4px 0;padding-left:18px;color:{color};">{"".join(parts)}</ul>'

    def _build_grade_card(self, grade: dict, index: int) -> QWidget:
        card = QWidget()
        card.setProperty("glass", True)
        apply_glass_style(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        score = float(grade.get("score", 0) or 0)
        total = float(grade.get("total_score", 100) or 100)
        score_color = "#10B981" if score >= 80 else ("#F59E0B" if score >= 60 else "#EF4444")

        header = QLabel(
            f"第 {index} 次批改 &nbsp;&nbsp;"
            f"<span style='font-size:20px;font-weight:bold;color:{score_color};'>{score:.0f}</span>"
            f"<span style='color:#8A93A8;'> / {total:.0f}</span>"
        )
        header.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(header)

        browser = MarkdownBrowser()
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet("background:transparent;border:none;")
        html_parts = []

        user_answer = str(grade.get("user_answer", "") or "").strip()
        if user_answer:
            html_parts.append(
                "<b>学生作答：</b>"
                f"<div style='margin:4px 0;padding:6px;background:rgba(128,128,128,0.08);border-radius:6px;'>"
                f"{html.escape(user_answer)}</div>"
            )

        feedback = str(grade.get("feedback", "") or "").strip()
        if feedback:
            html_parts.append(f"<b>总体点评：</b><div style='margin:4px 0;'>{html.escape(feedback)}</div>")

        html_parts.append("<b>得分点：</b>" + self._format_list(grade.get("scoring_points", []), "#10B981"))
        html_parts.append("<b>失分点：</b>" + self._format_list(grade.get("lost_points", []), "#EF4444"))
        html_parts.append("<b>错误原因：</b>" + self._format_list(grade.get("error_reasons", []), "#F59E0B"))

        improvement = str(grade.get("improvement", "") or "").strip()
        if improvement:
            html_parts.append(
                f"<b>改进建议：</b><div style='margin:4px 0;color:#3B82F6;'>{html.escape(improvement)}</div>"
            )

        created = str(grade.get("created_at", "") or "").strip()
        if created:
            html_parts.append(
                f"<div style='margin-top:8px;color:#8A93A8;font-size:11px;'>批改时间：{html.escape(created)}</div>"
            )

        browser.set_html("".join(html_parts))
        layout.addWidget(browser)
        return card


class ErrorBookPanel(QWidget):
    """错题本界面。"""

    def __init__(
        self,
        errorbook_service: ErrorBookService,
        flashcard_service: Optional[FlashcardService] = None,
        note_annotation_service: Optional[NoteAnnotationService] = None,
        mindmap_service: Optional[MindMapService] = None,
        tts_service: Optional[TTSService] = None,
        error_paper_service: Optional[ErrorPaperService] = None,
    ):
        super().__init__()
        self.errorbook_service = errorbook_service
        self.flashcard_service = flashcard_service
        self.note_annotation_service = note_annotation_service
        self.mindmap_service = mindmap_service
        self.tts_service = tts_service
        self.error_paper_service = error_paper_service or ErrorPaperService(
            db=errorbook_service.db, errorbook_service=errorbook_service
        )
        self.selected_id: int | None = None
        # AI 按钮单飞占用（同一时间只跑一个 AI 任务，防止连点并发请求）
        self._ai_busy = False
        self._ai_button: Optional[QPushButton] = None
        # 导出试卷弹窗引用：后台任务回调期间必须保持存活
        self._paper_dialog = None
        self._build_ui()
        self._refresh_list()
        self._init_shortcuts()

    def _init_shortcuts(self) -> None:
        """Ctrl+S 保存当前错题编辑。"""
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        save_shortcut.activated.connect(self._save_error)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        outer_scroll = QScrollArea()
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        main_layout.addWidget(outer_scroll)

        container = QWidget()
        apply_form_container_style(container)
        outer_scroll.setWidget(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：卡片列表与筛选
        left = QWidget()
        left.setProperty("glass", True)
        apply_glass_style(left)
        left.setMinimumWidth(260)
        left.setMaximumWidth(340)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        left_title = create_section_title("错题列表")
        left_layout.addWidget(left_title)

        # 筛选区
        filter_layout = QFormLayout()
        self.filter_subject = QComboBox()
        self.filter_subject.setEditable(True)
        self.filter_subject.setPlaceholderText("全部科目")
        self.filter_subject.currentTextChanged.connect(self._refresh_list)
        filter_layout.addRow("科目：", self.filter_subject)

        self.filter_tag = QLineEdit()
        self.filter_tag.setPlaceholderText("标签/知识点关键字")
        self.filter_tag.returnPressed.connect(self._refresh_list)
        filter_layout.addRow("标签：", self.filter_tag)

        self.filter_difficulty = QComboBox()
        self.filter_difficulty.addItems(["全部难度", "1", "2", "3", "4", "5"])
        self.filter_difficulty.currentIndexChanged.connect(self._refresh_list)
        filter_layout.addRow("难度：", self.filter_difficulty)

        self.filter_mastery = QComboBox()
        self.filter_mastery.addItems(["全部掌握度", "未掌握", "基本掌握", "已掌握"])
        self.filter_mastery.currentIndexChanged.connect(self._refresh_list)
        filter_layout.addRow("掌握度：", self.filter_mastery)

        left_layout.addLayout(filter_layout)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_item_selected)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_item_menu)
        left_layout.addWidget(self.list_widget)

        self.empty_state = create_empty_state_widget("暂无错题，点击新增按钮添加")
        left_layout.addWidget(self.empty_state)

        list_btn_layout = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_list)
        list_btn_layout.addWidget(btn_refresh)

        btn_import = QPushButton("导入")
        btn_import.clicked.connect(self._import_errors)
        list_btn_layout.addWidget(btn_import)

        btn_export = QPushButton("导出")
        btn_export.clicked.connect(self._export_errors)
        list_btn_layout.addWidget(btn_export)
        left_layout.addLayout(list_btn_layout)

        splitter.addWidget(left)

        # 右侧：编辑（整体套 QScrollArea，防止表单字段被截断）
        right = QWidget()
        right.setProperty("glass", True)
        apply_glass_style(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        right_title = create_section_title("错题编辑")
        right_layout.addWidget(right_title)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_layout.addWidget(right_scroll, 1)

        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.question_edit = QTextEdit()
        self.question_edit.setPlaceholderText("题目内容...")
        self.question_edit.setMinimumHeight(80)
        self.question_edit.setMaximumHeight(160)
        form.addRow("题目：", self.question_edit)

        # 题目 Markdown + LaTeX 实时预览
        self.question_preview = MathTextBrowser(self)
        self.question_preview.setMaximumHeight(120)
        self.question_preview.set_placeholder_text("输入 \\(公式\\) 或 \\[公式\\] 可在此预览")
        self._question_preview_connector = attach_live_preview(
            self.question_edit, self.question_preview
        )
        form.addRow("题目预览：", self.question_preview)

        self.answer_edit = QTextEdit()
        self.answer_edit.setPlaceholderText("答案...")
        self.answer_edit.setMinimumHeight(80)
        self.answer_edit.setMaximumHeight(140)
        form.addRow("答案：", self.answer_edit)

        self.analysis_edit = QTextEdit()
        self.analysis_edit.setPlaceholderText("解析...")
        self.analysis_edit.setMinimumHeight(120)
        self.analysis_edit.setMaximumHeight(200)
        self.analysis_edit.textChanged.connect(self._update_analysis_preview)
        form.addRow("解析：", self.analysis_edit)

        # 解析 Markdown + LaTeX 实时预览
        self.analysis_preview = MarkdownBrowser()
        self.analysis_preview.setMaximumHeight(140)
        self.analysis_preview.setVisible(False)
        form.addRow("解析预览：", self.analysis_preview)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("如：数学")
        form.addRow("科目：", self.subject_edit)

        self.chapter_edit = QLineEdit()
        self.chapter_edit.setPlaceholderText("如：函数与导数")
        form.addRow("章节：", self.chapter_edit)

        self.knowledge_point_edit = QLineEdit()
        self.knowledge_point_edit.setPlaceholderText("如：导数的几何意义")
        form.addRow("主要知识点：", self.knowledge_point_edit)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用逗号分隔，如：微积分,导数")
        form.addRow("标签：", self.tags_edit)

        self.knowledge_points_edit = QLineEdit()
        self.knowledge_points_edit.setPlaceholderText("用逗号分隔，如：导数,极值,函数单调性")
        form.addRow("知识点标签：", self.knowledge_points_edit)

        self.question_type_combo = QComboBox()
        self.question_type_combo.addItems(["", "选择题", "填空题", "解答题"])
        form.addRow("题型：", self.question_type_combo)

        difficulty_layout = QHBoxLayout()
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems(["1", "2", "3", "4", "5"])
        difficulty_layout.addWidget(self.difficulty_combo)
        difficulty_layout.addStretch()
        form.addRow("难度：", difficulty_layout)

        self.mastery_combo = QComboBox()
        self.mastery_combo.addItems(_MASTERY_LABELS)
        form.addRow("掌握度：", self.mastery_combo)

        form_layout.addLayout(form)

        # 批注列表
        self.annotation_browser = MarkdownBrowser()
        self.annotation_browser.setMinimumHeight(80)
        self.annotation_browser.setMaximumHeight(140)
        self.annotation_browser.set_placeholder_text("选中题目批注将显示在这里...")
        form_layout.addWidget(self.annotation_browser)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("新增")
        btn_add.clicked.connect(self._add_error)
        btn_layout.addWidget(btn_add)

        btn_auto_tag = QPushButton("AI 自动打标")
        btn_auto_tag.setToolTip("调用本地模型识别科目、章节、知识点、难度")
        btn_auto_tag.clicked.connect(self._auto_tag_current)
        btn_layout.addWidget(btn_auto_tag)

        btn_save = QPushButton("保存")
        set_primary_button_style(btn_save)
        btn_save.clicked.connect(self._save_error)
        btn_layout.addWidget(btn_save)

        btn_del = QPushButton("删除")
        set_danger_button_style(btn_del)
        btn_del.clicked.connect(self._delete_error)
        btn_layout.addWidget(btn_del)

        # 一键格式化：三个文本框一起清理乱码、修复数学符号、补公式标记
        from .math_text import make_format_button

        btn_format = make_format_button(
            [self.question_edit, self.answer_edit, self.analysis_edit], self
        )
        btn_layout.addWidget(btn_format)

        self.btn_favorite = QPushButton("☆ 收藏")
        self.btn_favorite.setToolTip("收藏/取消收藏当前错题")
        self.btn_favorite.clicked.connect(self._toggle_favorite)
        btn_layout.addWidget(self.btn_favorite)

        self.btn_subjective_grades = QPushButton("查看批改记录")
        self.btn_subjective_grades.setToolTip("查看该错题的主观题批改记录")
        self.btn_subjective_grades.clicked.connect(self._show_subjective_grades)
        btn_layout.addWidget(self.btn_subjective_grades)
        form_layout.addLayout(btn_layout)

        self.auto_tag_time_label = QLabel("")
        self.auto_tag_time_label.setStyleSheet("color: #8A93A8; font-size: 11px;")
        form_layout.addWidget(self.auto_tag_time_label)

        # 扩展操作
        ext_layout = QHBoxLayout()
        btn_variant = QPushButton("AI 生成变式题")
        set_primary_button_style(btn_variant)
        btn_variant.clicked.connect(self._generate_variant)
        ext_layout.addWidget(btn_variant)

        btn_mindmap = QPushButton("生成思维导图")
        btn_mindmap.clicked.connect(self._generate_mindmap)
        ext_layout.addWidget(btn_mindmap)

        btn_flashcard = QPushButton("加入闪卡")
        btn_flashcard.clicked.connect(self._add_to_flashcard)
        ext_layout.addWidget(btn_flashcard)

        btn_tts = QPushButton("朗读题目")
        btn_tts.clicked.connect(self._speak_question)
        ext_layout.addWidget(btn_tts)

        btn_annotate = QPushButton("添加批注")
        btn_annotate.setToolTip("对题目/答案/解析中选中的文本添加高亮批注")
        btn_annotate.clicked.connect(self._add_annotation_from_selection)
        ext_layout.addWidget(btn_annotate)

        btn_paper = QPushButton("导出试卷")
        btn_paper.setToolTip("把错题导出为 Word / PDF 试卷样式文档，可直接打印")
        btn_paper.clicked.connect(self._export_paper)
        ext_layout.addWidget(btn_paper)
        ext_layout.addStretch()
        form_layout.addLayout(ext_layout)

        # 变式题展示区（支持 Markdown + LaTeX 公式渲染）
        self.variant_time_label = QLabel("")
        self.variant_time_label.setStyleSheet("color: #8A93A8; font-size: 11px;")
        form_layout.addWidget(self.variant_time_label)

        self.variant_browser = MarkdownBrowser()
        self.variant_browser.setMinimumHeight(120)
        self.variant_browser.setMaximumHeight(240)
        self.variant_browser.set_placeholder_text("AI 生成的变式题将显示在这里...")
        form_layout.addWidget(self.variant_browser)

        right_scroll.setWidget(form_container)

        splitter.addWidget(right)
        splitter.setSizes([320, 880])

        layout.addWidget(splitter)

    def refresh_data(self) -> None:
        """刷新数据（用户切换后调用）。"""
        self._refresh_filter_options()
        self._refresh_list()

    def _refresh_filter_options(self) -> None:
        """刷新科目下拉选项。"""
        try:
            self.filter_subject.clear()
            self.filter_subject.addItem("全部科目")
            subjects = self.errorbook_service.list_subjects()
            self.filter_subject.addItems(subjects)
        except Exception as e:
            logger.warning("Failed to refresh filter subjects: %s", e)

    def _refresh_list(self) -> None:
        try:
            subject = self.filter_subject.currentText()
            if subject == "全部科目":
                subject = None
            tag = self.filter_tag.text().strip() or None
            difficulty = self.filter_difficulty.currentIndex()
            difficulty = difficulty if difficulty > 0 else None
            mastery = self.filter_mastery.currentIndex()
            mastery = mastery - 1 if mastery > 0 else None

            errors = self.errorbook_service.list_errors(
                subject=subject,
                tag=tag,
                difficulty=difficulty,
                mastery_level=mastery,
                limit=200,
            )
        except Exception as e:
            logger.error("Failed to list errors: %s", e)
            show_error(self, "刷新失败", str(e))
            return

        self.list_widget.clear()
        has_errors = len(errors) > 0
        self.list_widget.setVisible(has_errors)
        self.empty_state.setVisible(not has_errors)

        for row in errors:
            try:
                error_id = int(row["id"])
                question = row.get("question") or ""
                tags = row.get("knowledge_tags") or ""
                mastery = int(row.get("mastery_level") or 0)
                subject = row.get("subject") or ""
                difficulty = int(row.get("difficulty") or 1)
                question_type = row.get("question_type") or ""

                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, error_id)
                # 注意：QListWidget.iconSize() 默认是无效值 QSize(-1,-1)，
                # 直接用作 sizeHint 会导致行高塌缩为 ~12px、卡片被裁切
                item.setSizeHint(QSize(0, 72))
                self.list_widget.addItem(item)

                card = _ErrorCard(question, tags, mastery, subject, difficulty, question_type)
                card.setFixedHeight(72)
                self.list_widget.setItemWidget(item, card)
            except Exception as e:
                logger.warning("Failed to build error card: %s", e)

    def _show_item_menu(self, position) -> None:
        """错题列表右键菜单。"""
        item = self.list_widget.itemAt(position)
        if item is None:
            return
        error_id = int(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)

        tag_action = QAction("自动打标", self)
        tag_action.triggered.connect(lambda: self._auto_tag_error(error_id))
        menu.addAction(tag_action)

        variant_action = QAction("生成变式题", self)
        variant_action.triggered.connect(lambda: self._generate_variant_for(error_id))
        menu.addAction(variant_action)

        mindmap_action = QAction("生成思维导图", self)
        mindmap_action.triggered.connect(lambda: self._generate_mindmap_for(error_id))
        menu.addAction(mindmap_action)

        flashcard_action = QAction("加入闪卡", self)
        flashcard_action.triggered.connect(lambda: self._add_error_to_flashcard(error_id))
        menu.addAction(flashcard_action)

        menu.exec(self.list_widget.mapToGlobal(position))

    def _on_item_selected(self, item: QListWidgetItem) -> None:
        try:
            error_id = int(item.data(Qt.ItemDataRole.UserRole))
        except (ValueError, TypeError):
            return
        error = self.errorbook_service.get_error(error_id)
        if error is None:
            return
        self.selected_id = error_id
        self.question_edit.setPlainText(error.get("question") or "")
        self.answer_edit.setPlainText(error.get("answer") or "")
        self.analysis_edit.setPlainText(error.get("analysis") or "")
        self.subject_edit.setText(error.get("subject") or "")
        self.chapter_edit.setText(error.get("chapter") or "")
        self.knowledge_point_edit.setText(error.get("knowledge_point") or "")
        self.tags_edit.setText(error.get("knowledge_tags") or "")

        try:
            kp_list = json.loads(error.get("knowledge_points", "[]") or "[]")
            if not isinstance(kp_list, list):
                kp_list = []
        except Exception:
            kp_list = []
        self.knowledge_points_edit.setText(", ".join(str(k) for k in kp_list))

        qt_index = self.question_type_combo.findText(error.get("question_type") or "")
        self.question_type_combo.setCurrentIndex(max(0, qt_index))

        self.difficulty_combo.setCurrentIndex(max(0, min(4, int(error.get("difficulty") or 1) - 1)))
        self.mastery_combo.setCurrentIndex(max(0, min(2, int(error.get("mastery_level") or 0))))
        self.variant_browser.clear()
        self._load_annotations(error_id)
        self._update_favorite_button(error.get("is_favorite", 0))

    def _add_error(self) -> None:
        self.selected_id = None
        self.question_edit.clear()
        self.answer_edit.clear()
        self.analysis_edit.clear()
        self.subject_edit.clear()
        self.chapter_edit.clear()
        self.knowledge_point_edit.clear()
        self.tags_edit.clear()
        self.knowledge_points_edit.clear()
        self.question_type_combo.setCurrentIndex(0)
        self.difficulty_combo.setCurrentIndex(0)
        self.mastery_combo.setCurrentIndex(0)
        self.variant_browser.clear()
        self.annotation_browser.clear()
        self._update_analysis_preview()
        self._update_favorite_button(0)

    def _update_favorite_button(self, is_favorite: int) -> None:
        """根据收藏状态更新按钮文本。"""
        if is_favorite:
            self.btn_favorite.setText("★ 已收藏")
        else:
            self.btn_favorite.setText("☆ 收藏")

    def _toggle_favorite(self) -> None:
        """切换当前错题收藏状态。"""
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        try:
            new_state = self.errorbook_service.toggle_favorite(self.selected_id)
            self._update_favorite_button(1 if new_state else 0)
            show_success(self, "已收藏" if new_state else "已取消收藏")
        except Exception as e:
            logger.error("Toggle favorite failed: %s", e)
            show_error(self, "操作失败", str(e))

    def _show_subjective_grades(self) -> None:
        """打开当前错题的主观题批改记录弹窗。"""
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        dialog = _SubjectiveGradesDialog(
            self.errorbook_service, self.selected_id, parent=self
        )
        dialog.exec()

    def _update_analysis_preview(self) -> None:
        """根据解析输入实时渲染 Markdown + LaTeX 预览。"""
        text = self.analysis_edit.toPlainText().strip()
        self.analysis_preview.setVisible(bool(text))
        if text:
            self.analysis_preview.set_markdown(text)

    def _save_error(self) -> None:
        question = self.question_edit.toPlainText().strip()
        if not question:
            show_warning(self, "提示", "题目内容不能为空。")
            return

        try:
            kp_text = self.knowledge_points_edit.text().strip()
            knowledge_points = [k.strip() for k in kp_text.split(",") if k.strip()]

            data = {
                "question": question,
                "answer": self.answer_edit.toPlainText().strip(),
                "analysis": self.analysis_edit.toPlainText().strip(),
                "knowledge_tags": self.tags_edit.text().strip(),
                "knowledge_points": knowledge_points,
                "question_type": self.question_type_combo.currentText(),
                "mastery_level": self.mastery_combo.currentIndex(),
                "subject": self.subject_edit.text().strip(),
                "chapter": self.chapter_edit.text().strip(),
                "knowledge_point": self.knowledge_point_edit.text().strip(),
                "difficulty": self.difficulty_combo.currentIndex() + 1,
            }
            if self.selected_id is None:
                new_id = self.errorbook_service.add_error(**data)
                self.selected_id = new_id
                # 手动新增错题后自动触发 AI 打标
                self._auto_tag_error(new_id)
            else:
                self.errorbook_service.update_error(self.selected_id, **data)
            show_success(self, "错题已保存")
            self._refresh_filter_options()
            self._refresh_list()
        except Exception as e:
            logger.error("Save error failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _delete_error(self) -> None:
        if self.selected_id is None:
            return
        if not ask_confirm(self, "确认", "确定删除这条错题？"):
            return
        try:
            self.errorbook_service.delete_error(self.selected_id)
            self.selected_id = None
            self._add_error()
            self._refresh_list()
        except Exception as e:
            logger.error("Delete error failed: %s", e)
            show_error(self, "删除失败", str(e))

    def _auto_tag_current(self) -> None:
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        self._auto_tag_error(self.selected_id)

    def _acquire_ai_slot(self) -> bool:
        """AI 按钮防重入：同一时间只允许一个 AI 任务，避免连点并发多请求。

        获取成功时立即禁用触发按钮，任务结束（成功/失败）后恢复。
        """
        if getattr(self, "_ai_busy", False):
            return False
        self._ai_busy = True
        button = self.sender()
        self._ai_button = button if isinstance(button, QPushButton) else None
        if self._ai_button is not None:
            self._ai_button.setEnabled(False)
        return True

    def _release_ai_slot(self) -> None:
        """释放 AI 任务占用，恢复按钮可点击。"""
        self._ai_busy = False
        button = getattr(self, "_ai_button", None)
        self._ai_button = None
        if button is not None:
            try:
                button.setEnabled(True)
            except RuntimeError:
                pass  # 控件已销毁

    def _auto_tag_error(self, error_id: int) -> None:
        if not self._acquire_ai_slot():
            return
        worker = AutoTagWorker(self.errorbook_service, error_id=error_id)
        # 绑定本次任务的 error_id，回调时校验用户是否已切换到别的错题
        worker.signals.result.connect(lambda tags, eid=error_id: self._on_auto_tag_done(eid, tags))
        worker.signals.meta.connect(self._on_auto_tag_meta)
        worker.signals.error.connect(lambda msg: show_error(self, "自动打标失败", msg))
        worker.signals.finished.connect(self._release_ai_slot)
        try:
            ThreadPool.start_worker(worker)
        except Exception as e:
            # 启动失败必须归还占用，否则按钮会永久停在禁用态
            self._release_ai_slot()
            show_error(self, "启动失败", str(e))

    def _on_auto_tag_done(self, error_id: int, tags: dict) -> None:
        # 后台打标期间用户已切换到其他错题：结果已写入数据库，仅刷新列表，
        # 不回填当前表单，避免把 A 题的标签覆盖到正在编辑的 B 题界面
        if error_id != self.selected_id:
            self._refresh_filter_options()
            self._refresh_list()
            return
        self.subject_edit.setText(tags.get("subject", ""))
        self.chapter_edit.setText(tags.get("chapter", ""))
        self.knowledge_point_edit.setText(tags.get("knowledge_point", ""))
        self.tags_edit.setText(tags.get("tags", ""))

        kp_list = tags.get("knowledge_points", [])
        if isinstance(kp_list, list):
            self.knowledge_points_edit.setText(", ".join(str(k) for k in kp_list))

        qt_text = tags.get("question_type", "")
        qt_index = self.question_type_combo.findText(qt_text)
        self.question_type_combo.setCurrentIndex(max(0, qt_index))

        difficulty = int(tags.get("difficulty", 1) or 1)
        self.difficulty_combo.setCurrentIndex(max(0, min(4, difficulty - 1)))

        show_info(
            self,
            "自动打标完成",
            f"知识点：{', '.join(str(k) for k in kp_list[:3])}\n题型：{qt_text}",
        )
        self._refresh_filter_options()
        self._refresh_list()

    def _on_auto_tag_meta(self, meta: dict) -> None:
        duration = meta.get("duration_ms")
        if duration is not None:
            self.auto_tag_time_label.setText(f"打标推理耗时：{duration} ms")

    def _generate_variant(self) -> None:
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        self._generate_variant_for(self.selected_id)

    def _generate_variant_for(self, error_id: int) -> None:
        if not self._acquire_ai_slot():
            return
        # 开始新任务前清空缓存，避免失败后确认保存时误用上次的旧变式题
        self._last_variant = None
        worker = VariantQuestionWorker(self.errorbook_service, error_id)
        worker.signals.result.connect(lambda v: self._on_variant_ready(error_id, v))
        worker.signals.meta.connect(self._on_variant_meta)
        worker.signals.error.connect(self._on_variant_error)
        worker.signals.finished.connect(self._release_ai_slot)
        try:
            ThreadPool.start_worker(worker)
        except Exception as e:
            # 启动失败必须归还占用，否则按钮会永久停在禁用态
            self._release_ai_slot()
            show_error(self, "启动失败", str(e))

    def _on_variant_error(self, msg: str) -> None:
        self._last_variant = None
        show_error(self, "生成失败", msg)

    def _on_variant_ready(self, error_id: int, variant: dict) -> None:
        # 后台生成期间用户已切换到其他错题：不刷新当前表单与弹窗
        if error_id != self.selected_id:
            return
        # 缓存本次生成的变式题，供保存弹窗使用
        self._last_variant = variant
        question = variant.get("question", "") or ""
        answer = variant.get("answer", "") or ""
        analysis = variant.get("analysis", "") or ""
        md = f"**【变式题】**\n\n{question}\n\n**答案：**{answer}\n\n**解析：**{analysis}"
        self.variant_browser.set_markdown(md)
        # 保存确认只在成功生成后弹出（meta 信号失败时也会触发，不能挂在那里）
        if ask_confirm(self, "变式题生成完成", "是否将变式题保存到错题本并加入闪卡？"):
            self._save_variant(variant)

    def _save_variant(self, variant: dict) -> None:
        try:
            new_id = self.errorbook_service.add_error(
                question=variant.get("question", "") or "",
                answer=variant.get("answer", "") or "",
                analysis=variant.get("analysis", "") or "",
                knowledge_tags=self.tags_edit.text().strip(),
                subject=self.subject_edit.text().strip(),
                chapter=self.chapter_edit.text().strip(),
                knowledge_point=self.knowledge_point_edit.text().strip(),
                difficulty=self.difficulty_combo.currentIndex() + 1,
            )
            if self.flashcard_service is not None:
                self.flashcard_service.add_card(
                    front_content=variant.get("question", "") or "",
                    back_content=variant.get("answer", "") or "",
                    subject=self.subject_edit.text().strip(),
                    tags=self.tags_edit.text().strip(),
                )
            show_success(self, f"已保存变式题到错题本 ID={new_id} 并加入闪卡")
            self._refresh_list()
        except Exception as e:
            logger.error("Save variant failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _on_variant_meta(self, meta: dict) -> None:
        duration = meta.get("duration_ms")
        if duration is not None:
            self.variant_time_label.setText(f"变式题推理耗时：{duration} ms")

    def _generate_mindmap(self) -> None:
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        self._generate_mindmap_for(self.selected_id)

    def _generate_mindmap_for(self, error_id: int) -> None:
        error = self.errorbook_service.get_error(error_id)
        if error is None:
            return
        if not self._acquire_ai_slot():
            return
        content = f"题目：{error['question']}\n解析：{error['analysis']}\n知识点：{error.get('knowledge_point', '')}"
        worker = MindMapWorker(
            self.mindmap_service,
            title=f"错题 #{error_id}",
            content=content,
            target_type="error_book",
            target_id=error_id,
        )
        worker.signals.result.connect(lambda g: self._show_mindmap(f"错题 #{error_id}", g))
        worker.signals.error.connect(lambda msg: show_error(self, "生成失败", msg))
        worker.signals.finished.connect(self._release_ai_slot)
        try:
            ThreadPool.start_worker(worker)
        except Exception as e:
            # 启动失败必须归还占用，否则按钮会永久停在禁用态
            self._release_ai_slot()
            show_error(self, "启动失败", str(e))

    def _show_mindmap(self, title: str, graph: dict) -> None:
        dialog = MindMapViewerDialog(title, graph, self)
        dialog.exec()

    def _add_to_flashcard(self) -> None:
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        self._add_error_to_flashcard(self.selected_id)

    def _add_error_to_flashcard(self, error_id: int) -> None:
        if self.flashcard_service is None:
            show_error(self, "功能不可用", "闪卡服务未初始化")
            return
        error = self.errorbook_service.get_error(error_id)
        if error is None:
            return
        try:
            card_id = self.flashcard_service.add_card(
                front_content=error["question"],
                back_content=f"答案：{error['answer']}\n解析：{error['analysis']}",
                subject=error.get("subject", ""),
                tags=error.get("knowledge_tags", ""),
            )
            show_success(self, f"已生成闪卡 #{card_id}")
        except Exception as e:
            logger.error("Add error to flashcard failed: %s", e)
            show_error(self, "加入失败", str(e))

    def _speak_question(self) -> None:
        text = self.question_edit.toPlainText().strip()
        if not text:
            return
        if self.tts_service is None:
            show_error(self, "功能不可用", "TTS 服务未初始化")
            return
        worker = TTSWorker(self.tts_service, text[:2000])
        worker.signals.error.connect(lambda msg: logger.warning("TTS error: %s", msg))
        ThreadPool.start_worker(worker)

    def _load_annotations(self, error_id: int) -> None:
        if self.note_annotation_service is None:
            return
        try:
            annotations = self.note_annotation_service.list_annotations(
                target_type="error_book", target_id=error_id
            )
            if not annotations:
                self.annotation_browser.set_html("<i>暂无批注</i>")
                return
            html_parts = ["<b>批注：</b><br/>"]
            for a in annotations:
                # 用户内容先做 HTML 转义，防止 <、& 等字符破坏排版或注入标签
                color = a.get("highlight_color") or "#F59E0B"
                if not re.fullmatch(r"#[0-9A-Fa-f]{3,8}", color):
                    color = "#F59E0B"
                selected = html.escape(str(a.get("selected_text") or "")[:80])
                note = html.escape(str(a.get("note_content") or ""))
                html_parts.append(
                    f"<div style='margin:6px 0; padding:6px; border-left:3px solid {color};'>"
                    f"<b>原文：</b>{selected}...<br/>"
                    f"<b>笔记：</b>{note}</div>"
                )
            self.annotation_browser.set_html("".join(html_parts))
        except Exception as e:
            logger.warning("Load annotations failed: %s", e)

    def _add_annotation_from_selection(self) -> None:
        """取题目/答案/解析编辑框中当前选中的文本，弹出批注对话框。"""
        if self.selected_id is None:
            show_warning(self, "提示", "请先选择一条错题。")
            return
        if self.note_annotation_service is None:
            show_error(self, "功能不可用", "批注服务未初始化")
            return
        selected_text = ""
        for edit in (self.question_edit, self.answer_edit, self.analysis_edit):
            selected_text = edit.textCursor().selectedText()
            if selected_text:
                break
        if not selected_text:
            show_warning(self, "提示", "请先在题目、答案或解析中选中要批注的文本。")
            return
        self._add_annotation(selected_text)

    def _add_annotation(self, selected_text: str) -> None:
        if self.selected_id is None or self.note_annotation_service is None:
            return
        dialog = AnnotationDialog(selected_text, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        note = dialog.note()
        if not note:
            return
        try:
            self.note_annotation_service.add_annotation(
                target_type="error_book",
                target_id=self.selected_id,
                selected_text=selected_text,
                note_content=note,
            )
            self._load_annotations(self.selected_id)
            show_success(self, "批注已保存")
        except Exception as e:
            logger.error("Add annotation failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _import_errors(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入错题",
            "",
            "JSON/CSV (*.json *.csv)",
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            if path.suffix.lower() == ".json":
                count = self.errorbook_service.import_from_json(path)
            elif path.suffix.lower() == ".csv":
                count = self.errorbook_service.import_from_csv(path)
            else:
                show_error(self, "格式不支持", "仅支持 JSON 或 CSV 文件")
                return
            show_info(self, "导入成功", f"成功导入 {count} 条错题")
            self._refresh_filter_options()
            self._refresh_list()
        except Exception as e:
            logger.error("Import errors failed: %s", e)
            show_error(self, "导入失败", str(e))

    def _export_errors(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出错题",
            "errorbook.json",
            "JSON (*.json);;CSV (*.csv);;Markdown (*.md)",
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            if path.suffix.lower() == ".json":
                count = self.errorbook_service.export_to_json(path)
            elif path.suffix.lower() == ".csv":
                count = self.errorbook_service.export_to_csv(path)
            elif path.suffix.lower() == ".md":
                count = self.errorbook_service.export_to_markdown(path)
            else:
                show_error(self, "格式不支持", "仅支持 JSON、CSV 或 Markdown 文件")
                return
            show_info(self, "导出成功", f"成功导出 {count} 条错题到 {path}")
        except Exception as e:
            logger.error("Export errors failed: %s", e)
            show_error(self, "导出失败", str(e))

    def _export_paper(self) -> None:
        """唤起「导出试卷」弹窗；生成与反馈全部在弹窗内闭环。"""
        if not self.errorbook_service.list_errors(limit=1):
            show_warning(self, "无法导出", "错题本中还没有题目，请先录入错题")
            return

        from .error_paper_dialog import ErrorPaperExportDialog

        dialog = ErrorPaperExportDialog(self.error_paper_service, parent=self)
        # 持有引用：后台线程回调期间弹窗对象不能被回收
        self._paper_dialog = dialog
        dialog.export_succeeded.connect(self._on_paper_exported)
        dialog.export_failed.connect(
            lambda message: show_error(self, "导出失败", message)
        )
        try:
            dialog.exec()
        finally:
            self._paper_dialog = None

    def _on_paper_exported(self, file_path: str) -> None:
        """导出成功：Toast 提示并附带打开文件 / 打开文件夹快捷操作。"""
        path = Path(file_path)
        shown = show_toast(
            f"试卷已导出：{path.name}",
            level="success",
            duration_ms=8000,
            actions=[
                ("打开文件", lambda: self._open_path(path)),
                ("打开所在文件夹", lambda: self._open_path(path.parent)),
            ],
        )
        if not shown:  # 未注册 Toast 管理器时降级为模态提示
            show_info(self, "导出成功", f"试卷已导出到：\n{path}")

    @staticmethod
    def _open_path(target: Path) -> None:
        """用系统默认程序打开文件或文件夹。"""
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except Exception as e:
            logger.warning("Open path failed: %s", e)

    def locate_item(self, item_type: str, item_id: int) -> None:
        """根据全局搜索结果定位到错题。"""
        if item_type != "error_book":
            return
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == item_id:
                self.list_widget.setCurrentItem(item)
                self._on_item_selected(item)
                return
