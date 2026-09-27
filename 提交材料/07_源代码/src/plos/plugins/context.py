"""插件运行上下文：注入给插件 ``register(ctx)`` 的 API 门面。

设计原则：
- 插件不直接 import 主窗口，所有与主程序的交互都通过 ctx 方法，
  避免插件失效时拖垮主程序；
- 每个插件拥有独立可写数据目录，状态文件按当前账号命名空间隔离，
  切换账号不会串数据；
- register_panel 只登记工厂回调，由 PluginManager / 主窗口决定何时
  构建与销毁面板（禁用时立即从导航与堆栈中移除）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..utils.logger import get_logger


class PluginContext:
    """传给插件 register() 的上下文对象。

    由 PluginManager 在加载每个插件时创建并注入，插件无需自行实例化。
    """

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        data_root: Path,
        current_user_id_cb: Callable[[], int],
        register_panel_cb: Callable[..., str],
        navigate_cb: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self._data_root = Path(data_root)
        self._current_user_id_cb = current_user_id_cb
        self._register_panel_cb = register_panel_cb
        self._navigate_cb = navigate_cb
        self.logger = get_logger(f"plugin.{plugin_id}")
        # 本次 register() 期间登记的面板全键，管理器据此做禁用撤销
        self.panel_keys: List[str] = []

    # ------------------------------------------------------------------
    # 面板注册
    # ------------------------------------------------------------------
    def register_panel(
        self,
        key: str,
        title: str,
        factory: Callable[["PluginContext"], Any],
        icon: str = "🧩",
        subtitle: str = "",
    ) -> str:
        """向主界面注册一个功能页。

        Args:
            key: 插件内唯一的面板标识（无需带插件名，管理器会自动加前缀）。
            title: 导航与页头显示名。
            factory: 无界面时延迟构建的工厂函数，接收本 ctx，返回 QWidget。
            icon: 导航图标（emoji 或单个字符）。
            subtitle: 页头副标题（可选）。

        Returns:
            面板在主窗口中的全局唯一键。
        """
        full_key = self._register_panel_cb(
            plugin_id=self.plugin_id,
            key=str(key),
            title=str(title),
            icon=str(icon) or "🧩",
            subtitle=str(subtitle),
            factory=factory,
            ctx=self,
        )
        if full_key not in self.panel_keys:
            self.panel_keys.append(full_key)
        return full_key

    def navigate(self, panel_key: str) -> bool:
        """跳转到主程序内置面板（如 'errorbook'、'notes'）。成功返回 True。"""
        if self._navigate_cb is None:
            return False
        try:
            return bool(self._navigate_cb(panel_key))
        except Exception as e:
            self.logger.warning("navigate(%s) failed: %s", panel_key, e)
            return False

    # ------------------------------------------------------------------
    # 账号与私有数据
    # ------------------------------------------------------------------
    def current_user_id(self) -> int:
        """当前登录账号 ID（未登录时为 0）。"""
        try:
            return int(self._current_user_id_cb() or 0)
        except Exception:
            return 0

    @property
    def data_dir(self) -> Path:
        """插件私有可写目录（按账号隔离的状态文件也放在这里）。"""
        path = self._data_root / "plugin_data" / self.plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _state_path(self, name: str) -> Path:
        safe = "".join(c for c in str(name) if c.isalnum() or c in ("_", "-")) or "state"
        return self.data_dir / f"{safe}.u{self.current_user_id()}.json"

    def load_state(self, name: str, default: Any = None) -> Any:
        """读取按账号隔离的 JSON 状态；文件不存在或损坏时返回 default。"""
        path = self._state_path(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.logger.warning("load_state(%s) failed: %s", name, e)
            return default

    def save_state(self, name: str, data: Any) -> bool:
        """写入按账号隔离的 JSON 状态。"""
        path = self._state_path(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as e:
            self.logger.error("save_state(%s) failed: %s", name, e)
            return False

    # ------------------------------------------------------------------
    # 主题与 UI 工具（惰性引用，避免无界面环境导入失败）
    # ------------------------------------------------------------------
    def theme_colors(self) -> Dict[str, str]:
        try:
            from ..ui.ui_utils import theme_colors

            return theme_colors()
        except Exception:
            return {}

    def show_info(self, parent: Any, title: str, message: str) -> None:
        from ..ui.ui_utils import show_info

        show_info(parent, title, message)

    def show_warning(self, parent: Any, title: str, message: str) -> None:
        from ..ui.ui_utils import show_warning

        show_warning(parent, title, message)

    def show_error(self, parent: Any, title: str, message: str) -> None:
        from ..ui.ui_utils import show_error

        show_error(parent, title, message)

    def ask_confirm(self, parent: Any, title: str, message: str) -> bool:
        from ..ui.ui_utils import ask_confirm

        return ask_confirm(parent, title, message)
