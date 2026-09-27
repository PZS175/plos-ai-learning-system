"""内置运行日志面板。

实时滚动查看日志、搜索关键词、一键打包全部日志为 zip。
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..utils.logger import get_logger
from ..utils.paths import get_logs_dir

logger = get_logger("ui.log_panel")


class LogPanel(QWidget):
    """系统日志查看面板。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._log_file = Path(get_logs_dir()) / "plos.log"
        self._last_pos = 0
        self._build_ui()
        self._load_logs()
        # 每 2 秒刷新一次日志
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._load_logs)
        self._timer.start(2000)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("系统日志")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        # 工具栏
        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索日志关键词...")
        self.search_input.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_input)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._load_logs)
        toolbar.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("打包日志为 ZIP")
        self.export_btn.clicked.connect(self._export_zip)
        toolbar.addWidget(self.export_btn)

        layout.addLayout(toolbar)

        # 日志显示区
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.text_edit)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #86909C; font-size: 11px;")
        layout.addWidget(self.status_label)

    def _load_logs(self) -> None:
        try:
            if not self._log_file.exists():
                self.text_edit.setPlainText("(暂无日志文件)")
                return
            text = self._log_file.read_text(encoding="utf-8", errors="replace")
            self._full_text = text
            self._apply_filter()
            self.status_label.setText(
                f"日志文件：{self._log_file.name} | 大小：{self._log_file.stat().st_size / 1024:.1f} KB"
            )
        except Exception as e:
            self.text_edit.setPlainText(f"读取日志失败：{e}")

    def _apply_filter(self) -> None:
        keyword = self.search_input.text().strip()
        text = getattr(self, "_full_text", "")
        if keyword:
            lines = text.splitlines()
            filtered = [ln for ln in lines if keyword in ln]
            text = "\n".join(filtered)
        self.text_edit.setPlainText(text)
        # 滚动到底部
        self.text_edit.verticalScrollBar().setValue(self.text_edit.verticalScrollBar().maximum())

    def _export_zip(self) -> None:
        logs_dir = Path(get_logs_dir())
        default_name = f"plos_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "打包日志", str(logs_dir / default_name), "ZIP 文件 (*.zip)"
        )
        if not path:
            return
        try:
            log_files = list(logs_dir.glob("plos.log*"))
            if not log_files:
                QMessageBox.information(self, "提示", "没有可打包的日志文件")
                return
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in log_files:
                    zf.write(f, arcname=f.name)
            QMessageBox.information(self, "成功", f"日志已打包到：\n{path}")
        except Exception as e:
            logger.error("Failed to export logs: %s", e)
            QMessageBox.critical(self, "失败", f"打包日志失败：{e}")
