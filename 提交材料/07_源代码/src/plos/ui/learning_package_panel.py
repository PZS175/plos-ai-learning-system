"""学习包面板。

支持将错题、闪卡、学习计划、批注、术语、文档打包导出，
通过 zip 文件导入，以及生成/解析分享码。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QRunnable, Qt
from PyQt6.QtWidgets import (
    QInputDialog,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.learning_package_service import LearningPackageService
from ..utils.logger import get_logger
from .math_text import MathLabel
from .ui_utils import (
    create_section_title,
    apply_glass_style,
    theme_colors,
    ask_confirm,
    create_empty_state_widget,
    set_danger_button_style,
    set_primary_button_style,
    show_error,
    show_info,
    show_success,
    show_warning,
)
from .workers import (
    PackageExportWorker,
    PackageImportWorker,
    ThreadPool,
    WorkerSignals,
)

logger = get_logger("ui.learning_package_panel")


class LearningPackagePanel(QWidget):
    """学习包导入分享面板。"""

    def __init__(
        self,
        package_service: Optional[LearningPackageService] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.package_service = package_service
        self._packages: List[dict] = []
        self._build_ui()
        self.refresh_data()

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧：学习包列表与操作
        left_panel = QWidget()
        left_panel.setProperty("glass", True)
        apply_glass_style(left_panel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        title = create_section_title("学习包")
        left_layout.addWidget(title)

        hint = QLabel("打包、导入或分享错题/闪卡/计划/文档等学习资料。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #86909c; font-size: 12px;")
        left_layout.addWidget(hint)

        btn_layout = QHBoxLayout()
        import_btn = QPushButton("导入学习包")
        import_btn.clicked.connect(self._import_package)
        btn_layout.addWidget(import_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_data)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addStretch()
        left_layout.addLayout(btn_layout)

        self.package_table = QTableWidget()
        self.package_table.setColumnCount(4)
        self.package_table.setHorizontalHeaderLabels(["标题", "描述", "分享码", "操作"])
        self.package_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.package_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.package_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.package_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.package_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.package_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        left_layout.addWidget(self.package_table)

        self.empty_state = create_empty_state_widget("暂无学习包，可在右侧新建或导入")
        left_layout.addWidget(self.empty_state)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #86909c; font-size: 12px;")
        left_layout.addWidget(self.status_label)

        splitter.addWidget(left_panel)

        # 右侧：新建学习包
        right_panel = QWidget()
        right_panel.setProperty("glass", True)
        apply_glass_style(right_panel)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        right_title = create_section_title("新建学习包")
        right_layout.addWidget(right_title)

        right_layout.addWidget(QLabel("标题："))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：高三数学一轮复习资料")
        right_layout.addWidget(self.title_edit)

        right_layout.addWidget(QLabel("描述："))
        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText("简要说明包内包含的内容...")
        self.desc_edit.setMaximumHeight(80)
        right_layout.addWidget(self.desc_edit)

        right_layout.addWidget(QLabel("包含内容："))
        self.include_checks: Dict[str, QCheckBox] = {}
        check_items = [
            ("error_book", "错题本"),
            ("flashcards", "闪卡"),
            ("study_plans", "学习计划"),
            ("study_plan_tasks", "学习任务"),
            ("note_annotations", "批注"),
            ("terms", "术语"),
            ("documents", "知识库文档"),
        ]
        for key, label in check_items:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.include_checks[key] = cb
            right_layout.addWidget(cb)

        ai_row = QHBoxLayout()
        self.ai_requirement_edit = QLineEdit()
        self.ai_requirement_edit.setPlaceholderText("描述需求，AI 帮你选：如“期末复习，函数与导数”")
        ai_row.addWidget(self.ai_requirement_edit, 1)
        ai_btn = QPushButton("AI 智能组包")
        ai_btn.clicked.connect(self._ai_recommend)
        ai_row.addWidget(ai_btn)
        right_layout.addLayout(ai_row)

        webdav_row = QHBoxLayout()
        self.webdav_upload_btn = QPushButton("上传到网盘")
        self.webdav_upload_btn.setToolTip("把当前配置的学习包上传到设置的 WebDAV 网盘")
        self.webdav_upload_btn.clicked.connect(self._webdav_upload)
        webdav_row.addWidget(self.webdav_upload_btn)
        self.webdav_download_btn = QPushButton("从网盘拉取")
        self.webdav_download_btn.clicked.connect(self._webdav_download)
        webdav_row.addWidget(self.webdav_download_btn)
        webdav_row.addStretch()
        right_layout.addLayout(webdav_row)

        export_btn = QPushButton("导出学习包")
        set_primary_button_style(export_btn)
        export_btn.clicked.connect(self._export_package)
        right_layout.addWidget(export_btn)

        right_layout.addWidget(QLabel("分享码解析："))
        code_layout = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("输入 10 位分享码...")
        code_layout.addWidget(self.code_edit)

        parse_btn = QPushButton("解析")
        parse_btn.clicked.connect(self._parse_share_code)
        code_layout.addWidget(parse_btn)
        right_layout.addLayout(code_layout)

        self.code_result = MathLabel("")
        self.code_result.setStyleSheet("color: #86909c; font-size: 12px;")
        right_layout.addWidget(self.code_result)

        right_layout.addStretch()
        splitter.addWidget(right_panel)
        splitter.setSizes([420, 780])

    def refresh_data(self) -> None:
        """刷新学习包列表。"""
        if self.package_service is None:
            return
        try:
            self._packages = self.package_service.list_packages()
        except Exception as e:
            logger.error("Failed to list learning packages: %s", e)
            self._packages = []
            show_error(self, "刷新失败", str(e))

        self.package_table.setRowCount(len(self._packages))
        self.package_table.setVisible(len(self._packages) > 0)
        self.empty_state.setVisible(len(self._packages) == 0)

        for i, pkg in enumerate(self._packages):
            self.package_table.setItem(i, 0, QTableWidgetItem(pkg.get("title", "")))
            self.package_table.setItem(i, 1, QTableWidgetItem(pkg.get("description", "")))
            share_code = pkg.get("share_code", "") or "未分享"
            self.package_table.setItem(i, 2, QTableWidgetItem(share_code))

            op_widget = QWidget()
            op_layout = QHBoxLayout(op_widget)
            op_layout.setContentsMargins(4, 2, 4, 2)
            op_layout.setSpacing(6)

            share_btn = QPushButton("分享")
            share_btn.setToolTip("生成分享码")
            share_btn.clicked.connect(lambda checked, pid=pkg["id"]: self._share_package(pid))
            op_layout.addWidget(share_btn)

            del_btn = QPushButton("删除")
            set_danger_button_style(del_btn)
            del_btn.clicked.connect(lambda checked, pid=pkg["id"]: self._delete_package(pid))
            op_layout.addWidget(del_btn)
            op_layout.addStretch()

            self.package_table.setCellWidget(i, 3, op_widget)

    def _collect_includes(self) -> Dict[str, bool]:
        return {key: cb.isChecked() for key, cb in self.include_checks.items()}

    def _ai_recommend(self) -> None:
        """AI 根据需求描述推荐学习包内容并自动填充表单。"""
        if self.package_service is None:
            show_error(self, "功能不可用", "学习包服务未初始化")
            return
        requirement = self.ai_requirement_edit.text().strip()
        if not requirement:
            show_warning(self, "输入错误", "请先描述学习包需求")
            return

        ai_btn = self.sender()
        if isinstance(ai_btn, QPushButton):
            ai_btn.setEnabled(False)
            ai_btn.setText("AI 分析中...")
        service = self.package_service

        class _AiWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = WorkerSignals()

            def run(self):
                try:
                    self.signals.result.emit(service.ai_recommend_package(requirement))
                except Exception as e:
                    self.signals.error.emit(str(e))

        worker = _AiWorker()

        def _restore():
            if isinstance(ai_btn, QPushButton):
                ai_btn.setEnabled(True)
                ai_btn.setText("AI 智能组包")

        def _on_done(rec):
            _restore()
            self.title_edit.setText(rec["title"])
            if rec.get("description"):
                self.desc_edit.setPlainText(rec["description"])
            for key, checked in rec.get("includes", {}).items():
                cb = self.include_checks.get(key)
                if cb is not None:
                    cb.setChecked(checked)
            reason = rec.get("reason") or "已按需求自动勾选内容"
            show_success(self, f"AI 推荐：{reason}")

        def _on_fail(msg):
            _restore()
            show_error(self, "AI 组包失败", msg)

        worker.signals.result.connect(_on_done)
        worker.signals.error.connect(_on_fail)
        ThreadPool.start_worker(worker)

    def _sync_client(self):
        from ..config import load_config
        from ..services.webdav_sync_service import WebDAVSyncService

        sync_cfg = load_config().get("sync", {})
        base_url = sync_cfg.get("base_url", "").strip()
        if not base_url:
            show_warning(self, "未配置同步", "请先到「模型管理」设置页填写 WebDAV 同步地址与账号。")
            return None
        return WebDAVSyncService(
            base_url=base_url,
            username=sync_cfg.get("username", ""),
            password=sync_cfg.get("password", ""),
        )

    def _webdav_upload(self) -> None:
        """把最近导出的学习包 zip 上传到 WebDAV。"""
        client = self._sync_client()
        if client is None:
            return
        export_dir = Path("data") / "packages"
        zips = sorted(export_dir.glob("*.zip"), key=lambda f: f.stat().st_mtime, reverse=True) if export_dir.exists() else []
        if not zips:
            show_warning(self, "没有可上传的包", "请先「导出学习包」生成 zip，再上传。")
            return
        try:
            name = client.upload_package(zips[0])
            show_success(self, "上传成功", f"已上传到网盘：{name}")
        except Exception as e:
            show_error(self, "上传失败", str(e))

    def _webdav_download(self) -> None:
        """从 WebDAV 拉取学习包列表，选择一个下载并导入。"""
        client = self._sync_client()
        if client is None:
            return
        try:
            items = client.list_remote()
        except Exception as e:
            show_error(self, "拉取失败", str(e))
            return
        if not items:
            show_warning(self, "网盘为空", "远端没有学习包。")
            return
        names = [it["name"] for it in items]
        name, ok = QInputDialog.getItem(self, "从网盘拉取", "选择学习包：", names, 0, False)
        if not ok:
            return
        try:
            local = client.download_package(name, Path("data") / "packages")
            self.package_service.import_package(local)
            show_success(self, "导入完成", f"已从网盘拉取并导入：{name}")
            self.refresh_data()
        except Exception as e:
            show_error(self, "导入失败", str(e))

    def _export_package(self) -> None:
        if self.package_service is None:
            show_error(self, "功能不可用", "学习包服务未初始化")
            return
        title = self.title_edit.text().strip()
        if not title:
            show_warning(self, "输入错误", "请输入学习包标题")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出学习包",
            f"{title}.zip",
            "Zip 学习包 (*.zip)",
        )
        if not file_path:
            return

        description = self.desc_edit.toPlainText().strip()
        includes = self._collect_includes()

        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("准备导出...")

        worker = PackageExportWorker(
            self.package_service,
            Path(file_path),
            title,
            description,
            includes,
        )
        worker.signals.result.connect(self._on_export_success)
        worker.signals.error.connect(lambda msg: show_error(self, "导出失败", msg))
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_worker_finished)
        ThreadPool.start_worker(worker)

    def _on_export_success(self, path: str) -> None:
        show_info(self, "导出成功", f"学习包已保存到：\n{path}")

    def _import_package(self) -> None:
        if self.package_service is None:
            show_error(self, "功能不可用", "学习包服务未初始化")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入学习包",
            "",
            "Zip 学习包 (*.zip)",
        )
        if not file_path:
            return

        try:
            preview = self.package_service.preview_package(Path(file_path))
        except Exception as e:
            show_error(self, "预览失败", str(e))
            return

        selected, conflict_strategy = self._show_import_preview(preview)
        if selected is None:
            return

        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("准备导入...")

        worker = PackageImportWorker(
            self.package_service,
            Path(file_path),
            selected=selected,
            conflict_strategy=conflict_strategy,
        )
        worker.signals.result.connect(self._on_import_success)
        worker.signals.error.connect(lambda msg: show_error(self, "导入失败", msg))
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_worker_finished)
        ThreadPool.start_worker(worker)

    def _on_import_success(self, counts: Dict[str, int]) -> None:
        details = "\n".join(f"{k}: {v}" for k, v in counts.items() if v > 0)
        show_info(self, "导入成功", f"已导入以下内容：\n{details or '无新增数据'}")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

    def _on_worker_finished(self) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText("")
        self.refresh_data()

    def _show_import_preview(self, preview: Dict[str, Any]) -> tuple:
        """显示导入预览对话框，返回 (selected_dict, conflict_strategy) 或 (None, None)。"""
        meta = preview.get("meta", {})
        counts = preview.get("counts", {})
        checksum_ok = preview.get("checksum_ok", True)

        dialog = QDialog(self)
        dialog.setWindowTitle("导入预览")
        dialog.resize(420, 420)
        layout = QVBoxLayout(dialog)

        info = QLabel(
            f"标题：{meta.get('title', '')}\n"
            f"描述：{meta.get('description', '')}\n"
            f"版本：{meta.get('version', '')}\n"
            f"校验：{'通过' if checksum_ok else '不通过'}"
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme_colors()['fg_primary']}; font-size: 13px;")
        layout.addWidget(info)

        layout.addWidget(QLabel("选择要导入的内容："))
        check_items = [
            ("error_book", "错题本"),
            ("flashcards", "闪卡"),
            ("study_plans", "学习计划"),
            ("study_plan_tasks", "学习任务"),
            ("note_annotations", "批注"),
            ("terms", "术语"),
            ("documents", "知识库文档"),
        ]
        selected_checks: Dict[str, QCheckBox] = {}
        for key, label in check_items:
            count = counts.get(key, 0)
            cb = QCheckBox(f"{label}（{count} 条）")
            cb.setChecked(count > 0)
            cb.setEnabled(count > 0)
            selected_checks[key] = cb
            layout.addWidget(cb)

        conflict_cb = QCheckBox("文档已存在时覆盖（默认跳过）")
        layout.addWidget(conflict_cb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None, None

        selected = {key: cb.isChecked() for key, cb in selected_checks.items()}
        conflict_strategy = "overwrite" if conflict_cb.isChecked() else "skip"
        return selected, conflict_strategy

    def _share_package(self, package_id: int) -> None:
        if self.package_service is None:
            return
        try:
            code = self.package_service.share_package(package_id)
            from PyQt6.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(code)
            show_info(self, "分享码已生成", f"分享码：{code}\n已复制到剪贴板")
            self.refresh_data()
        except Exception as e:
            logger.error("Failed to share package: %s", e)
            show_error(self, "生成失败", str(e))

    def _delete_package(self, package_id: int) -> None:
        if self.package_service is None:
            return
        if not ask_confirm(self, "确认删除", "删除仅移除学习包记录，不会删除已导入的学习数据。"):
            return
        try:
            self.package_service.delete_package(package_id)
            self.refresh_data()
        except Exception as e:
            logger.error("Failed to delete package: %s", e)
            show_error(self, "删除失败", str(e))

    def _parse_share_code(self) -> None:
        if self.package_service is None:
            show_error(self, "功能不可用", "学习包服务未初始化")
            return
        code = self.code_edit.text().strip()
        if not code:
            return
        try:
            info = self.package_service.parse_share_code(code)
            self.code_result.set_rich_text(
                f"有效分享码\n标题：{info.get('title', '')}\n描述：{info.get('description', '')}"
            )
        except Exception as e:
            self.code_result.set_rich_text(f"解析失败：{e}")
