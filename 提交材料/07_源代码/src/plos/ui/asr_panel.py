"""语音转文字面板。

支持本地麦克风录音，后台调用 faster-whisper / openai-whisper 离线转写，
提供实时波形可视化、VAD 状态提示、示例文本与模型路径配置。
"""

from __future__ import annotations

import sys

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services.asr_service import ASRService, AudioRecorder
from ..utils.logger import get_logger
from .math_text import MathTextBrowser, attach_live_preview
from .ui_utils import (
    create_section_title,
    apply_glass_style,
    theme_colors,
    set_danger_button_style,
    set_primary_button_style,
    show_error,
    show_success,
)
from .workers import ASRWorker, ThreadPool

logger = get_logger("ui.asr_panel")


class WaveformWidget(QWidget):
    """实时录音波形可视化组件。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(60)
        self.setMaximumHeight(80)
        self._values: list[float] = [0.0] * 60
        self._is_speaking = False

    def add_amplitude(self, value: float) -> None:
        self._values.pop(0)
        self._values.append(min(1.0, max(0.0, value)))
        self.update()

    def set_speaking(self, speaking: bool) -> None:
        self._is_speaking = speaking
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(theme_colors()["bg_tertiary"]))

        bar_count = len(self._values)
        bar_width = rect.width() / bar_count
        color = QColor(theme_colors()["accent"]) if self._is_speaking else QColor(theme_colors()["fg_secondary"])
        pen = QPen(color)
        pen.setWidth(max(2, int(bar_width * 0.6)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        mid_y = rect.height() / 2
        for i, value in enumerate(self._values):
            x = i * bar_width + bar_width / 2
            height = value * mid_y * 0.9
            painter.drawLine(int(x), int(mid_y - height), int(x), int(mid_y + height))


class ASRPanel(QWidget):
    """离线语音转文字面板。"""

    def __init__(
        self,
        asr_service: Optional[ASRService] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.asr_service = asr_service
        self._recorder = AudioRecorder(self)
        self._recorder.error_occurred.connect(lambda msg: show_error(self, "录音错误", msg))
        self._recorder.amplitude_changed.connect(self._on_amplitude)
        self._recorder.vad_state_changed.connect(self._on_vad_state)
        self._build_ui()
        self._update_engine_info()

    def refresh_data(self) -> None:
        """用户切换后清空识别文本（转写结果属于会话，不入库）。"""
        try:
            self.text_edit.clear()
        except Exception as e:
            logger.warning("Failed to reset asr panel after user switch: %s", e)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        card = QWidget()
        card.setProperty("glass", True)
        apply_glass_style(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = create_section_title("语音转文字")
        layout.addWidget(title)

        hint = QLabel("本地录音 + 可选 Whisper 离线转写。录音数据仅在本地处理。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #86909c; font-size: 12px;")
        layout.addWidget(hint)

        self.engine_label = QLabel("引擎状态：检测中...")
        self.engine_label.setStyleSheet("color: #86909c; font-size: 12px;")
        layout.addWidget(self.engine_label)

        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("模型路径/尺寸："))
        self.model_edit = QLineEdit("base")
        self.model_edit.setPlaceholderText("base、small 或本地模型绝对路径")
        model_layout.addWidget(self.model_edit)
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(self._apply_model)
        model_layout.addWidget(apply_btn)
        layout.addLayout(model_layout)

        self.waveform = WaveformWidget()
        layout.addWidget(self.waveform)

        self.vad_label = QLabel("VAD：静音")
        self.vad_label.setStyleSheet("color: #86909c; font-size: 12px;")
        layout.addWidget(self.vad_label)

        btn_layout = QHBoxLayout()
        self.record_btn = QPushButton("开始录音")
        set_primary_button_style(self.record_btn)
        self.record_btn.clicked.connect(self._toggle_recording)
        btn_layout.addWidget(self.record_btn)

        self.stop_btn = QPushButton("停止并转写")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_and_transcribe)
        btn_layout.addWidget(self.stop_btn)

        demo_btn = QPushButton("加载示例文本")
        demo_btn.clicked.connect(self._load_demo_text)
        btn_layout.addWidget(demo_btn)

        clear_btn = QPushButton("清空")
        set_danger_button_style(clear_btn)
        clear_btn.clicked.connect(self._clear_text)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        copy_btn = QPushButton("复制文本")
        copy_btn.clicked.connect(self._copy_text)
        layout.addWidget(copy_btn)

        self.status_label = QLabel("准备就绪")
        self.status_label.setStyleSheet("color: #86909c; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("识别结果将显示在这里...")
        self.text_edit.setMinimumHeight(200)
        layout.addWidget(self.text_edit)

        # 转写文本里的公式在下方预览区混排渲染（编辑框保持纯文本，可继续编辑/复制）
        self.formula_preview = MathTextBrowser(self)
        self.formula_preview.setMinimumHeight(70)
        self.formula_preview.setMaximumHeight(130)
        self.formula_preview.setPlaceholderText("公式预览")
        layout.addWidget(self.formula_preview)
        self._asr_preview_connector = attach_live_preview(
            self.text_edit, self.formula_preview
        )

        main_layout.addWidget(card)
        main_layout.addStretch()

    def _apply_model(self) -> None:
        if self.asr_service is None:
            return
        self.asr_service.set_model(self.model_edit.text().strip() or "base")
        self._update_engine_info()
        show_success(self, "模型设置已更新")

    def _update_engine_info(self) -> None:
        if self.asr_service is None:
            self.engine_label.setText("引擎状态：服务未初始化")
            return
        info = self.asr_service.get_engine_info()
        if info["available"]:
            if info.get("loaded"):
                self.engine_label.setText(
                    f"引擎状态：{info['backend']} / 模型：{info['model']}（已加载，占用内存）"
                )
            else:
                # 懒加载：模型在首次转写时才加载，启动不再联网下载
                self.engine_label.setText(
                    f"引擎状态：已就绪 / 模型：{info['model']}"
                    "（首次转写时自动加载，不占用启动时间）"
                )
            self._show_unload_button(bool(info.get("loaded")))
        else:
            self.engine_label.setText(
                "引擎状态：未安装离线 ASR 引擎（约需下载 1GB 模型，装完重启生效）"
            )
            if not hasattr(self, "_install_btn"):
                from PyQt6.QtWidgets import QPushButton

                install_btn = QPushButton("一键安装 faster-whisper")
                install_btn.clicked.connect(self._install_asr_engine)
                self.engine_label.parentWidget().layout().addWidget(install_btn)
                self._install_btn = install_btn
            self._install_btn.setVisible(True)
            self._install_btn.setEnabled(True)
            self._install_btn.setText("一键安装 faster-whisper")
            self._show_unload_button(False)

    def _show_unload_button(self, visible: bool) -> None:
        """按需显示「卸载模型」按钮，供低配机器释放内存。"""
        if not visible and not hasattr(self, "_unload_btn"):
            return
        if not hasattr(self, "_unload_btn"):
            from PyQt6.QtWidgets import QPushButton

            btn = QPushButton("卸载模型（释放内存）")
            btn.setToolTip("从内存中释放已加载的语音识别模型，下次转写时自动重新加载")
            btn.clicked.connect(self._unload_model)
            self.engine_label.parentWidget().layout().addWidget(btn)
            self._unload_btn = btn
        self._unload_btn.setVisible(visible)

    def _unload_model(self) -> None:
        """卸载已加载的 ASR 模型，释放内存。"""
        if self.asr_service is None:
            return
        released = self.asr_service.unload_model()
        if released:
            show_success(self, "模型已卸载，内存已释放")
        self._update_engine_info()

    def _install_asr_engine(self) -> None:
        """后台运行 pip 安装 faster-whisper，完成后刷新引擎状态。"""
        if getattr(self, "_installing", False):
            return
        self._installing = True
        btn = self._install_btn
        btn.setText("正在安装，请稍候…")
        btn.setEnabled(False)
        from PyQt6.QtCore import QProcess

        proc = QProcess(self)
        self._install_proc = proc
        proc.finished.connect(
            lambda code, _s: self._on_install_finished(code)
        )
        proc.start(sys.executable, ["-m", "pip", "install", "faster-whisper"])

    def _on_install_finished(self, exit_code: int) -> None:
        self._installing = False
        from ..services.asr_service import ASRService

        if exit_code == 0:
            # 重新探测引擎：直接重建服务实例，避免旧状态残留
            self.asr_service = ASRService()
            self._update_engine_info()
            show_success(self, "安装完成", "faster-whisper 已安装，引擎已就绪。")
        else:
            self._install_btn.setText("安装失败，点此重试")
            self._install_btn.setEnabled(True)
            show_error(
                self,
                "安装失败",
                "pip 安装出错，请检查网络后重试，或手动执行：\npip install faster-whisper",
            )

    def _on_amplitude(self, value: float) -> None:
        self.waveform.add_amplitude(value)

    def _on_vad_state(self, speaking: bool) -> None:
        self.waveform.set_speaking(speaking)
        self.vad_label.setText(f"VAD：{'说话中' if speaking else '静音'}")

    def _toggle_recording(self) -> None:
        if self._recorder.is_recording():
            self._stop_and_transcribe()
            return
        if self._recorder.start_recording():
            self.record_btn.setText("录音中...")
            self.stop_btn.setEnabled(True)
            self.status_label.setText("正在录音，点击停止并转写")

    def _stop_and_transcribe(self) -> None:
        if not self._recorder.is_recording():
            return
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("正在转写...")

        audio_bytes = self._recorder.stop_recording().data()
        self.record_btn.setText("开始录音")

        if not audio_bytes:
            self.status_label.setText("未检测到录音数据")
            self.record_btn.setEnabled(True)
            return

        if self.asr_service is None:
            show_error(self, "功能不可用", "ASR 服务未初始化")
            self.record_btn.setEnabled(True)
            self.status_label.setText("准备就绪")
            return

        worker = ASRWorker(self.asr_service, audio_bytes)
        worker.signals.result.connect(self._on_transcribe_success)
        worker.signals.error.connect(self._on_transcribe_error)
        worker.signals.finished.connect(lambda: self.record_btn.setEnabled(True))
        ThreadPool.start_worker(worker)

    def _on_transcribe_success(self, text: str) -> None:
        self.text_edit.setPlainText(text)
        self.status_label.setText(f"转写完成，共 {len(text)} 字")

    def _on_transcribe_error(self, message: str) -> None:
        show_error(self, "转写失败", message)
        self.status_label.setText("转写失败")

    def _load_demo_text(self) -> None:
        self.text_edit.setPlainText(ASRService.get_demo_text())
        self.status_label.setText("已加载示例文本")

    def _clear_text(self) -> None:
        self.text_edit.clear()
        self.status_label.setText("准备就绪")

    def _copy_text(self) -> None:
        text = self.text_edit.toPlainText()
        if not text:
            return
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            show_success(self, "识别文本已复制到剪贴板")
