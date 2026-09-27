"""系统托盘图标。

提供最小化到托盘、显示窗口、退出等菜单。
"""

from __future__ import annotations

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..utils.logger import get_logger

logger = get_logger("ui.tray_icon")


class TrayIcon(QSystemTrayIcon):
    """系统托盘图标。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolTip("PLOS AI")
        # 如果没有图标文件，使用默认样式图标
        self.setIcon(QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_ComputerIcon))

        self.menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._show_window)
        self.menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit)
        self.menu.addAction(quit_action)

        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _show_window(self) -> None:
        if self.parent():
            self.parent().showNormal()
            self.parent().raise_()
            self.parent().activateWindow()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _quit(self) -> None:
        logger.info("Quit from tray menu")
        QApplication.instance().quit()
