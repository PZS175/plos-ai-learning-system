"""插件系统：声明式面板/服务扩展加载器。

约定：数据目录 plugins/<name>/plugin.json：
{
    "name": "示例插件",
    "version": "1.0.0",
    "entry": "main",            // 同目录 main.py，需暴露 register(ctx) -> Any
    "description": "..."
}
加载器扫描目录、校验清单、导入 entry 并调用 register(registry)；
返回注册表 dict，失败插件跳过并记录，不阻塞主程序。
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("services.plugins")

MANIFEST_NAME = "plugin.json"


@dataclass
class LoadedPlugin:
    name: str
    version: str
    description: str
    path: Path
    register_result: Any = None


def _load_manifest(plugin_dir: Path) -> Optional[dict]:
    manifest_path = plugin_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("name") or not data.get("entry"):
            logger.warning("Plugin manifest incomplete: %s", manifest_path)
            return None
        return data
    except Exception as e:
        logger.warning("Plugin manifest parse failed %s: %s", manifest_path, e)
        return None


def _import_entry(plugin_dir: Path, entry: str) -> Optional[Any]:
    module_path = plugin_dir / f"{entry}.py"
    if not module_path.exists():
        logger.warning("Plugin entry missing: %s", module_path)
        return None
    spec = importlib.util.spec_from_file_location(
        f"plos_plugin_{plugin_dir.name}_{entry}", module_path
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_plugins(
    plugins_dir: Path, registry: Optional[Dict[str, Any]] = None
) -> List[LoadedPlugin]:
    """扫描插件目录并注册，返回成功加载的插件列表。

    registry：传给每个插件 register(registry) 的共享注册表，
    插件可往里写面板工厂/服务实例等。
    """
    plugins_dir = Path(plugins_dir)
    registry = registry if registry is not None else {}
    loaded: List[LoadedPlugin] = []
    if not plugins_dir.exists():
        return loaded

    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest = _load_manifest(plugin_dir)
        if manifest is None:
            continue
        try:
            module = _import_entry(plugin_dir, str(manifest["entry"]))
            register = getattr(module, "register", None)
            result = register(registry) if callable(register) else None
            loaded.append(
                LoadedPlugin(
                    name=str(manifest["name"]),
                    version=str(manifest.get("version", "0.0.0")),
                    description=str(manifest.get("description", "")),
                    path=plugin_dir,
                    register_result=result,
                )
            )
            logger.info("Plugin loaded: %s v%s", manifest["name"], manifest.get("version"))
        except Exception as e:
            logger.error("Plugin load failed %s: %s", plugin_dir.name, e)
    return loaded
