"""端到端对话测试。

使用本地 Ollama 模型进行真实对话，验证底层 + 业务编排链路。

运行：
    cd plos_ai
    .venv\\Scripts\\Activate.ps1
    $env:PYTHONPATH="src"; py -m tests.e2e_chat
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plos.ai import ModelManager
from plos.config import detect_hardware, load_config
from plos.services import ChatService, SessionManager
from plos.utils.logger import setup_logger


def main() -> int:
    setup_logger()

    print("=" * 60)
    print("PLOS AI E2E Chat Test")
    print("=" * 60)

    config = load_config()
    hardware = detect_hardware()
    manager = ModelManager(config, hardware=hardware)

    print(f"Backend: {manager.backend_type.value}")
    print(f"Text models: {manager.list_text_models()}")
    print(f"Vision models: {manager.list_vision_models()}")
    print(f"Text available: {manager.is_text_available()}")
    print(f"Vision available: {manager.is_vision_available()}")

    if not manager.is_text_available():
        print("ERROR: Text model not available. Please check Ollama.")
        return 1

    session = SessionManager()
    chat = ChatService(manager, session)
    conv_id = chat.create_conversation("E2E Test")

    test_prompt = "请用一句话介绍牛顿第二定律。"
    print(f"\nUser: {test_prompt}")
    try:
        response = chat.send_message(conv_id, test_prompt, use_rag=False)
        print(f"AI: {response.content}")
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    print("\nE2E chat test passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
