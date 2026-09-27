"""插件管理面板：启用 / 禁用内置与外部插件，查看加载状态与错误。

插件由 ``plos.plugins.PluginManager`` 统一管理：
- 内置插件随程序分发（plos/plugins/builtin/），不可卸载；
- 外部插件位于数据目录 plugins/ 下，可从插件库一键安装，也可卸载；
- 启用状态持久化在数据目录 plugins_config.json；
- 启用立即注册导航入口与面板，禁用立即移除（无需重启）。
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..plugins import PluginManager
from ..utils.logger import get_logger
from ..utils.paths import get_data_dir
from .ui_utils import ask_confirm, show_info, theme_colors

logger = get_logger("ui.plugin_panel")


class _InstallDialog(QDialog):
    """插件库安装对话框：勾选要安装的外部插件。"""

    def __init__(self, items: List[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("从插件库安装")
        self.resize(520, 380)
        self._checkboxes: List[tuple] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel("勾选要安装的插件，安装后会立即出现在左侧导航中（默认启用）。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.list_widget = QListWidget()
        for item in items:
            entry = QListWidgetItem()
            checkbox = QCheckBox(
                f"{item['name']}　v{item['version']}"
                + ("　（已安装）" if item.get("installed") else "")
            )
            checkbox.setEnabled(not item.get("installed"))
            if item.get("description"):
                checkbox.setToolTip(item["description"])
            self.list_widget.addItem(entry)
            self.list_widget.setItemWidget(entry, checkbox)
            self._checkboxes.append((item["plugin_id"], checkbox, bool(item.get("installed"))))
        layout.addWidget(self.list_widget, 1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        install_btn = QPushButton("安装选中")
        install_btn.clicked.connect(self.accept)
        button_row.addWidget(install_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def selected_ids(self) -> List[str]:
        return [
            plugin_id
            for plugin_id, checkbox, installed in self._checkboxes
            if checkbox.isChecked() and not installed
        ]


class PluginPanel(QWidget):
    """插件管理面板：展示插件清单并支持启用 / 禁用 / 安装 / 卸载 / 重新扫描。"""

    def __init__(self, plugin_manager: Optional[PluginManager] = None) -> None:
        super().__init__()
        self._manager = plugin_manager
        self._external_dir = get_data_dir() / "plugins"
        self._build_ui()
        self.refresh_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("插件扩展管理")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel(
            "插件可为 PLOS-AI 增加独立功能页（如倒计时、换算器、计算器等）。\n"
            "启用 / 禁用即时生效，状态自动记住，下次启动保持一致。\n"
            "内置插件随程序分发；外部插件位于数据目录，可从插件库一键安装或卸载。\n"
            f"外部插件目录：{self._external_dir}\n"
            "自己编写插件：在该目录下新建 <插件名>/ 文件夹，放入 plugin.json 清单\n"
            "（字段 name / version / entry / description）与入口 main.py，"
            "入口暴露 register(ctx) 并调用 ctx.register_panel(...) 注册页面。"
        )
        desc.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 13px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        btn_row = QHBoxLayout()
        self.store_btn = QPushButton("📦 从插件库安装")
        self.store_btn.setToolTip("安装插件库中提供的外部插件")
        self.store_btn.clicked.connect(self._on_open_store)
        btn_row.addWidget(self.store_btn)

        self.refresh_btn = QPushButton("⟳ 重新扫描")
        self.refresh_btn.setToolTip("扫描插件目录，加载新插件并应用启用状态")
        self.refresh_btn.clicked.connect(self._on_rescan)
        btn_row.addWidget(self.refresh_btn)

        self.open_dir_btn = QPushButton("打开外部插件目录")
        self.open_dir_btn.clicked.connect(self._open_plugin_dir)
        btn_row.addWidget(self.open_dir_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["插件名称", "来源", "版本", "运行状态", "描述", "操作"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme_colors()['fg_secondary']}; font-size: 12px;")
        layout.addWidget(self.status_label)

    def refresh_data(self) -> None:
        """刷新插件列表（主窗口切换账号时也会调用）。"""
        self._refresh()

    def _refresh(self) -> None:
        try:
            if self._manager is None:
                self.status_label.setText("插件管理器未初始化")
                return
            plugins = self._manager.list_plugins()
            self.table.setRowCount(len(plugins))
            running = 0
            external = 0
            for row, info in enumerate(plugins):
                name_item = QTableWidgetItem(info.name)
                name_item.setToolTip(info.plugin_id)
                self.table.setItem(row, 0, name_item)
                self.table.setItem(row, 1, QTableWidgetItem("内置" if info.builtin else "外部"))
                self.table.setItem(row, 2, QTableWidgetItem(info.version))
                status_item = QTableWidgetItem(info.status_text)
                if info.error:
                    status_item.setForeground(QColor(theme_colors()["error"]))
                elif info.loaded:
                    status_item.setForeground(QColor(theme_colors()["success"]))
                self.table.setItem(row, 3, status_item)
                self.table.setItem(row, 4, QTableWidgetItem(info.description))
                self.table.setCellWidget(row, 5, self._build_actions(info))
                if info.loaded:
                    running += 1
                if not info.builtin:
                    external += 1
            if plugins:
                self.status_label.setText(
                    f"共 {len(plugins)} 个插件（外部 {external} 个），{running} 个运行中。"
                    "禁用后导航入口立即移除，启用后立即出现。"
                )
            else:
                self.status_label.setText("未发现任何插件")
        except Exception as e:
            logger.error("Refresh plugins failed: %s", e)
            self.status_label.setText(f"刷新失败：{e}")

    def _build_actions(self, info) -> QWidget:
        """操作列：启用/禁用，外部插件额外提供卸载。"""
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toggle = QPushButton("禁用" if info.loaded else "启用")
        toggle.setMinimumWidth(70)
        toggle.clicked.connect(lambda _=False, pid=info.plugin_id: self._on_toggle(pid))
        layout.addWidget(toggle)

        if not info.builtin:
            uninstall = QPushButton("卸载")
            uninstall.setMinimumWidth(60)
            uninstall.clicked.connect(
                lambda _=False, pid=info.plugin_id: self._on_uninstall(pid)
            )
            layout.addWidget(uninstall)
        layout.addStretch()
        return holder

    # ------------------------------------------------------------------
    def _on_toggle(self, plugin_id: str) -> None:
        if self._manager is None:
            return
        try:
            info = self._manager._plugins.get(plugin_id)
            if info is None:
                return
            if info.loaded:
                self._manager.disable(plugin_id)
            else:
                self._manager.enable(plugin_id)
        except Exception as e:
            logger.error("Toggle plugin %s failed: %s", plugin_id, e)
        self._refresh()

    def _on_open_store(self) -> None:
        if self._manager is None:
            return
        try:
            items = self._manager.list_store_plugins()
        except Exception as e:
            logger.error("List store plugins failed: %s", e)
            items = []
        pending = [item for item in items if not item.get("installed")]
        if not items:
            show_info(self, "插件库", "插件库暂无可安装的插件。")
            return
        if not pending:
            show_info(self, "插件库", "插件库中的插件都已安装。")
            return
        dialog = _InstallDialog(items, self)
        dialog.exec()
        selected = dialog.selected_ids()
        if selected:
            self._install_plugins(selected)

    def _install_plugins(self, plugin_ids: List[str]) -> int:
        """安装给定的插件库插件，返回成功数量。"""
        if self._manager is None:
            return 0
        done = 0
        failed: List[str] = []
        for plugin_id in plugin_ids:
            try:
                if self._manager.install_from_store(plugin_id):
                    done += 1
                else:
                    failed.append(plugin_id)
            except Exception as e:
                logger.error("Install %s failed: %s", plugin_id, e)
                failed.append(plugin_id)
        self._refresh()
        if failed:
            self.status_label.setText(f"已安装 {done} 个插件；失败：{', '.join(failed)}")
        elif done:
            self.status_label.setText(f"已安装 {done} 个插件，导航入口已添加。")
        return done

    def _on_uninstall(self, plugin_id: str) -> None:
        self._uninstall_plugin(plugin_id)

    def _uninstall_plugin(self, plugin_id: str, confirm: bool = True) -> bool:
        """卸载外部插件（confirm=False 时跳过确认，便于自动化测试）。"""
        if self._manager is None:
            return False
        if confirm and not ask_confirm(
            self,
            "确认卸载",
            f"确定卸载外部插件「{plugin_id}」吗？\n"
            "插件文件将从数据目录中删除，其导航入口会立即移除。",
        ):
            return False
        try:
            ok = self._manager.uninstall(plugin_id)
        except Exception as e:
            logger.error("Uninstall %s failed: %s", plugin_id, e)
            ok = False
        self._refresh()
        if ok:
            self.status_label.setText(f"已卸载插件：{plugin_id}")
        else:
            self.status_label.setText(f"卸载失败：{plugin_id}（仅外部插件可卸载）")
        return ok

    def _on_rescan(self) -> None:
        if self._manager is not None:
            try:
                self._manager.rescan()
            except Exception as e:
                logger.error("Rescan plugins failed: %s", e)
        self._refresh()

    def _open_plugin_dir(self) -> None:
        try:
            self._external_dir.mkdir(parents=True, exist_ok=True)
            import os

            if sys.platform.startswith("win"):
                os.startfile(str(self._external_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", str(self._external_dir)])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(self._external_dir)])
        except Exception as e:
            logger.warning("Open plugin dir failed: %s", e)
