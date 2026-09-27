"""插件管理器：发现、加载、启用/禁用插件并持久化启用状态。

加载来源（同名 ID 时内置优先，外部插件不允许覆盖内置）：
1. 内置插件：``plos/plugins/builtin/<id>/``（随程序分发）；
2. 外部插件：数据目录 ``plugins/<id>/``（用户自行安装）。

插件库（可选安装源）：``plos/plugins/store/<id>/`` 存放可安装的外部插件，
用户可在插件管理面板里一键安装到数据目录，也可随时卸载。

持久化规则（保证“用户意图优先”）：
- 扫描发现（discover）**不写**配置；
- 新发现插件默认启用，但仅在用户显式启用/禁用时才落盘；
- ``enable`` / ``disable`` 是唯一修改启用状态的入口；
- 启动时只加载 ``enabled != False`` 的插件。

禁用时立即通知主窗口撤销该插件注册的全部导航入口与面板
（removeWidget + deleteLater），无需重启即可生效。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from ..utils.paths import get_data_dir
from .context import PluginContext

logger = get_logger("plugins.manager")

MANIFEST_NAME = "plugin.json"
CONFIG_NAME = "plugins_config.json"


@dataclass
class PluginInfo:
    """插件元数据与运行状态。"""

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    path: Path
    builtin: bool
    enabled: bool = True
    loaded: bool = False
    error: str = ""
    panels: List[str] = field(default_factory=list)

    @property
    def status_text(self) -> str:
        if self.error:
            return f"加载失败：{self.error}"
        if self.loaded:
            return "运行中"
        return "已禁用" if not self.enabled else "未加载"


class PluginManager:
    """扫描、加载并管理插件的生命周期。"""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.builtin_dir = Path(__file__).resolve().parent / "builtin"
        self.store_dir = Path(__file__).resolve().parent / "store"
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.user_plugins_dir = self.data_dir / "plugins"
        self.config_path = self.data_dir / CONFIG_NAME
        self._config: Dict[str, Any] = {"enabled": {}}
        self._plugins: Dict[str, PluginInfo] = {}
        self._modules: Dict[str, Any] = {}
        self._contexts: Dict[str, PluginContext] = {}
        self._main_window: Any = None
        self._load_config()

    # ------------------------------------------------------------------
    # 配置持久化
    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        try:
            if self.config_path.exists():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("enabled"), dict):
                    self._config = {"enabled": {str(k): bool(v) for k, v in data["enabled"].items()}}
        except Exception as e:
            logger.warning("Load plugins config failed: %s", e)
            self._config = {"enabled": {}}

    def _save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("Save plugins config failed: %s", e)

    def _is_enabled(self, plugin_id: str) -> bool:
        # 未显式配置过的插件默认启用
        return bool(self._config.get("enabled", {}).get(plugin_id, True))

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------
    def _load_manifest(self, plugin_dir: Path) -> Optional[dict]:
        manifest_path = plugin_dir / MANIFEST_NAME
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("name"):
                logger.warning("Plugin manifest incomplete: %s", manifest_path)
                return None
            data.setdefault("entry", "main")
            return data
        except Exception as e:
            logger.warning("Plugin manifest parse failed %s: %s", manifest_path, e)
            return None

    def discover(self) -> List[PluginInfo]:
        """扫描内置与外部插件目录，刷新元数据（不执行入口代码、不写配置）。"""
        discovered: Dict[str, PluginInfo] = {}

        def scan(base: Path, builtin: bool) -> None:
            if not base.exists():
                return
            for plugin_dir in sorted(base.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                manifest = self._load_manifest(plugin_dir)
                if manifest is None:
                    continue
                pid = plugin_dir.name
                if pid in discovered:
                    logger.warning("Plugin id conflict, skip external: %s", pid)
                    continue
                discovered[pid] = PluginInfo(
                    plugin_id=pid,
                    name=str(manifest.get("name", pid)),
                    version=str(manifest.get("version", "0.0.0")),
                    description=str(manifest.get("description", "")),
                    author=str(manifest.get("author", "")),
                    path=plugin_dir,
                    builtin=builtin,
                    enabled=self._is_enabled(pid),
                )

        scan(self.builtin_dir, builtin=True)
        scan(self.user_plugins_dir, builtin=False)

        # 保留已加载插件的运行态字段
        for pid, info in discovered.items():
            old = self._plugins.get(pid)
            if old is not None:
                info.loaded = old.loaded
                info.error = old.error
                info.panels = list(old.panels)
        self._plugins = discovered
        return list(discovered.values())

    def list_plugins(self) -> List[PluginInfo]:
        """返回全部已知插件（若尚未扫描会先触发一次 discover）。"""
        if not self._plugins:
            self.discover()
        return sorted(self._plugins.values(), key=lambda p: (not p.builtin, p.plugin_id))

    # ------------------------------------------------------------------
    # 加载 / 卸载
    # ------------------------------------------------------------------
    def _import_entry(self, info: PluginInfo) -> Optional[Any]:
        entry = "main"
        manifest = self._load_manifest(info.path)
        if manifest is not None:
            entry = str(manifest.get("entry", "main"))
        module_path = info.path / f"{entry}.py"
        if not module_path.exists():
            raise FileNotFoundError(f"入口文件缺失：{module_path.name}")
        mod_name = f"plos_plugin_{info.plugin_id}_{entry}"
        spec = importlib.util.spec_from_file_location(mod_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载入口模块：{module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(mod_name, None)
            raise
        return module

    def _make_context(self, info: PluginInfo) -> PluginContext:
        pid = info.plugin_id

        def _register_panel_cb(plugin_id, key, title, icon, subtitle, factory, ctx):
            full_key = f"plugin:{plugin_id}:{key}"
            if self._main_window is not None:
                self._main_window.register_plugin_panel(
                    plugin_id=plugin_id,
                    full_key=full_key,
                    title=title,
                    icon=icon,
                    subtitle=subtitle,
                    factory=factory,
                    ctx=ctx,
                )
            return full_key

        def _navigate_cb(panel_key):
            if self._main_window is not None:
                return self._main_window.switch_to_panel(panel_key)
            return False

        def _current_user_id():
            try:
                mw = self._main_window
                if mw is not None and getattr(mw, "user_service", None) is not None:
                    return mw.user_service.get_current_user_id()
            except Exception:
                pass
            return 0

        return PluginContext(
            plugin_id=pid,
            plugin_dir=info.path,
            data_root=self.data_dir,
            current_user_id_cb=_current_user_id,
            register_panel_cb=_register_panel_cb,
            navigate_cb=_navigate_cb,
        )

    def _load(self, info: PluginInfo) -> bool:
        """加载单个插件：导入入口并调用 register(ctx)。"""
        try:
            module = self._import_entry(info)
            self._modules[info.plugin_id] = module
            register = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError("入口文件未暴露 register(ctx) 函数")
            ctx = self._make_context(info)
            self._contexts[info.plugin_id] = ctx
            register(ctx)
            info.loaded = True
            info.error = ""
            info.panels = list(ctx.panel_keys)
            logger.info("Plugin loaded: %s v%s (%d panels)", info.name, info.version, len(info.panels))
            return True
        except Exception as e:
            info.loaded = False
            info.error = f"{type(e).__name__}: {e}"
            logger.error("Plugin load failed %s: %s", info.plugin_id, e)
            return False

    def _unload(self, info: PluginInfo) -> None:
        """卸载插件：调用插件清理回调并撤销其注册的全部面板与导航入口。"""
        # 先给插件一次清理机会（如关闭置顶悬浮窗、停止定时器）
        module = self._modules.get(info.plugin_id)
        if module is not None:
            teardown = getattr(module, "unregister", None)
            if callable(teardown):
                try:
                    teardown(self._contexts.get(info.plugin_id))
                except Exception as e:
                    logger.warning("Plugin unregister(%s) failed: %s", info.plugin_id, e)
        self._contexts.pop(info.plugin_id, None)

        for full_key in list(info.panels):
            try:
                if self._main_window is not None:
                    self._main_window.unregister_plugin_panel(full_key)
            except Exception as e:
                logger.warning("Revoke panel %s failed: %s", full_key, e)
        info.panels.clear()
        info.loaded = False
        # 从 sys.modules 移除，便于下次启用时代码热更新
        mod_name = next((n for n in list(sys.modules) if n.startswith(f"plos_plugin_{info.plugin_id}_")), None)
        if mod_name:
            sys.modules.pop(mod_name, None)
        self._modules.pop(info.plugin_id, None)

    def load_all(self, main_window: Any) -> None:
        """启动时调用：发现并加载全部已启用插件。失败插件跳过，不阻塞主程序。"""
        self._main_window = main_window
        self.discover()
        for info in self._plugins.values():
            if info.enabled and not info.loaded:
                self._load(info)

    # ------------------------------------------------------------------
    # 启用 / 禁用（唯一写配置入口，立即生效）
    # ------------------------------------------------------------------
    def enable(self, plugin_id: str) -> bool:
        info = self._plugins.get(plugin_id)
        if info is None:
            return False
        self._config.setdefault("enabled", {})[plugin_id] = True
        self._save_config()
        info.enabled = True
        if not info.loaded:
            return self._load(info)
        return True

    def disable(self, plugin_id: str) -> bool:
        info = self._plugins.get(plugin_id)
        if info is None:
            return False
        self._config.setdefault("enabled", {})[plugin_id] = False
        self._save_config()
        info.enabled = False
        if info.loaded:
            self._unload(info)
        return True

    def rescan(self) -> List[PluginInfo]:
        """重新扫描目录：新启用的加载，已被禁用/删除的卸载。"""
        previous = dict(self._plugins)
        self.discover()
        # 目录已被删除（如手工卸载）的插件：撤销其面板，避免残留入口
        for plugin_id, old_info in previous.items():
            if plugin_id not in self._plugins and old_info.loaded:
                self._unload(old_info)
        for info in self._plugins.values():
            if info.enabled and not info.loaded:
                self._load(info)
            elif not info.enabled and info.loaded:
                self._unload(info)
        return list(self._plugins.values())

    # ------------------------------------------------------------------
    # 插件库：安装 / 卸载外部插件
    # ------------------------------------------------------------------
    def list_store_plugins(self) -> List[Dict[str, Any]]:
        """列出插件库中的可安装插件，并标记是否已安装。"""
        items: List[Dict[str, Any]] = []
        if not self.store_dir.exists():
            return items
        for plugin_dir in sorted(self.store_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest = self._load_manifest(plugin_dir)
            if manifest is None:
                continue
            plugin_id = plugin_dir.name
            items.append(
                {
                    "plugin_id": plugin_id,
                    "name": str(manifest.get("name", plugin_id)),
                    "version": str(manifest.get("version", "0.0.0")),
                    "description": str(manifest.get("description", "")),
                    "author": str(manifest.get("author", "")),
                    "installed": (self.user_plugins_dir / plugin_id).exists(),
                }
            )
        return items

    def install_from_store(self, plugin_id: str) -> bool:
        """把插件库中的插件复制到数据目录并立即启用。"""
        source = self.store_dir / plugin_id
        if not source.is_dir():
            logger.warning("Store plugin not found: %s", plugin_id)
            return False
        target = self.user_plugins_dir / plugin_id
        if target.exists():
            logger.warning("Plugin already installed: %s", plugin_id)
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
        except Exception as e:
            logger.error("Install plugin %s failed: %s", plugin_id, e)
            return False
        self.rescan()
        info = self._plugins.get(plugin_id)
        if info is not None:
            self.enable(plugin_id)
        logger.info("Plugin installed from store: %s", plugin_id)
        return True

    def uninstall(self, plugin_id: str) -> bool:
        """卸载外部插件（仅限数据目录 plugins/ 下的插件）并清除其启用状态。"""
        target = (self.user_plugins_dir / plugin_id).resolve()
        # 安全校验：只允许删除数据目录插件根之下的内容
        try:
            root = self.user_plugins_dir.resolve()
        except Exception:
            return False
        if root not in target.parents or not target.is_dir():
            logger.warning("Refuse to uninstall non-external plugin: %s", plugin_id)
            return False

        info = self._plugins.get(plugin_id)
        if info is not None and info.loaded:
            self._unload(info)
        self._plugins.pop(plugin_id, None)
        try:
            shutil.rmtree(target)
        except Exception as e:
            logger.error("Remove plugin dir %s failed: %s", target, e)
            return False
        self._config.setdefault("enabled", {}).pop(plugin_id, None)
        self._save_config()
        logger.info("Plugin uninstalled: %s", plugin_id)
        return True
