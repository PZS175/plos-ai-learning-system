"""UI 应用启动入口。

负责：
- 安装全局异常捕获
- 初始化 Qt 应用
- 加载配置与模型管理器
- 启动主窗口与系统托盘

启动顺序：引导对话框（如需配置）→ 品牌闪屏（真实进度，6 阶段）
→ 交叉淡入主窗口。具体阶段编排见 ``plos.ui.bootstrap.StartupCoordinator``。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

# 确保 src 在路径中
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plos.config import detect_hardware, load_config, save_config
from plos.ui.startup_dialog import StartupDialog
from plos.utils.crash_handler import install_crash_handler
from plos.utils.logger import setup_logger

logger = setup_logger()


def _resolve_icon_path() -> Path:
    """应用图标：开发态取项目 installer/，打包态取 _MEIPASS。"""
    icon_path = Path(__file__).resolve().parents[3] / "installer" / "app.ico"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and (Path(meipass) / "installer" / "app.ico").exists():
        icon_path = Path(meipass) / "installer" / "app.ico"
    return icon_path


def _create_splash() -> Optional[object]:
    """创建并显示品牌闪屏（失败则不阻塞启动）。"""
    try:
        from PyQt6.QtGui import QIcon

        from plos.ui.splash_screen import SplashScreen

        icon_path = _resolve_icon_path()
        pixmap = QIcon(str(icon_path)).pixmap(256, 256) if icon_path.exists() else None
        splash = SplashScreen(logo=pixmap)
        splash.start()
        return splash
    except Exception as e:
        logger.warning("Splash screen skipped: %s", e)
        return None


def _resume_skipped_in_background(coordinator, outcome) -> None:
    """主窗口显示后，在后台补做被跳过的非核心初始化（不阻塞界面）。"""
    if "ocr" not in outcome.skipped:
        return

    def _work() -> None:
        try:
            from plos.services.ocr_service import OCRService

            available = OCRService(coordinator.model_manager).is_image_service_available()
            logger.info("后台补做 OCR 初始化完成: available=%s", available)
        except Exception as e:
            logger.warning("后台补做 OCR 初始化失败: %s", e)

    QTimer.singleShot(800, lambda: threading.Thread(target=_work, daemon=True).start())


def _fade_in_window(window) -> None:
    """显示并平滑唤起主窗口（时长受全局动画策略限制，低配设备直接显示）。"""
    try:
        window.setWindowOpacity(0.0)
        window.show()
        window.raise_()
    except Exception as e:
        logger.warning("Failed to show main window: %s", e)
        return
    try:
        from plos.ui.interactions import policy

        duration = policy().duration(180)
    except Exception:
        duration = 180
    if duration <= 0:
        window.setWindowOpacity(1.0)
        return
    fade_in = QPropertyAnimation(window, b"windowOpacity", window)
    fade_in.setDuration(duration)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
    fade_in.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    # 兜底：无论动画是否被打断，主窗口最终必须完全可见
    QTimer.singleShot(duration + 120, lambda: window.setWindowOpacity(1.0))


def main() -> int:
    """应用主入口。"""
    install_crash_handler()

    # WebEngine（教材导入）要求在 QApplication 创建前设置共享 OpenGL 上下文，
    # 否则 QtWebEngineWidgets 拒绝初始化，内置浏览器显示空白
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    # 启用高分屏适配
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # 在 QApplication 创建前预导入 WebEngine 模块（失败不阻塞，面板有降级提示）
    try:
        from PyQt6 import QtWebEngineWidgets  # noqa: F401
    except Exception:
        pass

    app = QApplication(sys.argv)
    # 修复：系统 locale 为 zh_HK 时 Qt6 QSpinBox 数字字形渲染异常，统一用拉丁数字 locale
    from PyQt6.QtCore import QLocale

    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.China))
    app.setApplicationName("PLOS AI")
    app.setApplicationDisplayName("PLOS AI 个人学习操作系统")

    # 应用窗口图标
    try:
        from PyQt6.QtGui import QIcon

        icon_path = _resolve_icon_path()
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
    except Exception as icon_error:
        logger.warning("Failed to set window icon: %s", icon_error)

    # 全局字体：优先微软雅黑 UI，保证中英文混排清晰且与主题 QSS 一致
    font = QFont("Microsoft YaHei UI", 10)
    if not QFont("Microsoft YaHei UI").exactMatch():
        font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    try:
        config = load_config()
        hardware = detect_hardware()
    except Exception as e:
        logger.error("Failed to load config or detect hardware: %s", e)
        QMessageBox.critical(None, "启动失败", f"初始化失败：\n{e}")
        return 1

    # 显示启动引导对话框：探测 Ollama、选择模式、配置 API Key
    try:
        startup_result = StartupDialog.run(config, hardware)
    except Exception as e:
        logger.error("Startup dialog failed: %s", e)
        QMessageBox.critical(None, "启动失败", f"启动引导失败：\n{e}")
        return 1

    if startup_result.get("action") == "exit":
        logger.info("User chose to exit from startup dialog")
        return 0

    config = startup_result.get("config", config)
    try:
        save_config(config)
    except Exception as e:
        logger.warning("Failed to save startup config: %s", e)

    # 品牌闪屏：分阶段执行初始化，进度与文案按真实业务更新
    splash = _create_splash()

    from plos.ui.bootstrap import StartupCoordinator
    from plos.ui.splash_screen import SplashScreen

    coordinator = StartupCoordinator(
        splash=splash if isinstance(splash, SplashScreen) else None,
        config=config,
        hardware=hardware,
    )

    try:
        outcome = coordinator.run()
    except Exception as e:
        logger.exception("启动编排失败: %s", e)
        if splash is not None:
            try:
                splash.close_now()
            except Exception:
                pass
        QMessageBox.critical(None, "启动失败", f"启动过程出错：\n{e}")
        return 1

    # 用户选择退出 / 关键依赖不可用：安静退出，不闪退也不留残留窗口
    if outcome.action != "ok" or outcome.window is None:
        if splash is not None:
            try:
                splash.close_now()
            except Exception:
                pass
        logger.info("启动中止: action=%s", outcome.action)
        return 0

    window = outcome.window
    theme_manager = coordinator.theme_manager

    # 主题切换后保存到配置
    if theme_manager is not None:

        def _on_theme_changed(theme: str) -> None:
            config.setdefault("ui", {})["theme"] = theme
            try:
                save_config(config)
            except Exception as e:
                logger.warning("Failed to save theme preference: %s", e)

        theme_manager.theme_changed.connect(_on_theme_changed)

    # 主窗口保持隐藏，交由启动遮罩收尾：遮罩完全淡出后再显示并淡入主窗口，
    # 保证启动期间遮罩外圈不会露出主窗口标题栏 / 菜单栏 / 背景
    if splash is not None:
        try:
            splash.finish_to(window)
        except Exception as e:
            logger.warning("Splash transition failed, fallback to fade-in: %s", e)
            _fade_in_window(window)
    else:
        _fade_in_window(window)

    # 用户跳过 / 超时跳过的非核心初始化，改到后台补齐
    _resume_skipped_in_background(coordinator, outcome)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
