"""ASR 语音转文字服务测试。

验证：
1. WAV 文件写入与读取
2. RMS 能量计算
3. 未安装引擎时正确提示
4. 示例文本返回
5. 模型路径切换
"""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from plos.services.asr_service import ASRService, AudioRecorder


def test_write_and_read_wav():
    service = ASRService()
    pcm = b"\x00\x01" * 1600
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "test.wav"
        result_path = service._write_wav(path, pcm)
        assert result_path.exists()

        with wave.open(str(result_path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.readframes(wf.getnframes()) == pcm

    print("test_write_and_read_wav passed")


def test_rms_energy():
    # 静音
    silence = struct.pack("<100h", *([0] * 100))
    assert AudioRecorder._compute_rms(silence) == 0.0

    # 满幅正弦波 RMS ≈ 0.707
    samples = [int(32767 * math.sin(2 * math.pi * i / 20)) for i in range(1000)]
    pcm = struct.pack("<{}h".format(len(samples)), *samples)
    rms = AudioRecorder._compute_rms(pcm)
    assert 0.6 < rms < 0.8

    print("test_rms_energy passed")


def test_engine_unavailable_without_whisper():
    """可用性应与 faster-whisper 是否可导入一致（装上引擎后本测试自动翻转）。"""
    service = ASRService()
    try:
        import faster_whisper  # noqa: F401

        installed = True
    except Exception:
        installed = False
    assert service.is_available() is installed
    print("test_engine_unavailable_without_whisper passed")


def test_transcribe_empty_audio():
    service = ASRService()
    assert service.transcribe_audio(b"") == ""
    print("test_transcribe_empty_audio passed")


def test_demo_text():
    text = ASRService.get_demo_text()
    assert "演示模式" in text
    assert len(text) > 0
    print("test_demo_text passed")


def test_set_model():
    service = ASRService(model_size_or_path="base")
    service.set_model("small")
    assert service.model_size_or_path == "small"
    assert service._model is None
    print("test_set_model passed")


if __name__ == "__main__":
    test_write_and_read_wav()
    test_rms_energy()
    test_engine_unavailable_without_whisper()
    test_transcribe_empty_audio()
    test_demo_text()
    test_set_model()
    print("All ASR tests passed")
