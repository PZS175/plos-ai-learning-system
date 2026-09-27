"""启动探测工具：检测 Ollama 服务、本机模型、硬件推荐。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from ..config import MODEL_PROFILES, detect_hardware, get_recommended_model_profile
from ..core.models import HardwareInfo
from ..core.constants import DEFAULT_OLLAMA_HOST
from ..utils.logger import get_logger

logger = get_logger("utils.startup_probe")

TEXT_MODEL_PREFIXES = ["qwen2.5:7b", "qwen2.5:3b"]
VL_MODEL_PREFIXES = ["qwen2.5-vl:7b", "qwen2.5vl:7b", "qwen2.5-vl:3b", "qwen2.5vl:3b"]


def _model_prefix_match(name: str, prefixes: List[str]) -> bool:
    """检查模型名是否以任一前缀开头（含别名）。"""
    name_lower = name.lower()
    for prefix in prefixes:
        if name_lower.startswith(prefix.lower()):
            return True
    return False


def _size_token(model_name: str) -> str:
    """取出模型名里的参数量标签，如 ``qwen2.5:7b-q4_K_M`` → ``7b``。"""
    tag = model_name.split(":", 1)[1] if ":" in model_name else ""
    matched = re.search(r"(\d+(?:\.\d+)?)b", tag.lower())
    return f"{matched.group(1)}b" if matched else ""


def _pick_installed_model(
    installed: Sequence[str],
    target_name: str,
    extra_prefixes: Sequence[str] = (),
) -> Optional[str]:
    """在已安装模型里挑出最贴近目标套装的那一个。

    必须同时比「base 名」和「参数量」：同系列不同规格的 base 名完全相同
    （qwen2.5:3b-instruct 与 qwen2.5:7b-instruct 的 base 都是 qwen2.5），
    只比 base 名会先撞上列表靠前的 3B，于是高配机器也只跑上了 3B 模型。
    """
    prefixes = {target_name.split(":", 1)[0], *extra_prefixes}
    size = _size_token(target_name)
    base_match: Optional[str] = None
    for name in installed:
        if name.split(":", 1)[0] not in prefixes:
            continue
        if size and _size_token(name) == size:
            return name
        if base_match is None:
            base_match = name
    return base_match


def probe_ollama(host: str = DEFAULT_OLLAMA_HOST, timeout: int = 5) -> Tuple[bool, Optional[List[str]], str]:
    """探测 Ollama 服务是否可连接并返回模型列表。

    Returns:
        (connected, models_or_None, message)
    """
    try:
        response = httpx.get(f"{host}/api/tags", timeout=timeout)
        response.raise_for_status()
        data = response.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name") or m.get("model")
            if name:
                models.append(name)
        logger.info("Ollama probe success at %s, found %d models", host, len(models))
        return True, models, "Ollama 服务已连接"
    except httpx.ConnectError as e:
        logger.warning("Ollama not reachable at %s: %s", host, e)
        return False, None, f"无法连接到 Ollama（{host}），请确认服务已启动"
    except Exception as e:
        logger.warning("Ollama probe failed at %s: %s", host, e)
        return False, None, f"Ollama 探测失败：{e}"


def check_models(models: List[str]) -> Dict[str, Any]:
    """从模型列表中判断文本/VL 模型是否存在。"""
    text_exists = any(_model_prefix_match(m, TEXT_MODEL_PREFIXES) for m in models)
    vl_exists = any(_model_prefix_match(m, VL_MODEL_PREFIXES) for m in models)
    return {
        "text_exists": text_exists,
        "vl_exists": vl_exists,
        "models": models,
    }


def get_hardware_recommendation(hardware: Optional[HardwareInfo] = None) -> Dict[str, Any]:
    """根据硬件返回推荐模型套装和 pull 命令。"""
    try:
        hw = hardware or detect_hardware()
        profile = get_recommended_model_profile(hw)
    except Exception as e:
        logger.warning("Hardware detection failed: %s", e)
        profile = "3b"
        hw = None

    models = MODEL_PROFILES[profile]
    return {
        "profile": profile,
        "text_model": models["text_model"],
        "vision_model": models["vision_model"],
        "embedding_model": "nomic-embed-text",
        "is_low_spec": profile == "3b",
        "hardware": hw,
        "pull_commands": [
            f"ollama pull {models['text_model']}",
            f"ollama pull {models['vision_model']}  # 图片 OCR 功能（可选）",
        ],
    }


def resolve_ollama_models(
    available_models: List[str],
    hardware: Optional[HardwareInfo] = None,
) -> Dict[str, Any]:
    """Ollama 已连接且有文本模型时，解析应使用的实际模型名。"""
    rec = get_hardware_recommendation(hardware)
    profile = rec["profile"]

    # 在可用模型中查找与推荐套装匹配的模型名（同时比 base 名与参数量）
    text_model = _pick_installed_model(
        available_models, MODEL_PROFILES[profile]["text_model"]
    )
    vision_model = _pick_installed_model(
        available_models,
        MODEL_PROFILES[profile]["vision_model"],
        extra_prefixes=("qwen2.5vl", "qwen2.5-vl"),
    )

    # 兜底：任意 qwen2.5 文本模型
    if text_model is None:
        for m in available_models:
            if _model_prefix_match(m, TEXT_MODEL_PREFIXES):
                text_model = m
                break

    logger.info(
        "Resolved ollama models for profile=%s: text=%s vision=%s (installed=%d)",
        profile, text_model, vision_model, len(available_models),
    )
    return {
        "profile": profile,
        "text_model": text_model or MODEL_PROFILES[profile]["text_model"],
        "vision_model": vision_model,
        "embedding_model": "nomic-embed-text",
        "vl_available": vision_model is not None,
    }
