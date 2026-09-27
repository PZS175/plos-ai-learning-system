"""语音学习面板：听写/跟读练习（TTS 播报 + 评分反馈）。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services.tts_service import TTSService
from ..services.voice_learning_service import VoiceLearningService
from ..utils.logger import get_logger
from .math_text import MathLabel
from .ui_utils import (
    apply_card_style,
    create_section_title,
    set_primary_button_style,
    theme_colors,
)

logger = get_logger("ui.voice_learning_panel")


class VoiceLearningPanel(QWidget):
    """听写练习：原文播报 → 学生作答 → 评分反馈。"""

    def __init__(
        self,
        voice_service: VoiceLearningService,
        tts_service: Optional[TTSService] = None,
        asr_service=None,
    ):
        super().__init__()
        self.voice_service = voice_service
        self.tts_service = tts_service
        self.asr_service = asr_service
        self._recording = False
        self._build_ui()

    def _build_ui(self) -> None:
        c = theme_colors()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QVBoxLayout()
        scroll_area.setContentsMargins(24, 24, 24, 24)
        scroll_area.setSpacing(16)
        layout.addLayout(scroll_area)

        # 原文卡
        original_card = QWidget()
        original_card.setProperty("card", True)
        apply_card_style(original_card)
        original_layout = QVBoxLayout(original_card)
        original_layout.setContentsMargins(20, 16, 20, 16)
        original_layout.addWidget(create_section_title("练习原文"))
        self.original_edit = QLineEdit()
        self.original_edit.setPlaceholderText("输入要听写的句子，例如：床前明月光，疑是地上霜。")
        original_layout.addWidget(self.original_edit)

        play_row = QHBoxLayout()
        self.play_btn = QPushButton("🔊 播报原文")
        set_primary_button_style(self.play_btn)
        self.play_btn.clicked.connect(self._play_original)
        play_row.addWidget(self.play_btn)
        play_row.addStretch()
        self.tts_hint = QLabel(
            "TTS 不可用时可直接看原文默写" if self.tts_service is None else ""
        )
        self.tts_hint.setStyleSheet(f"color: {c['fg_muted']}; font-size: 11px; background-color: transparent;")
        play_row.addWidget(self.tts_hint)
        original_layout.addLayout(play_row)
        scroll_area.addWidget(original_card)

        # 作答卡
        answer_card = QWidget()
        answer_card.setProperty("card", True)
        apply_card_style(answer_card)
        answer_layout = QVBoxLayout(answer_card)
        answer_layout.setContentsMargins(20, 16, 20, 16)
        answer_layout.addWidget(create_section_title("你的作答"))
        self.answer_edit = QLineEdit()
        self.answer_edit.setPlaceholderText("听完后输入你听到的内容，按回车提交评分")
        self.answer_edit.returnPressed.connect(self._grade)
        answer_layout.addWidget(self.answer_edit)

        grade_row = QHBoxLayout()
        if self.asr_service is not None:
            self.mic_btn = QPushButton("🎤 录音作答")
            self.mic_btn.setToolTip("点击开始录音，再点一次停止并自动转写评分")
            self.mic_btn.clicked.connect(self._toggle_mic)
            grade_row.addWidget(self.mic_btn)
        self.grade_btn = QPushButton("提交评分")
        set_primary_button_style(self.grade_btn)
        self.grade_btn.clicked.connect(self._grade)
        grade_row.addWidget(self.grade_btn)
        grade_row.addStretch()
        answer_layout.addLayout(grade_row)
        scroll_area.addWidget(answer_card)

        # 结果卡
        result_card = QWidget()
        result_card.setProperty("card", True)
        apply_card_style(result_card)
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(20, 16, 20, 16)
        result_layout.addWidget(create_section_title("评分结果"))
        self.result_label = MathLabel("提交作答后显示得分与漏词明细", markdown=True)
        self.result_label.setObjectName("subtitle_label")
        result_layout.addWidget(self.result_label)
        scroll_area.addWidget(result_card)
        scroll_area.addStretch()

    def _play_original(self) -> None:
        text = self.original_edit.text().strip()
        if not text:
            return
        if self.tts_service is None:
            self.tts_hint.setText("TTS 服务未初始化，请直接看原文默写")
            return
        try:
            self.tts_service.speak(text)
        except Exception as e:
            logger.warning("TTS speak failed: %s", e)
            self.tts_hint.setText(f"播报失败：{e}")

    def _toggle_mic(self) -> None:
        """录音作答：开始录音 → 停止 → ASR 转写 → 自动评分。"""
        if self.asr_service is None:
            return
        if not self._recording:
            if not self.original_edit.text().strip():
                self.result_label.set_rich_text("请先填写原文再开始录音。")
                return
            if not self.asr_service.start_recording():
                self.result_label.set_rich_text("录音启动失败：请检查麦克风权限。")
                return
            self._recording = True
            self.mic_btn.setText("⏹ 停止并评分")
            self.result_label.set_rich_text("● 录音中……对着麦克风朗读原文，说完点停止。")
            return
        self._recording = False
        self.mic_btn.setText("转写中…")
        self.mic_btn.setEnabled(False)
        try:
            audio = self.asr_service.stop_recording()
            if not audio:
                self.result_label.set_rich_text("未录到音频，请重试。")
                return
            spoken = self.asr_service.transcribe_audio(bytes(audio)).strip()
            if not spoken:
                self.result_label.set_rich_text("没能识别出语音内容，请靠近麦克风重试。")
                return
            self.answer_edit.setText(spoken)
            self._grade()
        except Exception as e:
            logger.warning("Mic grading failed: %s", e)
            self.result_label.set_rich_text(f"语音识别失败：{e}")
        finally:
            self.mic_btn.setText("🎤 录音作答")
            self.mic_btn.setEnabled(True)

    def _grade(self) -> None:
        original = self.original_edit.text().strip()
        spoken = self.answer_edit.text().strip()
        if not original or not spoken:
            self.result_label.set_rich_text("请先填写原文并输入作答。")
            return
        result = self.voice_service.grade(original, spoken)
        c = theme_colors()
        color = c["success"] if result["score"] >= 80 else (
            c["warning"] if result["score"] >= 60 else c["error"]
        )
        missing = "、".join(result["missing"][:20]) or "无"
        extra = "、".join(result["extra"][:10]) or "无"
        self.result_label.set_rich_text(
            f"<b style='color:{color}; font-size:18px;'>{result['score']} 分</b>　"
            f"{result['feedback']}<br/>"
            f"<b>漏掉：</b>{missing}<br/><b>多出：</b>{extra}"
        )
        logger.info("Voice learning graded: %s", result["score"])

    def refresh_data(self) -> None:
        """用户切换后清空练习状态。"""
        self.original_edit.clear()
        self.answer_edit.clear()
        self.result_label.set_rich_text("提交作答后显示得分与漏词明细")
