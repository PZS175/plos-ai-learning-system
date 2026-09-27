"""Pydantic schemas for PLOS AI configuration."""

from __future__ import annotations

from typing import Any, Dict

from ..core.constants import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_TIMEOUT

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - optional dependency guard
    BaseModel = object

    class Field:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass


class BackendConfig(BaseModel):
    type: str = "ollama"
    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_timeout: int = DEFAULT_OLLAMA_TIMEOUT
    llamacpp_model_dir: str = ""


class CloudConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout: int = 120


class ModelsConfig(BaseModel):
    text_model: str = "qwen2.5:7b-q4_K_M"
    vision_model: str = "qwen2.5-vl:7b-q4_K_M"
    embedding_model: str = "nomic-embed-text"
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2048
    context_length: int = 8192


class ChatConfig(BaseModel):
    """对话教学行为配置。"""

    teaching_mode: str = "normal"   # "normal" / "socratic"
    no_direct_answer: bool = False  # 是否禁止直接给出完整答案


class OCRPreprocessConfig(BaseModel):
    deskew: bool = True
    denoise: bool = True
    grayscale: bool = False


class OCRConfig(BaseModel):
    engine: str = "vl"              # 纯 VL 实现，不再使用 PaddleOCR
    confidence_threshold: float = 0.7
    language: str = "ch"
    use_vl_correction: bool = True
    preprocess: OCRPreprocessConfig = Field(default_factory=OCRPreprocessConfig)


class RAGConfig(BaseModel):
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 4
    embedding_dimension: int = 768


class HardwareConfig(BaseModel):
    low_spec_mode: str = "auto"
    use_gpu: bool = True
    gpu_layers: int = 0
    num_threads: int = 0


class StorageConfig(BaseModel):
    data_dir: str = ""
    images_dir: str = ""
    attachments_dir: str = ""
    db_path: str = ""
    chroma_dir: str = ""
    logs_dir: str = ""


class UIConfig(BaseModel):
    theme: str = "light"
    window_width: int = 1200
    window_height: int = 800
    window_x: int = -1
    window_y: int = -1
    window_maximized: bool = True
    language: str = "zh_CN"
    minimize_to_tray: bool = True
    start_minimized: bool = False
    demo_mode: bool = False


class HotkeysConfig(BaseModel):
    screenshot: str = "ctrl+alt+a"
    clipboard_capture: str = "ctrl+alt+v"
    show_window: str = "ctrl+alt+s"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    max_file_size_mb: int = 10
    backup_count: int = 5


class AppConfig(BaseModel):
    """Root configuration model for PLOS AI."""

    model_profile: str = "auto"     # "auto" / "7b" / "3b"
    backend: BackendConfig = Field(default_factory=BackendConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    hotkeys: HotkeysConfig = Field(default_factory=HotkeysConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Export the configuration as a nested dictionary."""
        return self.model_dump() if hasattr(self, "model_dump") else self.dict()


__all__ = ["AppConfig"]
