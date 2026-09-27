"""Core enumerations used throughout PLOS AI."""

from __future__ import annotations

from enum import Enum


class InferenceBackend(Enum):
    """Supported inference backends."""

    OLLAMA = "ollama"
    LLAMA_CPP = "llama_cpp"
    CLOUD_API = "cloud_api"


class ModelType(Enum):
    """Categories of AI models."""

    TEXT = "text"
    VISION = "vision"
    EMBEDDING = "embedding"


class Role(Enum):
    """Chat message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MasteryLevel(Enum):
    """Approximate mastery level for a concept or flashcard."""

    NOVICE = "novice"
    LEARNING = "learning"
    PROFICIENT = "proficient"
    MASTERED = "mastered"


class FlashcardRating(Enum):
    """SM-2 performance ratings.

    Values mirror the classic SuperMemo quality scale:
    0 = forgot, 1 = hard, 2 = good, 3 = easy.
    """

    FORGOT = (0, "忘记", "完全没想起来")
    HARD = (1, "困难", "勉强想起，很困难")
    GOOD = (2, "良好", "正常回忆起来")
    EASY = (3, "简单", "非常容易想起")

    def __new__(cls, value: int, label: str, description: str):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.label = label
        obj.description = description
        return obj


class OCRSource(Enum):
    """Source of an OCR request."""

    FILE = "file"
    SCREENSHOT = "screenshot"
    CLIPBOARD = "clipboard"


class LowSpecMode(Enum):
    """Low-spec performance mode setting."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class HardwareTier(Enum):
    """硬件性能三档分级，按系统总物理内存划分。

    LOW: ≤7GB，仅基础功能 + 3B 文本模型
    MID: 8~12GB，7B 文本 + 3B-VL，知识脑图可选
    HIGH: >12GB，全部功能无限制
    """

    LOW = "low"
    MID = "mid"
    HIGH = "high"


class ActionType(Enum):
    """High-level actions the application can perform."""

    CHAT = "chat"
    OCR = "ocr"
    FLASHCARD_REVIEW = "flashcard_review"
    SCREENSHOT = "screenshot"
    SETTINGS = "settings"


__all__ = [
    "ActionType",
    "FlashcardRating",
    "HardwareTier",
    "InferenceBackend",
    "LowSpecMode",
    "MasteryLevel",
    "ModelType",
    "OCRSource",
    "Role",
]
