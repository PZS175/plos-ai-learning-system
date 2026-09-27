"""本地录音与离线语音识别服务。

录音基于 PyQt6.QtMultimedia 实现，不引入额外录音库；
内置基于能量的 VAD（语音活动检测），支持可视化波形数据输出；
转写优先使用 faster-whisper，其次 openai-whisper；两者都未安装时返回引擎不可用提示。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QByteArray, QIODevice, QObject, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from ..utils.logger import get_logger
from ..utils.paths import get_data_dir

logger = get_logger("services.asr_service")


SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # Int16


class AudioRecorder(QObject):
    """基于 QAudioSource 的录音器，带 VAD 与波形数据输出。"""

    state_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    amplitude_changed = pyqtSignal(float)
    vad_state_changed = pyqtSignal(bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._buffer = QByteArray()
        self._source: Optional[QAudioSource] = None
        self._device: Optional[QIODevice] = None
        self._vad_enabled = True
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False
        self._vad_threshold = 0.015
        self._silence_timeout_frames = int(1.5 * SAMPLE_RATE / 512)
        self._speech_start_frames = 2
        self._rms_window: List[float] = []

    def start_recording(self, vad_enabled: bool = True) -> bool:
        """开始录音，返回是否成功。"""
        devices = QMediaDevices.audioInputs()
        if not devices:
            self.error_occurred.emit("未检测到麦克风")
            return False

        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        default_device = QMediaDevices.defaultAudioInput()
        self._source = QAudioSource(default_device, fmt, self)
        self._device = self._source.start()
        if self._device is None:
            self.error_occurred.emit("无法启动音频输入")
            return False

        self._buffer.clear()
        self._vad_enabled = vad_enabled
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False
        self._rms_window.clear()
        self._device.readyRead.connect(self._read_data)
        self.state_changed.emit("recording")
        logger.info("Audio recording started (vad=%s)", vad_enabled)
        return True

    def stop_recording(self) -> QByteArray:
        """停止录音并返回原始音频字节。"""
        if self._source is not None:
            self._source.stop()
            self._source = None
        if self._device is not None:
            self._read_data()
            self._device = None
        self.state_changed.emit("stopped")
        logger.info("Audio recording stopped, bytes=%d", self._buffer.size())
        return QByteArray(self._buffer)

    def _read_data(self) -> None:
        if self._device is None:
            return
        data = self._device.readAll()
        if data.size() > 0:
            self._buffer.append(data)
            if self._vad_enabled:
                self._process_vad(data.data())
            else:
                self.amplitude_changed.emit(self._compute_rms(data.data()))

    @staticmethod
    def _compute_rms(pcm_bytes: bytes) -> float:
        """计算 PCM 片段的 RMS 能量。"""
        if len(pcm_bytes) < SAMPLE_WIDTH:
            return 0.0
        count = len(pcm_bytes) // SAMPLE_WIDTH
        fmt = "<{}h".format(count)
        samples = struct.unpack(fmt, pcm_bytes[: count * SAMPLE_WIDTH])
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0

    def _process_vad(self, pcm_bytes: bytes) -> None:
        """基于能量的简单 VAD，检测语音开始/结束。"""
        frame_size = 512 * SAMPLE_WIDTH
        for i in range(0, len(pcm_bytes), frame_size):
            frame = pcm_bytes[i : i + frame_size]
            rms = self._compute_rms(frame)
            self._rms_window.append(rms)
            if len(self._rms_window) > 20:
                self._rms_window.pop(0)
            smoothed = sum(self._rms_window) / len(self._rms_window)
            self.amplitude_changed.emit(smoothed)

            if smoothed > self._vad_threshold:
                self._speech_frames += 1
                self._silence_frames = 0
                if self._speech_frames >= self._speech_start_frames and not self._is_speaking:
                    self._is_speaking = True
                    self.vad_state_changed.emit(True)
            else:
                if self._is_speaking:
                    self._silence_frames += 1
                    if self._silence_frames >= self._silence_timeout_frames:
                        self._is_speaking = False
                        self.vad_state_changed.emit(False)
                else:
                    self._speech_frames = max(0, self._speech_frames - 1)

    def is_recording(self) -> bool:
        return self._source is not None

    def is_speaking(self) -> bool:
        return self._is_speaking


class ASRService:
    """语音识别服务：录音 + 离线转写。"""

    def __init__(
        self,
        model_size_or_path: str = "base",
        language: str = "zh",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size_or_path = model_size_or_path
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._model_backend: Optional[str] = None

    def set_model(self, model_size_or_path: str) -> None:
        """设置模型路径或尺寸，下次转写时重新加载。"""
        if self.model_size_or_path != model_size_or_path:
            self.model_size_or_path = model_size_or_path
            self._model = None
            self._model_backend = None

    def _load_model(self):
        """延迟加载本地 Whisper 模型。"""
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size_or_path,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._model_backend = "faster-whisper"
            logger.info("Loaded faster-whisper model: %s", self.model_size_or_path)
            return
        except ImportError:
            logger.info("faster-whisper not installed, trying openai-whisper")
        except Exception as e:
            logger.warning("Failed to load faster-whisper: %s", e)

        try:
            import whisper

            self._model = whisper.load_model(self.model_size_or_path)
            self._model_backend = "openai-whisper"
            logger.info("Loaded openai-whisper model: %s", self.model_size_or_path)
            return
        except ImportError:
            logger.info("openai-whisper not installed")
        except Exception as e:
            logger.warning("Failed to load openai-whisper: %s", e)

    @staticmethod
    def _engine_installed() -> bool:
        """离线 ASR 引擎依赖是否已安装（只探测模块，不加载模型）。"""
        import importlib.util

        return (
            importlib.util.find_spec("faster_whisper") is not None
            or importlib.util.find_spec("whisper") is not None
        )

    def is_available(self) -> bool:
        """检查是否有可用的离线 ASR 引擎。

        注意：**不会加载或下载模型**。启动阶段会调用本方法，
        必须保持轻量；模型只在真正转写时（``transcribe_*``）才加载，
        避免启动时联网下载 1GB 模型导致界面卡死或离线不可用。
        """
        if self._model is not None:
            return True
        try:
            return self._engine_installed()
        except Exception:
            return False

    def get_engine_info(self) -> dict:
        """返回当前引擎状态信息。"""
        return {
            "backend": self._model_backend or "none",
            "model": self.model_size_or_path,
            "available": self.is_available(),
            "loaded": self._model is not None,
        }

    def is_loaded(self) -> bool:
        """模型是否已加载到内存。"""
        return self._model is not None

    def unload_model(self) -> bool:
        """卸载已加载的 Whisper 模型，释放内存。

        低配机器可在不转写时释放内存；下次转写会自动重新加载。
        """
        if self._model is None:
            return False
        try:
            self._model = None
            self._model_backend = None
            import gc

            gc.collect()
            logger.info("ASR 模型已卸载，内存已释放: %s", self.model_size_or_path)
            return True
        except Exception as e:
            logger.warning("卸载 ASR 模型失败: %s", e)
            return False

    @staticmethod
    def _write_wav(path: Path, audio_bytes: bytes) -> Path:
        """将原始 PCM 字节写入 WAV 文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_bytes)
        return path

    def transcribe_audio(self, audio_bytes: bytes) -> str:
        """转写音频字节，返回识别文本。"""
        if not audio_bytes:
            return ""
        self._load_model()
        if self._model is None:
            raise RuntimeError(
                "未安装离线 ASR 引擎。请安装 faster-whisper 或 openai-whisper：\n"
                "pip install faster-whisper"
            )

        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        wav_path = data_dir / "temp_asr.wav"
        self._write_wav(wav_path, audio_bytes)

        try:
            if self._model_backend == "faster-whisper":
                segments, _ = self._model.transcribe(
                    str(wav_path),
                    language=self.language,
                    vad_filter=True,
                )
                text = "".join(segment.text for segment in segments).strip()
            else:
                result = self._model.transcribe(
                    str(wav_path),
                    language=self.language,
                )
                text = result.get("text", "").strip()
        finally:
            try:
                wav_path.unlink()
            except Exception:
                pass

        logger.info("ASR transcribed text length=%d", len(text))
        return text

    def transcribe_file(self, audio_path: Path) -> str:
        """转写本地音频文件。"""
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"音频文件不存在：{audio_path}")
        audio_bytes = audio_path.read_bytes()
        if audio_path.suffix.lower() == ".wav":
            try:
                with wave.open(str(audio_path), "rb") as wf:
                    audio_bytes = wf.readframes(wf.getnframes())
            except Exception:
                pass
        return self.transcribe_audio(audio_bytes)

    @staticmethod
    def get_demo_text() -> str:
        """离线演示用的示例转写文本。"""
        return "（演示模式）这是一段本地离线语音转文字的示例文本，用于在没有安装 Whisper 模型时展示功能入口。"
