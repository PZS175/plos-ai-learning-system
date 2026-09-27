"""批量题库导入对话框：选择文件 → 解析预览（可修改）→ 确认入库。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services.question_import_service import QuestionImportService
from ..utils.logger import get_logger
from .ui_utils import theme_colors

logger = get_logger("ui.question_import_dialog")


class QuestionImportDialog(QDialog):
    """批量导入题库预览对话框。"""

    def __init__(
        self,
        import_service: Optional[QuestionImportService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量导入题库")
        self.resize(900, 600)
        self.import_service = import_service or QuestionImportService()
        self._questions = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hint = QLabel(
            "支持 Excel(.xlsx) / Word(.docx) 题库文件。\n"
            "自动识别字段：题干、选项、标准答案、得分要点、知识点、难度、题型。\n"
            "选项写法二选一：① 独立列 A/B/C/D；② 合并到一格，用换行或分号分隔，"
            "如「A. 选项一；B. 选项二」（竖线 | 分隔不会被识别）。\n"
            "导入前可在下方表格中直接修改识别错误的内容，带错误标记的行不会入库。"
        )
        hint.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self.select_btn = QPushButton("选择题库文件…")
        self.select_btn.setStyleSheet(
            f"background-color: {theme_colors()['accent']}; color: white; padding: 6px 16px; border-radius: 4px;"
        )
        self.select_btn.clicked.connect(self._select_file)
        btn_row.addWidget(self.select_btn)
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet(f"color: {theme_colors()['fg_secondary']};")
        btn_row.addWidget(self.file_label, 1)
        layout.addLayout(btn_row)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["题干", "选项(每行一个)", "标准答案", "得分要点", "知识点", "难度(1-5)", "题型"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(True)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.status_label)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(self.cancel_btn)
        self.confirm_btn = QPushButton("确认导入")
        self.confirm_btn.setStyleSheet(
            f"background-color: {theme_colors()['success']}; color: white; padding: 6px 20px; border-radius: 4px;"
        )
        self.confirm_btn.clicked.connect(self._confirm)
        self.confirm_btn.setEnabled(False)
        bottom_row.addWidget(self.confirm_btn)
        layout.addLayout(bottom_row)

    def _select_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择题库文件",
            "",
            "题库文件 (*.xlsx *.docx);;Excel (*.xlsx);;Word (*.docx);;所有文件 (*.*)",
        )
        if not file_path:
            return
        self.file_label.setText(Path(file_path).name)
        try:
            self._questions = self.import_service.parse_file(Path(file_path))
            self._fill_table()
        except Exception as e:
            logger.error("Import parse failed: %s", e)
            QMessageBox.critical(self, "解析失败", str(e))

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._questions))
        error_count = 0
        for row, q in enumerate(self._questions):
            is_error = bool(q.get("_error"))
            if is_error:
                error_count += 1
            values = [
                q.get("question", ""),
                "\n".join(q.get("options", [])),
                q.get("answer", ""),
                q.get("analysis", ""),
                q.get("knowledge_point", ""),
                str(q.get("difficulty", 3)),
                q.get("question_type", ""),
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if is_error:
                    item.setToolTip(q.get("_error", ""))
                    item.setBackground(__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor("#ffece8"))
                self.table.setItem(row, col, item)
            if is_error:
                self.table.setVerticalHeaderItem(row, QTableWidgetItem(f"第{q.get('_row')}行⚠"))
            else:
                self.table.setVerticalHeaderItem(row, QTableWidgetItem(f"第{q.get('_row')}行"))

        valid = len(self._questions) - error_count
        self.status_label.setText(
            f"共解析 {len(self._questions)} 题，有效 {valid} 题，错误 {error_count} 行（标红行将跳过）"
        )
        c = theme_colors()
        self.status_label.setStyleSheet(
            f"font-size: 12px; color: {c['error'] if error_count else c['success']};"
        )
        self.confirm_btn.setEnabled(valid > 0)

    def _confirm(self) -> None:
        # 把表格中用户修改后的内容回写到题目列表
        for row in range(self.table.rowCount()):
            if row >= len(self._questions):
                break
            q = self._questions[row]
            q["question"] = self.table.item(row, 0).text().strip()
            opt_text = self.table.item(row, 1).text()
            q["options"] = [line.strip() for line in opt_text.splitlines() if line.strip()]
            q["answer"] = self.table.item(row, 2).text().strip()
            q["analysis"] = self.table.item(row, 3).text().strip()
            q["knowledge_point"] = self.table.item(row, 4).text().strip()
            try:
                q["difficulty"] = max(1, min(5, int(self.table.item(row, 5).text().strip())))
            except Exception:
                q["difficulty"] = 3
            qtype = self.table.item(row, 6).text().strip()
            if qtype in ("choice", "fill", "judge", "short", "open"):
                q["question_type"] = qtype
            # 重新校验
            if not q["question"]:
                q["_error"] = "题干为空"
            elif not q["answer"] and q.get("question_type") != "open":
                q["_error"] = "缺少标准答案"
            else:
                q.pop("_error", None)

        try:
            saved = self.import_service.commit_questions(self._questions)
            QMessageBox.information(self, "导入成功", f"成功导入 {saved} 道题目到题库。")
            self.accept()
        except Exception as e:
            logger.error("Commit questions failed: %s", e)
            QMessageBox.critical(self, "导入失败", str(e))
