"""试卷生成面板。

功能：
- 组卷配置：标题、知识点（多个）、各题型题量、难度范围
- 后台 AI 生成试卷，列表展示历史试卷
- 选中试卷预览题目明细
- 导出 Word / PDF，同时生成【试卷本体】+【参考答案】两套文档
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services import ErrorBookService, ExamService
from ..services.practice_service import QUESTION_TYPES
from ..utils.logger import get_logger
from .math_text import attach_math_delegate
from .ui_utils import (
    apply_glass_style,
    ask_confirm,
    create_header_widget,
    fix_spinbox_text,
    set_primary_button_style,
    show_error,
    show_info,
    show_warning,
    ButtonBusy,
)
from .workers import ExamGenWorker, ThreadPool

logger = get_logger("ui.exam_panel")

# 题型显示顺序与默认题量
_TYPE_ORDER = ["choice", "fill", "judge", "short", "open"]
_DEFAULT_COUNTS = {"choice": 5, "fill": 5, "judge": 5, "short": 2, "open": 1}


class ExamPanel(QWidget):
    """试卷生成与导出界面。"""

    def __init__(
        self,
        exam_service: ExamService,
        errorbook_service: Optional[ErrorBookService] = None,
    ):
        super().__init__()
        self.exam_service = exam_service
        self.errorbook_service = errorbook_service
        self._current_paper_id: Optional[int] = None
        self._count_spins = {}
        # 试卷导出模板配置（字体/字号/行距/页边距/答案位置/知识点标注）
        self._paper_template = {
            "font_name": "Microsoft YaHei",
            "font_size": 11.0,
            "line_spacing": 1.5,
            "margin_mm": 25.0,
            "answer_mode": "separate",
            "show_kp": False,
        }
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

        header = create_header_widget("试卷生成", "按知识点组卷，导出试卷与参考答案")
        layout.addWidget(header)

        # ---- 组卷配置卡片 ----
        config_card = QWidget()
        config_card.setProperty("glass", True)
        apply_glass_style(config_card)
        config_layout = QVBoxLayout(config_card)
        config_layout.setContentsMargins(16, 16, 16, 16)
        config_layout.setSpacing(12)

        config_layout.addWidget(QLabel("试卷标题"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：函数与导数专项测试卷")
        config_layout.addWidget(self.title_edit)

        config_layout.addWidget(QLabel("知识点范围（用逗号分隔，可下拉选择）"))
        self.kp_combo = QComboBox()
        self.kp_combo.setEditable(True)
        self.kp_combo.setToolTip("可下拉选择已有知识点，也可手动输入多个，用逗号分隔")
        config_layout.addWidget(self.kp_combo)

        counts_row = QHBoxLayout()
        counts_row.setSpacing(10)
        for key in _TYPE_ORDER:
            label = QLabel(f"{QUESTION_TYPES[key]}：")
            spin = QSpinBox()
            fix_spinbox_text(spin)
            spin.setRange(0, 10)
            spin.setValue(_DEFAULT_COUNTS[key])
            self._count_spins[key] = spin
            wrap = QVBoxLayout()
            wrap.addWidget(label)
            wrap.addWidget(spin)
            holder = QWidget()
            holder.setLayout(wrap)
            counts_row.addWidget(holder)
        config_layout.addLayout(counts_row)

        diff_row = QHBoxLayout()
        diff_row.addWidget(QLabel("难度范围："))
        self.diff_min_spin = QSpinBox()
        fix_spinbox_text(self.diff_min_spin)
        self.diff_min_spin.setRange(1, 5)
        self.diff_min_spin.setValue(2)
        self.diff_min_spin.setToolTip("1 最简单，5 最难")
        diff_row.addWidget(self.diff_min_spin)
        diff_row.addWidget(QLabel("到"))
        self.diff_max_spin = QSpinBox()
        fix_spinbox_text(self.diff_max_spin)
        self.diff_max_spin.setRange(1, 5)
        self.diff_max_spin.setValue(4)
        diff_row.addStretch()
        config_layout.addLayout(diff_row)

        self.total_hint_label = QLabel("")
        self.total_hint_label.setStyleSheet("color: #86909C; font-size: 12px;")
        config_layout.addWidget(self.total_hint_label)

        self.gen_btn = QPushButton("生成试卷")
        set_primary_button_style(self.gen_btn)
        self.gen_btn.setMinimumWidth(120)
        self.gen_btn.clicked.connect(self._on_generate)
        config_layout.addWidget(self.gen_btn)

        layout.addWidget(config_card)

        # ---- 试卷列表 + 预览 ----
        list_card = QWidget()
        list_card.setProperty("glass", True)
        apply_glass_style(list_card)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(16, 16, 16, 16)
        list_layout.setSpacing(8)

        list_header = QHBoxLayout()
        list_title = QLabel("我的试卷")
        list_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        list_header.addWidget(list_title)
        list_header.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_data)
        list_header.addWidget(refresh_btn)
        delete_btn = QPushButton("删除试卷")
        delete_btn.clicked.connect(self._on_delete)
        list_header.addWidget(delete_btn)
        list_layout.addLayout(list_header)

        self.paper_list = QListWidget()
        self.paper_list.currentItemChanged.connect(self._on_paper_selected)
        self.paper_list.setMaximumHeight(180)
        self.paper_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.paper_list.customContextMenuRequested.connect(self._show_paper_context_menu)
        list_layout.addWidget(self.paper_list)
        layout.addWidget(list_card)

        # ---- 预览卡片 ----
        preview_card = QWidget()
        preview_card.setProperty("glass", True)
        apply_glass_style(preview_card)
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setSpacing(8)

        preview_header = QHBoxLayout()
        preview_title = QLabel("试卷预览")
        preview_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        self.template_btn = QPushButton("⚙ 导出模板设置")
        self.template_btn.clicked.connect(self._open_template_dialog)
        preview_header.addWidget(self.template_btn)
        self.export_docx_btn = QPushButton("导出 Word")
        self.export_docx_btn.setEnabled(False)
        self.export_docx_btn.clicked.connect(lambda: self._on_export("docx"))
        preview_header.addWidget(self.export_docx_btn)
        self.export_pdf_btn = QPushButton("导出 PDF")
        self.export_pdf_btn.setEnabled(False)
        self.export_pdf_btn.clicked.connect(lambda: self._on_export("pdf"))
        preview_header.addWidget(self.export_pdf_btn)
        preview_layout.addLayout(preview_header)

        self.paper_tree = QTreeWidget()
        self.paper_tree.setHeaderLabels(["#", "题目", "知识点", "分值"])
        self.paper_tree.setColumnWidth(0, 36)
        self.paper_tree.setColumnWidth(1, 480)
        self.paper_tree.setColumnWidth(2, 140)
        self.paper_tree.setColumnWidth(3, 60)
        self.paper_tree.setWordWrap(True)
        # 题目 / 知识点列启用公式混排：只有真正含公式的单元格才走富文本绘制
        self._paper_math_delegates = [
            attach_math_delegate(self.paper_tree, 1),
            attach_math_delegate(self.paper_tree, 2),
        ]
        preview_layout.addWidget(self.paper_tree)

        layout.addWidget(preview_card)
        layout.addStretch()

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------

    def refresh_data(self) -> None:
        """刷新知识点候选与试卷列表。"""
        candidates = []
        if self.errorbook_service is not None:
            try:
                for kp in self.errorbook_service.list_knowledge_points():
                    if kp:
                        candidates.append(kp)
            except Exception as e:
                logger.warning("Load knowledge points failed: %s", e)

        current = self.kp_combo.currentText()
        self.kp_combo.blockSignals(True)
        self.kp_combo.clear()
        self.kp_combo.addItems(candidates)
        if current:
            self.kp_combo.setCurrentText(current)
        self.kp_combo.blockSignals(False)

        self.paper_list.clear()
        try:
            papers = self.exam_service.list_papers()
            for p in papers:
                kp_text = "、".join(p.get("knowledge_point_list", [])[:3])
                item = QListWidgetItem(
                    f"{p['title']}  |  {kp_text}  |  总分 {p.get('total_score', 0)}"
                )
                item.setData(Qt.ItemDataRole.UserRole, p["id"])
                self.paper_list.addItem(item)
        except Exception as e:
            logger.warning("Load papers failed: %s", e)

        self._update_total_hint()

    def _update_total_hint(self) -> None:
        total = sum(spin.value() for spin in self._count_spins.values())
        self.total_hint_label.setText(f"当前配置共 {total} 题。生成后可导出试卷与参考答案两套文档。")

    # ------------------------------------------------------------------
    # 组卷
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            show_warning(self, "组卷失败", "请先输入试卷标题。")
            return

        kp_text = self.kp_combo.currentText().strip()
        knowledge_points = [p.strip() for p in kp_text.replace("，", ",").split(",") if p.strip()]
        if not knowledge_points:
            show_warning(self, "组卷失败", "请输入至少一个知识点。")
            return

        type_counts = {key: spin.value() for key, spin in self._count_spins.items()}
        if sum(type_counts.values()) == 0:
            show_warning(self, "组卷失败", "各题型题量不能全为 0。")
            return

        diff_min = self.diff_min_spin.value()
        diff_max = self.diff_max_spin.value()
        if diff_max < diff_min:
            diff_min, diff_max = diff_max, diff_min

        self._gen_busy = ButtonBusy(self.gen_btn, "组卷中...").start()
        worker = ExamGenWorker(
            self.exam_service,
            title,
            knowledge_points,
            type_counts,
            diff_min,
            diff_max,
        )
        worker.signals.result.connect(self._on_paper_ready)
        worker.signals.error.connect(self._on_gen_error)
        worker.signals.finished.connect(self._on_gen_finished)
        ThreadPool.start_worker(worker)

    def _on_gen_finished(self) -> None:
        busy = getattr(self, "_gen_busy", None)
        if busy is not None:
            busy.restore()

    def _on_gen_error(self, message: str) -> None:
        logger.error("Exam paper generation failed: %s", message)
        show_error(self, "组卷失败", message)

    def _on_paper_ready(self, paper: dict) -> None:
        """试卷生成成功，刷新列表并选中。"""
        failures = paper.get("failures", [])
        self.refresh_data()
        paper_id = paper.get("id")
        for i in range(self.paper_list.count()):
            item = self.paper_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == paper_id:
                self.paper_list.setCurrentItem(item)
                break
        if failures:
            QMessageBox.warning(
                self,
                "部分题型生成失败",
                "以下题型生成失败，其余题型已成功：\n" + "\n".join(failures),
            )
        else:
            QMessageBox.information(
                self,
                "组卷完成",
                f"试卷「{paper.get('title', '')}」已生成，共 {len(paper.get('questions', []))} 题。",
            )

    # ------------------------------------------------------------------
    # 预览与导出
    # ------------------------------------------------------------------

    def _on_paper_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if current is None:
            self._current_paper_id = None
            self.export_docx_btn.setEnabled(False)
            self.export_pdf_btn.setEnabled(False)
            return
        paper_id = current.data(Qt.ItemDataRole.UserRole)
        self._current_paper_id = paper_id
        try:
            paper = self.exam_service.get_paper(paper_id)
        except Exception as e:
            logger.warning("Load paper detail failed: %s", e)
            paper = None
        self.paper_tree.clear()
        if not paper:
            self.export_docx_btn.setEnabled(False)
            self.export_pdf_btn.setEnabled(False)
            return

        self.export_docx_btn.setEnabled(bool(paper.get("questions")))
        self.export_pdf_btn.setEnabled(bool(paper.get("questions")))
        for index, q in enumerate(paper.get("questions", []), 1):
            type_label = QUESTION_TYPES.get(q.get("question_type", ""), "")
            text = f"[{type_label}] {q.get('question', '')}"
            item = QTreeWidgetItem(
                [str(index), text, q.get("knowledge_point", ""), str(q.get("score", 0))]
            )
            self.paper_tree.addTopLevelItem(item)

    def _show_paper_context_menu(self, position) -> None:
        """试卷列表右键菜单：导出、删除。"""
        item = self.paper_list.itemAt(position)
        if item is None:
            return
        self.paper_list.setCurrentItem(item)
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        act_docx = menu.addAction("导出为 DOCX")
        act_pdf = menu.addAction("导出为 PDF")
        menu.addSeparator()
        act_delete = menu.addAction("删除试卷")
        chosen = menu.exec(self.paper_list.mapToGlobal(position))
        if chosen is act_docx:
            self._on_export("docx")
        elif chosen is act_pdf:
            self._on_export("pdf")
        elif chosen is act_delete:
            self._on_delete()

    def _on_delete(self) -> None:
        if self._current_paper_id is None:
            show_warning(self, "删除失败", "请先在列表中选择试卷。")
            return
        if not ask_confirm(self, "删除确认", "确定删除这份试卷吗？题目仍保留在题库中。"):
            return
        try:
            self.exam_service.delete_paper(self._current_paper_id)
        except Exception as e:
            show_error(self, "删除失败", str(e))
            return
        self._current_paper_id = None
        self.paper_tree.clear()
        self.refresh_data()

    def _open_template_dialog(self) -> None:
        """打开导出模板配置对话框。"""
        from PyQt6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("导出模板设置")
        form = QFormLayout(dlg)

        font_combo = QComboBox()
        font_combo.addItems(["Microsoft YaHei", "SimSun", "SimHei", "KaiTi", "FangSong"])
        font_combo.setCurrentText(self._paper_template.get("font_name", "Microsoft YaHei"))
        form.addRow("正文字体：", font_combo)

        size_spin = QDoubleSpinBox()
        size_spin.setRange(8.0, 20.0)
        size_spin.setSingleStep(0.5)
        size_spin.setValue(float(self._paper_template.get("font_size", 11)))
        form.addRow("字号(pt)：", size_spin)

        spacing_spin = QDoubleSpinBox()
        spacing_spin.setRange(1.0, 3.0)
        spacing_spin.setSingleStep(0.25)
        spacing_spin.setValue(float(self._paper_template.get("line_spacing", 1.5)))
        form.addRow("行间距(倍)：", spacing_spin)

        margin_spin = QDoubleSpinBox()
        margin_spin.setRange(10.0, 50.0)
        margin_spin.setSingleStep(1.0)
        margin_spin.setValue(float(self._paper_template.get("margin_mm", 25)))
        form.addRow("页边距(mm)：", margin_spin)

        answer_combo = QComboBox()
        answer_combo.addItems(["答案单独成文件（方便打印给学生）", "答案放在试卷文末"])
        answer_combo.setCurrentIndex(0 if self._paper_template.get("answer_mode") == "separate" else 1)
        form.addRow("答案位置：", answer_combo)

        kp_check = QCheckBox("在每道题后标注知识点名称")
        kp_check.setChecked(bool(self._paper_template.get("show_kp", False)))
        form.addRow("", kp_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._paper_template = {
                "font_name": font_combo.currentText(),
                "font_size": float(size_spin.value()),
                "line_spacing": float(spacing_spin.value()),
                "margin_mm": float(margin_spin.value()),
                "answer_mode": "separate" if answer_combo.currentIndex() == 0 else "end",
                "show_kp": kp_check.isChecked(),
            }
            show_info(self, "已保存", "导出模板设置已更新，将在下次导出时生效。")

    def _on_export(self, fmt: str) -> None:
        if self._current_paper_id is None:
            show_warning(self, "导出失败", "请先在列表中选择试卷。")
            return
        output_dir = QFileDialog.getExistingDirectory(
            self, "选择导出目录", str(Path.home() / "Documents")
        )
        if not output_dir:
            return
        try:
            paths = self.exam_service.export_paper(
                self._current_paper_id, Path(output_dir), fmt=fmt,
                template=self._paper_template,
            )
        except Exception as e:
            logger.error("Export exam paper failed: %s", e)
            show_error(self, "导出失败", str(e))
            return
        QMessageBox.information(
            self,
            "导出成功",
            "已导出以下文档：\n" + "\n".join(str(p) for p in paths),
        )
