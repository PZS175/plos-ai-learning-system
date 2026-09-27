"""用户管理面板。

支持新建、切换、删除用户，展示当前登录用户信息。
所有数据按用户隔离。
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services import UserService
from ..utils.logger import get_logger
from .ui_utils import apply_glass_style, ask_confirm, create_header_widget, show_success

logger = get_logger("ui.user_panel")


class UserPanel(QWidget):
    """用户账户管理界面。"""

    def __init__(
        self,
        user_service: UserService,
        on_user_switched: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.user_service = user_service
        self.on_user_switched = on_user_switched
        self._build_ui()
        self.refresh_data()

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

        header = create_header_widget("用户管理", "切换、新建或删除账户，数据完全隔离")
        layout.addWidget(header)

        # 当前用户卡片
        current_card = QWidget()
        current_card.setProperty("glass", True)
        apply_glass_style(current_card)
        current_layout = QVBoxLayout(current_card)
        self.current_label = QLabel("当前用户：")
        self.current_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        current_layout.addWidget(self.current_label)
        layout.addWidget(current_card)

        # 新建用户
        new_card = QWidget()
        new_card.setProperty("glass", True)
        apply_glass_style(new_card)
        new_layout = QHBoxLayout(new_card)
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("用户名")
        self.nickname_edit = QLineEdit()
        self.nickname_edit.setPlaceholderText("昵称")
        add_btn = QPushButton("新建用户")
        add_btn.setObjectName("primary_btn")
        add_btn.clicked.connect(self._on_add_user)
        new_layout.addWidget(QLabel("新建："))
        new_layout.addWidget(self.username_edit)
        new_layout.addWidget(self.nickname_edit)
        new_layout.addWidget(add_btn)
        layout.addWidget(new_card)

        # 用户列表
        list_card = QWidget()
        list_card.setProperty("glass", True)
        apply_glass_style(list_card)
        list_layout = QVBoxLayout(list_card)
        self.user_list = QListWidget()
        self.user_list.setMinimumHeight(300)
        list_layout.addWidget(self.user_list)

        btn_layout = QHBoxLayout()
        switch_btn = QPushButton("切换选中用户")
        switch_btn.setObjectName("primary_btn")
        switch_btn.clicked.connect(self._on_switch_user)
        delete_btn = QPushButton("删除选中用户")
        delete_btn.setObjectName("danger_btn")
        delete_btn.clicked.connect(self._on_delete_user)
        btn_layout.addWidget(switch_btn)
        btn_layout.addWidget(delete_btn)
        btn_layout.addStretch()
        list_layout.addLayout(btn_layout)
        layout.addWidget(list_card, 1)

    def refresh_data(self) -> None:
        """刷新用户列表和当前用户。"""
        current = self.user_service.get_current_user()
        if current:
            self.current_label.setText(
                f"当前用户：{current.get('nickname', '')} ({current.get('username', '')})"
            )
        else:
            self.current_label.setText("当前用户：未登录")

        self.user_list.clear()
        for user in self.user_service.list_users():
            uid = user["id"]
            overview = self._user_overview(uid)
            marker = " ●" if uid == self.user_service.get_current_user_id() else ""
            item = QListWidgetItem(
                f"{user['nickname']} ({user['username']}){marker}\n"
                f"    {overview}"
            )
            item.setData(Qt.ItemDataRole.UserRole, uid)
            item.setToolTip(overview)
            self.user_list.addItem(item)

    def _user_overview(self, user_id: int) -> str:
        """汇总一个用户的资源与最近学习情况（查询失败的字段留空显示）。"""
        db = self.user_service.db

        def count(sql: str) -> int:
            try:
                return int(db.fetchone(sql, (user_id,))["c"] or 0)
            except Exception:
                return 0

        errors = count("SELECT COUNT(*) AS c FROM error_book WHERE user_id = ?")
        cards = count("SELECT COUNT(*) AS c FROM flashcards WHERE user_id = ?")
        docs = count("SELECT COUNT(*) AS c FROM documents WHERE user_id = ?")
        minutes = count(
            "SELECT COALESCE(SUM(value), 0) AS c FROM statistics_records "
            "WHERE user_id = ? AND record_type = 'study_duration'"
        )
        last_active = "暂无记录"
        try:
            row = db.fetchone(
                "SELECT MAX(record_date) AS d FROM statistics_records WHERE user_id = ?",
                (user_id,),
            )
            if row and row["d"]:
                last_active = str(row["d"])
        except Exception:
            pass
        return (
            f"错题 {errors} · 闪卡 {cards} · 文档 {docs} · "
            f"累计专注 {minutes} 分钟 · 最近学习 {last_active}"
        )

    def _on_add_user(self) -> None:
        username = self.username_edit.text().strip()
        nickname = self.nickname_edit.text().strip()
        if not username:
            QMessageBox.warning(self, "输入错误", "用户名不能为空")
            return
        try:
            self.user_service.create_user(username, nickname or username)
            self.username_edit.clear()
            self.nickname_edit.clear()
            self.refresh_data()
        except Exception as e:
            logger.warning("Failed to create user: %s", e)
            QMessageBox.warning(self, "创建失败", str(e))

    def _on_switch_user(self) -> None:
        item = self.user_list.currentItem()
        if item is None:
            return
        user_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            self.user_service.set_current_user(user_id)
        except Exception as e:
            logger.warning("Failed to switch user to %s: %s", user_id, e)
            QMessageBox.warning(self, "切换失败", str(e))
            return
        self.refresh_data()
        if self.on_user_switched:
            self.on_user_switched()
        show_success(self, "已切换到选中用户，所有页面数据已刷新。")

    def _on_delete_user(self) -> None:
        item = self.user_list.currentItem()
        if item is None:
            return
        user_id = item.data(Qt.ItemDataRole.UserRole)
        current_id = self.user_service.get_current_user_id()
        if user_id == current_id:
            QMessageBox.warning(self, "无法删除", "不能删除当前登录的用户，请先切换到其他用户。")
            return
        if not ask_confirm(
            self,
            "确认删除",
            "删除用户将清空该用户的所有学习数据，且无法恢复。是否继续？",
        ):
            return
        try:
            self.user_service.delete_user(user_id)
        except Exception as e:
            logger.warning("Failed to delete user %s: %s", user_id, e)
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.refresh_data()
