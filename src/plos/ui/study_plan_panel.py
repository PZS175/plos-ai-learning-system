"""学习计划面板。

生成周学习任务计划、展示任务列表、勾选完成、一键导入闪卡。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services import FlashcardService, StudyPlanService
from ..utils.logger import get_logger
from .markdown_browser import MarkdownBrowser
from .math_text import attach_math_delegate
from .ui_utils import (
    ButtonBusy,
    create_section_title,
    apply_glass_style,
    ask_confirm,
    create_empty_state_widget,
    create_header_widget,
    fix_spinbox_text,
    show_success,
)
from .workers import StudyPlanGenerateWorker, ThreadPool

logger = get_logger("ui.study_plan_panel")


_TRIGGER_LABELS = {
    "error_added": "错题新增",
    "mastery_changed": "掌握度变化",
    "knowledge_weak": "知识漏洞",
    "manual": "手动调整",
}


class _AdjustmentDialog(QDialog):
    """学习计划变更记录弹窗。"""

    def __init__(
        self,
        study_plan_service: StudyPlanService,
        plan_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.study_plan_service = study_plan_service
        self.plan_id = plan_id
        self.setWindowTitle("学习路径变更记录")
        self.resize(640, 480)
        self._build_ui()
        self._load_adjustments()

    def _build_ui(self) -> None:
        self.setProperty("glass", True)
        apply_glass_style(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = create_section_title("系统自动调整记录")
        layout.addWidget(title)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        container = QWidget()
        container.setLayout(self.content_layout)
        layout.addWidget(container, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load_adjustments(self) -> None:
        # 清空
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            adjustments = self.study_plan_service.list_adjustments(self.plan_id)
        except Exception as e:
            logger.warning("Failed to load plan adjustments: %s", e)
            adjustments = []

        if not adjustments:
            self.content_layout.addWidget(
                create_empty_state_widget("暂无系统自动调整记录")
            )
            return

        for adj in adjustments:
            card = self._build_card(adj)
            self.content_layout.addWidget(card)
        self.content_layout.addStretch()

    def _build_card(self, adj: dict) -> QWidget:
        card = QWidget()
        card.setProperty("glass", True)
        apply_glass_style(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        trigger_type = adj.get("trigger_type", "")
        label = _TRIGGER_LABELS.get(trigger_type, trigger_type)

        header = QLabel(
            f"<span style='color:#3B82F6;font-weight:bold;'>{label}</span>"
            f"<span style='color:#8A93A8;margin-left:10px;font-size:12px;'>{adj.get('created_at', '')}</span>"
        )
        layout.addWidget(header)

        browser = MarkdownBrowser()
        browser.setStyleSheet("background:transparent;border:none;")
        html = ""
        trigger_desc = str(adj.get("trigger_desc", "") or "").strip()
        if trigger_desc:
            html += f"<div style='margin:4px 0;'><b>触发原因：</b>{trigger_desc}</div>"
        adjustment_desc = str(adj.get("adjustment_desc", "") or "").strip()
        if adjustment_desc:
            html += f"<div style='margin:4px 0;'><b>调整动作：</b>{adjustment_desc}</div>"
        browser.set_html(html)
        layout.addWidget(browser)
        return card


class StudyPlanPanel(QWidget):
    """学习计划管理界面。"""

    def __init__(
        self,
        study_plan_service: StudyPlanService,
        flashcard_service: Optional[FlashcardService] = None,
    ):
        super().__init__()
        self.study_plan_service = study_plan_service
        self.flashcard_service = flashcard_service
        self._current_plan_id: Optional[int] = None
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

        header = create_header_widget("学习计划", "AI 生成周任务，勾选完成并导入闪卡")
        layout.addWidget(header)

        # 生成计划卡片
        create_card = QWidget()
        create_card.setProperty("glass", True)
        apply_glass_style(create_card)
        create_layout = QVBoxLayout(create_card)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("计划标题，例如：高三数学冲刺")
        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("学习目标，例如：掌握函数与导数")
        self.weak_edit = QLineEdit()
        self.weak_edit.setPlaceholderText("薄弱科目，用逗号分隔，例如：数学,物理")
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("每日可用时长（分钟）："))
        self.time_spin = QSpinBox()
        fix_spinbox_text(self.time_spin)
        self.time_spin.setRange(10, 600)
        self.time_spin.setValue(60)
        self.time_spin.setSingleStep(10)
        time_layout.addWidget(self.time_spin)
        time_layout.addStretch()

        self._generate_btn = QPushButton("生成周学习计划")
        self._generate_btn.setObjectName("primary_btn")
        self._generate_btn.clicked.connect(self._on_generate)

        create_layout.addWidget(QLabel("计划标题"))
        create_layout.addWidget(self.title_edit)
        create_layout.addWidget(QLabel("学习目标"))
        create_layout.addWidget(self.goal_edit)
        create_layout.addWidget(QLabel("薄弱科目"))
        create_layout.addWidget(self.weak_edit)
        create_layout.addLayout(time_layout)
        create_layout.addWidget(self._generate_btn)
        layout.addWidget(create_card)

        # 计划列表
        list_card = QWidget()
        list_card.setProperty("glass", True)
        apply_glass_style(list_card)
        list_layout = QVBoxLayout(list_card)

        self.plan_list = QListWidget()
        self.plan_list.currentItemChanged.connect(self._on_plan_selected)
        self.plan_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plan_list.customContextMenuRequested.connect(self._show_plan_context_menu)
        list_layout.addWidget(self.plan_list)

        top_btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_data)
        delete_btn = QPushButton("删除计划")
        delete_btn.setObjectName("danger_btn")
        delete_btn.clicked.connect(self._on_delete_plan)
        top_btn_layout.addWidget(refresh_btn)
        top_btn_layout.addWidget(delete_btn)
        top_btn_layout.addStretch()
        list_layout.addLayout(top_btn_layout)
        layout.addWidget(list_card, 1)

        # 任务详情
        task_card = QWidget()
        task_card.setProperty("glass", True)
        apply_glass_style(task_card)
        task_layout = QVBoxLayout(task_card)

        header_layout = QHBoxLayout()
        self.progress_label = QLabel("完成进度：0%")
        self.progress_label.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(self.progress_label)
        header_layout.addStretch()

        self.btn_adjustments = QPushButton("查看变更记录")
        self.btn_adjustments.setToolTip("查看系统根据错题/掌握度自动调整学习路径的记录")
        self.btn_adjustments.setEnabled(False)
        self.btn_adjustments.clicked.connect(self._on_show_adjustments)
        header_layout.addWidget(self.btn_adjustments)
        task_layout.addLayout(header_layout)

        self.task_tree = QTreeWidget()
        self.task_tree.setHeaderLabels(["完成", "任务", "科目", "预计时长", "操作"])
        self.task_tree.setColumnWidth(0, 60)
        # 任务标题列可能含公式，单元格走混排渲染（完成/操作列是自定义控件，不动）
        self._task_math_delegate = attach_math_delegate(self.task_tree, 1)
        task_layout.addWidget(self.task_tree)
        layout.addWidget(task_card, 2)

    def refresh_data(self) -> None:
        """刷新计划列表。"""
        self.plan_list.clear()
        for plan in self.study_plan_service.list_plans():
            item = QListWidgetItem(
                f"{plan['title']} ({plan['start_date']} ~ {plan['end_date']}) 进度 {plan['progress']}%"
            )
            item.setData(Qt.ItemDataRole.UserRole, plan["id"])
            self.plan_list.addItem(item)
        self.task_tree.clear()
        self.progress_label.setText("完成进度：0%")
        self._current_plan_id = None

    def _on_generate(self) -> None:
        title = self.title_edit.text().strip()
        goal = self.goal_edit.text().strip()
        weak = self.weak_edit.text().strip()
        minutes = self.time_spin.value()
        if not title or not goal:
            QMessageBox.warning(self, "输入错误", "计划标题和学习目标不能为空")
            return
        # AI 生成耗时较长，放入后台线程执行，避免冻结界面
        self._generate_busy = ButtonBusy(self._generate_btn, "生成中...").start()
        worker = StudyPlanGenerateWorker(
            self.study_plan_service,
            title=title,
            goal=goal,
            weak_subjects=weak,
            daily_minutes=minutes,
        )
        worker.signals.result.connect(self._on_plan_generated)
        worker.signals.error.connect(self._on_generate_error)
        worker.signals.finished.connect(self._on_generate_finished)
        ThreadPool.start_worker(worker)

    def _on_plan_generated(self, plan_id: int) -> None:
        self.refresh_data()
        show_success(self, "已生成一周学习计划，可在下方查看任务。")

    def _on_generate_error(self, message: str) -> None:
        logger.warning("Failed to generate study plan: %s", message)
        QMessageBox.warning(self, "生成失败", message)

    def _on_generate_finished(self) -> None:
        busy = getattr(self, "_generate_busy", None)
        if busy is not None:
            busy.restore()

    def _on_plan_selected(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            return
        plan_id = current.data(Qt.ItemDataRole.UserRole)
        self._current_plan_id = plan_id
        self._load_tasks(plan_id)

    def _load_tasks(self, plan_id: int) -> None:
        self.task_tree.clear()
        self.btn_adjustments.setEnabled(plan_id is not None)
        plan = self.study_plan_service.get_plan(plan_id)
        if plan is None:
            return
        self.progress_label.setText(f"完成进度：{plan.get('progress', 0)}%")
        for task in plan.get("tasks", []):
            item = QTreeWidgetItem()
            checkbox = QCheckBox()
            checkbox.setChecked(bool(task.get("is_completed")))
            checkbox.stateChanged.connect(
                lambda state, tid=task["id"]: self._on_task_toggled(tid, state)
            )
            item.setText(1, task.get("title", ""))
            item.setText(2, task.get("subject", ""))
            item.setText(3, f"{task.get('estimated_minutes', 0)} 分钟")

            import_btn = QPushButton("导入闪卡")
            import_btn.setObjectName("primary_btn")
            import_btn.clicked.connect(lambda _, t=task: self._on_import_flashcard(t))

            self.task_tree.addTopLevelItem(item)
            self.task_tree.setItemWidget(item, 0, checkbox)
            self.task_tree.setItemWidget(item, 4, import_btn)

    def _on_task_toggled(self, task_id: int, state: int) -> None:
        is_completed = state == Qt.CheckState.Checked.value
        try:
            self.study_plan_service.toggle_task_complete(task_id, is_completed)
        except Exception as e:
            logger.warning("Failed to toggle task %s: %s", task_id, e)
            QMessageBox.warning(self, "操作失败", f"更新任务状态失败：\n{e}")
        if self._current_plan_id is not None:
            self._load_tasks(self._current_plan_id)

    def _on_import_flashcard(self, task: dict) -> None:
        if self.flashcard_service is None:
            QMessageBox.warning(self, "功能不可用", "闪卡服务未初始化")
            return
        try:
            card_id = self.study_plan_service.import_task_to_flashcard(
                task_id=task["id"],
                front_content=task.get("title", ""),
                back_content=task.get("description", ""),
            )
            show_success(self, f"已导入闪卡 #{card_id}")
        except Exception as e:
            logger.warning("Failed to import task to flashcard: %s", e)
            QMessageBox.warning(self, "导入失败", str(e))

    def _on_show_adjustments(self) -> None:
        """打开当前计划的变更记录弹窗。"""
        if self._current_plan_id is None:
            QMessageBox.information(self, "提示", "请先选择一个学习计划")
            return
        dialog = _AdjustmentDialog(
            self.study_plan_service, self._current_plan_id, parent=self
        )
        dialog.exec()

    def _show_plan_context_menu(self, position) -> None:
        """学习计划列表右键菜单：查看变更记录、删除。"""
        item = self.plan_list.itemAt(position)
        if item is None:
            return
        self.plan_list.setCurrentItem(item)
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        act_adjustments = menu.addAction("查看变更记录")
        menu.addSeparator()
        act_delete = menu.addAction("删除计划")
        chosen = menu.exec(self.plan_list.mapToGlobal(position))
        if chosen is act_adjustments:
            self._on_show_adjustments()
        elif chosen is act_delete:
            self._on_delete_plan()

    def _on_delete_plan(self) -> None:
        item = self.plan_list.currentItem()
        if item is None:
            return
        plan_id = item.data(Qt.ItemDataRole.UserRole)
        if ask_confirm(self, "确认删除", "是否删除该学习计划？"):
            try:
                self.study_plan_service.delete_plan(plan_id)
            except Exception as e:
                logger.warning("Failed to delete plan %s: %s", plan_id, e)
                QMessageBox.warning(self, "删除失败", str(e))
                return
            self.refresh_data()
