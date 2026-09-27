"""Hardware detection and recommendation helpers."""

from __future__ import annotations

import os
import platform
from typing import Any, Dict, Optional

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

try:
    import pynvml
except Exception:  # pragma: no cover
    pynvml = None  # type: ignore

from ..core.enums import HardwareTier, LowSpecMode
from ..core.models import HardwareInfo


# 三档分级阈值（单位 GB）
_RAM_LOW_MAX = 7        # ≤7GB：低配
_RAM_MID_MAX = 12       # 8~12GB：中配
# >12GB：高配

# 兼容旧版两档逻辑的阈值
_LOW_SPEC_RAM_GB = 8
_LOW_SPEC_VRAM_GB = 4
_HIGH_SPEC_RAM_GB = 16
_HIGH_SPEC_VRAM_GB = 8


def _get_cpu_info() -> tuple[str, int]:
    """Return CPU model name and physical/logical core count."""
    cpu_model = platform.processor() or "Unknown CPU"
    if psutil is not None:
        cpu_count = psutil.cpu_count(logical=True) or 1
    else:
        cpu_count = os.cpu_count() or 1
    return cpu_model, cpu_count


def _get_ram_gb() -> float:
    """Return total system RAM in GB."""
    if psutil is not None:
        return round(psutil.virtual_memory().total / (1024**3), 2)
    return 0.0


def _get_gpu_info() -> tuple[Optional[str], float]:
    """Return primary GPU name and total VRAM in GB."""
    if pynvml is None:
        return None, 0.0

    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="ignore")
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        vram_gb = round(info.total / (1024**3), 2)
        return name, vram_gb
    except Exception:
        return None, 0.0
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def is_high_spec(hardware: HardwareInfo) -> bool:
    """高性能：显存≥8G 或 内存≥16G。"""
    if hardware.gpu_name and hardware.gpu_vram_gb >= _HIGH_SPEC_VRAM_GB:
        return True
    if hardware.ram_total_gb >= _HIGH_SPEC_RAM_GB:
        return True
    return False


def detect_hardware() -> HardwareInfo:
    """Detect key hardware capabilities of the host machine."""
    cpu_model, cpu_cores = _get_cpu_info()
    ram_total_gb = _get_ram_gb()
    gpu_name, gpu_vram_gb = _get_gpu_info()

    info = HardwareInfo(
        os_name=platform.system(),
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        ram_total_gb=ram_total_gb,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram_gb,
        is_low_spec=False,
    )
    info.is_low_spec = not is_high_spec(info)
    return info


def is_low_spec(hardware: HardwareInfo) -> bool:
    """Determine whether the machine should run in low-spec mode."""
    return not is_high_spec(hardware)


def get_hardware_tier(hardware: HardwareInfo, force_low_spec: bool = False) -> HardwareTier:
    """按系统总物理内存返回三档硬件分级。

    - ``force_low_spec=True`` 时无论实际硬件如何，一律返回 LOW，
      供设置页「强制低配模式」开关使用。
    - 分级依据仅看内存：≤7GB → LOW，8~12GB → MID，>12GB → HIGH。
    """
    if force_low_spec:
        return HardwareTier.LOW
    ram = hardware.ram_total_gb or 0.0
    if ram <= _RAM_LOW_MAX:
        return HardwareTier.LOW
    if ram <= _RAM_MID_MAX:
        return HardwareTier.MID
    return HardwareTier.HIGH


def is_low_spec_forced(settings_value: Optional[str]) -> bool:
    """从 settings 表的 force_low_spec 值判断是否强制低配。

    settings 中存储字符串 "true"/"false"（或不存在），统一在此解析。
    """
    if not settings_value:
        return False
    return str(settings_value).strip().lower() in ("1", "true", "yes", "on")


def get_force_low_spec_setting(db=None) -> bool:
    """从数据库 settings 表读取「强制低配模式」开关。

    允许传入 db 实例；不传则惰性导入 get_db，避免模块级循环依赖。
    读取失败时安全返回 False（不启用强制低配）。
    """
    try:
        if db is None:
            from ..db import get_db

            db = get_db()
        row = db.fetchone("SELECT value FROM settings WHERE key = ?", ("force_low_spec",))
        if row:
            return is_low_spec_forced(row["value"])
    except Exception:
        pass
    return False


# 三档模型套装
MODEL_PROFILES = {
    # 高配：7B 文本 + 7B 视觉
    "7b": {
        "text_model": "qwen2.5:7b-q4_K_M",
        "vision_model": "qwen2.5-vl:7b-q4_K_M",
    },
    # 中配：7B 文本 + 3B 视觉（需求要求中配「7B 文本可用、VL 强制 3B-VL」）
    "mid": {
        "text_model": "qwen2.5:7b-q4_K_M",
        "vision_model": "qwen2.5-vl:3b-q4_K_M",
    },
    # 低配：全部 3B，并启用低配模式（视觉等功能关闭）
    "3b": {
        "text_model": "qwen2.5:3b-instruct-q4_K_M",
        "vision_model": "qwen2.5-vl:3b-q4_K_M",
    },
}

#: 各套装是否启用低配模式（关闭视觉等重资源功能）
_PROFILE_LOW_SPEC = {"7b": False, "mid": False, "3b": True}


def get_recommended_model_profile(hardware: HardwareInfo) -> str:
    """按三档硬件分级推荐模型套装。

    - 高配（显存 ≥8G 或 内存 ≥16G）→ ``7b``：7B 文本 + 7B 视觉；
    - 中配（内存 8~12G / 无大显存）→ ``mid``：7B 文本 + 3B 视觉；
    - 低配（内存 ≤7G）→ ``3b``：全 3B 并启用低配模式。

    显存充足时优先按高配处理（例如 8G 内存 + 12G 显存的本子仍可跑 7B 视觉）。
    """
    if is_high_spec(hardware):
        return "7b"
    ram = hardware.ram_total_gb or 0.0
    if ram > _RAM_LOW_MAX:
        return "mid"
    return "3b"


def get_recommended_settings(hardware: HardwareInfo) -> Dict[str, Any]:
    """Recommend backend settings based on detected hardware."""
    profile = get_recommended_model_profile(hardware)
    models = MODEL_PROFILES[profile]
    low_spec = _PROFILE_LOW_SPEC.get(profile, True)
    num_threads = max(1, (hardware.cpu_cores // 2)) if hardware.cpu_cores > 1 else 1
    use_gpu = hardware.gpu_name is not None

    return {
        "text_model": models["text_model"],
        "vision_model": models["vision_model"],
        "embedding_model": "nomic-embed-text",
        "use_gpu": use_gpu,
        "low_spec_mode": LowSpecMode.ON.value if low_spec else LowSpecMode.OFF.value,
        "num_threads": 0 if profile == "7b" else num_threads,
        "gpu_layers": 0 if not use_gpu else 32,
        "model_profile": profile,
    }


__all__ = [
    "detect_hardware",
    "get_recommended_settings",
    "is_low_spec",
    "is_high_spec",
    "get_recommended_model_profile",
    "MODEL_PROFILES",
    "get_hardware_tier",
    "is_low_spec_forced",
    "get_force_low_spec_setting",
    "HardwareTier",
]
