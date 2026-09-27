"""首次启动新手向导。

5 步：欢迎 → 硬件检测 → 运行模式选择 → 模型配置 → 完成。
支持深浅色主题。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..config.hardware import detect_hardware, get_hardware_tier, get_recommended_settings
from ..utils.logger import get_logger

logger = get_logger("ui.onboarding")


class WelcomePage(QWizardPage):
    """步骤1：欢迎介绍。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("欢迎使用 PLOS-AI")
        self.setSubTitle("个人学习操作系统")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "PLOS-AI 是一款本地离线的 AI 学习助手，提供以下核心功能：\n\n"
            "• AI 对话与启发式教学\n"
            "• OCR 搜题与错题本\n"
            "• RAG 知识库与笔记\n"
            "• 自适应出题与试卷生成\n"
            "• 知识图谱与学习仪表盘\n"
            "• 艾宾浩斯复习调度\n\n"
            "接下来将通过几步引导你完成初始配置。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 14px; line-height: 1.6;")
        layout.addWidget(intro)
        layout.addStretch()


class HardwarePage(QWizardPage):
    """步骤2：硬件检测与模型推荐。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("硬件检测")
        self.setSubTitle("系统将自动检测你的电脑配置并推荐模型方案")
        layout = QVBoxLayout(self)

        self._hardware = detect_hardware()
        tier = get_hardware_tier(self._hardware)
        tier_text = {"low": "低配（≤7GB）", "mid": "中配（8~12GB）", "high": "高配（>12GB）"}.get(tier.value, "未知")

        info = QLabel(
            f"系统内存：{self._hardware.ram_total_gb:.1f} GB\n"
            f"CPU：{self._hardware.cpu_model}\n"
            f"CPU 核心数：{self._hardware.cpu_cores}\n"
            f"GPU：{self._hardware.gpu_name or '未检测到'}\n"
            f"显存：{self._hardware.gpu_vram_gb:.1f} GB\n\n"
            f"硬件分级：{tier_text}\n\n"
            f"推荐模型方案：\n"
            f"  文本模型：{get_recommended_settings(self._hardware)['text_model']}\n"
            f"  视觉模型：{get_recommended_settings(self._hardware)['vision_model']}"
        )
        info.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        layout.addWidget(info)

        self.force_low = QCheckBox("强制低配模式（无论硬件如何，仅启用基础功能）")
        layout.addWidget(self.force_low)
        layout.addStretch()


class ModePage(QWizardPage):
    """步骤3：选择运行模式。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("运行模式")
        self.setSubTitle("选择 AI 模型运行方式")
        layout = QVBoxLayout(self)

        self.local_radio = QRadioButton("本地 Ollama 模式")
        self.local_radio.setChecked(True)
        local_desc = QLabel("  优点：数据完全本地，隐私安全，无网络依赖\n  缺点：需要本机安装 Ollama 并下载模型")
        local_desc.setStyleSheet("color: #86909C; padding-left: 20px;")

        self.cloud_radio = QRadioButton("云端 API 模式")
        cloud_desc = QLabel("  优点：无需本机算力，响应快\n  缺点：需要联网，数据会上传到云端")
        cloud_desc.setStyleSheet("color: #86909C; padding-left: 20px;")

        layout.addWidget(self.local_radio)
        layout.addWidget(local_desc)
        layout.addWidget(self.cloud_radio)
        layout.addWidget(cloud_desc)
        layout.addStretch()


class ModelConfigPage(QWizardPage):
    """步骤4：模型配置。"""

    def __init__(self, hardware) -> None:
        super().__init__()
        self._hardware = hardware
        self.setTitle("模型配置")
        self.setSubTitle("按硬件推荐模型，可手动修改")
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.text_model = QComboBox()
        self.text_model.addItems([
            "qwen2.5:7b-q4_K_M",
            "qwen2.5:3b-instruct-q4_K_M",
            "qwen2.5:1.5b-instruct-q4_K_M",
        ])
        self.vision_model = QComboBox()
        self.vision_model.addItems([
            "qwen2.5-vl:7b-q4_K_M",
            "qwen2.5-vl:3b-q4_K_M",
        ])
        self.embedding_model = QLineEdit("nomic-embed-text")
        form.addRow("文本模型：", self.text_model)
        form.addRow("视觉模型：", self.vision_model)
        form.addRow("嵌入模型：", self.embedding_model)
        layout.addLayout(form)

        rec = get_recommended_settings(hardware)
        idx = self.text_model.findText(rec["text_model"])
        if idx >= 0:
            self.text_model.setCurrentIndex(idx)
        idx = self.vision_model.findText(rec["vision_model"])
        if idx >= 0:
            self.vision_model.setCurrentIndex(idx)

        hint = QLabel("注：低配机器仅推荐 3B 文本模型，视觉模型可能不可用。")
        hint.setStyleSheet("color: #F59E0B; font-size: 11px;")
        layout.addWidget(hint)
        layout.addStretch()


class FinishPage(QWizardPage):
    """步骤5：完成。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("完成设置")
        self.setSubTitle("配置已就绪，点击完成进入主程序")
        layout = QVBoxLayout(self)
        msg = QLabel("恭喜！你已完成 PLOS-AI 的初始配置。\n\n"
                     "你可以在「设置」中随时修改这些配置，\n"
                     "也可以重新运行本向导。\n\n"
                     "祝你学习愉快！")
        msg.setStyleSheet("font-size: 14px; line-height: 1.6;")
        layout.addWidget(msg)
        layout.addStretch()


class OnboardingWizard(QWizard):
    """5 步新手向导。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PLOS-AI 新手向导")
        self.setMinimumSize(640, 480)

        self.welcome_page = WelcomePage()
        self.hardware_page = HardwarePage()
        self.mode_page = ModePage()
        self.model_page = ModelConfigPage(self.hardware_page._hardware)
        self.finish_page = FinishPage()

        self.addPage(self.welcome_page)
        self.addPage(self.hardware_page)
        self.addPage(self.mode_page)
        self.addPage(self.model_page)
        self.addPage(self.finish_page)

        self.setButtonText(QWizard.WizardButton.BackButton, "上一步")
        self.setButtonText(QWizard.WizardButton.NextButton, "下一步")
        self.setButtonText(QWizard.WizardButton.FinishButton, "完成")
        self.setButtonText(QWizard.WizardButton.CancelButton, "取消")

    def get_config(self) -> dict:
        """收集向导中的配置。"""
        return {
            "text_model": self.model_page.text_model.currentText(),
            "vision_model": self.model_page.vision_model.currentText(),
            "embedding_model": self.model_page.embedding_model.text(),
            "run_mode": "local" if self.mode_page.local_radio.isChecked() else "cloud",
            "force_low_spec": self.hardware_page.force_low.isChecked(),
        }


__all__ = ["OnboardingWizard"]
