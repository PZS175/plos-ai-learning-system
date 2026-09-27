"""UI 启动测试 demo。

不进入 QApplication 主循环，仅验证应用和各面板能成功初始化。

运行：
    cd plos_ai
    $env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; py -m tests.demo_ui
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 必须在创建 QApplication 前设置平台插件
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from plos.ai import ModelManager
from plos.config import detect_hardware, load_config
from plos.ui.main_window import MainWindow
from plos.ui.theme_manager import ThemeManager


def main() -> int:
    app = QApplication(sys.argv)

    print("Loading config and model manager...")
    config = load_config()
    hardware = detect_hardware()
    model_manager = ModelManager(config, hardware=hardware)

    print("Applying theme...")
    theme_manager = ThemeManager(config.get("ui", {}).get("theme", "light"))
    theme_manager.apply(app)

    print("Creating MainWindow...")
    window = MainWindow(config=config, model_manager=model_manager, theme_manager=theme_manager)
    print(f"MainWindow created: {window.windowTitle()}")

    # 验证各面板已注册
    panels = ["dashboard", "chat", "ocr", "knowledge", "errorbook", "flashcard", "settings"]
    for key in panels:
        assert key in window._panels, f"Panel {key} not registered"
        print(f"  Panel '{key}' OK")

    window.close()
    print("UI demo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
