"""Core data models for PLOS AI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .enums import OCRSource, Role


@dataclass
class ChatMessage:
    """A single chat message."""

    role: Role
    content: str
    images: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a backend-agnostic dictionary."""
        result: Dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.images:
            result["images"] = self.images
        return result


@dataclass
class ModelInfo:
    """Metadata about an available model."""

    name: str
    type: str
    backend: str
    available: bool = True


@dataclass
class InferenceResponse:
    """Response from an inference request."""

    content: str
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)
    finish_reason: Optional[str] = None
    raw: Optional[Any] = None


@dataclass
class EmbeddingResult:
    """Result of an embedding request."""

    embedding: List[float]
    model: str


@dataclass
class OCRBlock:
    """A single block of OCR output."""

    text: str
    confidence: float
    bbox: List[List[float]]
    page_num: int = 0
    source: OCRSource = OCRSource.FILE


@dataclass
class OCRResult:
    """Aggregated OCR result for an image or document."""

    text: str
    blocks: List[OCRBlock]
    vl_corrected_regions: int = 0
    used_vl_fallback: bool = False
    source: OCRSource = OCRSource.FILE
    image_path: Optional[Any] = None


@dataclass
class DocumentChunk:
    """A text chunk extracted from a document."""

    id: str
    text: str
    source: str
    page_num: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """A single result from a vector or knowledge search."""

    content: str
    source: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorBookEntry:
    """An entry in the user's error book / mistake log."""

    id: Optional[str] = None
    question: str = ""
    answer: str = ""
    reason: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Flashcard:
    """A flashcard with SM-2 scheduling state."""

    id: Optional[str] = None
    front: str = ""
    back: str = ""
    deck: str = "default"
    ease: float = 2.5
    interval: int = 0
    repetitions: int = 0
    next_review: datetime = field(default_factory=datetime.now)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class StudyLog:
    """A record of a single flashcard review."""

    id: Optional[str] = None
    flashcard_id: Optional[str] = None
    rating: int = 0
    reviewed_at: datetime = field(default_factory=datetime.now)


@dataclass
class HardwareInfo:
    """Detected hardware summary."""

    os_name: str
    cpu_model: str
    cpu_cores: int
    ram_total_gb: float
    gpu_name: Optional[str] = None
    gpu_vram_gb: float = 0.0
    is_low_spec: bool = False


__all__ = [
    "ChatMessage",
    "DocumentChunk",
    "EmbeddingResult",
    "ErrorBookEntry",
    "Flashcard",
    "HardwareInfo",
    "InferenceResponse",
    "ModelInfo",
    "OCRBlock",
    "OCRResult",
    "SearchResult",
    "StudyLog",
]
