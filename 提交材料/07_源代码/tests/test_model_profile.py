"""验证 7B/3B 模型套装自动选择逻辑。"""

from __future__ import annotations

from plos.config import (
    MODEL_PROFILES,
    get_recommended_model_profile,
    get_recommended_settings,
)
from plos.core.models import HardwareInfo


def _hardware(ram_gb: float, vram_gb: float, low_spec: bool) -> HardwareInfo:
    return HardwareInfo(
        os_name="Windows",
        cpu_model="Test CPU",
        cpu_cores=8,
        ram_total_gb=ram_gb,
        gpu_name="RTX 3060" if vram_gb else None,
        gpu_vram_gb=vram_gb,
        is_low_spec=low_spec,
    )


def test_high_ram_selects_7b_profile():
    assert get_recommended_model_profile(_hardware(32.0, 0.0, False)) == "7b"


def test_low_ram_selects_3b_profile():
    # 低配：内存 ≤7G（旧夹具用 8.0G + is_low_spec=True 与三档分级自相矛盾）
    assert get_recommended_model_profile(_hardware(4.0, 0.0, True)) == "3b"


def test_mid_ram_selects_mid_profile():
    """中配（8~12G，无大显存）：7B 文本 + 3B 视觉。"""
    assert get_recommended_model_profile(_hardware(8.0, 0.0, False)) == "mid"
    settings = get_recommended_settings(_hardware(12.0, 0.0, False))
    assert settings["text_model"] == MODEL_PROFILES["7b"]["text_model"], "中配应有 7B 文本模型"
    assert settings["vision_model"] == MODEL_PROFILES["3b"]["vision_model"], "中配 VL 应强制 3B"
    assert settings["low_spec_mode"] != "on", "中配不应启用低配模式"


def test_high_vram_selects_7b_profile():
    assert get_recommended_model_profile(_hardware(8.0, 12.0, False)) == "7b"


def test_recommended_settings_match_profile():
    high_ram = _hardware(32.0, 0.0, False)
    settings = get_recommended_settings(high_ram)
    assert settings["text_model"] == MODEL_PROFILES["7b"]["text_model"]
    assert settings["vision_model"] == MODEL_PROFILES["7b"]["vision_model"]
    assert settings["embedding_model"] == "nomic-embed-text"

    low_ram = _hardware(4.0, 0.0, True)
    settings = get_recommended_settings(low_ram)
    assert settings["text_model"] == MODEL_PROFILES["3b"]["text_model"]
    assert settings["vision_model"] == MODEL_PROFILES["3b"]["vision_model"]
    assert settings["low_spec_mode"] == "on", "低配应启用低配模式"
