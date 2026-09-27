"""全局快捷键监听。

优先使用 pynput 实现真正的全局热键；如果未安装，则降级为应用内 QShortcut。
热键配置从 config 中读取。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut

from ..utils.logger import get_logger

logger = get_logger("ui.hotkey_listener")


class HotkeyListener(QObject):
    """全局/应用级快捷键监听。"""

    screenshot_requested = pyqtSignal()
    clipboard_requested = pyqtSignal()
    show_window_requested = pyqtSignal()

    def __init__(self, parent=None, config: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.config = config or {}
        self._global_listener = None
        self._fallback_shortcuts: list = []

    def start(self) -> None:
        """启动热键监听。"""
        try:
            self._start_pynput()
            logger.info("Global hotkeys started via pynput")
        except Exception as e:
            logger.warning("Failed to start pynput global hotkeys: %s", e)
            self._start_fallback()
            logger.info("Fallback application-level hotkeys started")

    def stop(self) -> None:
        """停止热键监听。"""
        if self._global_listener is not None:
            try:
                self._global_listener.stop()
            except Exception as e:
                logger.warning("Failed to stop global hotkey listener: %s", e)
            self._global_listener = None

    @staticmethod
    def _to_pynput_combo(seq: str) -> str:
        """将 'ctrl+alt+a' 转换为 pynput 需要的 '<ctrl>+<alt>+a'。"""
        parts = [p.strip().lower() for p in seq.split("+")]
        converted = []
        for p in parts:
            if p in ("ctrl", "alt", "shift", "cmd"):
                converted.append(f"<{p}>")
            else:
                converted.append(p)
        return "+".join(converted)

    def _start_pynput(self) -> None:
        """使用 pynput 注册全局热键。"""
        from pynput import keyboard

        hotkeys_cfg = self.config.get("hotkeys", {})
        combo_map: Dict[str, Any] = {}

        def on_screenshot():
            self.screenshot_requested.emit()

        def on_clipboard():
            self.clipboard_requested.emit()

        def on_show():
            self.show_window_requested.emit()

        if "screenshot" in hotkeys_cfg:
            combo_map[self._to_pynput_combo(hotkeys_cfg["screenshot"])] = on_screenshot
        if "clipboard_capture" in hotkeys_cfg:
            combo_map[self._to_pynput_combo(hotkeys_cfg["clipboard_capture"])] = on_clipboard
        if "show_window" in hotkeys_cfg:
            combo_map[self._to_pynput_combo(hotkeys_cfg["show_window"])] = on_show

        self._global_listener = keyboard.GlobalHotKeys(combo_map)
        self._global_listener.start()

    def _start_fallback(self) -> None:
        """降级方案：应用内 QShortcut。"""
        parent = self.parent()
        if parent is None:
            return

        hotkeys_cfg = self.config.get("hotkeys", {})
        mappings = {
            "screenshot": self.screenshot_requested,
            "clipboard_capture": self.clipboard_requested,
            "show_window": self.show_window_requested,
        }
        for key, signal in mappings.items():
            seq = hotkeys_cfg.get(key, "")
            if not seq:
                continue
            shortcut = QShortcut(QKeySequence(seq.replace("+", "+")), parent)
            shortcut.activated.connect(signal.emit)
            self._fallback_shortcuts.append(shortcut)
