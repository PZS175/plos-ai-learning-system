"""账号选择/登录/注册对话框。

程序启动时弹出，用户选择账号进入主程序。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services.user_service import UserService
from ..utils.logger import get_logger
from .ui_utils import shake_widget

logger = get_logger("ui.login_dialog")


class LoginDialog(QDialog):
    """账号选择对话框。

    选择账号并验证密码后，发出 ``user_selected(user_id)`` 信号。
    """

    user_selected = pyqtSignal(int)

    def __init__(self, user_service: UserService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.user_service = user_service
        self.setWindowTitle("选择账号")
        self.setMinimumSize(380, 420)
        self._build_ui()
        self._refresh_users()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("PLOS-AI 登录")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("选择一个账号进入，或创建新账号")
        subtitle.setStyleSheet("color: #86909C;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        self.user_list = QListWidget()
        self.user_list.itemDoubleClicked.connect(self._on_login)
        layout.addWidget(self.user_list, stretch=1)

        # 密码输入
        pwd_layout = QHBoxLayout()
        pwd_layout.addWidget(QLabel("密码："))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("无密码账号留空")
        pwd_layout.addWidget(self.password_input)
        layout.addLayout(pwd_layout)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.login_btn = QPushButton("登录")
        self.login_btn.clicked.connect(self._on_login)
        self.new_btn = QPushButton("新建账号")
        self.new_btn.clicked.connect(self._on_new_user)
        self.delete_btn = QPushButton("删除账号")
        self.delete_btn.clicked.connect(self._on_delete_user)
        btn_layout.addWidget(self.new_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.login_btn)
        layout.addLayout(btn_layout)

    def _refresh_users(self) -> None:
        self.user_list.clear()
        for u in self.user_service.list_users():
            item = QListWidgetItem(f"{u['nickname']}（@{u['username']}）")
            item.setData(Qt.ItemDataRole.UserRole, u["id"])
            self.user_list.addItem(item)
        if self.user_list.count() > 0:
            self.user_list.setCurrentRow(0)

    def _on_login(self) -> None:
        item = self.user_list.currentItem()
        if not item:
            QMessageBox.warning(self, "提示", "请选择一个账号")
            return
        user_id = item.data(Qt.ItemDataRole.UserRole)
        password = self.password_input.text()
        if not self.user_service.verify_password(user_id, password):
            # 苹果式：无效输入给轻微抖动提醒，并清空以便重新输入
            shake_widget(self.password_input)
            QMessageBox.critical(self, "登录失败", "密码错误，请重试")
            return
        logger.info("User logged in: id=%d", user_id)
        self.accept()
        self.user_selected.emit(user_id)

    def _on_new_user(self) -> None:
        username, ok = QInputDialog.getText(self, "新建账号", "用户名：")
        if not ok or not username.strip():
            return
        nickname, ok2 = QInputDialog.getText(self, "新建账号", "昵称（可留空）：")
        if not ok2:
            return
        password, ok3 = QInputDialog.getText(
            self, "新建账号", "密码（可留空表示无密码）：", QLineEdit.EchoMode.Password
        )
        if not ok3:
            return
        try:
            user_id = self.user_service.create_user(username.strip(), nickname.strip() or username.strip(), password)
            QMessageBox.information(self, "成功", f"账号创建成功，ID：{user_id}")
            self._refresh_users()
        except Exception as e:
            logger.error("Create user failed: %s", e)
            QMessageBox.critical(self, "失败", f"创建账号失败：{e}")

    def _on_delete_user(self) -> None:
        item = self.user_list.currentItem()
        if not item:
            QMessageBox.warning(self, "提示", "请选择要删除的账号")
            return
        user_id = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "确认删除",
            "删除账号将清除该账号的所有数据（错题、笔记、学习记录等），且不可恢复。\n\n确定删除？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.user_service.delete_user(user_id)
            QMessageBox.information(self, "成功", "账号已删除")
            self._refresh_users()
        except Exception as e:
            logger.error("Delete user failed: %s", e)
            QMessageBox.critical(self, "失败", f"删除账号失败：{e}")


__all__ = ["LoginDialog"]
