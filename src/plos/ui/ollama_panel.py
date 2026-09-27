"""Ollama 本地模型管理面板：查看已下载模型、启动/停止模型、服务状态。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services.ollama_service import OllamaService
from ..utils.logger import get_logger
from .ui_utils import show_info, show_warning, theme_colors

logger = get_logger("ui.ollama_panel")


class OllamaManagerPanel(QWidget):
    """Ollama 本地模型管理面板。"""

    def __init__(self, ollama_service: Optional[OllamaService] = None) -> None:
        super().__init__()
        self.ollama_service = ollama_service or OllamaService()
        self._build_ui()
        # 定时刷新状态
        self._timer = QTimer(self)
        self._timer.setInterval(10000)  # 10 秒
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 状态栏
        status_row = QHBoxLayout()
        self.status_label = QLabel("● 检测中...")
        self.status_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        status_row.addWidget(self.status_label)
        status_row.addStretch()

        self.refresh_btn = QPushButton("⟳ 刷新")
        self.refresh_btn.clicked.connect(self._refresh)
        status_row.addWidget(self.refresh_btn)
        layout.addLayout(status_row)

        # 模型表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["模型名称", "大小", "显存预估", "状态", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)

        hint = QLabel("提示：启动模型会将其加载到显存/内存，停止模型可释放资源。")
        hint.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 12px;")
        layout.addWidget(hint)

    def _refresh(self) -> None:
        """刷新模型列表和服务状态。"""
        try:
            online = self.ollama_service.is_service_online()
            c = theme_colors()
            if online:
                self.status_label.setText("● Ollama 服务在线")
                self.status_label.setStyleSheet(f"color: {c['success']}; font-size: 13px; font-weight: bold;")
            else:
                self.status_label.setText("● Ollama 服务未连接（请先启动 Ollama）")
                self.status_label.setStyleSheet(f"color: {c['error']}; font-size: 13px; font-weight: bold;")

            models = self.ollama_service.list_models()
            running = {m["name"] for m in self.ollama_service.list_running()}

            self.table.setRowCount(len(models))
            for row, model in enumerate(models):
                name = model["name"]
                self.table.setItem(row, 0, QTableWidgetItem(name))
                self.table.setItem(row, 1, QTableWidgetItem(model.get("size", "")))
                self.table.setItem(row, 2, QTableWidgetItem(self.ollama_service.get_vram_estimate(name)))

                status_item = QTableWidgetItem("运行中" if name in running else "已停止")
                status_item.setForeground(Qt.GlobalColor.darkGreen if name in running else Qt.GlobalColor.gray)
                self.table.setItem(row, 3, status_item)

                # 操作按钮
                btn_widget = QWidget()
                btn_layout = QHBoxLayout(btn_widget)
                btn_layout.setContentsMargins(4, 2, 4, 2)
                btn_layout.setSpacing(6)

                start_btn = QPushButton("启动")
                start_btn.setFixedWidth(60)
                start_btn.clicked.connect(lambda checked, n=name: self._start_model(n))
                btn_layout.addWidget(start_btn)

                stop_btn = QPushButton("停止")
                stop_btn.setFixedWidth(60)
                stop_btn.clicked.connect(lambda checked, n=name: self._stop_model(n))
                btn_layout.addWidget(stop_btn)

                self.table.setCellWidget(row, 4, btn_widget)
        except Exception as e:
            logger.error("Failed to refresh ollama models: %s", e)
            self.status_label.setText(f"● 刷新失败：{e}")

    def _start_model(self, name: str) -> None:
        try:
            ok = self.ollama_service.start_model(name)
            if ok:
                show_info(self, "启动模型", f"正在加载模型 {name}，请稍候...")
                self._refresh()
            else:
                show_warning(self, "启动失败", f"无法启动模型 {name}，请检查 Ollama 服务是否运行。")
        except Exception as e:
            logger.error("Start model failed: %s", e)
            show_warning(self, "启动失败", str(e))

    def _stop_model(self, name: str) -> None:
        try:
            ok = self.ollama_service.stop_model(name)
            if ok:
                show_info(self, "停止模型", f"模型 {name} 已停止，资源已释放。")
                self._refresh()
            else:
                show_warning(self, "停止失败", f"无法停止模型 {name}。")
        except Exception as e:
            logger.error("Stop model failed: %s", e)
            show_warning(self, "停止失败", str(e))
