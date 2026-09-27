"""学习诊断/复盘报告面板。

一键生成报告，预览 Markdown 内容，并支持导出为 Markdown 文件。
兼容深浅色主题，玻璃拟态 UI。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..services.diagnostic_report_service import DiagnosticReportService
from ..utils.logger import get_logger
from .markdown_browser import MarkdownBrowser
from .ui_utils import create_section_title, apply_glass_style, create_empty_state_widget

logger = get_logger("ui.diagnostic_report_panel")


class DiagnosticReportPanel(QWidget):
    """学习诊断复盘报告面板。"""

    def __init__(
        self,
        report_service: DiagnosticReportService,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.report_service = report_service
        self._current_report: Optional[Dict[str, Any]] = None
        self._build_ui()
        self._generate_report()

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧概览卡片
        left_panel = QWidget()
        left_panel.setProperty("glass", True)
        apply_glass_style(left_panel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        title = create_section_title("学习诊断报告")
        left_layout.addWidget(title)

        hint = QLabel(
            "基于错题本、练习记录与学习时长自动生成复盘报告，"
            "包含掌握牢固知识点、知识盲区、高频错误类型与后续学习建议。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8A93A8; font-size: 12px;")
        left_layout.addWidget(hint)

        self.generate_btn = QPushButton("重新生成报告")
        self.generate_btn.clicked.connect(self._generate_report)
        left_layout.addWidget(self.generate_btn)

        self.export_btn = QPushButton("导出 Markdown")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_report)
        left_layout.addWidget(self.export_btn)

        self.summary_container = QVBoxLayout()
        self.summary_container.setSpacing(8)
        self.summary_container.setContentsMargins(0, 0, 0, 0)
        summary_widget = QWidget()
        summary_widget.setLayout(self.summary_container)
        left_layout.addWidget(summary_widget)

        left_layout.addStretch()
        splitter.addWidget(left_panel)

        # 右侧报告预览
        right_panel = QWidget()
        right_panel.setProperty("glass", True)
        apply_glass_style(right_panel)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.browser = MarkdownBrowser()
        right_layout.addWidget(self.browser)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 900])

    def refresh_data(self) -> None:
        """用户切换后清空旧报告，避免跨用户数据混淆。"""
        self._current_report = None
        self.export_btn.setEnabled(False)
        self._render_summary()
        self._render_report()

    def _generate_report(self) -> None:
        try:
            self._current_report = self.report_service.generate_report()
        except Exception as e:
            logger.error("Failed to generate diagnostic report: %s", e)
            QMessageBox.warning(self, "生成失败", f"生成诊断报告失败：{e}")
            self._current_report = None
            return

        self.export_btn.setEnabled(True)
        self._render_summary()
        self._render_report()

    def _render_summary(self) -> None:
        # 清空旧摘要
        while self.summary_container.count():
            item = self.summary_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._current_report:
            self.summary_container.addWidget(create_empty_state_widget("暂无诊断报告，生成后在此展示"))
            return

        s = self._current_report["summary"]
        for label, value in [
            ("错题总数", s["total_errors"]),
            ("已掌握错题", s["mastered_errors"]),
            ("薄弱错题", s["weak_errors"]),
            ("近 7 天学习时长", f"{s['total_study_minutes']} 分钟"),
            ("练习总次数", s["practice_total"]),
            ("练习正确率", f"{s['practice_accuracy']}%"),
        ]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            name = QLabel(label)
            name.setStyleSheet("color: #8A93A8; font-size: 12px;")
            val = QLabel(str(value))
            val.setStyleSheet("font-weight: bold; font-size: 13px;")
            row_layout.addWidget(name)
            row_layout.addStretch()
            row_layout.addWidget(val)
            self.summary_container.addWidget(row)

    def _render_report(self) -> None:
        if not self._current_report:
            self.browser.set_plain_text("点击「重新生成报告」查看学习复盘")
            return
        markdown = self.report_service.render_markdown(self._current_report)
        self.browser.set_markdown(markdown)

    def _export_report(self) -> None:
        if not self._current_report:
            return
        default_name = f"学习复盘报告_{self._current_report.get('generated_at', '')}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出报告", str(Path.home() / default_name), "Markdown (*.md)"
        )
        if not path:
            return
        try:
            self.report_service.export_markdown(self._current_report, Path(path))
            QMessageBox.information(self, "导出成功", f"报告已保存至：\n{path}")
        except Exception as e:
            logger.error("Failed to export report: %s", e)
            QMessageBox.warning(self, "导出失败", str(e))
