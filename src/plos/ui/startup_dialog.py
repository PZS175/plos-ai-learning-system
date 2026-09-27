"""启动引导对话框。

程序启动时根据 Ollama 探测结果和用户选择决定运行模式：
- 本地 Ollama 模式
- 云端 API 模式
- 仅展示安装/下载指引后退出
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import DEFAULT_OLLAMA_HOST
from ..core.enums import InferenceBackend
from ..core.models import HardwareInfo
from ..utils.logger import get_logger
from .ui_utils import create_section_title, show_warning
from ..utils.startup_probe import (
    check_models,
    get_hardware_recommendation,
    probe_ollama,
    resolve_ollama_models,
)

logger = get_logger("ui.startup_dialog")


class _CopyableCommandBox(QWidget):
    """展示可复制命令的组件。"""

    def __init__(self, commands: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.browser = QTextBrowser()
        self.browser.setPlainText("\n".join(commands))
        layout.addWidget(self.browser)

        btn = QPushButton("复制全部命令")
        btn.clicked.connect(self._copy)
        layout.addWidget(btn)

    def _copy(self) -> None:
        text = self.browser.toPlainText()
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "已复制", "命令已复制到剪贴板")


class StartupDialog(QDialog):
    """启动模式选择对话框。"""

    def __init__(self, config: Dict[str, Any], hardware: Optional[HardwareInfo] = None):
        super().__init__()
        self.config = config
        self.hardware = hardware
        self.result: Dict[str, Any] = {"action": "exit"}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("PLOS AI 启动引导")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        self.title_label = create_section_title("正在探测本机 Ollama 服务...")
        layout.addWidget(self.title_label)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.command_box: Optional[_CopyableCommandBox] = None

        self.button_box = QDialogButtonBox()
        layout.addWidget(self.button_box)

        self._probe_and_update()

    def _probe_and_update(self) -> None:
        host = self.config.get("backend", {}).get("ollama_host", DEFAULT_OLLAMA_HOST)
        connected, models, message = probe_ollama(host)

        if connected and models is not None:
            status = check_models(models)
            if status["text_exists"]:
                self._show_local_ready(models)
            else:
                self._show_local_missing_model()
        else:
            self._show_ollama_not_running(message)

    def _clear_buttons(self) -> None:
        self.button_box.clear()

    def _remove_command_box(self) -> None:
        if self.command_box is not None:
            self.command_box.setParent(None)
            self.command_box.deleteLater()
            self.command_box = None

    def _show_local_ready(self, models: List[str]) -> None:
        resolved = resolve_ollama_models(models, self.hardware)
        self.title_label.setText("Ollama 已就绪，将使用本地离线模式")

        info_lines = [
            f"检测到文本模型：{resolved['text_model']}",
        ]
        if resolved["vl_available"]:
            info_lines.append(f"检测到视觉模型：{resolved['vision_model']}")
        else:
            info_lines.append("未检测到 VL 视觉模型，图片 OCR/识图功能将不可用，文本功能正常")
        info_lines.append("点击「进入主程序」开始学习。")

        self.info_label.setText("\n".join(info_lines))
        self._remove_command_box()
        self._clear_buttons()

        btn_enter = self.button_box.addButton("进入主程序", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_enter.clicked.connect(lambda: self._set_result("local", resolved))

        btn_cloud = self.button_box.addButton("改用云端 API", QDialogButtonBox.ButtonRole.ActionRole)
        btn_cloud.clicked.connect(self._show_cloud_input)

        btn_exit = self.button_box.addButton("退出", QDialogButtonBox.ButtonRole.RejectRole)
        btn_exit.clicked.connect(self.reject)

    def _download_missing_models(self) -> None:
        """按推荐套装一键拉取缺失模型，完成后自动重新探测。"""
        from .model_download_dialog import ModelDownloadDialog

        rec = get_hardware_recommendation(self.hardware)
        commands = rec.get("pull_commands", [])
        models: List[Tuple[str, str]] = []
        for cmd in commands:
            parts = cmd.split()
            if len(parts) >= 3 and parts[-2] == "pull":
                models.append((parts[-1], "文本模型" if "qwen2.5" in parts[-1] and "vl" not in parts[-1] else "视觉模型"))
        if not models:
            show_warning(self, "无法下载", "未解析到推荐模型名，请使用命令手动拉取。")
            return
        dlg = ModelDownloadDialog(models, parent=self)
        dlg.exec()
        self._probe_and_update()

    def _show_local_missing_model(self) -> None:
        rec = get_hardware_recommendation(self.hardware)
        self.title_label.setText("Ollama 已连接，但缺少 qwen2.5 文本模型")

        hw = rec.get("hardware")
        hw_text = ""
        if hw:
            hw_text = f"\n内存：{hw.ram_total_gb:.1f} GB | GPU：{hw.gpu_name or '无'}"

        self.info_label.setText(
            f"本机硬件推荐：{rec['profile']} 套装（{'低配' if rec['is_low_spec'] else '高配'}）{hw_text}\n\n"
            "请复制下方命令到终端手动拉取模型，完成后重启本软件。\n"
            "文本模型为必须；VL 模型仅用于图片识别，可选下载。"
        )

        self._remove_command_box()
        self.command_box = _CopyableCommandBox(rec["pull_commands"])
        self.layout().insertWidget(2, self.command_box)

        self._clear_buttons()
        btn_download = self.button_box.addButton("一键下载缺失模型", QDialogButtonBox.ButtonRole.ActionRole)
        btn_download.clicked.connect(self._download_missing_models)

        btn_cloud = self.button_box.addButton("使用云端 API 模式", QDialogButtonBox.ButtonRole.ActionRole)
        btn_cloud.clicked.connect(self._show_cloud_input)

        btn_exit = self.button_box.addButton("退出，稍后手动拉取", QDialogButtonBox.ButtonRole.RejectRole)
        btn_exit.clicked.connect(self.reject)

    def _show_ollama_not_running(self, message: str) -> None:
        rec = get_hardware_recommendation(self.hardware)
        self.title_label.setText("未检测到 Ollama 服务")
        self.info_label.setText(
            f"{message}\n\n"
            "您可以选择：\n"
            "1. 使用云端 API 模式，无需安装 Ollama；\n"
            "2. 使用本地离线模式，先安装 Ollama 并拉取推荐模型。\n\n"
            "注意：程序不会自动下载任何模型。"
        )

        self._remove_command_box()
        self.command_box = _CopyableCommandBox(
            ["# 1. 安装 Ollama：https://ollama.com/download", "# 2. 拉取模型"] + rec["pull_commands"]
        )
        self.layout().insertWidget(2, self.command_box)

        self._clear_buttons()
        btn_cloud = self.button_box.addButton("使用云端 API 模式", QDialogButtonBox.ButtonRole.ActionRole)
        btn_cloud.clicked.connect(self._show_cloud_input)

        btn_local = self.button_box.addButton("我已安装，复制命令后退出", QDialogButtonBox.ButtonRole.RejectRole)
        btn_local.clicked.connect(self.reject)

    def _show_cloud_input(self) -> None:
        self.title_label.setText("云端 API 模式")
        self.info_label.setText(
            "请输入云端 API Key 和 Base URL。\n"
            "默认使用阿里云百炼兼容接口，也可替换为其他 OpenAI 兼容服务。"
        )
        self._remove_command_box()
        self._clear_buttons()

        cloud_cfg = self.config.get("cloud", {})

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Base URL："))
        self.url_edit = QLineEdit(cloud_cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
        url_layout.addWidget(self.url_edit)
        form_layout.addLayout(url_layout)

        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("API Key："))
        self.key_edit = QLineEdit(cloud_cfg.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self.key_edit)
        form_layout.addLayout(key_layout)

        self.layout().insertWidget(2, form)
        self._cloud_form = form

        btn_save = self.button_box.addButton("保存并进入主程序", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_save.clicked.connect(self._save_cloud_and_enter)

        btn_back = self.button_box.addButton("返回", QDialogButtonBox.ButtonRole.ActionRole)
        btn_back.clicked.connect(self._back_to_probe)

    def _back_to_probe(self) -> None:
        if hasattr(self, "_cloud_form") and self._cloud_form is not None:
            self._cloud_form.setParent(None)
            self._cloud_form.deleteLater()
            self._cloud_form = None
        self._probe_and_update()

    def _save_cloud_and_enter(self) -> None:
        api_key = self.key_edit.text().strip()
        base_url = self.url_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请输入 API Key")
            return

        self.config.setdefault("cloud", {})
        self.config["cloud"]["api_key"] = api_key
        self.config["cloud"]["base_url"] = base_url
        self.config["backend"]["type"] = InferenceBackend.CLOUD_API.value

        # 云端模式下使用推荐的云端默认模型
        self.config["models"]["text_model"] = "qwen-turbo"
        self.config["models"]["vision_model"] = "qwen-vl-plus"
        self.config["models"]["embedding_model"] = "text-embedding-v3"

        self.result = {
            "action": "cloud",
            "config": self.config,
        }
        self.accept()

    def _set_result(self, action: str, resolved: Optional[Dict[str, Any]] = None) -> None:
        if action == "local" and resolved:
            self.config["backend"]["type"] = InferenceBackend.OLLAMA.value
            self.config["models"]["text_model"] = resolved["text_model"]
            if resolved.get("vision_model"):
                self.config["models"]["vision_model"] = resolved["vision_model"]
            self.config["models"]["embedding_model"] = resolved.get("embedding_model", "nomic-embed-text")
            self.config["model_profile"] = resolved.get("profile", "auto")

        self.result = {
            "action": action,
            "config": self.config,
        }
        self.accept()

    @classmethod
    def run(
        cls,
        config: Dict[str, Any],
        hardware: Optional[HardwareInfo] = None,
    ) -> Dict[str, Any]:
        """显示启动对话框并返回用户选择。"""
        dialog = cls(config, hardware)
        dialog.exec()
        return dialog.result
