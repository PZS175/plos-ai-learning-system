"""教材缓存管理面板：查看离线教材缓存、删除/清空、磁盘上限设置。

低配机器（≤7GB）不创建入口（main_window 按硬件分级隐藏），
面板自身所有 IO 操作均包 try-except。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services.textbook_cache_service import TextbookCacheService
from ..utils.logger import get_logger
from .interactions import friendly_error_message
from .ui_utils import ask_confirm, show_info, show_warning, theme_colors

logger = get_logger("ui.textbook_cache_panel")


def _format_size(num_bytes: int) -> str:
    """字节数转可读字符串。"""
    try:
        size = float(num_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
    except Exception:
        return "0 B"
    return "0 B"


class TextbookCachePanel(QWidget):
    """教材缓存管理面板。"""

    def __init__(self, cache_service: Optional[TextbookCacheService] = None) -> None:
        super().__init__()
        self.cache_service = cache_service or TextbookCacheService()
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("教材缓存管理")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        hint = QLabel(
            "下载的教材会保存到本地缓存，离线无网络也可阅读。\n"
            "低配模式下缓存功能自动禁用，仅支持在线预览。"
        )
        hint.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 13px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 统计 + 上限设置行
        ctrl_row = QHBoxLayout()
        self.usage_label = QLabel("已用空间：计算中...")
        self.usage_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        ctrl_row.addWidget(self.usage_label)
        ctrl_row.addStretch()
        ctrl_row.addWidget(QLabel("缓存上限(MB)："))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(50, 10000)
        self.limit_spin.setValue(self.cache_service._get_limit_mb())
        ctrl_row.addWidget(self.limit_spin)
        self.save_limit_btn = QPushButton("保存上限")
        self.save_limit_btn.clicked.connect(self._save_limit)
        ctrl_row.addWidget(self.save_limit_btn)
        layout.addLayout(ctrl_row)

        # 操作按钮行
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("⟳ 刷新")
        self.refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self.refresh_btn)
        self.clear_btn = QPushButton("一键清空全部缓存")
        self.clear_btn.setStyleSheet(f"color: {theme_colors()['error']};")
        self.clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 缓存列表
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["教材名称", "学科", "大小", "下载时间", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

    def _refresh(self) -> None:
        try:
            items = self.cache_service.list_cache()
            total = self.cache_service.get_total_size()
            limit = self.cache_service._get_limit_mb()
            self.usage_label.setText(
                f"已用空间：{_format_size(total)} / 上限 {limit} MB"
            )

            self.table.setRowCount(len(items))
            for row, item in enumerate(items):
                self.table.setItem(row, 0, QTableWidgetItem(item.get("name", "")))
                self.table.setItem(row, 1, QTableWidgetItem(item.get("subject", "") or "-"))
                self.table.setItem(row, 2, QTableWidgetItem(_format_size(item.get("file_size", 0))))
                self.table.setItem(row, 3, QTableWidgetItem(item.get("downloaded_at", "")))

                del_btn = QPushButton("删除")
                del_btn.setStyleSheet(f"color: {theme_colors()['error']};")
                del_btn.clicked.connect(
                    lambda checked, cid=item.get("id", 0): self._delete_one(cid)
                )
                self.table.setCellWidget(row, 4, del_btn)
        except Exception as e:
            logger.error("Refresh textbook cache failed: %s", e)
            self.usage_label.setText(f"刷新失败：{e}")

    def _delete_one(self, cache_id: int) -> None:
        try:
            if not ask_confirm(self, "删除缓存", "确定删除这本教材的本地缓存吗？删除后需重新下载才能离线阅读。"):
                return
            if self.cache_service.delete_cache(cache_id):
                show_info(self, "删除成功", "教材缓存已删除。")
            else:
                show_warning(self, "删除失败", "未找到该缓存记录。")
            self._refresh()
        except Exception as e:
            logger.error("Delete cache item failed: %s", e)
            show_warning(self, "删除失败", friendly_error_message(e, "缓存删除失败，请稍后重试"))

    def _clear_all(self) -> None:
        try:
            if not ask_confirm(self, "清空缓存", "确定清空全部教材缓存吗？此操作不可恢复！"):
                return
            count = self.cache_service.clear_all()
            show_info(self, "清空成功", f"已清空 {count} 本教材缓存。")
            self._refresh()
        except Exception as e:
            logger.error("Clear all cache failed: %s", e)
            show_warning(self, "清空失败", friendly_error_message(e, "缓存清理失败，请稍后重试"))

    def _save_limit(self) -> None:
        try:
            self.cache_service.set_limit_mb(self.limit_spin.value())
            show_info(self, "保存成功", f"缓存上限已设置为 {self.limit_spin.value()} MB。")
            self._refresh()
        except Exception as e:
            logger.error("Save cache limit failed: %s", e)
            show_warning(self, "保存失败", friendly_error_message(e, "缓存上限保存失败，请稍后重试"))
