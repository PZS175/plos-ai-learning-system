"""错题本「导出试卷」对话框。

一次配置完格式、选题范围、排版选项、文件名与保存位置，点击生成后
在后台线程渲染文档，界面保持可操作，右下角 Toast 反馈结果。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services.errorpaper_service import (
    PAPER_TITLE,
    ErrorPaperService,
    default_filename,
    sanitize_filename,
)
from ..utils.logger import get_logger
from .interactions import friendly_error_message
from .math_text import attach_math_delegate
from .ui_utils import ButtonBusy, set_primary_button_style, show_warning
from .workers import CallableWorker, ThreadPool

logger = get_logger("ui.error_paper_dialog")

#: 简答/主观题预留的作答空白行数
_BLANK_LINES = 6


class ErrorPaperExportDialog(QDialog):
    """错题试卷导出配置对话框（生成过程走后台线程）。"""

    export_succeeded = pyqtSignal(str)
    export_failed = pyqtSignal(str)
    _progress_changed = pyqtSignal(int, str)

    def __init__(
        self,
        paper_service: ErrorPaperService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.paper_service = paper_service
        self._busy = False
        self._errors: List[Dict[str, Any]] = []

        self.setWindowTitle("导出试卷")
        self.resize(680, 720)
        self._build_ui()
        self._progress_changed.connect(self._on_progress)
        self._load_data()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("导出错题试卷")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel("按中小学试卷版式生成 Word / PDF，可直接打印或二次编辑。")
        subtitle.setObjectName("card_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll, 1)

        container = QWidget()
        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        scroll.setWidget(container)

        body.addWidget(self._build_format_group())
        body.addWidget(self._build_scope_group())
        body.addWidget(self._build_layout_group())
        body.addWidget(self._build_output_group())

        # 局部加载区（生成期间才显示）
        self.progress_row = QWidget()
        progress_layout = QVBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("card_subtitle")
        progress_layout.addWidget(self.progress_label)
        self.progress_row.setVisible(False)
        layout.addWidget(self.progress_row)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(self.cancel_btn)
        self.export_btn = QPushButton("导出生成")
        set_primary_button_style(self.export_btn)
        self.export_btn.setMinimumWidth(120)
        self.export_btn.clicked.connect(self._start_export)
        self._export_busy = ButtonBusy(self.export_btn, "正在生成…")
        bottom.addWidget(self.export_btn)
        layout.addLayout(bottom)

    def _group(self, title: str) -> tuple:
        box = QGroupBox(title)
        box.setProperty("card", True)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(14, 14, 14, 14)
        inner.setSpacing(8)
        return box, inner

    def _build_format_group(self):
        box, inner = self._group("① 导出格式")
        self.radio_docx = QRadioButton("Word（.docx，可二次编辑）")
        self.radio_docx.setChecked(True)
        self.radio_pdf = QRadioButton("PDF（不可编辑，打印效果稳定）")
        inner.addWidget(self.radio_docx)
        inner.addWidget(self.radio_pdf)
        return box

    def _build_scope_group(self):
        box, inner = self._group("② 选题范围")

        self.radio_all = QRadioButton("全部错题")
        self.radio_all.setChecked(True)
        self.radio_all.toggled.connect(self._on_scope_changed)
        inner.addWidget(self.radio_all)

        kp_row = QHBoxLayout()
        self.radio_kp = QRadioButton("按知识点筛选：")
        self.radio_kp.toggled.connect(self._on_scope_changed)
        kp_row.addWidget(self.radio_kp)
        self.kp_combo = QComboBox()
        self.kp_combo.setEnabled(False)
        kp_row.addWidget(self.kp_combo, 1)
        inner.addLayout(kp_row)

        self.radio_custom = QRadioButton("自定义选择题目")
        self.radio_custom.toggled.connect(self._on_scope_changed)
        inner.addWidget(self.radio_custom)

        custom_row = QHBoxLayout()
        custom_row.setContentsMargins(20, 0, 0, 0)
        custom_col = QVBoxLayout()
        custom_col.setSpacing(6)
        self.error_list = QListWidget()
        self.error_list.setMinimumHeight(150)
        self.error_list.setEnabled(False)
        self.error_list.itemChanged.connect(self._update_custom_count)
        # 题干带公式标记，单元格走混排渲染（复选框由样式照常绘制）
        self._error_math_delegate = attach_math_delegate(self.error_list, 0)
        custom_col.addWidget(self.error_list)

        pick_row = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setEnabled(False)
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        pick_row.addWidget(self.select_all_btn)
        self.clear_all_btn = QPushButton("清空")
        self.clear_all_btn.setEnabled(False)
        self.clear_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        pick_row.addWidget(self.clear_all_btn)
        self.custom_count_label = QLabel("已选 0 题")
        self.custom_count_label.setObjectName("card_subtitle")
        pick_row.addWidget(self.custom_count_label)
        pick_row.addStretch()
        custom_col.addLayout(pick_row)
        custom_row.addLayout(custom_col, 1)
        inner.addLayout(custom_row)
        return box

    def _build_layout_group(self):
        box, inner = self._group("③ 排版设置")
        self.check_answers = QCheckBox("显示答案与解析（取消勾选则只导出空白试卷）")
        self.check_answers.setChecked(True)
        inner.addWidget(self.check_answers)
        self.check_wrong_answer = QCheckBox("显示错题原错误作答记录")
        self.check_wrong_answer.setToolTip(
            "取该错题最近一次主观题批改中记录的作答内容，没有记录时自动跳过"
        )
        inner.addWidget(self.check_wrong_answer)
        return box

    def _build_output_group(self):
        box, inner = self._group("④ 文件名与保存位置")

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("文件名："))
        self.name_edit = QLineEdit(default_filename())
        self.name_edit.setPlaceholderText("不含扩展名")
        name_row.addWidget(self.name_edit, 1)
        self._suffix_label = QLabel(".docx")
        self._suffix_label.setObjectName("card_subtitle")
        name_row.addWidget(self._suffix_label)
        inner.addLayout(name_row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("保存到："))
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_edit.setPlaceholderText("请选择保存位置")
        dir_row.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("选择…")
        browse_btn.clicked.connect(self._choose_dir)
        dir_row.addWidget(browse_btn)
        inner.addLayout(dir_row)

        self.radio_docx.toggled.connect(
            lambda checked: self._suffix_label.setText(".docx" if checked else ".pdf")
        )
        return box

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def _load_data(self) -> None:
        """填充知识点下拉与自定义选题列表，并给出默认保存目录。"""
        try:
            self.kp_combo.addItems(self.paper_service.list_knowledge_points())
        except Exception as e:
            logger.warning("Load knowledge points failed: %s", e)
        try:
            self._errors = self.paper_service.list_errors_for_pick()
        except Exception as e:
            logger.warning("Load error list failed: %s", e)
        self._fill_error_list()

        documents = Path.home() / "Documents"
        self.dir_edit.setText(str(documents if documents.exists() else Path.home()))

        if not self._errors:
            self.radio_all.setEnabled(False)
            self.radio_kp.setEnabled(False)
            self.radio_custom.setEnabled(False)

    def _fill_error_list(self) -> None:
        type_labels = {"选择题": "选择", "填空题": "填空", "判断题": "判断"}
        for index, error in enumerate(self._errors, 1):
            qtype = type_labels.get(str(error.get("question_type") or ""), "主观")
            stem = str(error.get("question") or "").replace("\n", " ").strip()
            if len(stem) > 40:
                stem = stem[:40] + "…"
            item = QListWidgetItem(f"{index}. [{qtype}] {stem}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, int(error["id"]))
            self.error_list.addItem(item)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _on_scope_changed(self) -> None:
        use_kp = self.radio_kp.isChecked()
        use_custom = self.radio_custom.isChecked()
        self.kp_combo.setEnabled(use_kp)
        self.error_list.setEnabled(use_custom)
        self.select_all_btn.setEnabled(use_custom)
        self.clear_all_btn.setEnabled(use_custom)
        self.custom_count_label.setVisible(use_custom)
        if use_custom:
            self._update_custom_count()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.error_list.blockSignals(True)
        for row in range(self.error_list.count()):
            self.error_list.item(row).setCheckState(state)
        self.error_list.blockSignals(False)
        self._update_custom_count()

    def _update_custom_count(self) -> None:
        self.custom_count_label.setText(f"已选 {len(self._checked_ids())} 题")

    def _checked_ids(self) -> List[int]:
        return [
            int(self.error_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.error_list.count())
            if self.error_list.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _choose_dir(self) -> None:
        current = self.dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择保存位置", current)
        if chosen:
            self.dir_edit.setText(chosen)

    # ------------------------------------------------------------------
    # 校验与导出
    # ------------------------------------------------------------------

    def _validate(self) -> Optional[str]:
        """返回错误提示；校验通过返回 None。"""
        if not self.dir_edit.text().strip():
            return "请先选择试卷的保存位置"
        if not self.name_edit.text().strip():
            return "请填写试卷文件名"
        if self.radio_kp.isChecked() and not self.kp_combo.currentText().strip():
            return "请选择要导出的知识点"
        if self.radio_custom.isChecked() and not self._checked_ids():
            return "请至少勾选一道题目"
        if not self._errors:
            return "错题本中还没有题目，请先录入错题"
        return None

    def _start_export(self) -> None:
        if self._busy:
            return
        problem = self._validate()
        if problem:
            show_warning(self, "无法导出", problem)
            return

        try:
            errors = self.paper_service.select_errors(
                scope=self._scope(),
                knowledge_point=self.kp_combo.currentText().strip(),
                error_ids=self._checked_ids(),
            )
        except Exception as e:
            logger.error("Select errors for paper failed: %s", e)
            self._show_error(friendly_error_message(e, "读取错题失败，请稍后重试"))
            return
        if not errors:
            show_warning(self, "无法导出", "所选范围内没有错题，请调整条件后重试")
            return

        target = self._target_path()
        fmt = "docx" if self.radio_docx.isChecked() else "pdf"
        options = self._options(len(errors))

        self._set_busy(True)
        self.progress_label_reset()

        # 后台线程渲染，进度经信号回到主线程刷新，UI 全程不卡死
        task = partial(
            self.paper_service.export,
            errors,
            target,
            fmt,
            options,
            None,
            self._progress_changed.emit,
        )
        worker = CallableWorker(task)
        worker.signals.result.connect(self._on_success)
        worker.signals.error.connect(self._on_error)
        ThreadPool.start_worker(worker)

    def _scope(self) -> str:
        if self.radio_kp.isChecked():
            return "knowledge_point"
        if self.radio_custom.isChecked():
            return "custom"
        return "all"

    def _options(self, total: int) -> Dict[str, Any]:
        if self.radio_kp.isChecked():
            label = f"知识点：{self.kp_combo.currentText().strip()}"
        elif self.radio_custom.isChecked():
            label = f"自定义选题（{total} 题）"
        else:
            label = "全部错题"
        return {
            "title": PAPER_TITLE,
            "with_answers": self.check_answers.isChecked(),
            "with_wrong_answer": self.check_wrong_answer.isChecked(),
            "blank_lines": _BLANK_LINES,
            "knowledge_label": label,
        }

    def _target_path(self) -> Path:
        suffix = ".docx" if self.radio_docx.isChecked() else ".pdf"
        name = sanitize_filename(self.name_edit.text())
        return Path(self.dir_edit.text().strip()) / f"{name}{suffix}"

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _progress_label_reset(self) -> None:
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setStyleSheet("")
        self.progress_label.setText("正在整理题目…")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, int(percent))))
        if message:
            self.progress_label.setText(message)

    def _on_success(self, path: Any) -> None:
        self._set_busy(False)
        self.export_succeeded.emit(str(path))
        self.accept()

    def _on_error(self, raw: str) -> None:
        """失败时把友好文案就地显示在进度区，不弹代码级报错堆栈。"""
        self._set_busy(False)
        self._show_error(
            friendly_error_message(raw, "导出失败，请检查保存位置是否可写后重试")
        )

    def _show_error(self, message: str) -> None:
        """在弹窗内就地展示失败原因（含权限不足 / 路径不存在）。"""
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"导出失败：{message}")
        self.progress_row.setVisible(True)
        self.export_failed.emit(message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress_row.setVisible(busy or bool(self.progress_label.text()))
        self.cancel_btn.setEnabled(not busy)
        self.radio_docx.setEnabled(not busy)
        self.radio_pdf.setEnabled(not busy)
        has_errors = bool(self._errors)
        self.radio_all.setEnabled(not busy and has_errors)
        self.radio_kp.setEnabled(not busy and has_errors)
        self.radio_custom.setEnabled(not busy and has_errors)
        self.check_answers.setEnabled(not busy)
        self.check_wrong_answer.setEnabled(not busy)
        self.name_edit.setEnabled(not busy)
        if busy:
            self._export_busy.start()
        else:
            self._export_busy.restore()

    def closeEvent(self, event) -> None:
        """生成过程中不允许关闭，避免后台任务写到一半被打断。"""
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
