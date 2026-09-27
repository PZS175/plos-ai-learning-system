"""Default configuration values for PLOS AI."""

from __future__ import annotations

from typing import Any, Dict

from ..core.constants import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_TIMEOUT

DEFAULT_CONFIG: Dict[str, Any] = {
    # ---- Inference backend ----
    "backend": {
        "type": "ollama",           # "ollama" / "llama_cpp" / "cloud_api"
        "ollama_host": DEFAULT_OLLAMA_HOST,
        "ollama_timeout": DEFAULT_OLLAMA_TIMEOUT,
        "llamacpp_model_dir": "",   # directory containing GGUF files
    },

    # ---- Cloud API (optional, used when backend.type == "cloud_api") ----
    "cloud": {
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "timeout": 120,
    },

    # ---- Models ----
    # 默认模型套装在首次启动时由硬件检测自动写入。
    # 用户可在设置面板强制选择 7b / 3b 套装。
    "models": {
        "text_model": "qwen2.5:3b-instruct-q4_K_M",
        "vision_model": "qwen2.5-vl:7b-q4_K_M",
        "embedding_model": "nomic-embed-text",
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 2048,
        "context_length": 8192,
    },

    # ---- Model profile ----
    "model_profile": "auto",    # "auto" / "7b" / "3b"

    # ---- OCR ----
    "ocr": {
        "engine": "vl",             # 纯 VL OCR，不再使用 PaddleOCR
        "confidence_threshold": 0.7,
        "language": "ch",           # ch for Chinese+English
        "use_vl_correction": True,  # keep for compatibility
        "preprocess": {
            "deskew": True,
            "denoise": True,
            "grayscale": False,
        },
    },

    # ---- RAG / Knowledge ----
    "rag": {
        "chunk_size": 500,
        "chunk_overlap": 50,
        "top_k": 4,
        "embedding_dimension": 768,
    },

    # ---- Hardware / Performance ----
    "hardware": {
        "low_spec_mode": "auto",    # "auto" / "on" / "off"
        "use_gpu": True,
        "gpu_layers": 0,            # for llama.cpp offload
        "num_threads": 0,           # 0 = auto
    },

    # ---- Storage paths ----
    "storage": {
        "data_dir": "",             # empty = use platform default
        "images_dir": "",
        "attachments_dir": "",
        "db_path": "",
        "chroma_dir": "",
        "logs_dir": "",
    },

    # ---- UI ----
    "ui": {
        "theme": "light",           # "light" / "dark"；首次启动默认浅色
        "window_width": 1200,
        "window_height": 800,
        "window_x": -1,             # -1 表示未记录位置
        "window_y": -1,
        "window_maximized": True,   # 首次启动默认最大化
        "language": "zh_CN",
        "minimize_to_tray": True,
        "start_minimized": False,
        "demo_mode": False,         # 演示模式：放大 UI 元素，适合投屏
    },

    # ---- Chat / teaching behavior ----
    "chat": {
        "teaching_mode": "normal",      # "normal" / "socratic"
        "no_direct_answer": False,      # 开启后 AI 不直接给出完整答案
        "explanation_mode": "basic",    # "basic" / "advanced" / "exam" / "research"
    },

    # ---- Global hotkeys ----
    "hotkeys": {
        "screenshot": "ctrl+alt+a",
        "clipboard_capture": "ctrl+alt+v",
        "show_window": "ctrl+alt+s",
    },

    # ---- Logging ----
    "logging": {
        "level": "INFO",           # DEBUG / INFO / WARNING / ERROR
        "max_file_size_mb": 10,
        "backup_count": 5,
    },
}
