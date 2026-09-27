"""自适应练习面板。

功能：
- 按知识点自动出题（选择/填空/判断/简答/主观开放）
- 自适应难度：默认「自动」档，根据近期正确率动态调整
- 做题交互：题目展示、选项、作答输入、提交判分
- 判分结果展示：得分、正确答案、解析、AI 点评
- 练习统计与最近做题记录
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..services import ErrorBookService, PracticeService
from ..services.practice_service import QUESTION_TYPES
from ..utils.latex_clean import latex_to_unicode
from ..utils.logger import get_logger
from .interactions import friendly_error_message, policy
from .math_text import MathLabel
from .ui_utils import (
    ButtonBusy,
    apply_glass_style,
    create_header_widget,
    set_primary_button_style,
    shake_widget,
    show_error,
    show_success,
    show_warning,
)
from .workers import PracticeGenWorker, PracticeJudgeWorker, ThreadPool

logger = get_logger("ui.practice_panel")


class PracticePanel(QWidget):
    """自适应练习界面。"""

    def __init__(
        self,
        practice_service: PracticeService,
        errorbook_service: Optional[ErrorBookService] = None,
        question_import_service=None,
    ):
        super().__init__()
        self.practice_service = practice_service
        self.errorbook_service = errorbook_service
        self.question_import_service = question_import_service
        self._current_question: Optional[Dict[str, Any]] = None
        self._option_group: Optional[QButtonGroup] = None
        self._build_ui()
        self.refresh_data()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

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

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        header = create_header_widget("自适应练习", "按知识点自动出题，难度随正确率动态调整")
        layout.addWidget(header)

        # ---- 批量导入题库入口 ----
        import_row = QHBoxLayout()
        import_row.addStretch()
        self.import_btn = QPushButton("📥 批量导入题库")
        self.import_btn.setToolTip("从 Excel(.xlsx) / Word(.docx) 文件批量导入题目")
        self.import_btn.clicked.connect(self._open_import_dialog)
        import_row.addWidget(self.import_btn)
        layout.addLayout(import_row)

        # ---- 出题设置卡片 ----
        setting_card = QWidget()
        setting_card.setProperty("glass", True)
        apply_glass_style(setting_card)
        setting_layout = QVBoxLayout(setting_card)
        setting_layout.setContentsMargins(16, 16, 16, 16)
        setting_layout.setSpacing(12)

        setting_row1 = QHBoxLayout()
        setting_row1.setSpacing(10)
        setting_row1.addWidget(QLabel("知识点："))
        self.kp_combo = QComboBox()
        self.kp_combo.setEditable(True)
        self.kp_combo.setToolTip("可下拉选择薄弱知识点，也可手动输入任意知识点")
        self.kp_combo.setMinimumWidth(220)
        setting_row1.addWidget(self.kp_combo, 1)

        setting_row1.addWidget(QLabel("题型："))
        self.type_combo = QComboBox()
        for key, label in QUESTION_TYPES.items():
            self.type_combo.addItem(label, key)
        self.type_combo.setMinimumWidth(110)
        setting_row1.addWidget(self.type_combo)

        setting_row1.addWidget(QLabel("难度："))
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItem("自动（自适应）", 0)
        for level in range(1, 6):
            self.difficulty_combo.addItem(f"{level} 星", level)
        self.difficulty_combo.setToolTip("自动档会根据该知识点近期正确率动态调整难度")
        self.difficulty_combo.setMinimumWidth(110)
        setting_row1.addWidget(self.difficulty_combo)
        setting_layout.addLayout(setting_row1)

        self.gen_btn = QPushButton("生成题目")
        set_primary_button_style(self.gen_btn)
        self.gen_btn.setMinimumWidth(120)
        self.gen_btn.clicked.connect(self._on_generate)
        setting_layout.addWidget(self.gen_btn)

        self.kp_hint_label = QLabel("")
        self.kp_hint_label.setStyleSheet("color: #8A93A8; font-size: 12px;")
        self.kp_hint_label.setWordWrap(True)
        setting_layout.addWidget(self.kp_hint_label)

        layout.addWidget(setting_card)

        # ---- 题目卡片 ----
        self.question_card = QWidget()
        self.question_card.setProperty("glass", True)
        apply_glass_style(self.question_card)
        question_layout = QVBoxLayout(self.question_card)
        question_layout.setContentsMargins(16, 16, 16, 16)
        question_layout.setSpacing(12)

        self.question_meta_label = QLabel("")
        self.question_meta_label.setStyleSheet(
            "color: #8A93A8; font-size: 12px;"
        )
        question_layout.addWidget(self.question_meta_label)

        from .markdown_browser import MarkdownBrowser

        self.question_label = MarkdownBrowser()
        self.question_label.set_markdown("尚未生成题目，请在上方选择知识点后点击「生成题目」。")
        self.question_label.setStyleSheet("font-size: 14px;")
        self.question_label.browser.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        question_layout.addWidget(self.question_label)

        # 选项 / 作答区容器（按题型动态切换）
        self.answer_container = QWidget()
        self.answer_layout = QVBoxLayout(self.answer_container)
        self.answer_layout.setContentsMargins(0, 0, 0, 0)
        self.answer_layout.setSpacing(8)
        question_layout.addWidget(self.answer_container)

        # 主观题文字作答区
        self.answer_edit = QPlainTextEdit()
        self.answer_edit.setPlaceholderText("在此输入你的答案...")
        self.answer_edit.setVisible(False)
        self.answer_edit.setMinimumHeight(110)
        self.answer_layout.addWidget(self.answer_edit)

        btn_row = QHBoxLayout()
        self.submit_btn = QPushButton("提交答案")
        set_primary_button_style(self.submit_btn)
        self.submit_btn.setMinimumWidth(120)
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self._on_submit)
        btn_row.addWidget(self.submit_btn)

        self.next_btn = QPushButton("下一题（同知识点）")
        self.next_btn.setMinimumWidth(140)
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self._on_generate)
        btn_row.addWidget(self.next_btn)
        btn_row.addStretch()
        question_layout.addLayout(btn_row)

        layout.addWidget(self.question_card)

        # ---- 判分结果卡片 ----
        self.result_card = QWidget()
        self.result_card.setProperty("glass", True)
        apply_glass_style(self.result_card)
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(16, 16, 16, 16)
        result_layout.setSpacing(8)

        self.result_title_label = QLabel("判分结果")
        self.result_title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        result_layout.addWidget(self.result_title_label)

        self.result_label = MathLabel(
            "提交作答后，这里会显示得分、正确答案、解析与 AI 点评。", markdown=True
        )
        self.result_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        result_layout.addWidget(self.result_label)

        # 得分进度：平滑增长而不是瞬间跳到终值
        self.score_progress = QProgressBar()
        self.score_progress.setRange(0, 100)
        self.score_progress.setValue(0)
        self.score_progress.setFixedHeight(8)
        self.score_progress.setTextVisible(False)
        result_layout.addWidget(self.score_progress)

        self.encourage_label = QLabel("")
        self.encourage_label.setWordWrap(True)
        self.encourage_label.setObjectName("subtitle_label")
        self.encourage_label.setVisible(False)
        result_layout.addWidget(self.encourage_label)
        self.result_card.setVisible(False)

        layout.addWidget(self.result_card)

        # ---- 统计 + 记录卡片 ----
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(14)

        stats_card = QWidget()
        stats_card.setProperty("glass", True)
        apply_glass_style(stats_card)
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(16, 16, 16, 16)
        stats_layout.setSpacing(8)
        stats_title = QLabel("练习统计")
        stats_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        stats_layout.addWidget(stats_title)
        self.stats_label = QLabel("暂无数据")
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet("color: #8A93A8; font-size: 12px;")
        stats_layout.addWidget(self.stats_label)
        stats_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        bottom_row.addWidget(stats_card)

        record_card = QWidget()
        record_card.setProperty("glass", True)
        apply_glass_style(record_card)
        record_layout = QVBoxLayout(record_card)
        record_layout.setContentsMargins(16, 16, 16, 16)
        record_layout.setSpacing(8)
        record_title = QLabel("最近做题记录")
        record_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        record_layout.addWidget(record_title)
        self.record_list = QListWidget()
        self.record_list.setMaximumHeight(220)
        record_layout.addWidget(self.record_list)
        record_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bottom_row.addWidget(record_card, 1)

        layout.addLayout(bottom_row)
        layout.addStretch()

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------

    def _open_import_dialog(self) -> None:
        """打开批量导入题库对话框。"""
        try:
            from .question_import_dialog import QuestionImportDialog

            dlg = QuestionImportDialog(
                import_service=self.question_import_service, parent=self
            )
            if dlg.exec():
                self.refresh_data()
        except Exception as e:
            logger.error("Open import dialog failed: %s", e)
            try:
                from .ui_utils import show_error
                show_error(self, "导入失败", f"无法打开导入对话框：{e}")
            except Exception:
                pass

    def refresh_data(self) -> None:
        """刷新知识点候选、统计与记录（用户切换后由主窗口调用）。"""
        # 知识点候选：薄弱知识点优先 + 错题本知识点
        candidates = []
        if self.errorbook_service is not None:
            try:
                weak = self.errorbook_service.get_weak_knowledge_points(top_n=8)
                candidates.extend(w["knowledge_point"] for w in weak if w.get("knowledge_point"))
                for kp in self.errorbook_service.list_knowledge_points():
                    if kp and kp not in candidates:
                        candidates.append(kp)
            except Exception as e:
                logger.warning("Load knowledge point candidates failed: %s", e)

        current = self.kp_combo.currentText()
        self.kp_combo.blockSignals(True)
        self.kp_combo.clear()
        self.kp_combo.addItems(candidates)
        if current:
            self.kp_combo.setCurrentText(current)
        self.kp_combo.blockSignals(False)

        self._update_kp_hint()
        self._refresh_stats()

    def _update_kp_hint(self) -> None:
        """更新当前知识点的自适应难度提示。"""
        kp = self.kp_combo.currentText().strip()
        if not kp:
            self.kp_hint_label.setText("")
            return
        try:
            stats = self.practice_service.get_recent_stats(kp)
            suggested = self.practice_service.suggest_difficulty(kp)
            if stats["count"] == 0:
                self.kp_hint_label.setText(
                    f"「{kp}」暂无做题记录，自动难度将从 3 星开始。"
                )
            else:
                accuracy = int((stats["accuracy"] or 0) * 100)
                self.kp_hint_label.setText(
                    f"「{kp}」近期 {stats['count']} 题，正确率 {accuracy}%，"
                    f"自适应推荐难度 {suggested} 星。"
                )
        except Exception as e:
            logger.warning("Update kp hint failed: %s", e)
            self.kp_hint_label.setText("")

    def _refresh_stats(self) -> None:
        """刷新练习统计与最近记录。"""
        try:
            stats = self.practice_service.get_stats()
            accuracy = int((stats["accuracy"] or 0) * 100)
            self.stats_label.setText(
                f"累计做题：{stats['total']} 题\n答对：{stats['correct']} 题\n"
                f"总正确率：{accuracy}%"
            )
        except Exception as e:
            logger.warning("Refresh practice stats failed: %s", e)
            self.stats_label.setText("统计加载失败")

        self.record_list.clear()
        try:
            records = self.practice_service.list_records(limit=30)
            type_names = {k: v for k, v in QUESTION_TYPES.items()}
            for r in records:
                q_type = type_names.get(r.get("question_type", ""), r.get("question_type", ""))
                correct = "✓ 对" if r.get("is_correct") else "✗ 错"
                question_text = latex_to_unicode(
                    (r.get("question") or "")[:40].replace("\n", " ")
                )
                item = QListWidgetItem(
                    f"[{correct}] [{q_type}] {r.get('knowledge_point', '')} | {question_text}"
                )
                self.record_list.addItem(item)
        except Exception as e:
            logger.warning("Refresh practice records failed: %s", e)

    # ------------------------------------------------------------------
    # 出题
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        kp = self.kp_combo.currentText().strip()
        if not kp:
            show_warning(self, "出题失败", "请先输入或选择知识点。")
            return

        question_type = str(self.type_combo.currentData() or "choice")
        difficulty = int(self.difficulty_combo.currentData() or 0)
        difficulty = difficulty if difficulty > 0 else None

        self._gen_busy = ButtonBusy(self.gen_btn, "出题中...").start()
        self._next_busy_gen = ButtonBusy(self.next_btn, "出题中...").start()
        self.result_card.setVisible(False)

        worker = PracticeGenWorker(
            self.practice_service, kp, question_type, difficulty=difficulty
        )
        worker.signals.result.connect(self._on_question_ready)
        worker.signals.error.connect(self._on_gen_error)
        worker.signals.finished.connect(self._on_gen_finished)
        ThreadPool.start_worker(worker)

    def _on_gen_finished(self) -> None:
        for busy in (getattr(self, "_gen_busy", None), getattr(self, "_next_busy_gen", None)):
            if busy is not None:
                busy.restore()

    def _on_gen_error(self, message: str) -> None:
        logger.error("Practice generation failed: %s", message)
        show_error(self, "出题失败", message)

    def _on_question_ready(self, question: Dict[str, Any]) -> None:
        """题目生成成功，渲染题目与作答控件。"""
        self._current_question = question
        self.result_card.setVisible(False)
        self.encourage_label.setVisible(False)
        self.score_progress.setValue(0)

        type_label = QUESTION_TYPES.get(question.get("question_type", ""), "")
        difficulty = question.get("difficulty", 3)
        kp = question.get("knowledge_point", "")
        self.question_meta_label.setText(
            f"知识点：{kp}    题型：{type_label}    难度：{difficulty}/5    题目 ID：{question.get('id', 0)}"
        )
        self.question_label.set_markdown(
            latex_to_unicode(question.get("question", ""))
        )

        # 清空旧的作答控件
        while self.answer_layout.count():
            item = self.answer_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.answer_edit:
                widget.deleteLater()
        if self._option_group is not None:
            self._option_group.deleteLater()
            self._option_group = None

        q_type = question.get("question_type", "choice")
        options = question.get("options", []) or []
        self.answer_edit.clear()
        self.answer_edit.setVisible(q_type in {"short", "open"})

        if q_type == "choice" and options:
            self._option_group = QButtonGroup(self)
            for i, option in enumerate(options):
                letter = chr(ord("A") + i)
                radio = QRadioButton(f"{letter}. {latex_to_unicode(str(option))}")
                radio.setTextFormat(Qt.TextFormat.PlainText)
                radio.setStyleSheet("font-size: 13px;")
                self._option_group.addButton(radio, i)
                self.answer_layout.addWidget(radio)
        elif q_type == "judge":
            self._option_group = QButtonGroup(self)
            for i, label in enumerate(["正确", "错误"]):
                radio = QRadioButton(label)
                radio.setStyleSheet("font-size: 13px;")
                self._option_group.addButton(radio, i)
                self.answer_layout.addWidget(radio)
        elif q_type == "fill":
            edit = QLineEdit()
            edit.setPlaceholderText("在此填入答案...")
            edit.setMinimumHeight(36)
            edit.setStyleSheet("font-size: 14px;")
            self._fill_edit = edit
            self.answer_layout.addWidget(edit)

        self.submit_btn.setEnabled(True)
        self.next_btn.setEnabled(True)
        self._update_kp_hint()

    # ------------------------------------------------------------------
    # 判题
    # ------------------------------------------------------------------

    def _collect_answer(self) -> Optional[str]:
        """按当前题型收集用户作答。"""
        if self._current_question is None:
            return None
        q_type = self._current_question.get("question_type", "choice")
        if q_type == "choice":
            if self._option_group is None:
                return None
            checked = self._option_group.checkedId()
            if checked < 0:
                return None
            return chr(ord("A") + checked)
        if q_type == "judge":
            if self._option_group is None:
                return None
            checked = self._option_group.checkedId()
            if checked < 0:
                return None
            return "正确" if checked == 0 else "错误"
        if q_type == "fill":
            edit = getattr(self, "_fill_edit", None)
            return edit.text().strip() if edit is not None else None
        return self.answer_edit.toPlainText().strip()

    def _on_submit(self) -> None:
        if self._current_question is None or not self._current_question.get("id"):
            return
        answer = self._collect_answer()
        if not answer:
            # 苹果式：无效提交给轻微抖动提醒，并说明原因
            shake_widget(self.answer_edit)
            show_warning(self, "提交失败", "请先作答再提交。")
            return

        self._submit_busy = ButtonBusy(self.submit_btn, "判分中...").start()
        self._next_busy_judge = ButtonBusy(self.next_btn, "判分中...").start()

        worker = PracticeJudgeWorker(
            self.practice_service, int(self._current_question["id"]), answer
        )
        worker.signals.result.connect(self._on_judge_ready)
        worker.signals.error.connect(self._on_judge_error)
        worker.signals.finished.connect(self._on_judge_finished)
        ThreadPool.start_worker(worker)

    def _on_judge_finished(self) -> None:
        for busy in (getattr(self, "_submit_busy", None), getattr(self, "_next_busy_judge", None)):
            if busy is not None:
                busy.restore()

    def _on_judge_error(self, message: str) -> None:
        logger.error("Practice judge failed: %s", message)
        show_error(self, "判分失败", friendly_error_message(message))

    def _on_judge_ready(self, result: Dict[str, Any]) -> None:
        """展示判分结果。"""
        is_correct = bool(result.get("is_correct"))
        score = int(result.get("score", 0) or 0)
        title = "✓ 回答正确" if is_correct else "✗ 回答错误"
        color = "#10B981" if is_correct else "#EF4444"

        self.result_title_label.setText(f"判分结果：{title}")
        self.result_title_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {color};"
        )

        def _clean(value: Any) -> str:
            """把可能含 LaTeX 的字段转为可读 Unicode 文本（并做 HTML 转义）。"""
            return escape(latex_to_unicode(str(value or "")))

        # 得分点用柔和配色区分：绿=得分点、橙=失分点、红=错误原因
        rows = [
            f"<div>得分：<b>{score} / 100</b></div>",
            f"<div>正确答案：{_clean(result.get('correct_answer', ''))}</div>",
            f"<div>解析：{_clean(result.get('analysis', ''))}</div>",
        ]
        scoring_points = result.get("scoring_points")
        if scoring_points:
            items = "；".join(
                f"<span style='color:#10B981;'>✓ {_clean(p)}</span>" for p in scoring_points
            )
            rows.append(f"<div>得分点：{items}</div>")
        lost_points = result.get("lost_points")
        if lost_points:
            items = "；".join(
                f"<span style='color:#F59E0B;'>✗ {_clean(p)}</span>" for p in lost_points
            )
            rows.append(f"<div>失分点：{items}</div>")
        error_reasons = result.get("error_reasons")
        if error_reasons:
            items = "；".join(
                f"<span style='color:#EF4444;'>{_clean(r)}</span>" for r in error_reasons
            )
            rows.append(f"<div>错误原因：{items}</div>")
        if result.get("improvement"):
            rows.append(f"<div>改进建议：{_clean(result['improvement'])}</div>")
        rows.append(f"<div>AI 点评：{_clean(result.get('feedback', ''))}</div>")
        next_difficulty = result.get("next_difficulty")
        if next_difficulty is not None:
            rows.append(
                f"<div style='color:#8A93A8;font-size:11px;'>"
                f"自适应难度已更新，下一题推荐 {next_difficulty} 星。</div>"
            )
        self.result_label.set_rich_text("".join(rows))
        self.result_card.setVisible(True)

        # 极简反馈：一句鼓励 + 得分进度平滑增长
        encouragement = self._encouragement(score, is_correct)
        self.encourage_label.setText(encouragement)
        self.encourage_label.setVisible(True)
        self._animate_score(score)
        show_success(self, f"本次得分 {score} 分 · {encouragement}")

        self._refresh_stats()
        self._update_kp_hint()

    @staticmethod
    def _encouragement(score: int, is_correct: bool) -> str:
        """按得分给出简短自然的鼓励文案（克制、具体，不空洞）。"""
        if score >= 90:
            return "思路很完整，继续保持这个状态。"
        if score >= 70:
            return "基本掌握了，把失分点再看一遍就更稳。"
        if score >= 40:
            return "方向是对的，重点补一下上面的失分点。"
        if is_correct:
            return "答案对了，步骤还可以写得更严谨。"
        return "别着急，对照解析把这道题重做一遍会清楚很多。"

    def _animate_score(self, score: int) -> None:
        """得分进度条平滑增长（低配 / 闲置时直接显示终值）。"""
        target = max(0, min(100, int(score)))
        try:
            self.score_progress.setValue(0)
            duration = policy().duration(180)
            if duration <= 0:
                self.score_progress.setValue(target)
                return
            anim = QPropertyAnimation(self.score_progress, b"value", self)
            anim.setDuration(duration)
            anim.setStartValue(0)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            self._score_anim = anim
        except Exception:
            self.score_progress.setValue(target)
