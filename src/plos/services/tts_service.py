"""本地离线 TTS 语音朗读服务。

基于 pyttsx3 实现，无需联网即可朗读文本内容。
支持音色选择与语速调节，配置持久化到 settings 表。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from ..db import get_db
from ..utils.logger import get_logger

logger = get_logger("services.tts_service")

DEFAULT_RATE = 180
DEFAULT_VOLUME = 0.9


class TTSService:
    """文本转语音服务（离线）。"""

    def __init__(self, rate: Optional[int] = None, voice_id: Optional[str] = None):
        # pyttsx3 在 Windows 上走 SAPI5（COM），引擎绑定创建它的线程套间；
        # 主线程 is_available() 初始化后在后台线程 say/runAndWait 会跨套间失败，
        # 因此引擎按线程隔离存储，每个线程独立初始化
        self._tls = threading.local()
        self._rate = rate
        self._voice_id = voice_id

    def _load_settings(self) -> None:
        """从 settings 表读取语速/音色（仅在首次需要时）。"""
        if self._rate is not None and self._voice_id is not None:
            return
        try:
            db = get_db()
            if self._rate is None:
                row = db.fetchone("SELECT value FROM settings WHERE key = ?", ("tts_rate",))
                self._rate = int(row["value"]) if row else DEFAULT_RATE
            if self._voice_id is None:
                row = db.fetchone("SELECT value FROM settings WHERE key = ?", ("tts_voice_id",))
                self._voice_id = row["value"] if row else ""
        except Exception:
            self._rate = self._rate or DEFAULT_RATE
            self._voice_id = self._voice_id or ""

    def _get_engine(self):
        """延迟初始化当前线程专属的 pyttsx3 引擎。"""
        engine = getattr(self._tls, "engine", None)
        if engine is None:
            try:
                import pyttsx3

                engine = pyttsx3.init()
                self._load_settings()
                engine.setProperty("rate", int(self._rate or DEFAULT_RATE))
                engine.setProperty("volume", DEFAULT_VOLUME)
                if self._voice_id:
                    try:
                        engine.setProperty("voice", self._voice_id)
                    except Exception:
                        pass
                self._tls.engine = engine
            except Exception as e:
                logger.warning("TTS engine init failed: %s", e)
                self._tls.engine = None
        return engine

    def is_available(self) -> bool:
        """检查 TTS 是否可用。"""
        return self._get_engine() is not None

    def list_voices(self) -> List[dict]:
        """列出系统可用音色。"""
        engine = self._get_engine()
        if engine is None:
            return []
        try:
            voices = engine.getProperty("voices")
            return [
                {"id": getattr(v, "id", ""), "name": getattr(v, "name", "未知音色")}
                for v in voices
            ]
        except Exception as e:
            logger.warning("List voices failed: %s", e)
            return []

    def set_rate(self, rate: int) -> None:
        """设置语速（约 100-300），持久化并应用到所有引擎。"""
        self._rate = max(80, min(300, int(rate)))
        try:
            db = get_db()
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("tts_rate", str(self._rate)),
            )
        except Exception as e:
            logger.warning("Persist tts_rate failed: %s", e)
        # 应用到已创建的引擎
        engine = getattr(self._tls, "engine", None)
        if engine is not None:
            try:
                engine.setProperty("rate", self._rate)
            except Exception:
                pass

    def set_voice(self, voice_id: str) -> None:
        """设置音色，持久化。"""
        self._voice_id = voice_id or ""
        try:
            db = get_db()
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("tts_voice_id", self._voice_id),
            )
        except Exception as e:
            logger.warning("Persist tts_voice_id failed: %s", e)

    def get_rate(self) -> int:
        self._load_settings()
        return int(self._rate or DEFAULT_RATE)

    def speak(self, text: str) -> None:
        """朗读文本（非阻塞）。"""
        engine = self._get_engine()
        if engine is None:
            logger.warning("TTS engine not available")
            return
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            logger.warning("TTS speak failed: %s", e)

    def stop(self) -> None:
        """停止朗读。"""
        engine = self._get_engine()
        if engine is not None:
            try:
                engine.stop()
            except Exception as e:
                logger.warning("TTS stop failed: %s", e)

    def save_to_file(self, text: str, output_path: Path) -> None:
        """将文本保存为语音文件。"""
        engine = self._get_engine()
        if engine is None:
            raise RuntimeError("TTS engine not available")
        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            engine.save_to_file(text, str(output_path))
            engine.runAndWait()
        except Exception as e:
            logger.warning("TTS save_to_file failed: %s", e)
            raise
