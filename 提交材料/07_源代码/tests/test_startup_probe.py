"""验证启动探测逻辑。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plos.config import MODEL_PROFILES
from plos.core.models import HardwareInfo
from plos.utils.startup_probe import check_models, get_hardware_recommendation, resolve_ollama_models


def test_check_models() -> None:
    print("\n[1] check_models")

    models = ["qwen2.5:7b-instruct-q4_k_m", "qwen2.5vl:7b-q4_K_M"]
    status = check_models(models)
    assert status["text_exists"] is True
    assert status["vl_exists"] is True

    models = ["qwen2.5:3b-q4_K_M"]
    status = check_models(models)
    assert status["text_exists"] is True
    assert status["vl_exists"] is False

    models = ["nomic-embed-text:latest"]
    status = check_models(models)
    assert status["text_exists"] is False
    assert status["vl_exists"] is False

    print("  check_models: OK")


def test_resolve_ollama_models() -> None:
    print("\n[2] resolve_ollama_models")

    hw = HardwareInfo(
        os_name="Windows",
        cpu_model="Test CPU",
        cpu_cores=8,
        ram_total_gb=32.0,
        gpu_name=None,
        gpu_vram_gb=0.0,
        is_low_spec=False,
    )
    available = ["qwen2.5:7b-instruct-q4_k_m", "qwen2.5vl:7b-q4_K_M"]
    resolved = resolve_ollama_models(available, hw)

    assert resolved["profile"] == "7b"
    assert resolved["text_model"] == "qwen2.5:7b-instruct-q4_k_m"
    assert resolved["vision_model"] == "qwen2.5vl:7b-q4_K_M"
    assert resolved["vl_available"] is True
    print("  resolve_ollama_models: OK")


def test_hardware_recommendation() -> None:
    print("\n[3] get_hardware_recommendation")

    rec = get_hardware_recommendation(
        HardwareInfo(
            os_name="Windows",
            cpu_model="Test CPU",
            cpu_cores=8,
            ram_total_gb=4.0,
            gpu_name=None,
            gpu_vram_gb=0.0,
            is_low_spec=True,
        )
    )
    assert rec["profile"] == "3b"
    assert rec["text_model"] == MODEL_PROFILES["3b"]["text_model"]
    assert len(rec["pull_commands"]) == 2
    print("  hardware recommendation: OK")


def main() -> int:
    test_check_models()
    test_resolve_ollama_models()
    test_hardware_recommendation()
    print("\nStartup probe tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
